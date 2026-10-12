---
name: skill-update-guard
description: Use when the user wants to check or update the skills installed on this machine ("check skill updates", "update skill", "is this skill current", "skill outdated", "upgrade skill", "is this skill update safe"), or when a skill directory carries a .git repository or .huashu-skill-meta.json and its provenance needs establishing. Also applies when judging whether a skill's source is legitimate, or whether an incoming update would bring risky content. Not for rewriting SKILL.md content itself.
---

# skill-update-guard — Skill updates and safe retrieval

A skill is code and instructions that came from the internet. Updating one is a
**supply-chain operation**: the incoming content goes straight into your context
and may ship executable scripts. The order is therefore always **confirm scope →
read-only trace → review before writing → report by category**.

## Step 1: Confirm the scope (do not skip)

**When the user has not named a scope, ask — do not default to a whole-machine
scan on your own.** Use `ask_user_question` with:

- Option one (recommended): **scan every skill on this machine** (including
  framework-bundled ones)
- Option two: only the library at `~/Skills`
- Option three: only a directory or skill the user names

Once the user answers, **honour that scope exactly**. Do not widen or narrow it.
If the user already named a skill or directory, skip the question and proceed.

## Step 2: Read-only tracing

```bash
python3 trace.py                 # whole machine
python3 trace.py --root DIR      # restrict the scan (repeatable)
python3 trace.py --json          # machine-readable
```

The script de-duplicates by realpath, **clusters** skills by where they came
from, checks each source for legitimacy, and compares against upstream using
`git ls-remote` (no fetch, no download, no objects written). Skills with no
traceable origin get a local-only search for clues.

Cluster output looks like: `github.com/alchaincyf — 13`,
`authored in-library, no upstream recorded — 90`,
`bundled with an application (self-managed) — 171`.

**Legitimacy** is judged on: source is not https, host not on the trusted list,
URL embeds credentials, upstream is archived, suspiciously low star count. Any
hit must be surfaced in the report.

Three kinds are **not updatable by this tool** and land under "no source found":
application bundles (DeepSeek office-skills, Codex `.system`, plugin caches),
framework-shipped skills (Hermes), and skills nested inside a traceable bundle
(they inherit the parent's origin).

## Step 3: Safe update

Only update what is **traceable and actually behind**. Never touch anything
without a known origin.

```bash
python3 update.py --name X --dry-run   # always look first
python3 update.py --name X --confirm   # then write
python3 update.py --all --confirm      # batch
```

- **git installs**: fetch (writes `.git` only) → working tree must be clean →
  fast-forward only (`merge --ff-only`) → realign the manifest fingerprint
- **copy installs**: clone to a temp directory → safety scan → clean replace
  with `rsync --delete` (so files dropped upstream do not linger) → write back
  metadata → realign the fingerprint
- Both archive the skill directory before writing anything

### How "no pollution" and "no malware" are enforced

- **No pollution**: copy installs replace the whole tree with `--delete`; git
  installs fast-forward, so git handles deletions itself. The library's own
  management files (`.huashu-skill-meta.json`, `.last-update-check`) are
  explicitly excluded from the replace.
- **No malware**: incoming content is scanned before writing. High-severity
  patterns (`curl|bash`, base64 piped into a shell, `rm -rf /`, reading
  `~/.ssh/id_rsa`, instruction-override phrasing, reverse shells) **refuse the
  write**. Ordinary assets such as images and fonts are only counted.
- Use `--force` only after a confirmed false positive, and **always show the
  user the exact file and line that matched**.

`--dry-run` is the default. Always run it first.

## Step 4: Report by category (fixed order)

**Report in this order. Do not reorder.**

1. **Updated** — for each: name, source, `old -> new`, what changed, backup file
2. **No update** — skills already current, with their version. Fold inherited
   children (e.g. the 15 persona skills under nuwa-skill) into one line
3. **No source found** — group by kind; list separately anything where the local
   search turned up a probable origin, stating the confidence and making clear
   that nothing was downloaded or updated

Include in the report: any source flagged as not legitimate, and anything that
could not be compared because no local version was recorded.

## Quick reference

| Situation | Action |
| --- | --- |
| User gave no scope | **Ask first**; whole machine is the default option |
| Already current | Record under "no update" |
| Upstream has new commits | `--dry-run` then `--confirm` |
| Dirty tree / diverged | Stop; hand to a human. No merge, no rebase |
| Source not a trusted host | Flag it; needs user confirmation |
| Offline / auth failure | Report "cannot confirm" — **never** "up to date" |
| No .git and no install metadata | Search for the origin only; **no download, no update** |
| Application bundle / harness skill | Leave alone; it updates with its host |

## Red flags — stop

- You picked a scope yourself because the user did not name one
- `git pull` without `--ff-only`, or `reset --hard` / `clean` to "tidy up"
- `--confirm` without a preceding `--dry-run`
- `--force` on a high-severity finding without showing the user what matched
- Downloading or updating a skill that has no traceable origin
- Declaring success without realigning the manifest fingerprint

**Any of these turns a reversible update into an irreversible one.**

## Common mistakes

| Mistake | Consequence | Do this instead |
| --- | --- | --- |
| Treating a dependency's homepage as the skill's origin | Wrong provenance (e.g. the `matplotlib` skill pointing at `matplotlib/matplotlib`) | The script grades confidence; only high-confidence counts as an origin |
| Not realigning the manifest fingerprint after an update | The health check reports a content mismatch and the library looks broken | `update.py` does it automatically |
| Treating bundled/harness skills as updatable | Conflicts with the host application's own management | File them under "no source found" |
| Claiming "diverged" before fetching | A fast-forwardable update is reported as a conflict | When undecidable, say so — do not guess |
