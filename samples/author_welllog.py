"""Sample: author a WellLog `data` block with typed Pydantic models, then hand
the plain dict to osdu-python-client.

The schemas package types only the `data` payload; the client keeps the record
envelope (id/kind/acl/legal/...) and treats `data` as free-form. The bridge is
just ``model_dump()`` — no client changes required.

Run: ``python samples/author_welllog.py``  (no network — prints the request body)
"""

from __future__ import annotations

import json

from osdu_models.workproductcomponent.well_log.v1_5_0 import Curve, Data

# Typed authoring — IDE autocomplete + validation on every field.
data = Data(
    WellboreID="namespace:master-data--Wellbore:abc:",
    TopMeasuredDepth=1234.5,
    BottomMeasuredDepth=2345.6,
    Curves=[Curve(Mnemonic="GR", CurveID="namespace:..:GR:")],
)

# Bridge into the OSDU record envelope the client expects. In real use:
#
#     from osdu_python_client import OsduClient
#     from osdu_python_client.generated.wellbore_ddms.models.record import Record
#     osdu.wellbore_ddms.create_or_update_records(
#         body=[Record(kind="osdu:wks:work-product-component--WellLog:1.5.0",
#                      acl=..., legal=...,
#                      data=data.model_dump(by_alias=True, exclude_none=True))]
#     )
record = {
    "kind": "osdu:wks:work-product-component--WellLog:1.5.0",
    "acl": {"viewers": ["..."], "owners": ["..."]},
    "legal": {"legaltags": ["..."], "otherRelevantDataCountries": ["NO"]},
    "data": data.model_dump(by_alias=True, exclude_none=True),
}

print(json.dumps(record, indent=2))
