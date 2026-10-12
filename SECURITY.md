# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for a security problem. Use GitHub's
[private reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
(repository → Security → Report a vulnerability), or contact the owner directly.

Include: reproduction steps, impact, and any suggested fix.

## How this repository protects itself

The repository is public, so **the pre-push security gate is mandatory** and
runs in four layers:

| Layer | Location | Timing | Covers | Prevent a leak? |
| --- | --- | --- | --- | --- |
| 1 | `.githooks/pre-commit` | before commit | staged secrets | Yes, but `--no-verify` bypasses it |
| 2 | `.githooks/pre-push` | before push | full-history secrets + absolute paths | Yes, but `--no-verify` bypasses it |
| 3 | `.github/workflows/security.yml` | after push | same, server-side | **No** — it runs after the push, so it can only detect and alert |
| 4 | GitHub Push Protection | at push time | platform-known credential formats | **Yes**, and it cannot be bypassed |

**The important caveat**: layer 3 cannot be skipped with `--no-verify`, but
**GitHub Actions runs only after the push has completed, so it does not stop the
leak**. What it can do is surface the problem and — combined with branch
protection — prevent the offending commit from being merged into `main`.

Only layer 4 rejects a push at the moment it happens.

The key requirement for layers 1–3 is scanning the **entire history**. Once
something has been committed, deleting the file is not enough: the object stays
in the repository and can still be fetched through the API. A working-tree-only
check misses that case entirely.

```bash
scripts/install-hooks.sh    # run once per fresh clone
```

## Recommended: also enable GitHub's native protection

A repository admin can turn on, under **Settings → Code security**:

- **Secret scanning** — scans pushed content for known credential formats
- **Push protection** — **rejects** pushes containing them, server-side

Both are free for public repositories and sit at the platform layer, earlier
than any client-side measure. **The hooks and CI in this repository complement
them, they do not replace them** — only Push Protection can stop a leak before
it happens.
