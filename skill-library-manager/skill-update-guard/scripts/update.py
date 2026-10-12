#!/usr/bin/env python3
"""Update a skill to upstream safely: no pollution, no malware, always reversible.

Only handles skills whose origin is known and whose upstream has moved.
Skills with no traceable origin are never touched.

Safety guarantees:
  - dry run by default; --confirm is required to write anything
  - a safety scan runs first, and high-severity findings refuse the update
    (unless --force)
  - git installs are fast-forward only; a diverged branch is left alone
  - copy installs are cloned into a temp directory and scanned before a clean
    replace (rsync --delete), so files removed upstream do not linger
  - the skill directory is archived before any write
  - after a successful update the manifest fingerprint is realigned and the
    library health check is run

Usage:
    python3 update.py --name <skill> --dry-run
    python3 update.py --name <skill> --confirm
    python3 update.py --all --confirm

Exit code: 0 = success or dry run completed; 1 = not applied (needs attention);
2 = usage error.
"""
import argparse
import datetime
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sources as S          # noqa: E402
import trace as T            # noqa: E402

ROOT = S.ROOT
MANIFEST = ROOT / 'manifest.json'
BACKUPS = ROOT / '.maintenance/update-backups'
# The library's own management files: must survive a clean replace and must
# never be overwritten or deleted by upstream content.
KEEP_LOCAL = ['.huashu-skill-meta.json', '.last-update-check']
# Exclusions for the clean replace: our management files, plus the .git that
# arrives with the temporary clone. Without the latter, a --depth 1 clone's
# repository gets copied into the skill directory, which makes a copy install
# look like a git install and leaves a shallow history that fast-forwards
# badly. (Hit this in practice.)
RSYNC_EXCLUDES = ['.git'] + KEEP_LOCAL


def health_script():
    """Locate health.py in either layout.

    In a working library the tools live in <root>/.maintenance/; in this
    repository the same tools live in tools/ next to the skill directory. Both
    are legitimate, so try the library first and fall back to the sibling copy.
    """
    candidates = [
        ROOT / '.maintenance/health.py',
        HERE.parent.parent / 'tools/health.py',
    ]
    return next((p for p in candidates if p.is_file()), None)


def git(path, *args, timeout=120):
    return S.run(['git', '-C', str(path), *args], timeout=timeout)


def backup(dest, name, keep=3):
    """Archive to .maintenance/update-backups/, keeping only `keep` per skill.

    Measured: a single skill (126 asset files) produced a 190 MB archive.
    Without retention this grows without bound with every update.
    Returns (path, size, pruned).
    """
    BACKUPS.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    out = BACKUPS / f'{name}-{ts}.tar.gz'
    with tarfile.open(out, 'w:gz') as tf:
        for item in sorted(dest.rglob('*')):
            if '.git' in item.parts:
                continue
            tf.add(item, arcname=str(item.relative_to(dest)))

    old = sorted(BACKUPS.glob(f'{name}-*.tar.gz'),
                 key=lambda p: p.stat().st_mtime, reverse=True)
    pruned = []
    for stale in old[keep:]:
        try:
            size = stale.stat().st_size
            stale.unlink()
            pruned.append((stale.name, size))
        except OSError:
            pass
    return out, out.stat().st_size, pruned


def repin(dest):
    """Realign the manifest fingerprint. Returns ((old, new, count)|None, err)."""
    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    digest, count = S.fingerprint(dest)
    hit = next((e for e in manifest['entries']
                if Path(e['destination']) == Path(dest)), None)
    if hit is None:
        return None, 'NOT_IN_MANIFEST'
    old = hit['selected'].get('sha256')
    hit['selected']['sha256'] = digest
    hit['selected']['files'] = count
    if 'deployed_sha256' in hit:
        hit['deployed_sha256'] = digest
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                        encoding='utf-8')
    return (old, digest, count), None


def sev_rank(hits):
    return max(({'high': 2, 'medium': 1}.get(h['severity'], 0) for h in hits), default=0)


def print_hits(hits, suspicious, limit=10):
    for b in suspicious[:limit]:
        print(f'      [suspicious executable] {b}')
    if len(suspicious) > limit:
        print(f'      ... {len(suspicious) - limit} more suspicious file(s)')
    shown = set()
    for h in hits:
        key = (h['where'], h['label'])
        if key in shown:
            continue
        shown.add(key)
        print(f"      [{h['severity']}] {h['label']} @ {h['where']}:{h['line']}")
        if h.get('text'):
            print(f"          {h['text']}")
        if len(shown) >= limit:
            break


def scan_git_diff(path, old, new, timeout):
    code, out, _ = git(path, 'diff', '--unified=0', '--no-color', f'{old}..{new}',
                       timeout=timeout)
    if code != 0:
        return None, None, 'could not produce a diff'
    hits = S.scan_text_risks(out, 'diff')
    _, names, _ = git(path, 'diff', '--name-status', '--diff-filter=A', f'{old}..{new}',
                      timeout=timeout)
    binaries = []
    for line in names.splitlines():
        parts = line.split('\t')
        if len(parts) >= 2 and Path(parts[1]).suffix.lower() in S.BINARY_EXT:
            binaries.append(parts[1])
    return hits, binaries, None


