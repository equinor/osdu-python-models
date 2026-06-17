#!/usr/bin/env python3
"""Generate typed Pydantic models for OSDU `data` payloads.

OSDU record schemas keep the domain payload under a free-form `data` property
(an `allOf` of abstract building blocks + inline fields). `osdu-python-client`
leaves that untyped on purpose — matching `os-core-common`'s `Map<String,Object>`.
This generator types *only* that `data` block, as an opt-in companion package.

It uses ``datamodel-codegen`` **directory mode**, which resolves cross-file
``$ref``s into Python imports. So each shared ``abstract/*`` schema is generated
**once** (into ``osdu_models/abstract/<type>/v<ver>.py``) and every entity model
imports it, instead of inlining a private copy. This avoids the ~96 % class
duplication of a per-entity bundling approach and is the prerequisite for scaling
beyond a handful of entities. (Same idea as the C# library's
``ExternalReferenceCode`` abstract sharing.)

Pipeline:

1. discover targets (every type in ``SCOPE_GROUPS`` × versions present in the
   pinned snapshot),
2. lift each entity's ``data`` sub-schema and transitively collect the shared
   ``abstract/*`` files it references,
3. lay both out in a temp input tree that mirrors the desired package structure,
   cleaning schema quirks and rewriting ``$ref``s to the new relative locations,
4. run ``datamodel-codegen`` once over that tree (``extra='allow'`` everywhere,
   so unknown/forward fields round-trip like the C# ``[JsonExtensionData]``),
5. copy the generated ``abstract``/``workproductcomponent``/``masterdata``
   packages into ``src/osdu_models/``.

Entity import paths and the ``Data`` class name are unchanged; the shared
``osdu_models.abstract.*`` modules are an additive public surface. Generated code
is gitignored and regenerable; never hand-edited.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAPSHOT = REPO / "schemas" / "2026.05.22"
OUT_ROOT = REPO / "src" / "osdu_models"

# Entity scope: every entity type in these schema groups is generated (all
# versions present in the pinned snapshot). `abstract` is not listed here — its
# schemas are pulled in on demand as the transitive `$ref` closure of the
# selected entities, so only abstracts that are actually used get generated.
# A snapshot bump picks up new types and versions with no code change.
SCOPE_GROUPS = [
    "work-product-component",
    "master-data",
]

_PKG = {"work-product-component": "workproductcomponent", "master-data": "masterdata"}


def _discover_targets() -> list[tuple[str, str, str]]:
    """Discover every (group, type, version) in the snapshot for the scoped groups.

    Schema files are named ``<Type>.<version>.json`` (e.g. ``WellLog.1.5.0.json``).
    Types and versions sort naturally so modules are emitted in a stable order.
    """
    targets: list[tuple[str, str, str]] = []
    for group in SCOPE_GROUPS:
        group_dir = SNAPSHOT / group
        found: list[tuple[str, str]] = []
        for p in sorted(group_dir.glob("*.json")):
            type_name, version = _parse_name_version(p)
            found.append((type_name, version))
        if not found:
            print(f"  warning: no schema files for group {group}", file=sys.stderr)
        for type_name, version in sorted(found):
            targets.append((group, type_name, version))
    return targets


def _snake(name: str) -> str:
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


_NAME_VER = re.compile(r"^(?P<name>.+)\.(?P<ver>\d+\.\d+\.\d+)\.json$")


def _parse_name_version(path: Path) -> tuple[str, str]:
    """Split a schema filename into (Type, version), e.g. AbstractRemark.1.0.0.json."""
    m = _NAME_VER.match(path.name)
    if not m:
        raise ValueError(f"unexpected schema filename: {path.name}")
    return m["name"], m["ver"]


def _ver_module(version: str) -> str:
    return "v" + version.replace(".", "_")


def _clean_node(node: dict) -> dict:
    """Drop schema keywords that break codegen / strict validation.

    - ``format`` on string fields -> plain ``str``: this library types the ``data``
      payload for *lossless* round-tripping (the Python analogue of the C#
      library's ``[JsonExtensionData]``), not for semantic validation. Honouring
      ``format`` would make codegen emit validating/normalising types that defeat
      that goal or pull optional dependencies:
        * ``date``/``date-time``/``time`` -> OSDU example payloads carry
          non-conformant variants (e.g. ``...15.55Z``) a strict parser rejects;
        * ``email`` -> ``EmailStr``, requiring the optional ``email-validator``;
        * ``uri``/``uri-reference`` -> ``AnyUrl``, which normalises the value on
          re-serialisation.
      Keeping these as plain ``str`` preserves the exact input bytes.
    - String-only validation keywords (``pattern``/``minLength``/``maxLength``/
      ``format``) mis-applied to non-string nodes: some OSDU schemas attach
      ``pattern`` to a ``type: array``/``integer`` field (e.g.
      AbstractColumnBasedTable's ``IntegerColumn``), which makes Pydantic v2 raise
      at validation time ("Unable to apply constraint 'pattern' ... for schema of
      type 'list'").
    """
    node_type = node.get("type")
    is_string = node_type == "string" or (
        isinstance(node_type, list) and "string" in node_type
    )
    if is_string and "format" in node:
        node = {k: v for k, v in node.items() if k != "format"}
    if node_type is not None and not is_string:
        node = {
            k: v
            for k, v in node.items()
            if k not in {"pattern", "minLength", "maxLength", "format"}
        }
    return node


def _file_refs(node: object, base_dir: Path) -> Iterator[Path]:
    """Yield resolved absolute paths of every whole-file ``$ref`` in a node tree."""
    if isinstance(node, list):
        for x in node:
            yield from _file_refs(x, base_dir)
        return
    if not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if isinstance(ref, str) and not ref.startswith("#"):
        yield (base_dir / ref).resolve()
    for v in node.values():
        yield from _file_refs(v, base_dir)


def _abstract_closure(seeds: list[tuple[object, Path]]) -> dict[Path, dict]:
    """Transitively collect every referenced (shared) schema file.

    ``seeds`` are (node, base_dir) pairs — the lifted entity ``data`` schemas.
    OSDU abstract refs only point into ``abstract/`` (cross-entity links are
    tagged strings, not ``$ref``), so the closure stays bounded. Returns
    ``{absolute original path: parsed schema}``.
    """
    closure: dict[Path, dict] = {}
    stack: list[tuple[object, Path]] = list(seeds)
    while stack:
        node, base = stack.pop()
        for ref_path in _file_refs(node, base):
            if ref_path not in closure:
                schema = json.loads(ref_path.read_text())
                closure[ref_path] = schema
                stack.append((schema, ref_path.parent))
    return closure


def _shared_rel(path: Path) -> str:
    """Output-tree location for a shared (abstract) schema file."""
    name, ver = _parse_name_version(path)
    return f"abstract/{_snake(name)}/{_ver_module(ver)}.json"


def _entity_rel(group: str, type_name: str, version: str) -> str:
    return f"{_PKG[group]}/{_snake(type_name)}/{_ver_module(version)}.json"


def _rewrite(
    node: object, original_dir: Path, new_dir: Path, newmap: dict[Path, Path]
) -> object:
    """Clean a node tree and rewrite file ``$ref``s to the output-tree layout.

    ``original_dir`` resolves refs as written in the snapshot; ``new_dir`` is the
    schema file's directory in the temp output tree; ``newmap`` maps each shared
    file's original absolute path to its absolute path in the temp tree.

    Refs are emitted in a canonical *root-relative* form (``../../<pkg>/<snake>/
    v<ver>.json``) rather than a prefix-shortened sibling path. Every file in the
    temp tree lives at the same depth (``<pkg>/<snake>/v<ver>.json``), so this
    form resolves identically no matter which directory the resolver treats as
    the base. That sidesteps a ``datamodel-codegen`` quirk where a shared schema's
    own nested ``$ref``s are resolved relative to the *referencing* entity's
    directory instead of the shared schema's own directory.
    """
    if isinstance(node, list):
        return [_rewrite(x, original_dir, new_dir, newmap) for x in node]
    if not isinstance(node, dict):
        return node
    node = _clean_node(node)
    out: dict[str, object] = {}
    for k, v in node.items():
        if k == "$ref" and isinstance(v, str) and not v.startswith("#"):
            target = (original_dir / v).resolve()
            root = new_dir.parent.parent
            rel = newmap[target].relative_to(root).as_posix()
            out[k] = f"../../{rel}"
        else:
            out[k] = _rewrite(v, original_dir, new_dir, newmap)
    return out


_CODEGEN_OPTS = [
    "--input-file-type", "jsonschema",
    "--output-model-type", "pydantic_v2.BaseModel",
    "--allow-extra-fields",
    "--use-schema-description",
    "--use-field-description",
    "--target-python-version", "3.10",
    "--disable-timestamp",
    "--formatters", "black",
]


def _build_input_tree(
    entities: list[tuple[str, str, str, dict, Path]],
    shared: dict[Path, dict],
    temp_root: Path,
) -> None:
    """Write the restructured schema tree datamodel-codegen consumes."""
    newmap = {p: temp_root / _shared_rel(p) for p in shared}

    for orig_path, schema in shared.items():
        new_path = newmap[orig_path]
        new_path.parent.mkdir(parents=True, exist_ok=True)
        rewritten = _rewrite(schema, orig_path.parent, new_path.parent, newmap)
        new_path.write_text(json.dumps(rewritten))

    for group, type_name, version, data_schema, original_dir in entities:
        new_path = temp_root / _entity_rel(group, type_name, version)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        body = _rewrite(data_schema, original_dir, new_path.parent, newmap)
        # Promote `data` to a root object schema named `Data` (title drives the
        # class name in directory mode); our keys win over any spread from body.
        root = {
            **{k: v for k, v in body.items() if k != "$schema"},
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Data",
            "description": f"`data` payload for {type_name} {version}.",
            "type": "object",
        }
        new_path.write_text(json.dumps(root))


def _count_modules(pkg: str) -> int:
    d = OUT_ROOT / pkg
    if not d.is_dir():
        return 0
    return sum(1 for p in d.rglob("*.py") if p.name != "__init__.py")


def main() -> int:
    if not SNAPSHOT.is_dir():
        print(f"Snapshot not found: {SNAPSHOT}", file=sys.stderr)
        return 1

    targets = _discover_targets()

    entities: list[tuple[str, str, str, dict, Path]] = []
    seeds: list[tuple[object, Path]] = []
    for group, type_name, version in targets:
        original_dir = SNAPSHOT / group
        record = json.loads((original_dir / f"{type_name}.{version}.json").read_text())
        data_schema = record["properties"]["data"]
        entities.append((group, type_name, version, data_schema, original_dir))
        seeds.append((data_schema, original_dir))

    shared = _abstract_closure(seeds)

    with (
        tempfile.TemporaryDirectory() as td_in,
        tempfile.TemporaryDirectory() as td_out,
    ):
        temp_root = Path(td_in)
        out_dir = Path(td_out)
        _build_input_tree(entities, shared, temp_root)

        subprocess.run(
            ["datamodel-codegen", "--input", str(temp_root),
             "--output", str(out_dir), *_CODEGEN_OPTS],
            check=True,
        )

        OUT_ROOT.mkdir(parents=True, exist_ok=True)
        (OUT_ROOT / "__init__.py").touch()
        for pkg in ("abstract", "workproductcomponent", "masterdata"):
            src = out_dir / pkg
            if not src.is_dir():
                continue
            dst = OUT_ROOT / pkg
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))

    n_entities = _count_modules("workproductcomponent") + _count_modules("masterdata")
    n_abstracts = _count_modules("abstract")
    print(
        f"Done — {n_entities} entity model(s), "
        f"{n_abstracts} shared abstract module(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
