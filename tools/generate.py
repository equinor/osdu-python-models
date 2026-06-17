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

# Entity scope: the Wellbore DDMS surface — every entity the WBDDMS
# `/ddms/v3/*` endpoints handle (6 work-product-component + 3 master-data).
# Mirrors the C# library's v0.2. Versions are discovered from the pinned
# snapshot, so a snapshot bump picks up new versions with no code change.
SCOPE = [
    ("work-product-component", "WellLog"),
    ("work-product-component", "WellboreTrajectory"),
    ("work-product-component", "WellboreIntervalSet"),
    ("work-product-component", "WellboreMarkerSet"),
    ("work-product-component", "PPFGDataset"),
    ("work-product-component", "WellPressureTestRawMeasurement"),
    ("master-data", "Well"),
    ("master-data", "Wellbore"),
    ("master-data", "WellLogAcquisition"),
]

_PKG = {"work-product-component": "workproductcomponent", "master-data": "masterdata"}


def _discover_targets() -> list[tuple[str, str, str]]:
    """Expand SCOPE into (group, type, version) for every version in the snapshot.

    Schema files are named ``<Type>.<version>.json`` (e.g. ``WellLog.1.5.0.json``).
    Versions sort naturally so generated modules are emitted oldest-first.
    """
    targets: list[tuple[str, str, str]] = []
    for group, type_name in SCOPE:
        group_dir = SNAPSHOT / group
        versions = sorted(
            p.name[len(type_name) + 1 : -len(".json")]
            for p in group_dir.glob(f"{type_name}.*.json")
        )
        if not versions:
            print(f"  warning: no schema files for {group}/{type_name}", file=sys.stderr)
        for version in versions:
            targets.append((group, type_name, version))
    return targets


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

    # Drop string-only validation keywords mis-applied to non-string nodes.
    # Some OSDU schemas attach `pattern`/`minLength`/`maxLength`/`format` to
    # `array`/`integer` fields (e.g. AbstractColumnBasedTable's `IntegerColumn`
    # carries `pattern: '^[0-9]+$'` on a `type: array`). These keywords are
    # meaningless off a string and make Pydantic v2 raise at validation time
    # ("Unable to apply constraint 'pattern' ... for schema of type 'list'").
    node_type = node.get("type")
    is_string = node_type == "string" or (
        isinstance(node_type, list) and "string" in node_type
    )
    if node_type is not None and not is_string:
        node = {
            k: v
            for k, v in node.items()
            if k not in {"pattern", "minLength", "maxLength", "format"}
        }

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
    targets = _discover_targets()
    for group, type_name, version in targets:
        path = _generate_one(group, type_name, version)
        print(f"  generated {path.relative_to(REPO)}")
    print(f"Done — {len(targets)} model(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
