# opportunity-finder

Weekly opportunity discovery pipeline. Internal tool, single user. See [opportunity-finder-one-pager.md](docs/opportunity-finder-one-pager.md) and [opportunity-finder-design-doc.md](docs/opportunity-finder-design-doc.md).

## Quick start

```bash
uv sync
cp .env.example .env   # fill in values as they're needed per milestone
uv run python -m opfinder.main
uv run pytest
```

## Git hooks

The repo ships a `pre-commit` hook under [scripts/git-hooks/](scripts/git-hooks/) that enforces the `yaml.safe_load`-only constraint from §10 of the design doc. Activate it once per clone:

```bash
git config core.hooksPath scripts/git-hooks
chmod +x scripts/git-hooks/pre-commit
```

After that, any commit touching `src/` is rejected if it introduces a literal `yaml.load(` call.
