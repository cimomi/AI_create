#!/usr/bin/env python3
"""Mandatory security audit before a public push. Any finding exits non-zero.

Why the history is scanned: once a secret or path has been committed, deleting
the file is not enough — the object stays in the repository and remains
retrievable through the API. So the audit covers both the current tree and
every historical blob.

Checks:
  1. Absolute paths (/Users/<name>, /home/<name>) — leaks a username and layout
  2. Secrets (tokens, API keys, private keys, JWTs, generic password assignments)
  3. Forbidden files (.env, *.pem, *.key, credential files, backups, caches)
  4. Oversized files and archives
  5. LICENSE files in subdirectories (a hint that third-party content is bundled)

Usage:
    python3 scripts/audit.py              # current tree + full history
    python3 scripts/audit.py --no-history # current tree only (faster)
    python3 scripts/audit.py --json

Exit code: 0 = pass; 1 = findings; 2 = usage/environment error.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Documented placeholder paths that legitimately appear in examples.
PLACEHOLDER_USERS = {'me', 'user', 'you', 'your', 'username', 'yourname',
                     'example', 'someone', 'name', 'test'}

PATH_RE = re.compile(r'/(?:Users|home)/([A-Za-z0-9_.-]+)')

SECRET_PATTERNS = [
    ('GitHub PAT (classic)', re.compile(r'gh[pousr]_[A-Za-z0-9]{36,}')),
    ('GitHub PAT (fine-grained)', re.compile(r'github_pat_[A-Za-z0-9_]{60,}')),
    ('Anthropic API key', re.compile(r'sk-ant-[A-Za-z0-9_\-]{30,}')),
    ('OpenAI API key', re.compile(r'sk-[A-Za-z0-9]{32,}')),
    ('AWS access key', re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b')),
    ('Slack token', re.compile(r'xox[baprs]-[A-Za-z0-9\-]{10,}')),
    ('Google API key', re.compile(r'AIza[0-9A-Za-z_\-]{35}')),
    ('Private key block', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('JWT', re.compile(r'eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}')),
    ('Generic secret assignment', re.compile(
        r'(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)'
        r'\s*[:=]\s*["\']([^"\'\s]{16,})["\']')),
]

# Exemptions for the generic assignment rule: placeholders, test values, references.
BENIGN_VALUE = re.compile(
    r'(?i)^(your|my|test|dummy|fake|sample|example|placeholder|changeme|xxx|todo|'
    r'<|\{\{|\$\{|%\(|none|null|empty|redacted|\*{3,}|sk-your|sk-test|test-)')

FORBIDDEN_PATH = [
    (re.compile(r'(^|/)\.env(\.|$)'), '.env environment file'),
    (re.compile(r'(^|/)\.envrc$'), '.envrc'),
    (re.compile(r'\.(pem|key|p12|pfx|jks|keystore)$', re.I), 'key or certificate file'),
    (re.compile(r'(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$'), 'SSH private key'),
    (re.compile(r'(^|/)\.netrc$|(^|/)\.git-credentials$'), 'credential file'),
    (re.compile(r'(^|/)\.(aws|ssh|kube|docker)/'), 'credential directory'),
    (re.compile(r'(^|/)node_modules/'), 'node_modules'),
    (re.compile(r'(^|/)__pycache__/|\.pyc$'), 'Python cache'),
    (re.compile(r'(^|/)(update-backups|\.recovery|\.venv|venv)/'), 'backup or virtualenv'),
    (re.compile(r'(^|/)\.DS_Store$'), '.DS_Store'),
    (re.compile(r'(^|/)health-report\.json$'), 'tool run artefact'),
]

MAX_FILE_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 5_000_000
ARCHIVE_EXT = {'.tar', '.gz', '.tgz', '.zip', '.7z', '.rar', '.dmg', '.iso'}


def git(*args, cwd=None):
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def scan_text(text, where, findings):
    for m in PATH_RE.finditer(text):
        if m.group(1) not in PLACEHOLDER_USERS:
            line = text[:m.start()].count('\n') + 1
            findings.append({'type': 'absolute path', 'where': where, 'line': line,
                             'detail': m.group(0)})
    for name, pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            if name == 'Generic secret assignment' and BENIGN_VALUE.match(m.group(2) or ''):
                continue
            line = text[:m.start()].count('\n') + 1
            hit = m.group(0)
            findings.append({'type': f'suspected secret ({name})', 'where': where,
                             'line': line,
                             'detail': (hit[:60] + '...') if len(hit) > 60 else hit})


def check_path_listing(paths, findings):
    for p in paths:
        for pat, label in FORBIDDEN_PATH:
            if pat.search(p):
                findings.append({'type': 'forbidden file', 'where': p, 'line': 0,
                                 'detail': label})
                break


def scan_worktree(root, findings):
    code, out, _ = git('ls-files', '-z', cwd=root)
    files = [f for f in out.split('\0') if f]
    check_path_listing(files, findings)
    for rel in files:
        p = Path(root) / rel
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
            raw = p.read_bytes()
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            findings.append({'type': 'oversized file', 'where': rel, 'line': 0,
                             'detail': f'{size/1048576:.1f} MB'})
        if p.suffix.lower() in ARCHIVE_EXT and size > MAX_ARCHIVE_BYTES:
            findings.append({'type': 'oversized archive', 'where': rel, 'line': 0,
                             'detail': f'{size/1048576:.1f} MB'})
        if b'\x00' in raw[:1024]:
            continue
        if not Path(rel).name.lower().startswith('license'):
            scan_text(raw.decode('utf-8', errors='ignore'), rel, findings)
    for rel in files:
        if Path(rel).name.lower().startswith('license') and '/' in rel:
            findings.append({'type': 'note: LICENSE in a subdirectory', 'where': rel,
                             'line': 0,
                             'detail': 'confirm this is not bundled third-party content'})


def scan_history(root, findings, limit=20000):
    """Scan every unique blob in history (de-duplicated, so each is read once)."""
    code, out, _ = git('rev-list', '--objects', '--all', cwd=root)
    if code != 0:
        findings.append({'type': 'audit error', 'where': '-', 'line': 0,
                         'detail': 'could not enumerate history objects'})
        return 0
    seen, blobs = set(), []
    for line in out.splitlines():
        parts = line.split(' ', 1)
        if len(parts) < 2:
            continue
        sha, path = parts
        if sha in seen:
            continue
        seen.add(sha)
        blobs.append((sha, path))
        if len(blobs) >= limit:
            break

    scanned = 0
    for sha, path in blobs:
        code, content, _ = git('cat-file', '-p', sha, cwd=root)
        if code != 0 or '\x00' in content[:1024]:
            continue
        scanned += 1
        scan_text(content, f'{path} (history {sha[:7]})', findings)
    return scanned


def main():
    ap = argparse.ArgumentParser(description='Mandatory security audit before a public push')
    ap.add_argument('--no-history', action='store_true', help='skip the history scan (faster)')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--root', default=None)
    args = ap.parse_args()

    root = args.root or subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                                       capture_output=True, text=True).stdout.strip()
    if not root or not Path(root).is_dir():
        print('not inside a git repository', file=sys.stderr)
        return 2

    findings = []
    scan_worktree(root, findings)
    n_hist = 0
    if not args.no_history:
        n_hist = scan_history(root, findings)

    uniq, seen = [], set()
    for f in findings:
        key = (f['type'], f['where'], f['line'], f['detail'])
        if key not in seen:
            seen.add(key)
            uniq.append(f)

    hard = [f for f in uniq if not f['type'].startswith('note')]
    soft = [f for f in uniq if f['type'].startswith('note')]

    if args.json:
        print(json.dumps({'ok': not hard, 'hard': hard, 'soft': soft,
                          'history_blobs_scanned': n_hist}, ensure_ascii=False, indent=2))
    else:
        print(f'Security audit: {root}')
        print(f'  history blobs scanned: {n_hist}' if not args.no_history
              else '  history scan skipped')
        if hard:
            print(f'\n  FAIL: {len(hard)} blocking finding(s):')
            for f in hard[:40]:
                loc = f"{f['where']}:{f['line']}" if f['line'] else f['where']
                print(f"     [{f['type']}] {loc}")
                print(f"         {f['detail']}")
            if len(hard) > 40:
                print(f'     ... {len(hard) - 40} more')
        else:
            print('  OK: no blocking findings')
        if soft:
            print(f'\n  NOTE: {len(soft)} item(s) to confirm:')
            for f in soft[:10]:
                print(f"     {f['where']} — {f['detail']}")
        print('\nResult: ' + ('FAILED, operation blocked' if hard else 'passed'))

    return 1 if hard else 0


if __name__ == '__main__':
    raise SystemExit(main())