def update_git(e, plan, args):
    path = Path(e['path'])
    old = e['source']['commit']
    new = e['remote_commit']
    branch = e['source'].get('branch')

    # fetch writes only .git objects and remote refs, never the working tree.
    # Without it there is no local commit to compare against.
    print('  fetching remote objects (writes .git only, not the working tree)...')
    code, _, ferr = git(path, 'fetch', '--quiet', 'origin', branch or 'HEAD',
                        timeout=args.timeout)
    if code != 0:
        return False, f'fetch failed: {ferr}'

    print('  changed files:')
    _, changes, _ = git(path, 'diff', '--name-status', '--no-color', f'{old}..{new}',
                        timeout=args.timeout)
    for line in changes.splitlines()[:25]:
        print(f'    {line}')

    hits, suspicious, err = scan_git_diff(path, old, new, args.timeout)
    blocked = False
    if err:
        print(f'  WARN: {err}')
    elif hits or suspicious:
        print('  safety scan findings:')
        print_hits(hits, suspicious)
        blocked = sev_rank(hits) >= 2 and not args.force
    else:
        print('  safety scan: clean')

    if blocked and args.confirm:
        return False, 'high-severity findings; refusing to write (use --force to override)'

    if not args.confirm:
        note = ('dry run complete (high-severity findings; needs human sign-off '
                'before writing)') if blocked else 'dry run'
        return None, note

    bak, size, pruned = backup(path, e['name'])
    print(f'  backup written: {bak.name} ({size/1048576:.1f} MB)')
    for n, sz in pruned:
        print(f'  pruned old backup {n} ({sz/1048576:.1f} MB; keeping the latest 3)')

    code, out, gerr = git(path, 'merge', '--ff-only', e['remote_commit'], timeout=args.timeout)
    if code != 0:
        return False, f'fast-forward failed: {gerr or out}'

    _res, rerr = repin(path)
    if rerr and rerr != 'NOT_IN_MANIFEST':
        return False, f'fingerprint realignment failed: {rerr}'
    _, applied, _ = git(path, 'rev-parse', 'HEAD')
    return True, f'{old[:7]} -> {applied[:7]} (backup {bak.name})'


def update_copy(e, plan, args):
    path = Path(e['path'])
    src = e['source']
    url = src['url']
    subdir = src.get('subdir') or '.'
    old = src['commit']
    new = e['remote_commit']

    tmp = Path(tempfile.mkdtemp(prefix='skill-guard-'))
    try:
        print(f'  cloning to a temp directory for scanning (skill is untouched): {tmp}')
        code, _, err = S.run(['git', 'clone', '--quiet', '--depth', '1', url, str(tmp / 'repo')],
                             timeout=300)
        if code != 0:
            return False, f'clone failed: {err}'

        content = (tmp / 'repo' / subdir).resolve() if subdir not in (None, '', '.') \
            else tmp / 'repo'
        if not content.is_dir():
            return False, (f'subdirectory {subdir!r} not found in the repository; '
                           f'subdir metadata may be wrong')
        if not (content / 'SKILL.md').is_file():
            return False, (f'no SKILL.md under {subdir!r}; refusing to overwrite with '
                           f'non-skill content')

        hits, suspicious, assets, truncated = S.scan_tree(content)
        print(f'  safety scan ({subdir}/): skipped {assets} ordinary asset file(s)')
        blocked = False
        if hits or suspicious:
            print_hits(hits, suspicious)
            blocked = sev_rank(hits) >= 2 and not args.force
        else:
            print('    clean')
        if truncated:
            print('    WARN: file count exceeded the limit; the scan did not cover everything')

        _, new_commit, _ = S.run(['git', 'rev-parse', 'HEAD'], cwd=str(tmp / 'repo'),
                                 timeout=30)
        if new_commit:
            new = new_commit

        if blocked and args.confirm:
            return False, 'high-severity findings; refusing to write (use --force to override)'

        if not args.confirm:
            note = ('dry run complete (high-severity findings; needs human sign-off '
                    'before writing)') if blocked else 'dry run'
            return None, note

        bak, size, pruned = backup(path, e['name'])
        print(f'  backup written: {bak.name} ({size/1048576:.1f} MB)')
        for n, sz in pruned:
            print(f'  pruned old backup {n} ({sz/1048576:.1f} MB; keeping the latest 3)')

        # Clean replace: --delete removes files dropped upstream, while the
        # library's own management files are excluded from both sides.
        cmd = ['rsync', '-a', '--delete']
        for k in RSYNC_EXCLUDES:
            cmd += ['--exclude', k]
        cmd += [str(content) + '/', str(path) + '/']
        code, out, rerr = S.run(cmd, timeout=300)
        if code != 0:
            return False, f'clean replace failed (backup at {bak.name}): {rerr or out}'

        # Write back the install metadata.
        meta_file = path / '.huashu-skill-meta.json'
        if meta_file.is_file():
            try:
                d = json.loads(meta_file.read_text(encoding='utf-8'))
                d['commit'] = new
                d['installed_at'] = datetime.date.today().isoformat()
                d['subdir'] = subdir
                meta_file.write_text(json.dumps(d, ensure_ascii=False, indent=2) + '\n',
                                     encoding='utf-8')
            except Exception as ex:
                return False, f'failed to write back metadata: {ex}'

        _res, rerr = repin(path)
        if rerr and rerr != 'NOT_IN_MANIFEST':
            return False, f'fingerprint realignment failed: {rerr}'
        return True, f'{old[:7]} -> {new[:7]} (clean replace of {subdir}/, backup {bak.name})'
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def build_plan(names, args, api_cache):
    entities, _ = S.discover(extra_roots=args.root)
    entities = T.apply_inheritance(entities)
    by_name = {e['name']: e for e in entities}
    plans, missing = [], []
    for n in names:
        e = by_name.get(n)
        if not e:
            missing.append(n)
            continue
        plans.append(T.trace_entity(e, args.timeout, False, api_cache))
    return plans, missing


