#!/usr/bin/env python3
"""One-shot health check for the Skills library.

Read-only: never modifies any Skill content. Covers eight checks:

  A  Content integrity   destination exists, SKILL.md present, fingerprint matches manifest
  B  Entry links         every manifest link is a valid symlink pointing inside the library
  C  Entry parity        each harness entry directory matches the manifest exactly
  D  Metadata            frontmatter name/description valid, no duplicate names
  E  Platform isolation  Codex-only skills not leaked into public entry points
  F  Cruft               no .git / .DS_Store / __pycache__ in entry directories
  G  Catalogue coverage  library directories and manifest agree both ways
  H  Content duplicates  no two skills share the same fingerprint

Usage:
    python3 tools/health.py            # human-readable report
    python3 tools/health.py --json     # JSON only
    python3 tools/health.py --quiet    # single summary line
Exit code: 0 = all checks passed, 1 = problems found, 2 = environment error.
"""
import argparse
import collections
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import HOME, ROOT, fingerprint  # noqa: E402

MANIFEST = ROOT / 'manifest.json'
REPORT = Path(__file__).resolve().parent / 'health-report.json'

PUBLIC_ROOTS = ['.agents/skills', '.claude/skills', '.gemini/skills',
                '.dsh/skills', '.kimi-code/skills']
ENTRY_ROOTS = [HOME / '.agents/skills', HOME / '.codex/skills',
               HOME / '.claude/skills', HOME / '.cc-switch/skills']
CRUFT = {'.DS_Store', '__pycache__', '.git', '.pytest_cache'}
IGNORED_ENTRIES = {'.DS_Store', '.system', '.git', '.gitignore'}

SCOPES = [('common', ROOT / 'common'),
          ('codex', ROOT / 'platforms/codex'),
          ('claude', ROOT / 'platforms/claude')]


