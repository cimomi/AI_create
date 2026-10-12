#!/usr/bin/env python3
"""Register a new skill into the library (manifest, entry links, Hermes list, CATALOG).

Handles exactly one thing: adding a skill. It never moves or overwrites
existing content. Pre-flight validation runs first; a health check runs after,
and any failure rolls back everything already written.

Entry-link rules, derived from the library's actual distribution rather than
guessed:

    common  ->  .agents/skills (complete root) + .claude/skills (complete root)
                + Hermes external_dirs
    claude  ->  .claude/skills
    codex   ->  .codex/skills

The partial sets under .cc-switch and .codex are pre-migration entries kept as
they were; they are not extended automatically.

Usage:
    python3 register_skill.py --name <skill>
    python3 register_skill.py --name <skill> --scope claude --reason "..."
    python3 register_skill.py --name <skill> --dry-run

Exit code: 0 = success, 1 = validation or registration failed, 2 = usage error.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import HOME, ROOT  # noqa: E402

MANIFEST = ROOT / 'manifest.json'
HERMES_CONFIG = HOME / '.hermes/config.yaml'

SCOPE_DIRS = {'common': ROOT / 'common',
              'codex': ROOT / 'platforms/codex',
              'claude': ROOT / 'platforms/claude'}
SCOPE_LINK_ROOTS = {'common': [HOME / '.agents/skills', HOME / '.claude/skills'],
                    'claude': [HOME / '.claude/skills'],
                    'codex': [HOME / '.codex/skills']}

from inventory import fingerprint  # noqa: E402


def fail(msg):
    print(f'ERROR: {msg}', file=sys.stderr)
    raise SystemExit(1)


def parse_frontmatter(skill_md):
    text = skill_md.read_text(encoding='utf-8', errors='replace')
    m = re.match(r'^---\r?\n(.*?)\r?\n---', text, re.S)
    if not m:
        return None, 'SKILL.md has no frontmatter'
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        return None, f'frontmatter parse error: {e}'
    if not isinstance(meta, dict):
        return None, 'frontmatter is not a mapping'
    return meta, None


def hermes_add_external_dir(path_str):
    """Insert one entry into skills.external_dirs in ~/.hermes/config.yaml.

    This must be a text-level edit: the file carries over thirty comment lines
    and a yaml.safe_dump round-trip would discard every one of them. Only the
    external_dirs list is touched; all other bytes stay identical.

    Returns (changed, message). changed=False means the file was not written.
    """
    text = HERMES_CONFIG.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)

    start = next((i for i, l in enumerate(lines)
                  if l.rstrip('\n') == '  external_dirs:'), None)
    if start is None:
        return False, 'skills.external_dirs not found; file unchanged (add manually)'

    block = []
    j = start + 1
    while j < len(lines) and re.match(r'^  - /', lines[j]):
        block.append(j)
        j += 1
    if not block:
        return False, 'external_dirs is empty; cannot determine insertion point'

    want = '- ' + path_str
    if any(lines[i].strip() == want for i in block):
        return False, 'already present; nothing to do'

    insert_at = block[-1] + 1
    for i in block:
        if lines[i].strip() > want:
            insert_at = i
            break
    lines.insert(insert_at, '  - ' + path_str + '\n')
    HERMES_CONFIG.write_text(''.join(lines), encoding='utf-8')
    return True, f'inserted in order (external_dirs item {insert_at - start})'


def build_entry(name, scope, dest, meta):
    digest, count = fingerprint(dest)
    skill_md = dest / 'SKILL.md'
    selected = {
        'source': 'agents' if scope == 'common' else scope,
        'path': str(dest),
        'realpath': str(dest),
        'folder': name,
        'name': name,
        'metadata': meta,
        'supplemental': {},
        'parse_error': None,
        'sha256': digest,
        'files': count,
        'skill_sha256': hashlib.sha256(skill_md.read_bytes()).hexdigest(),
        'entry_mtime': skill_md.stat().st_mtime,
        'symlink': False,
    }
    return {
        'name': name,
        'scope': scope,
        'destination': str(dest),
        'selected': selected,
        'candidates': [selected],
        'managed': False,
        'nested': False,
        'reason': ('Newly authored in-library skill; no upstream candidate'
                   if scope == 'common' else 'Newly authored platform-specific skill'),
    }


def main():
    p = argparse.ArgumentParser(description='Register a new skill into the library')
    p.add_argument('--name', required=True)
    p.add_argument('--scope', choices=sorted(SCOPE_DIRS), default='common')
    p.add_argument('--reason', default=None)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    name, scope = args.name, args.scope
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', name):
        fail(f'invalid skill name: {name!r} (lowercase letters, digits, hyphens only)')

    dest = SCOPE_DIRS[scope] / name
    if not dest.is_dir():
        fail(f'directory does not exist: {dest}')
    skill_md = dest / 'SKILL.md'
    if not skill_md.is_file():
        fail(f'SKILL.md missing: {dest}')

    meta, err = parse_frontmatter(skill_md)
    if err:
        fail(err)
    if meta.get('name') != name:
        fail(f"frontmatter name = {meta.get('name')!r} differs from directory name {name!r}")
    if not meta.get('description'):
        fail('frontmatter has no description')
    desc = meta['description']
    if len(desc) > 500:
        print(f'WARN: description is long ({len(desc)} chars); '
              f'consider trimming under 500 for better retrieval')

    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if any(e['name'] == name for e in manifest['entries']):
        fail(f'manifest already has an entry named {name!r}')
    if any(Path(e['destination']) == dest for e in manifest['entries']):
        fail(f'destination already claimed by another entry: {dest}')

    link_roots = SCOPE_LINK_ROOTS[scope]
    for base in link_roots:
        target = base / name
        if target.exists() or target.is_symlink():
            fail(f'entry link already exists, refusing to overwrite: {target}')

    will_hermes = scope == 'common'

    print(f'About to register: {name}')
    print(f'  directory  {dest}')
    print(f'  scope      {scope}')
    print(f'  entries    {", ".join(str(b) for b in link_roots)}')
    print(f'  Hermes     {"add to external_dirs" if will_hermes else "not involved"}')
    print(f'  fingerprint {fingerprint(dest)[0][:16]} ({fingerprint(dest)[1]} files)')

    if args.dry_run:
        print('\nDry run complete, nothing written.')
        return 0

    entry = build_entry(name, scope, dest, meta)
    if args.reason:
        entry['reason'] = args.reason

    # Create links first (cheap to roll back).
    created = []
    try:
        for base in link_roots:
            base.mkdir(parents=True, exist_ok=True)
            target = base / name
            os.symlink(dest, target)
            created.append(target)
    except OSError as e:
        for t in created:
            t.unlink(missing_ok=True)
        fail(f'failed to create entry links, rolled back: {e}')

    # Then write the manifest.
    manifest_backup = MANIFEST.read_text(encoding='utf-8')
    hermes_backup = HERMES_CONFIG.read_text(encoding='utf-8') if HERMES_CONFIG.is_file() else None
    try:
        # Update the Hermes config first, then mirror its list into the manifest.
        # The two must match item for item *and in order* — the integration
        # check compares them positionally as a strict invariant.
        if will_hermes and HERMES_CONFIG.is_file():
            _changed, msg = hermes_add_external_dir(str(dest))
            print(f'   Hermes: {msg}')
            conf = yaml.safe_load(HERMES_CONFIG.read_text(encoding='utf-8'))
            fresh = conf.get('skills', {}).get('external_dirs', [])
            if fresh:
                manifest['hermes']['external_dirs'] = list(fresh)
        elif will_hermes and str(dest) not in manifest['hermes']['external_dirs']:
            manifest['hermes']['external_dirs'].append(str(dest))

        manifest['entries'].append(entry)
        for base in link_roots:
            manifest['links'][str(base / name)] = str(dest)
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                            encoding='utf-8')
    except Exception as e:
        MANIFEST.write_text(manifest_backup, encoding='utf-8')
        if hermes_backup is not None:
            HERMES_CONFIG.write_text(hermes_backup, encoding='utf-8')
        for t in created:
            t.unlink(missing_ok=True)
        fail(f'failed to write manifest/config, fully rolled back: {e}')

    print(f'\nRegistered: manifest entry + {len(created)} entry link(s)'
          + (', Hermes external_dirs updated' if will_hermes else ''))

    print('Rebuilding CATALOG...')
    r = subprocess.run([sys.executable, str(ROOT / '.maintenance/build_catalog.py')],
                       capture_output=True, text=True)
    print(f'   {r.stdout.strip() or r.stderr.strip()}')

    print('Running checks...')
    h = subprocess.run([sys.executable, str(ROOT / '.maintenance/health.py'), '--quiet'],
                       capture_output=True, text=True)
    print(f'   {h.stdout.strip()}')
    i = subprocess.run([sys.executable, str(ROOT / '.maintenance/check_integrations.py')],
                       capture_output=True, text=True)
    print(f'   {i.stdout.strip().splitlines()[-1] if i.stdout.strip() else i.stderr.strip()}')
    if h.returncode != 0 or i.returncode != 0:
        print('\nWARN: not every check passed; see the report above.')
        return 1
    print('\nDone. Health and integration checks both passed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
