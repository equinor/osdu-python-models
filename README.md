# osdu-python-schema (PoC)

Typed **Pydantic v2** models for OSDU `data` payloads — an opt-in companion to
[`osdu-python-client`](https://community.opengroup.org/osdu/platform/system/sdks/osdu-python-client),
mirroring the C# [`osdu-csharp-schemas`](https://github.com/equinor/osdu-csharp-schemas)
library.

> **Status: proof of concept.** Scoped to `WellLog` (v1.4.0 + v1.5.0) to prove the
> approach end-to-end. The generator is data-driven — widening to every Wellbore
> DDMS entity/version is a config change in `tools/generate.py`, not new code.

## Why

`osdu-python-client` leaves the record `data` block free-form (a `dict`), matching
the canonical `os-core-common` `Map<String, Object>` — the right call, because OSDU
`data` is schema-on-read and versioned by `kind`. But consumers building ingestion
or validation logic for a **specific kind + version** still want types: autocomplete,
runtime validation, and self-documenting payloads. This package provides exactly
that, without touching the client.

```python
from osdu_schemas.workproductcomponent.well_log.v1_5_0 import Data, Curve

# Typed authoring — autocomplete + validation
data = Data(
    WellboreID="namespace:master-data--Wellbore:abc:",
    TopMeasuredDepth=1234.5,
    Curves=[Curve(Mnemonic="GR")],
)

# Bridge into the client's free-form `data` — just model_dump(), no client changes
record = {"kind": "osdu:wks:work-product-component--WellLog:1.5.0",
          "acl": ..., "legal": ...,
          "data": data.model_dump(by_alias=True, exclude_none=True)}
```

Reading is the mirror image: `Data.model_validate(record["data"])`.

## Design

- **Types only the `data` block.** The client owns the envelope (id/kind/acl/legal);
  this package owns `data`. The two compose via `model_dump()` / `model_validate()`.
- **Side-by-side versions.** Each published version is its own module
  (`well_log/v1_4_0.py`, `v1_5_0.py`) — no "latest wins", explicit per-`kind`.
- **`extra='allow'` everywhere.** Unknown / forward-compatible fields round-trip
  untouched (the Pydantic equivalent of C#'s `[JsonExtensionData]`).
- **Temporal fields as `str`.** OSDU example payloads carry non-conformant
  date-time variants that a strict `datetime` parser rejects or rewrites; emitting
  them as `str` keeps round-trips byte-lossless. Same pragmatic choice the C#
  library makes.
- **Pinned snapshot.** `schemas/2026.05.22/` is a frozen copy of the OSDU
  `data-definitions` `Generated/` schemas (shared with the C# library). Bumping it
  is an explicit, reviewable change.

## Layout

```
osdu-python-schema/
├── schemas/2026.05.22/        # pinned data-definitions snapshot (abstract + entities)
├── tools/generate.py          # bundles the `data` sub-schema, runs datamodel-codegen
├── src/osdu_schemas/          # generated Pydantic models (gitignored, regenerable)
├── tests/                     # round-trip + typed-access tests vs real OSDU examples
└── samples/author_welllog.py  # end-to-end authoring demo (no network)
```

## Build & test

```sh
uv venv && uv pip install -e ".[dev]"
uv run python tools/generate.py     # generate models from the pinned snapshot
uv run pytest                       # round-trip tests against OSDU example payloads
uv run python samples/author_welllog.py
```

## How it works

OSDU record schemas put the payload under `properties.data` as an `allOf` of
abstract building blocks (`../abstract/*.json`) plus inline fields. `tools/generate.py`:

1. lifts that `data` sub-schema to a standalone root schema,
2. **bundles** every reachable file `$ref` into a self-contained `$defs` block
   (whole-file refs, no fragments — so this is a clean inline),
3. runs `datamodel-codegen` to emit one Pydantic v2 `Data` module per version.

Generated code is gitignored — regenerable from the pinned snapshot, never
hand-edited.
