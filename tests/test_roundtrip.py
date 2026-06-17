"""Round-trip and typed-access tests for every generated `data` model.

Validates the PoC premise across the full Wellbore DDMS surface: each real OSDU
example payload deserializes into the matching typed Pydantic model, exposes
typed fields, and serializes back byte-for-byte (modulo key ordering), with
unknown / forward fields preserved via ``extra='allow'``.

The test matrix is derived from the same scope the generator uses
(``tools/generate.py``), so adding an entity/version there automatically extends
coverage here. Run after generating: ``python tools/generate.py``.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# OSDU canonical example payloads live in the sibling data-definitions checkout.
_EXAMPLES = REPO.parent / "data-definitions" / "Examples"


def _load_generator():
    """Import tools/generate.py as a module (it is not an installed package)."""
    spec = importlib.util.spec_from_file_location(
        "_osdu_generate", REPO / "tools" / "generate.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_GEN = _load_generator()


def _module_name(group: str, type_name: str, version: str) -> str:
    pkg = _GEN._PKG[group]
    return f"osdu_models.{pkg}.{_GEN._snake(type_name)}.v{version.replace('.', '_')}"


def _targets():
    return _GEN._discover_targets()


def _example_path(group: str, type_name: str, version: str) -> Path:
    return _EXAMPLES / group / f"{type_name}.{version}.json"


def _id(target) -> str:
    group, type_name, version = target
    return f"{type_name}-{version}"


@pytest.fixture(scope="session")
def _data_for():
    cache: dict[Path, dict] = {}

    def loader(path: Path) -> dict:
        if path not in cache:
            cache[path] = json.loads(path.read_text())["data"]
        return cache[path]

    return loader


@pytest.mark.parametrize("target", _targets(), ids=_id)
def test_model_imports(target):
    group, type_name, version = target
    mod = pytest.importorskip(_module_name(group, type_name, version))
    assert hasattr(mod, "Data"), "generated module is missing the `Data` class"
    assert mod.Data.model_config.get("extra") == "allow"


@pytest.mark.parametrize("target", _targets(), ids=_id)
def test_validates_real_example(target, _data_for):
    group, type_name, version = target
    example = _example_path(group, type_name, version)
    if not example.is_file():
        pytest.skip(f"no example payload: {example.name}")
    mod = pytest.importorskip(_module_name(group, type_name, version))
    model = mod.Data.model_validate(_data_for(example))
    assert isinstance(model, mod.Data)


@pytest.mark.parametrize("target", _targets(), ids=_id)
def test_roundtrip_is_lossless(target, _data_for):
    group, type_name, version = target
    example = _example_path(group, type_name, version)
    if not example.is_file():
        pytest.skip(f"no example payload: {example.name}")
    mod = pytest.importorskip(_module_name(group, type_name, version))

    raw = _data_for(example)
    dumped = mod.Data.model_validate(raw).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    for key, value in raw.items():
        assert json.dumps(value, sort_keys=True) == json.dumps(
            dumped.get(key), sort_keys=True
        ), f"round-trip changed {key!r} in {type_name} {version}"


@pytest.mark.parametrize("target", _targets(), ids=_id)
def test_unknown_fields_round_trip(target, _data_for):
    group, type_name, version = target
    example = _example_path(group, type_name, version)
    if not example.is_file():
        pytest.skip(f"no example payload: {example.name}")
    mod = pytest.importorskip(_module_name(group, type_name, version))

    raw = dict(_data_for(example))
    raw["SomeFutureFieldNotInSchema"] = {"x": 1}
    dumped = mod.Data.model_validate(raw).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert dumped["SomeFutureFieldNotInSchema"] == {"x": 1}


def test_typed_nested_access_welllog():
    """Spot-check that nested OSDU objects deserialize to generated models,
    not bare dicts — the core value proposition of the package."""
    example = _example_path("work-product-component", "WellLog", "1.4.0")
    if not example.is_file():
        pytest.skip("WellLog 1.4.0 example not checked out")
    mod = pytest.importorskip(
        "osdu_models.workproductcomponent.well_log.v1_4_0"
    )
    data = mod.Data.model_validate(json.loads(example.read_text())["data"])
    assert data.Curves is not None and len(data.Curves) >= 1
    assert type(data.Curves[0]).__name__ == "Curve"
