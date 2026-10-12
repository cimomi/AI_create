# Engineering notes

This document records **real mistakes that were made**. The value of a
toolchain is not its feature count but whether what it says can be trusted —
and every entry below is a case where a tool produced a *plausible but wrong*
answer.

## 1. Declaring "diverged" before fetching

**Symptom**: the trace report labelled a perfectly fast-forwardable update as
"diverged, needs manual handling".

**Cause**: ancestry was tested with `git merge-base --is-ancestor <local>
<remote>`, but the trace phase is read-only and the remote commit had not been
fetched. `git` returns non-zero for an object it does not have, and the code
read that non-zero as "not an ancestor".

**Fix**: check that the object exists first (`git cat-file -e <sha>`); if it does
not, return *undecidable* and leave the fast-forward decision to the update
phase, where the objects are present.

```python
def is_ancestor(path, old, new):
    if cat_file_missing(new):
        return None          # cannot determine - never guess
    return merge_base_is_ancestor(old, new)
```

**Lesson**: three states (yes / no / unknown) are more honest than a boolean.
Squashing "unknown" into "no" shows the user a conflict that does not exist.

## 2. Mistaking a dependency's homepage for a skill's origin

**Symptom**: the tool reported "probable origin" for `matplotlib`, `polars`,
`sympy`, `markitdown` and friends — pointing at those libraries' own GitHub
repositories.

**Cause**: the first version extracted "GitHub URL + same name, or the word
install on the same line". But these are **documentation skills**: their text
naturally contains `pip install polars` and a link to the project homepage — and
the name matches too.

**Fix**: grade by confidence. Only these count as high:
- the URL appears in an installation metadata file
  (`.huashu-skill-meta.json` / `plugin.json`)
- the repository path carries `skill`/`claude`/`agent`/`plugin` and the same
  line reads as a provenance statement

Dependency install commands (`pip|npm|brew install`) are downgraded. High-
confidence false positives went from 7 to 0.

**Lesson**: a matching name is not evidence. The `matplotlib` skill is called
that because it *is about* matplotlib.

## 3. `\bexec\(` matching JavaScript's RegExp method

**Symptom**: the safety scan flagged `const m = /"([^"]+)"/.exec(font)` as
"dynamic evaluation".

**Cause**: `\b` holds between `.` and `e`, so `.exec(` matched `\bexec\(`. And
`.exec()` is the standard JavaScript RegExp method — it is everywhere.

**Fix**: add a negative lookbehind, `(?<![.\w])exec\s*\(`, so a preceding `.` or
word character is excluded. Same for `eval`.

**Lesson**: test regexes against a real codebase, not against your imagination.

## 4. NUL-byte detection treating images as suspicious binaries

**Symptom**: updating one skill reported all 126 png/woff asset files as
"suspicious binaries".

**Cause**: "first 1024 bytes contain `\x00`" classifies **every** binary file
that way, including perfectly ordinary images and fonts.

**Fix**: only executable extensions (`.exe`/`.dll`/`.so`/`.sh`…) count as
suspicious; images, fonts and archives go into an "ordinary asset" bucket that
is counted but never reported.

**Lesson**: alerts should target *suspicious*, not *binary*. Conflating the two
teaches the user to ignore the tool.

## 5. `rsync` copying a temporary clone's `.git` into a skill directory

**Symptom**: after updating a copy-installed skill, it gained a `.git`
directory.

**Cause**: the clean replace ran
`rsync -a --delete <clone>/ <skill>/` and excluded the library's own management
files — **but not `.git`**. The temporary clone (a `--depth 1` shallow clone)
was copied in wholesale.

**Consequence**: the skill would subsequently be classified as a git install
rather than a copy install, and fast-forwarding a shallow clone is unreliable.

**Fix**: add `.git` to the exclusion list.

```python
RSYNC_EXCLUDES = ['.git', '.huashu-skill-meta.json', '.last-update-check']
```

**Lesson**: the irony is that this bug appeared in a tool whose entire purpose is
preventing pollution. A clean replace's exclusion list must be explicit — never
assumed.

## 6. A YAML round-trip would have erased the config comments

**Symptom**: the registration script needed to append one entry to
`skills.external_dirs` in `~/.hermes/config.yaml`.

**Cause**: the obvious approach is `yaml.safe_load` → modify → `yaml.safe_dump`.
But that file carries **32 comment lines**, and `safe_dump` discards all of them.

**Fix**: do a text-level insertion — locate the `external_dirs:` line, collect
the consecutive list items that follow, and splice in one line in sorted
position. Measured: one line added, comments 32 → 32, and every other setting
semantically identical.

Two related details:
- De-duplication must compare the **direct link target**, not the fully resolved
  path. `.cc-switch/skills/dogfood` points at the library's
  `managed/hermes/dogfood`, which itself links through to `~/.hermes` — so the
  resolved path lands outside the library even though the design is correct.
- The manifest's Hermes list must match the config file **item for item and in
  order**, or the positional comparison in the integration check fails.

**Lesson**: someone else's config file is someone else's. Edit it like a
surgeon, not like a cosmetician.

## 7. A cache written inside the directory being checked

**Symptom**: the GitHub API cache file originally lived inside the skill's own
directory.

**Consequence**: every run rewrote it, changing the skill's content fingerprint,
which made the integrity check report a content mismatch — the tool was
detecting its own output.

**Fix**: move the cache outside the library, under `~/.maintenance/`.

**Lesson**: any self-updating state must live outside the range covered by the
fingerprint.

## 8. A tool's own run artefact committed to a public repository

**Symptom**: running `tools/health.py` once leaves a `health-report.json` beside
it, and that report contains **absolute paths** of everything it inspected. A
verification run inside the repository was followed by `git add -A`, which
committed and pushed the file to a public repository.

**Why the existing check missed it**: the sync script only scanned the
**whitelisted staging area**. This file is not on the whitelist — it is produced
by the tool at runtime. Checking "what I intend to commit" cannot see "what is
actually there".

**Fix**:
1. `.gitignore` changed from `.health-report.json` to `health-report.json` —
   the original pattern did not match a file inside `tools/`
2. The sync script's leak check now covers the **entire working tree**, not just
   the staging area
3. The already-pushed leak was removed with `--amend` plus a force push (the file
   existed only in the newest commit, so no full history rewrite was needed),
   followed by `reflog expire` and `gc --prune=now` to drop the local dangling
   objects

**Lesson**: draw the security boundary around the **output**, not the
**intention**. Checking "which files am I about to commit" is never as reliable
as checking "what is in the repository right now" — because the leak is usually
the file you had no intention of committing.

## 9. The risk of a stale git repository silently reverting a migration

**Symptom**: `~/.claude/skills/.git` was the pre-migration repository, and its
working tree no longer matched what the migration had done (1217 tracked files
reported as deleted, 116 symlinks untracked).

**Risk**: any `git checkout .` or `git clean -fd` would have restored the
pre-migration **duplicate directories** wholesale, undoing the single-source
design.

**Resolution**: after confirming its history was already covered by the
manifest fingerprints and the recovery snapshot, the repository was deleted and
its commit summary retained for reference.

**Lesson**: after a structural change, version-control metadata left in place is
a time bomb. It does not complain — it simply takes effect one day when nobody
is looking.

## One general principle

> **A tool that can be trusted matters more than a tool that is powerful.**

None of the entries above is a missing feature. Each is a case where a tool gave
a **plausible but wrong** answer — or where the tool itself created a new risk.
A checking tool that lies, or that quietly leaks data, is more dangerous than no
checking tool at all, because you will believe it.
