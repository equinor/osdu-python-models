# Contributing

Thanks for your interest in contributing to `osdu-python-models`! This project
generates typed Pydantic v2 models from a pinned snapshot of the OSDU schema
registry. Contributions are welcome via issues and pull requests.

## Reporting issues

- Search [existing issues](https://github.com/equinor/osdu-python-models/issues)
  before opening a new one.
- For security vulnerabilities, **do not** open a public issue — follow
  [`SECURITY.md`](SECURITY.md) instead.

## Development setup

This project uses [`uv`](https://docs.astral.sh/uv/) for environment management.

```bash
uv venv && uv pip install -e ".[dev]"
uv run python tools/generate.py        # generate models from the pinned snapshot
uv run ruff check tools tests samples   # lint hand-written sources (generated code excluded)
uv run pytest                           # round-trip tests against OSDU example payloads
```

Generated code under `src/osdu_models/` is gitignored and fully regenerable from
the pinned snapshot in `schemas/`. Do not hand-edit generated models; change the
generator (`tools/generate.py`) or the snapshot instead.

## Pull request process

1. Fork the repository (or create a branch if you have write access) and base
   your work on `main`.
2. Make your change, then run `ruff` and `pytest` locally — both must pass.
3. Open a pull request against `main`.
4. **PR titles must follow [Conventional Commits](https://www.conventionalcommits.org/)**
   (e.g. `feat: add dataset models`, `fix: correct ref rewriting`). This is
   enforced by CI and drives automated releases via release-please.
5. At least one approving review (including a CODEOWNERS review) is required
   before merging. Direct pushes to `main` are not permitted.
6. Pull requests are merged using **squash merge**.

## Releases

Releases are automated with
[release-please](https://github.com/googleapis/release-please). Merging
conventional commits to `main` opens/updates a release PR; merging that PR tags
the version and publishes the wheel as a GitHub release asset.

## License

By contributing, you agree that your contributions will be licensed under the
[Apache License 2.0](LICENSE).