def check():
    if not MANIFEST.is_file():
        print(f'Manifest not found: {MANIFEST}\n'
              f'Point SKILLS_ROOT at an initialised Skill library, '
              f'or run inventory.py first to generate the catalogue.',
              file=sys.stderr)
        raise SystemExit(2)
    manifest = json.loads(MANIFEST.read_text())
    entries, links = manifest['entries'], manifest['links']
    findings = collections.defaultdict(list)
    stats = {'skills': len(entries), 'links': len(links)}

    # ---------- A  Content integrity ----------
    for e in entries:
        dest = Path(e['destination'])
        if not dest.is_dir():
            findings['A_missing_destination'].append(e['name'])
            continue
        if not (dest / 'SKILL.md').is_file():
            findings['A_missing_skill_md'].append(e['name'])
            continue
        expected = e.get('deployed_sha256', e['selected']['sha256'])
        actual = fingerprint(dest)[0]
        if actual != expected:
            findings['A_content_mismatch'].append(e['name'])

    # ---------- B  Entry links ----------
    for src, dst in links.items():
        p = Path(src)
        if not p.is_symlink():
            findings['B_not_symlink'].append(src)
        elif not p.exists():
            findings['B_dangling'].append(f'{src} -> {os.readlink(src)}')
        elif os.path.realpath(src) != os.path.realpath(dst):
            findings['B_wrong_target'].append(f'{src} -> {os.readlink(src)}')

    # ---------- C  Entry parity ----------
    for root in ENTRY_ROOTS:
        rp = Path(root)
        if not rp.is_dir():
            findings['C_entry_root_absent'].append(str(root))
            continue
        actual = {x.name for x in rp.iterdir() if x.name not in IGNORED_ENTRIES}
        expected = {Path(s).name for s in links if Path(s).parent == rp}
        for name in sorted(actual - expected):
            findings['C_unexpected_entry'].append(f'{root}/{name}')
        for name in sorted(expected - actual):
            findings['C_missing_entry'].append(f'{root}/{name}')

    # ---------- D  Metadata ----------
    seen_names, by_hash = collections.defaultdict(list), collections.defaultdict(list)
    for e in entries:
        skill_md = Path(e['destination']) / 'SKILL.md'
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding='utf-8', errors='replace')
        m = re.match(r'^---\r?\n(.*?)\r?\n---', text, re.S)
        if not m:
            findings['D_no_frontmatter'].append(e['name'])
            continue
        body = m.group(1)
        nm = re.search(r'^name:\s*(.+)$', body, re.M)
        dm = re.search(r'^description:\s*(.+)$', body, re.M)
        name = nm.group(1).strip().strip('"\'') if nm else None
        if not name:
            findings['D_missing_name'].append(e['name'])
        elif name != e['name']:
            findings['D_name_mismatch'].append(f"{e['name']} (frontmatter: {name})")
        if not dm or not dm.group(1).strip():
            findings['D_missing_description'].append(e['name'])
        if name:
            seen_names[name].append(e['name'])
        by_hash[e.get('selected', {}).get('sha256', e['name'])].append(e['name'])
    for name, dupes in seen_names.items():
        if len(dupes) > 1:
            findings['D_duplicate_name'].append(f'{name}: {dupes}')
    for h, dupes in by_hash.items():
        if len(dupes) > 1:
            findings['H_duplicate_content'].append(f'{str(h)[:12]}: {dupes}')

    # ---------- E  Platform isolation ----------
    private = {Path(e['destination']).name for e in entries if e['scope'] == 'codex'}
    for rel in PUBLIC_ROOTS:
        base = HOME / rel
        if not base.is_dir():
            continue
        for name in private:
            cand = base / name
            if cand.is_symlink() or cand.exists():
                findings['E_private_leak'].append(str(cand))

    # ---------- F  Cruft ----------
    # A .git / .DS_Store / __pycache__ inside an entry directory breaks the
    # single-source design, so it counts as cruft.
    for root in ENTRY_ROOTS:
        rp = Path(root)
        if not rp.is_dir():
            continue
        for base, dirs, files in os.walk(rp):
            for n in list(dirs) + files:
                if n in CRUFT:
                    findings['F_entry_cruft'].append(str(Path(base) / n))
            if '.git' in dirs:
                dirs.remove('.git')
    # The library root is only checked one level deep, never inside skill bundles.
    for n in CRUFT:
        if (ROOT / n).exists():
            findings['F_entry_cruft'].append(str(ROOT / n))
    if (ROOT / '.maintenance/__pycache__').exists():
        findings['F_entry_cruft'].append(str(ROOT / '.maintenance/__pycache__'))

    # A git repository inside a skill bundle is that skill's upstream update
    # channel, not cruft. Recorded as information only.
    info = collections.defaultdict(list)
    for _scope, base in SCOPES:
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.is_dir() and (d / '.git').is_dir():
                info['nested_git'].append(str(d))

    # ---------- G  Catalogue coverage ----------
    catalogued = {e['destination'] for e in entries}
    for _scope, base in SCOPES:
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if not d.is_dir() or d.name == 'nuwa-skill':
                continue
            if str(d) not in catalogued:
                findings['G_uncatalogued'].append(str(d))
    for e in entries:
        if not Path(e['destination']).is_dir() \
                and e['destination'] not in findings['A_missing_destination']:
            findings['G_broken_catalog'].append(e['name'])

    stats['scope_counts'] = dict(collections.Counter(e['scope'] for e in entries))
    errors = {k: v for k, v in findings.items() if v}
    return {'ok': not errors, 'stats': stats, 'findings': errors,
            'info': {k: v for k, v in info.items() if v},
            'counts': {k: len(v) for k, v in sorted(findings.items()) if v}}


def main():
    ap = argparse.ArgumentParser(description='Skills library health check')
    ap.add_argument('--json', action='store_true', help='JSON output only')
    ap.add_argument('--quiet', action='store_true', help='single summary line')
    ap.add_argument('--no-write', action='store_true', help='do not write health-report.json')
    args = ap.parse_args()

    result = check()
    if not args.no_write:
        REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')

    if args.quiet:
        print(('OK' if result['ok'] else 'FAIL') +
              f" skills={result['stats']['skills']}"
              f" links={result['stats']['links']}"
              f" problems={sum(result['counts'].values())}")
        return 0 if result['ok'] else 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1

    s = result['stats']
    print('Skills library health report')
    print(f"  skills      {s['skills']}  "
          f"({', '.join(f'{k} {v}' for k, v in sorted(s['scope_counts'].items()))})")
    print(f"  entry links {s['links']}")
    print()
    if result['info']:
        for k, v in sorted(result['info'].items()):
            if k == 'nested_git':
                print(f'  info: {len(v)} skill(s) ship their own upstream git repo '
                      f'(update channel, not cruft)')
                for item in v:
                    print(f'    - {item}')
        print()
    if result['ok']:
        print('  result: all checks passed')
    else:
        print('  result: problems found')
        for k, v in sorted(result['findings'].items()):
            print(f"\n  [{k}] {len(v)} item(s)")
            for item in v[:12]:
                print(f'    - {item}')
            if len(v) > 12:
                print(f'    ... {len(v) - 12} more, see {REPORT}')
    print(f"\n  report: {REPORT}")
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
