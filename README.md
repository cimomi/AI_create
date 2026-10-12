# AI_create

[![Security](https://github.com/cimomi/AI_create/actions/workflows/security.yml/badge.svg)](https://github.com/cimomi/AI_create/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

A portfolio of AI-related projects. **Each project lives in its own top-level
directory** and can be read and run on its own.

---

## Projects

### [skill-library-manager](skill-library-manager/) · Skill library management

Consolidates skills scattered across **10 AI tools** (Codex, Claude Code,
Gemini CLI, Kimi, Hermes, Pi and others) into a **single source of truth**: one
real copy on disk, symlinked into every harness, with fingerprints, integrity
checks, and a verifiable update path.

| | |
| --- | --- |
| Scale | 121 skills, 313 entry links, 10 tool integrations |
| Verification | 8 integrity checks + 9 cross-tool integration checks |
| Update safety | read-only tracing, source vetting, fast-forward only, clean replace, risk scanning, automatic rollback |
| Stack | Python 3 · git · symlinks · content fingerprints |

**What makes it worth reading**: not the feature count, but that **the tools can
be trusted to say the truth**. The project's
[engineering notes](skill-library-manager/docs/ENGINEERING.md) record nine real
bugs — each one a case where a tool produced a plausible but *wrong* answer, and
how it was fixed.

---

## Repository conventions

### Mandatory security audit before pushing

This repository is public, and **a security audit runs before every push**. It
fails closed.

| Layer | Timing | Covers | Can it prevent a leak? |
| --- | --- | --- | --- |
| `.githooks/pre-commit` | before commit | staged secrets + current files | Yes, but `--no-verify` bypasses it |
| `.githooks/pre-push` | before push | **full history** secrets + absolute paths | Yes, but `--no-verify` bypasses it |
| GitHub Actions CI | **after** push | same, server-side | **No** — it detects and alerts |
| GitHub Push Protection | at push time | platform-known credential formats | **Yes**, and it cannot be bypassed |

> Note: CI runs *after* the push completes, so it cannot stop the leak itself.
> GitHub's native Push Protection is the only layer that blocks at push time
> (enable it under Settings → Code security).

```bash
scripts/install-hooks.sh                  # once per fresh clone
python3 scripts/audit.py                  # run manually at any time
gitleaks git --config .gitleaks.toml      # secret scan
```

**Why the full history must be scanned**: once something is committed, deleting
the file is not enough — the object stays in the repository and remains
retrievable through the API. Checking only the working tree misses exactly that
case.

See [SECURITY.md](SECURITY.md) for details.

### Adding a project

1. Create a top-level directory and give it its own `README.md`
2. Register it in the "Projects" section above
3. Make sure absolute paths, secrets and third-party content are excluded by the
   project's own `.gitignore`
4. The security audit runs automatically on commit

---

## License

MIT — see [LICENSE](LICENSE). If a subdirectory carries its own LICENSE, that
one applies there.
