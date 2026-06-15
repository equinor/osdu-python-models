#!/usr/bin/env python3
"""Generate typed Pydantic models for OSDU `data` payloads.

OSDU record schemas keep the domain payload under a free-form `data` property
(an `allOf` of abstract building blocks + inline fields). `osdu-python-client`
leaves that untyped on purpose — matching `os-core-common`'s `Map<String,Object>`.
This generator types *only* that `data` block, as an opt-in companion package.

For each configured entity version it:

1. reads the record schema from the pinned snapshot,
2. lifts the `data` sub-schema into a standalone root schema and *bundles* every
   reachable ``../abstract/*`` file ``$ref`` into a self-contained ``$defs`` block
   (one document, internal refs only),
3. runs ``datamodel-codegen`` to emit a single Pydantic v2 ``Data`` module
   (``extra='allow'`` so unknown/forward fields round-trip, like the C# library's
   ``[JsonExtensionData]``).

Generated code is gitignored and regenerable; never hand-edited.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "schemas" / "2026.05.22"
OUT_ROOT = REPO / "src" / "osdu_models"

# (group dir, Type, version) -> generated module path under OUT_ROOT.
# Scoped to WellLog for the PoC; two versions to show side-by-side typing.
TARGETS = [
    ("work-product-component", "WellLog", "1.4.0"),
    ("work-product-component", "WellLog", "1.5.0"),
]

_PKG = {"work-product-component": "workproductcomponent", "master-data": "masterdata"}


def _module_path(group: str, type_name: str, version: str) -> Path:
    ver = "v" + version.replace(".", "_")
    return OUT_ROOT / _PKG[group] / _snake(type_name) / f"{ver}.py"


def _snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _def_key(path: Path) -> str:
    """Stable `$defs` key from a schema file stem, e.g. AbstractRemark_1_0_0."""
    return path.name[: -len(".json")].replace(".", "_").replace("-", "_")


def _bundle(node: object, current_dir: Path, defs: dict[str, object]) -> object:
    """Inline whole-file ``$ref``s into ``defs``, rewriting them to ``#/$defs/key``.

    OSDU abstract refs are all whole-file (no fragments), so each file maps to one
    def. Cycles are handled by reserving the key before recursing.
    """
    if isinstance(node, list):
        return [_bundle(x, current_dir, defs) for x in node]
    if not isinstance(node, dict):
        return node

    # Emit temporal fields as plain `str`: OSDU example payloads carry
    # non-conformant date-time variants (e.g. `...15.55Z`) that a strict
    # `datetime` parser rejects or rewrites, breaking lossless round-trips.
    # Same pragmatic choice the C# library (and os-core-common) makes.
    if node.get("format") in {"date-time", "date", "time"} and node.get("type") == "string":
        node = {k: v for k, v in node.items() if k != "format"}

    ref = node.get("$ref")
    if isinstance(ref, str) and not ref.startswith("#"):
        target = (current_dir / ref).resolve()
        key = _def_key(target)
        if key not in defs:
            defs[key] = {}  # reserve to break ref cycles
            defs[key] = _bundle(json.loads(target.read_text()), target.parent, defs)
        rewritten = {k: v for k, v in node.items() if k != "$ref"}
        rewritten["$ref"] = f"#/$defs/{key}"
        return rewritten

    return {k: _bundle(v, current_dir, defs) for k, v in node.items()}


def _generate_one(group: str, type_name: str, version: str) -> Path:
    schema_dir = SNAPSHOT / group
    record_schema = json.loads(
        (schema_dir / f"{type_name}.{version}.json").read_text()
    )
    data_schema = record_schema["properties"]["data"]

    defs: dict[str, object] = {}
    bundled_data = _bundle(data_schema, schema_dir, defs)

    # Promote `data` to a self-contained root object schema (internal refs only).
    root = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Data",
        "description": f"`data` payload for {type_name} {version}.",
        "type": "object",
        **{k: v for k, v in bundled_data.items() if k != "$schema"},
        "$defs": defs,
    }

    out_path = _module_path(group, type_name, version)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_init(out_path)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(root, tmp)
        tmp_path = Path(tmp.name)
    try:
        subprocess.run(
            [
                "datamodel-codegen",
                "--input", str(tmp_path),
                "--input-file-type", "jsonschema",
                "--output", str(out_path),
                "--output-model-type", "pydantic_v2.BaseModel",
                "--class-name", "Data",
                "--allow-extra-fields",
                "--use-schema-description",
                "--use-field-description",
                "--target-python-version", "3.10",
                "--disable-timestamp",
                "--formatters", "black",
            ],
            check=True,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    return out_path


def _ensure_init(module_path: Path) -> None:
    """Create __init__.py up the generated tree so the packages import."""
    d = module_path.parent
    while OUT_ROOT in d.parents or d == OUT_ROOT:
        init = d / "__init__.py"
        if not init.exists():
            init.write_text("")
        if d == OUT_ROOT:
            break
        d = d.parent


def main() -> int:
    if not SNAPSHOT.is_dir():
        print(f"Snapshot not found: {SNAPSHOT}", file=sys.stderr)
        return 1
    for group, type_name, version in TARGETS:
        path = _generate_one(group, type_name, version)
        print(f"  generated {path.relative_to(REPO)}")
    print(f"Done — {len(TARGETS)} model(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
