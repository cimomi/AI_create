# Contributing

Thanks for taking a look. These are the conventions this repository follows.

## Before you commit

```bash
scripts/install-hooks.sh    # once only
```

The hooks run the security audit and the secret scan on commit and push.
**Do not bypass them with `--no-verify`** — this repository is public, and one
leak costs far more than one inconvenience.

## Layout

- One top-level directory per project, each with its own `README.md`
- New projects must be registered in the "Projects" section of the root `README.md`
- A project's own build artefacts, secrets and absolute paths belong in that
  project's `.gitignore`

## Code conventions

- Python 3.9+, standard library only — do not add dependencies for users
- Tools must be **path-portable**: use `SKILLS_ROOT` / `SKILLS_HOME`, never a
  hard-coded username
- Any checking tool must be able to report **three states** (pass / fail /
  **cannot determine**). Collapsing "I don't know" into "no problem" is not
  allowed — see the
  [engineering notes](skill-library-manager/docs/ENGINEERING.md)

## Language

The published artefacts in this repository are in English. The working library
under `~/Skills` is maintained in a different language; the copies here are
translations of it.

Because of that they are **not synced by copying** — a copy would overwrite the
translations. Instead a drift check records the source hash at translation time:

```bash
./scripts/check-translation-drift.sh            # report drift
./scripts/check-translation-drift.sh --accept   # accept current sources as the new baseline
```

Run it after changing any tool in the working library: if it reports drift, the
published translation is stale and should be refreshed.

Two exceptions: `sources.py` and `trace.py` deliberately contain Chinese
characters in their detection patterns, because they detect Chinese-language
prompt injection and provenance statements. Do not translate those.

## Commit messages

Use Conventional Commits prefixes: `feat:` `fix:` `docs:` `refactor:`
`harden:` `chore:`

Explain **why** the change is being made, not only what changed.
