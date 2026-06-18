# osdu-python-models (PoC)

Typed **Pydantic v2** models for OSDU `data` payloads — an opt-in companion to
[`osdu-python-client`](https://community.opengroup.org/osdu/platform/system/sdks/osdu-python-client),
mirroring the C# [`osdu-csharp-schemas`](https://github.com/equinor/osdu-csharp-schemas)
library.

> **Status: proof of concept.** Covers **all `work-product-component`,
> `master-data` and `dataset` entity types** in the pinned OSDU snapshot — **194
> entity types across 551 schema versions** (93 work-product-component + 73
> master-data + 28 dataset), plus **128 shared `abstract` modules** pulled in on
> demand. The generator is data-driven: the scope is the `SCOPE_GROUPS` list in
> `tools/generate.py`, and every type and version is discovered from the snapshot
> automatically, so a snapshot bump or adding a group needs no other code change.
> `abstract` schemas are not listed explicitly — only those reachable from a
> selected entity's `$ref` closure are generated.

## Why

`osdu-python-client` leaves the record `data` block free-form (a `dict`), matching
the canonical `os-core-common` `Map<String, Object>` — the right call, because OSDU
`data` is schema-on-read and versioned by `kind`. But consumers building ingestion
or validation logic for a **specific kind + version** still want types: autocomplete,
runtime validation, and self-documenting payloads. This package provides exactly
that, without touching the client.

```python
from osdu_models.workproductcomponent.well_log.v1_5_0 import Data, Curve

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

## Install

Released distributions are published as **GitHub Release assets** (this is a PoC —
not on PyPI). Install the wheel directly from a release:

```sh
pip install https://github.com/equinor/osdu-python-models/releases/download/v0.3.0/osdu_python_models-0.3.0-py3-none-any.whl
```

or with [uv](https://docs.astral.sh/uv/):

```sh
uv pip install https://github.com/equinor/osdu-python-models/releases/download/v0.3.0/osdu_python_models-0.3.0-py3-none-any.whl
```

To pin it in `requirements.txt` / `pyproject.toml`:

```
osdu-python-models @ https://github.com/equinor/osdu-python-models/releases/download/v0.3.0/osdu_python_models-0.3.0-py3-none-any.whl
```

See the [releases page](https://github.com/equinor/osdu-python-models/releases)
for the latest version and its `.whl` / `.tar.gz` assets. Models are shipped
pre-generated in the published distributions — no codegen step needed to consume
them. To build from source instead, see [Build & test](#build--test).

## Design

- **Types only the `data` block.** The client owns the envelope (id/kind/acl/legal);
  this package owns `data`. The two compose via `model_dump()` / `model_validate()`.
- **Side-by-side versions.** Each published version is its own module
  (`well_log/v1_4_0.py`, `v1_5_0.py`) — no "latest wins", explicit per-`kind`.
- **Shared abstract modules.** OSDU `abstract/*` building blocks are generated
  **once** under `osdu_models/abstract/<type>/v<ver>.py`; each entity model
  imports them rather than inlining a private copy. This removes the ~96 % class
  duplication of a per-entity bundling approach (551 models: ~65,000 → 2,071 class
  defs) and is what makes scaling to the full schema set viable. Same idea as
  the C# library's `ExternalReferenceCode` abstract sharing.
- **String-only constraints stripped off non-string nodes.** A few OSDU schemas
  attach `pattern`/`format` to `array`/`integer` fields (e.g.
  `AbstractColumnBasedTable.IntegerColumn`); the generator drops them so Pydantic
  v2 doesn't reject otherwise-valid payloads.
- **`extra='allow'` everywhere.** Unknown / forward-compatible fields round-trip
  untouched (the Pydantic equivalent of C#'s `[JsonExtensionData]`).
- **String `format` dropped → plain `str`.** This library types `data` for
  *lossless* round-tripping, not semantic validation. Honouring `format` would
  make codegen emit validating/normalising types that defeat that or pull
  optional deps: `date`/`date-time`/`time` (OSDU payloads carry non-conformant
  variants a strict parser rejects), `email` (`EmailStr`, needs `email-validator`),
  `uri` (`AnyUrl`, normalises the value). Keeping plain `str` preserves the input.
  Same pragmatic choice the C# library makes.
- **Pinned snapshot.** `schemas/2026.05.22/` is a frozen copy of the OSDU
  `data-definitions` `Generated/` schemas (shared with the C# library). Bumping it
  is an explicit, reviewable change.

## Layout

```
osdu-python-models/
├── schemas/2026.05.22/        # pinned data-definitions snapshot (abstract + entities)
├── tools/generate.py          # restructures the `data` sub-schemas, runs datamodel-codegen
├── src/osdu_models/            # generated Pydantic models (gitignored, regenerable)
│   ├── abstract/<type>/v<ver>.py          # shared abstract building blocks (generated once)
│   ├── workproductcomponent/<type>/v<ver>.py
│   ├── masterdata/<type>/v<ver>.py        # → class Data, importing the shared abstracts
│   └── dataset/<type>/v<ver>.py           # e.g. dataset/file_generic/v1_1_0.py
├── tests/test_roundtrip.py    # round-trip + typed-access tests vs real OSDU examples (all versions)
└── samples/author_welllog.py  # end-to-end authoring demo (no network)
```

## Build & test

```sh
uv venv && uv pip install -e ".[dev]"
uv run python tools/generate.py     # generate models from the pinned snapshot
uv run ruff check tools tests samples  # lint hand-written sources (not generated)
uv run pytest                       # round-trip tests against OSDU example payloads
uv run python samples/author_welllog.py
```

## How it works

OSDU record schemas put the payload under `properties.data` as an `allOf` of
abstract building blocks (`../abstract/*.json`) plus inline fields. `tools/generate.py`
uses `datamodel-codegen` **directory mode**, which turns cross-file `$ref`s into
Python imports:

1. lifts each entity's `data` sub-schema (titled `Data`) and transitively
   collects the shared `abstract/*` files it references,
2. lays both out in a temp tree mirroring the output package structure (every
   file at the same depth, `<pkg>/<snake>/v<ver>.json`), cleaning schema quirks
   and rewriting every `$ref` to a canonical root-relative path (`../../<pkg>/
   <snake>/v<ver>.json`) so it resolves identically regardless of which file the
   resolver treats as the base — sidestepping a `datamodel-codegen` quirk that
   otherwise resolves a shared schema's nested `$ref`s against the *referencing*
   entity's directory,
3. runs `datamodel-codegen` **once** over that tree — emitting each abstract as a
   shared module and each entity as a `Data` model that imports them.

Generated code is gitignored — regenerable from the pinned snapshot, never
hand-edited.
