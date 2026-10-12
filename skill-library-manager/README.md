# Skill Library Manager

**A management system that consolidates skills scattered across 10 AI tools into
a single trusted source.**

121 skills live as one real copy on this machine; every tool reads them through
symlinks. All content is fingerprinted, verified, and updatable through a safe
supply-chain path. This repository contains **the toolchain that manages that
library** — not any third-party skill content.

---

## The problem

The same skill gets installed into Codex, Claude Code, Gemini CLI, Kimi,
Hermes… each tool keeping its own copy. Before long:

- five copies of one skill, all at different versions; you fix one and the other
  four stay stale
- no way to tell which copy is canonical, so deleting any of them feels risky
- no idea whether updating will clobber a local change in one of the tools
- no record of where anything came from, whether it can be updated, or whether
  updating it is safe

This system makes **one real copy + symlink distribution + fingerprint
registration + verifiability** the only supported structure, and freezes the
maintenance actions into tools.

## Structure

```
~/Skills/
├── common/            shared skills (one real copy for every tool)
├── platforms/         platform-specific skills (claude / codex), kept out of public entries
├── managed/           references to application-bundled directories; no content copied
├── manifest.json      121 records: source, candidate versions, fingerprints, allocation, links
├── CATALOG.md         human-readable catalogue (generated from the manifest)
└── .maintenance/      toolchain + verification records + recovery snapshot
```

Each tool's skills directory contains **only symlinks** pointing back into
`~/Skills`. Change one file, and all 10 tools see it.

| Tool | Integration |
| --- | --- |
| Codex / ChatGPT local Codex engine | shared entry + Codex-specific entry |
| DeepSeek Harness | shared entry |
| Claude Code | shared entry + Claude-specific links |
| VS Code Claude Code extension | reuses the same Claude user library |
| Gemini CLI | shared entry |
| Kimi Code / Kimi Desktop | shared entry + native local plugin |
| Pi | shared entry |
| Hermes | native `skills.external_dirs` |
| CC Switch | directories replaced with links, sync method `symlink` |

## Toolchain

### Integrity: `tools/health.py`

Eight read-only checks. The exit code is the verdict, so it drops straight into
CI or cron.

```bash
python3 tools/health.py           # human-readable report
python3 tools/health.py --quiet   # single summary line
python3 tools/health.py --json    # machine-readable
```

| ID | Check |
| --- | --- |
| A | destination exists, SKILL.md present, fingerprint matches the manifest |
| B | every entry link is a valid symlink pointing inside the library |
| C | each tool's entry directory matches the manifest exactly |
| D | frontmatter `name`/`description` valid, no duplicate names |
| E | platform-specific skills not leaked into shared entries |
| F | no `.git` / `.DS_Store` / `__pycache__` in entry directories |
| G | library directories and manifest agree in both directions |
| H | no two skills share a fingerprint |

### Integration: `tools/check_integrations.py`

`health.py` only checks internal consistency. This script checks that external
tools still point at the library: the four entry roots, cc-switch's sync method,
Hermes' external directory list, the Kimi plugin registry, and the shared paths
in `kimi.plugin.json`.

### Registration: `tools/register_skill.py`

Adding a skill is no longer a manual manifest edit. The script validates the
directory and frontmatter, writes the manifest entry, creates entry links, syncs
the Hermes list, rebuilds the catalogue, and then runs both checks — rolling
everything back if any step fails.

```bash
python3 tools/register_skill.py --name <skill> --dry-run
python3 tools/register_skill.py --name <skill>
```

### Tracing and safe updates: `skill-update-guard/`

```bash
python3 skill-update-guard/scripts/trace.py                      # read-only trace
python3 skill-update-guard/scripts/update.py --name X --dry-run  # preview
python3 skill-update-guard/scripts/update.py --name X --confirm  # write
```

`trace.py` discovers every skill on the machine, de-duplicates by realpath,
clusters by source, vets each source, and compares against upstream. Anything
without a traceable origin gets a local-only search for clues.

`update.py` only touches what is traceable and actually behind. Git installs are
fast-forward only; copy installs are cloned into a temp directory, scanned, then
replaced with `rsync --delete`. Both archive before writing and realign the
manifest fingerprint afterwards.

## Safety model

A skill is code and instructions that came from the internet; updating one is a
**supply-chain operation**. Four non-negotiable rules:

1. **Read-only retrieval never touches the local repository** — `git ls-remote`
   does not fetch and writes no objects
2. **Local changes are never touched** — a dirty working tree stops the update;
   no `reset --hard`, no `checkout`, no `clean`
3. **Fast-forward only** — a diverged branch goes to a human; no merge, no rebase
4. **Fingerprints are realigned after every update** — otherwise the health
   check reports a content mismatch and the library looks broken

Before writing, incoming content is scanned for high-severity patterns
(`curl|bash`, base64 piped into a shell, `rm -rf /`, reading `~/.ssh/id_rsa`,
instruction-override phrasing, reverse shells). A hit refuses the write.

## Engineering notes

The hard part was not writing the features — it was making the tools **stop
saying things that are not true**. See
[docs/ENGINEERING.md](docs/ENGINEERING.md), which covers:

- declaring "diverged" before fetching, turning a fast-forwardable update into a
  phantom conflict
- a documentation skill's dependency homepage being mistaken for its origin
  (`matplotlib` → `matplotlib/matplotlib`)
- `\bexec\(` matching JavaScript's RegExp method `.exec(font)`
- NUL-byte detection treating png/woff assets as suspicious binaries
- `rsync` copying a temporary clone's `.git` into a skill directory, making a
  copy install look like a git install
- a YAML round-trip that would have erased all 32 comment lines in a config file

## Quick start

Python 3.9+; the registration and integration checks additionally need PyYAML.

```bash
python3 tools/health.py
```

Paths default to `~/Skills` and can be overridden:

```bash
SKILLS_ROOT=/path/to/Skills python3 tools/health.py
```

## Scope

This repository contains only the management system's code and documentation.
The skills it manages belong to their respective authors and carry their own
licences; **they are not distributed here**. The `examples/` directory holds
redacted excerpts purely to illustrate the data structures.

## License

MIT — see the repository-root [LICENSE](../LICENSE).