def main():
    ap = argparse.ArgumentParser(description='Safely update skills with a known origin')
    ap.add_argument('--name', action='append', default=[])
    ap.add_argument('--all', action='store_true', help='update everything updatable')
    ap.add_argument('--confirm', action='store_true',
                    help='actually write (default is a dry run)')
    ap.add_argument('--dry-run', action='store_true',
                    help='explicit dry run (the default; useful in self-documenting scripts)')
    ap.add_argument('--root', action='append', default=None,
                    help='additional scan directory (repeatable)')
    ap.add_argument('--force', action='store_true',
                    help='proceed despite high-severity scan findings (use with care)')
    ap.add_argument('--timeout', type=int, default=120)
    args = ap.parse_args()

    if not args.name and not args.all:
        print('Specify a scope with --name <skill> or --all', file=sys.stderr)
        return 2

    api_cache = T.load_cache()
    if args.all:
        entities, _ = S.discover(extra_roots=args.root)
        entities = T.apply_inheritance(entities)
        # Only consider entities that carry their own origin, so inherited
        # children do not trigger redundant network calls.
        candidates = [e['name'] for e in entities if e.get('source')]
        plans, missing = build_plan(candidates, args, api_cache)
        targets = [p for p in plans if p['update'] == 'outdated']
    else:
        plans, missing = build_plan(args.name, args, api_cache)
        targets = [p for p in plans if p['update'] == 'outdated']
        for p in plans:
            if p['update'] == 'current':
                print(f"OK         {p['name']} is current ({p['source']['commit'][:7]}), skipping")
            elif p['update'] == 'untraceable':
                print(f"NO-ORIGIN  {p['name']} has no traceable source; not updated")
            elif p['update'] == 'unknown':
                print(f"UNKNOWN    {p['name']} upstream state cannot be confirmed, skipping: "
                      f"{p['detail']}")

    for n in missing:
        print(f'WARN: no skill named {n} on this machine', file=sys.stderr)

    if not targets:
        print('\nNothing to update.')
        T.save_cache(api_cache)
        return 0

    mode = 'APPLY' if args.confirm else 'DRY RUN'
    updated, failed = [], []
    print(f'\n=== {mode}: {len(targets)} update(s) ===')
    for p in targets:
        print(f"\n> {p['name']}  [{p['kind']}]")
        print(f"  source {p['source']['url']}")
        print(f"  {p['detail']}")
        ok, msg = update_git(p, p, args) if p['kind'] == 'git' else update_copy(p, p, args)
        if ok is None:
            print(f'  -> {msg} (nothing written)')
        elif ok:
            print(f'  UPDATED: {msg}')
            updated.append((p['name'], msg))
        else:
            print(f'  NOT UPDATED: {msg}')
            failed.append((p['name'], msg))

    T.save_cache(api_cache)

    if args.confirm and updated:
        script = health_script()
        if script:
            print('\nRunning library health check...')
            r = subprocess.run([sys.executable, str(script), '--quiet'],
                               capture_output=True, text=True)
            print(f'  {r.stdout.strip()}')
            if r.returncode != 0:
                print('  WARN: health check did not pass; see the report above.')
        else:
            print('\nSkipped health check: health.py not found under <root>/.maintenance/.')

    print(f"\nResult: {len(updated)} succeeded, {len(failed)} failed, "
          f"{'dry run, nothing written' if not args.confirm else 'changes written'}")
    for n, m in failed:
        print(f'  failed {n}: {m}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
