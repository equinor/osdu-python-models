"""Round-trip and typed-access tests for the generated WellLog `data` models.

Validates the PoC premise: a real OSDU example payload deserializes into a typed
Pydantic model, exposes typed fields, and serializes back byte-for-byte (modulo
key ordering), with unknown/forward fields preserved via ``extra='allow'``.

Run after generating: ``python tools/generate.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Generated models (gitignored; run tools/generate.py first).
pytest.importorskip("osdu_models.workproductcomponent.well_log.v1_4_0")
from osdu_models.workproductcomponent.well_log.v1_4_0 import Data as WellLogV14  # noqa: E402

# OSDU canonical example payloads live in the sibling data-definitions checkout.
_EXAMPLES = Path(__file__).resolve().parents[2] / "data-definitions" / "Examples"
_WELLLOG_14 = _EXAMPLES / "work-product-component" / "WellLog.1.4.0.json"

pytestmark = pytest.mark.skipif(
    not _WELLLOG_14.is_file(), reason="data-definitions examples not checked out"
)


def _example_data() -> dict:
    return json.loads(_WELLLOG_14.read_text())["data"]


def test_validates_real_example():
    data = WellLogV14.model_validate(_example_data())
    assert data.WellboreID == "namespace:master-data--Wellbore:SomeUniqueWellboreID:"
    assert data.Curves is not None and len(data.Curves) == 1


def test_nested_objects_are_typed():
    data = WellLogV14.model_validate(_example_data())
    curve = data.Curves[0]
    # Curve is a generated nested model, not a bare dict.
    assert type(curve).__name__ == "Curve"
    assert curve.Mnemonic == "PRES_HDRB.BAR"


def test_roundtrip_is_lossless():
    raw = _example_data()
    data = WellLogV14.model_validate(raw)
    dumped = data.model_dump(mode="json", by_alias=True, exclude_none=True)
    for key, value in raw.items():
        assert json.dumps(value, sort_keys=True) == json.dumps(
            dumped.get(key), sort_keys=True
        ), f"round-trip changed {key!r}"


def test_unknown_fields_round_trip():
    raw = _example_data()
    raw["SomeFutureFieldNotInSchema"] = {"x": 1}
    data = WellLogV14.model_validate(raw)
    dumped = data.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert dumped["SomeFutureFieldNotInSchema"] == {"x": 1}
