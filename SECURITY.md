# Security Policy

## Supported versions

funding-arb is pre-1.0 software. Security fixes target the latest `main` branch and the most recent release tag. Older releases are not maintained.

| Version | Supported |
|---------|-----------|
| latest `main` | yes |
| latest release tag | yes |
| older tags | no |

## Reporting a vulnerability

This software handles **exchange API keys and wallet private keys** and can place **real orders with real money**. We take security reports seriously.

**Please do NOT open a public GitHub issue for security vulnerabilities.**

Instead, report privately via one of:

1. **GitHub Security Advisories** (preferred): Repo → Security → Advisories → "Report a vulnerability"
2. **Email**: `counterfactual5@users.noreply.github.com` — include `[security]` in the subject line

Please include:
- A description of the issue and its potential impact
- Steps to reproduce or a proof of concept
- Affected files / commit SHA if known
- Any suggested fix

We aim to acknowledge reports within **72 hours** and to coordinate a fix + disclosure timeline with you. Please give us a reasonable window to patch before publishing details publicly.

## Scope

**In scope:**
- Secret leakage (API keys, private keys, passphrases) via logs, error messages, git history, or network responses
- Authentication / authorization bypass in credential storage or exchange signing
- Order placement or position management logic that could cause unintended real trades
- Race conditions in the executor / position watcher that lead to inconsistent state
- Any code path that could send credentials to a third party

**Out of scope:**
- Losses from normal market risk, slippage, or exchange downtime — see the README disclaimer
- Vulnerabilities in third-party dependencies (report upstream)
- Issues requiring already-compromised exchange credentials
- Theoretical timing attacks on local credential files (use the keyring / systemd-creds / age backend instead of plaintext)

## Credential safety

The project stores credentials via a tiered backend system (keyring → systemd-creds → age → plaintext JSON). If you file a report about plaintext JSON exposure, we will likely respond by directing you to a stronger backend — the plaintext file is explicitly a low-security fallback.

When testing, **never use production API keys or mainnet wallet keys with withdrawal permission**. Use testnet credentials with trade-only, no-withdrawal permissions.

## Hardening checklist (for users)

- [ ] Use `keyring` or `systemd-creds` backend, not plaintext JSON
- [ ] API keys: **trade-only** permission, **withdrawal disabled**
- [ ] `dry_run: true` until you have verified behavior on testnet
- [ ] Restrict exchange API key IPs to your server's IP where supported
- [ ] Keep `.env` and `~/.funding-arb/` out of backups and screenshots
