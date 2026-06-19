# Contributing to funding-arb

Thanks for your interest in contributing. This is a trading system, so a few extra care rules apply beyond the usual open-source norms.

## Before you start

- **Never commit real API keys, private keys, passphrases, or `.env` files.** The `.gitignore` already blocks these — double-check before pushing.
- **Test on testnet / dry-run first.** Any change to execution, signing, or position logic must pass `dry_run` mode before live verification.
- **No withdrawal-permission keys anywhere** in CI, tests, or examples.

## Development setup

```bash
git clone https://github.com/counterfactual5/funding-arb.git
cd funding-arb
bash setup.sh
pip install -r requirements.txt
pip install -r server/requirements.txt
```

## Running tests

```bash
.venv/bin/python -m pytest scripts/tests/ -q
```

All tests must pass before a PR is merged. CI runs the suite on Ubuntu and Windows.

Venue SDK tests guard optional dependencies (`hyperliquid-python-sdk`, `lighter-sdk`, etc.) — they skip cleanly if the SDK is not installed, so you do not need every SDK to contribute.

## Code style

- Python 3.12+ (uses `from __future__ import annotations` and modern typing)
- Type hints on public functions
- No external formatter enforced; match the surrounding file's style
- Keep functions focused; the existing modules favor small, testable units

## Commit messages

Conventional commits, prefixed by area:

```
feat(pure-futures): add cross-interval basis-blend to scanner
fix(okx): handle empty funding response without crash
docs: update Pure Futures metric definitions
test: cover settle_mismatch planner edge case
chore: bump lighter-sdk dependency
```

## Pull requests

1. Branch from `main`, name it `<type>/<short-description>` (e.g. `fix/okx-funding-empty`)
2. Keep PRs scoped — one logical change per PR
3. Include or update tests for any behavior change
4. If you touch venue adapters or signing, note it explicitly in the PR description so it gets extra review
5. The CI workflow (`test` + `docs-sync`) must be green

## Adding a new venue

Follow the shared engineering checklist in [`ROADMAP.md`](ROADMAP.md) (the "Shared engineering tasks for each new venue" table). In short:

1. `scripts/venues/<name>.py` — ticker, funding, fees, positions, dry-run + live orders
2. Register in `scripts/venues/__init__.py` and the scanner's `PURE_ALL_VENUES`
3. Fee cache + settlement interval in `scan_pure_futures_spreads.py`
4. Historical funding in `funding_history_source.py`
5. Settings UI + credential schema
6. Integration tests with mocked HTTP

## Reporting issues

- **Security issue:** see [`SECURITY.md`](SECURITY.md) — do NOT open a public issue
- **Bug:** use the bug report template; include venue, dry-run vs live, and the relevant log lines (redact any keys)
- **Feature request:** use the feature request template

## License

By contributing you agree your contributions are licensed under the project's [MIT license](LICENSE).
