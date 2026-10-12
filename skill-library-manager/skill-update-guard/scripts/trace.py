#!/usr/bin/env python3
"""Provenance tracing: where each skill came from, whether the source is
legitimate, and whether upstream has anything newer.

Entirely read-only. Nothing is cloned, fetched into a working tree, or
downloaded. (`git ls-remote` only reads remote refs; the GitHub API only reads
metadata. Neither writes to a local repository.)

For skills with no traceable origin, this tries to find one from local evidence
only (repository links in SKILL.md, the recorded pre-migration source in the
manifest). It never installs anything — findings are collected and handed back
to the user to decide.

Usage:
    python3 trace.py                      # scan the whole machine
    python3 trace.py --root DIR           # restrict the scan (repeatable)
    python3 trace.py --json
    python3 trace.py --no-api             # skip the GitHub API (offline / rate limits)
    python3 trace.py --timeout 30

Exit code: 0 = everything current and nothing pending; 1 = updates available or
manual attention required; 2 = error.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sources as S  # noqa: E402

CACHE_FILE = S.ROOT / '.maintenance/.skill-guard-github-cache.json'

# Kinds whose upstream can be tracked.
TRACEABLE = {'git', 'copy'}


def remote_head(path, branch, url, timeout):
    """Read the remote branch HEAD. Falls back to HEAD when branch is unknown."""
    if branch:
        code, out, _err = S.run(['git', '-C', str(path), 'ls-remote', '--heads', url, branch],
                                timeout=timeout)
        if code == 0 and out:
            return out.split()[0], None
    code, out, err = S.run(['git', 'ls-remote', url, 'HEAD'], timeout=timeout)
    if code != 0:
        return None, err or 'ls-remote failed'
    if not out:
        return None, 'remote returned no refs'
    return out.split()[0], None


def is_ancestor(path, old, new, timeout=30):
    """Whether `old` is an ancestor of `new`.

    Returns True/False/None. None means *undecidable*: the remote commit has not
    been fetched yet, so the relationship cannot be computed. It must never be
    reported as "diverged" — that would turn a perfectly fast-forwardable update
    into a phantom conflict.
    """
    code, _, _ = S.run(['git', '-C', str(path), 'cat-file', '-e', f'{new}^{{commit}}'],
                       timeout=timeout)
    if code != 0:
        return None
    code, _, _ = S.run(['git', '-C', str(path), 'merge-base', '--is-ancestor', old, new],
                       timeout=timeout)
    return code == 0


def find_source_hints(path):
    """Local-only hunt for a missing origin. No network, no downloads.

    Confidence grading is essential. A hard lesson: matching "URL + same name +
    the word install" made documentation-only skills report their *dependency's*
    homepage as their own origin (matplotlib, polars, sympy, markitdown and
    exa-py all tripped this), producing confidently wrong provenance.

    So `high` requires one of:
      - the URL appears in an installation metadata file
        (.huashu-skill-meta.json / plugin.json)
      - the repository path carries skill/claude/agent/plugin keywords *and*
        the same line reads as a provenance statement.
    Everything else is `low` and is reported separately as "likely a dependency
    or reference", never as an origin.
    """
    PROVENANCE_FILES = {'.huashu-skill-meta.json', 'plugin.json'}
    SKILLISH = re.compile(r'skill|claude|agent|plugin|prompt|awesome|persona', re.I)
    # The Chinese alternatives below are deliberate: provenance statements in
    # the local library are often written in Chinese. Not documentation.
    STRONG = re.compile(r'来源|出处|仓库地址|安装自|origin|source\s+repo|upstream\s+repo|'
                        r'git\s+clone|installed\s+from', re.I)
    DEP_INSTALL = re.compile(r'(pip|npm|yarn|pnpm|conda|brew|apt|uv|cargo|go)\s+(install|add)\b',
                             re.I)
    hints = []
    for name in ('SKILL.md', 'README.md', 'README_CN.md', 'CHANGELOG.md',
                 'package.json', '.huashu-skill-meta.json', 'plugin.json',
                 'INSTALL.md', 'VERSION'):
        f = Path(path) / name
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for m in re.finditer(r'https?://(?:www\.)?(github|gitlab|gitee|codeberg)\.com/'
                             r'([\w.-]+)/([\w.-]+)', text):
            owner, repo_name = m.group(2), m.group(3)
            line_no = text[:m.start()].count('\n') + 1
            lines = text.splitlines()
            line = lines[line_no - 1] if line_no <= len(lines) else ''
            repo = f'{m.group(1)}.com/{owner}/{repo_name}'
            skill_dir = Path(path).name

            if name in PROVENANCE_FILES:
                conf = 'high'
            elif DEP_INSTALL.search(line):
                conf = 'low'          # a dependency install command, not an origin
            elif SKILLISH.search(repo) and STRONG.search(line):
                conf = 'high'
            elif SKILLISH.search(repo) and repo_name.lower() == skill_dir.lower():
                conf = 'high'
            else:
                conf = 'low'
            hints.append({'file': name, 'line': line_no, 'url': m.group(0),
                          'repo': repo, 'confidence': conf})
    seen, out = set(), []
    for h in hints:
        key = (h['repo'], h['confidence'])
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def apply_inheritance(entities):
    """Skills nested inside a traceable bundle inherit that bundle's origin.

    Observed in practice: the 15 persona skills live under
    common/nuwa-skill/examples/. They have no .git and no install metadata of
    their own, but they are part of the nuwa-skill copy-install, so their origin
    is inherited from it.
    """
    donors = [e for e in entities if e.get('source') and e['source'].get('owner_repo')]
    for e in entities:
        if e.get('source'):
            continue
        for d in donors:
            if e['path'] != d['path'] and e['path'].startswith(d['path'].rstrip('/') + '/'):
                e['inherited_from'] = d['name']
                e['inherited_source'] = d['source']
                break
    return entities


def manifest_origin(path):
    """Look up the origin recorded in the manifest (pre-migration local path)."""
    mf = S.ROOT / 'manifest.json'
    if not mf.is_file():
        return None
    try:
        m = json.loads(mf.read_text(encoding='utf-8'))
    except Exception:
        return None
    for e in m.get('entries', []):
        if e.get('destination') == str(path):
            sel = e.get('selected', {})
            return {'source': sel.get('source'), 'path': sel.get('path')}
    return None


def load_cache():
    if CACHE_FILE.is_file():
        try:
            return json.loads(CACHE_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}
    return {}


def save_cache(cache):
    try:
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + '\n',
                              encoding='utf-8')
    except OSError:
        pass


def trace_entity(e, timeout, use_api, api_cache):
    """Return the provenance and update status for one skill."""
    kind = e['kind']
    src = e.get('source')
    inherited = False
    if not src and e.get('inherited_source'):
        src = e['inherited_source']
        inherited = True
        kind = 'copy'          # handled like the parent's install method
        e = dict(e, kind=kind, source=src)

    out = dict(e)
    out['update'] = 'not-applicable'
    out['detail'] = ''
    out['legit'] = None
    if inherited:
        out['inherited_from'] = e.get('inherited_from')

    if kind not in TRACEABLE:
        reason = {
            'library': 'authored in-library / consolidated, no upstream recorded',
            'bundled': 'bundled with an application; updated with that application',
            'harness-native': 'shipped with a harness; updated with that harness',
            'unknown': 'no git repository and no install metadata',
        }.get(kind, 'untraceable kind')
        out['update'] = 'untraceable'
        out['detail'] = reason
        out['hints'] = find_source_hints(e['path'])
        mo = manifest_origin(e['path'])
        if mo:
            out['manifest_origin'] = mo
        return out

    url = src.get('url')
    if not url:
        out['update'] = 'untraceable'
        out['detail'] = 'install metadata carries no usable repository URL'
        out['hints'] = find_source_hints(e['path'])
        return out

    # ---- Source legitimacy ----
    legit_notes = []
    if e['risk']:
        legit_notes.append(e['risk'])
    if use_api and src.get('owner_repo'):
        meta = S.github_meta(src['owner_repo'], api_cache, timeout)
        out['repo_meta'] = meta
        if meta.get('checked'):
            if meta.get('archived'):
                legit_notes.append('upstream repository is archived (no longer maintained)')
            if meta.get('stars') is not None and meta['stars'] < 3:
                legit_notes.append(
                    f"upstream has very few stars ({meta['stars']}); verify it is the real source")
            out['legit'] = 'ok' if not legit_notes else 'review'
        else:
            out['legit'] = 'unknown'
    else:
        out['legit'] = 'ok' if not legit_notes else 'review'
    out['legit_notes'] = legit_notes

    # ---- Upstream update ----
    remote, err = remote_head(e['path'], src.get('branch'), url, timeout)
    if not remote:
        out['update'] = 'unknown'
        out['detail'] = f'cannot reach upstream: {err}'
        return out
    out['remote_commit'] = remote
    local = src.get('commit')

    if not local:
        out['update'] = 'unknown'
        out['detail'] = 'no installed version recorded locally; nothing to compare'
        return out

    if local == remote:
        out['update'] = 'current'
        out['detail'] = f'up to date ({local[:7]})'
        return out

    out['update'] = 'outdated'
    out['detail'] = f'local {local[:7]} -> remote {remote[:7]}'
    if kind == 'git':
        ff = is_ancestor(e['path'], local, remote, timeout)
        out['fast_forward'] = ff
        if ff is False:
            out['detail'] += ' (diverged, needs manual handling)'
        elif ff is None:
            out['detail'] += ' (not fetched; fast-forward decided at update time)'
    return out


def main():
    ap = argparse.ArgumentParser(
        description='Trace skill provenance and upstream updates (read-only)')
    ap.add_argument('--root', action='append', default=None,
                    help='restrict the scan to this directory (repeatable)')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--no-api', action='store_true', help='skip the GitHub API')
    ap.add_argument('--no-cache', action='store_true', help='ignore the API cache')
    ap.add_argument('--timeout', type=int, default=30)
    args = ap.parse_args()

    entities, groups = S.discover(extra_roots=args.root)
    entities = apply_inheritance(entities)
    api_cache = {} if args.no_cache else load_cache()

    results = [trace_entity(e, args.timeout, not args.no_api, api_cache)
               for e in sorted(entities, key=lambda x: x['name'])]
    save_cache(api_cache)

    clusters = S.cluster(results)
    counts = {}
    for r in results:
        counts[r['update']] = counts.get(r['update'], 0) + 1

    payload = {'entities': results, 'clusters': clusters,
               'bundled_groups': groups, 'counts': counts,
               'total': len(results)}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f'Unique skill entities on this machine: {len(results)} '
              f'(de-duplicated by realpath)\n')
        print('Clusters by source:')
        for c in clusters:
            print(f"  * {c['label']} — {len(c['items'])}")
        if groups:
            print('\nBundled with an application/framework (not updated by this tool):')
            for g in groups:
                print(f"  - {g['label']} — {g['count']}")

        traceable = [r for r in results if r['update'] in ('current', 'outdated', 'unknown')]
        untraceable = [r for r in results if r['update'] == 'untraceable']

        print(f'\nTraceable, upstream checked: {len(traceable)}')
        for r in traceable:
            mark = {'current': 'OK     ', 'outdated': 'UPDATE ',
                    'unknown': 'UNKNOWN'}[r['update']]
            extra = f" (inherited from {r['inherited_from']})" if r.get('inherited_from') else ''
            print(f"  [{mark}] {r['name']}{extra}")
            print(f"            {r['detail']}  source {r['source'].get('url')}")
            if r.get('legit') == 'review':
                for n in r.get('legit_notes', []):
                    print(f"            WARN: {n}")

        print(f'\nNo source found: {len(untraceable)}')
        by_kind = {}
        for r in untraceable:
            by_kind.setdefault(r['kind'], []).append(r)
        for k, items in sorted(by_kind.items()):
            print(f"  - {k} — {len(items)} ({items[0]['detail']})")
        with_hints = [r for r in untraceable
                      if any(h['confidence'] == 'high' for h in r.get('hints', []))]
        if with_hints:
            print(f'\n  Local search found a HIGH-confidence probable origin for '
                  f'{len(with_hints)} of them (nothing downloaded, nothing updated):')
            for r in with_hints:
                repos = sorted({h['repo'] for h in r['hints'] if h['confidence'] == 'high'})
                print(f"    {r['name']} -> {', '.join(repos)}")

        print('\nTotals: ' + ', '.join(f'{k} {v}' for k, v in sorted(counts.items())))

    need = counts.get('outdated', 0) or counts.get('unknown', 0)
    return 1 if need else 0


if __name__ == '__main__':
    raise SystemExit(main())
