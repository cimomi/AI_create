#!/usr/bin/env python3
"""Live check that every harness still points at this library. Repeatable.

Division of labour with health.py:
    health.py              checks that the library is internally consistent
    check_integrations.py  checks that external tools still point at the library

Covers cc-switch, Hermes, Kimi Desktop, Codex, and the four entry roots.
Read-only: never modifies any configuration.
Exit code: 0 = all passed, 1 = at least one error.

Note: Codex `config.toml` is maintained by the Codex application itself. App
upgrades rewrite the version string, browser service path, and UI preferences.
This script therefore only verifies the skill-related fact (the entry
directory); asserting on the whole file would raise false alarms on every
normal application upgrade.
"""
import json
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import HOME, ROOT  # noqa: E402

MANIFEST = json.loads((ROOT / 'manifest.json').read_text())

ENTRY_ROOTS = [HOME / '.agents/skills', HOME / '.codex/skills',
               HOME / '.claude/skills', HOME / '.cc-switch/skills']

errors, info = [], []


def ok(msg):
    info.append(('OK', msg))


def fail(msg):
    errors.append(msg)


# ---------- Entry roots ----------
# The criterion is the *direct* link target, not the fully resolved path.
# .cc-switch/skills/dogfood -> <library>/managed/hermes/dogfood is legitimate:
# managed/hermes itself is a link through to ~/.hermes, so the resolved path
# lands outside the library even though the design is correct.
for root in ENTRY_ROOTS:
    if not root.is_dir():
        fail(f'entry root missing: {root}')
        continue
    bad = []
    for x in root.iterdir():
        if x.name in ('.DS_Store', '.system'):
            continue
        if not (x.is_symlink() and x.exists() and os.readlink(x).startswith(str(ROOT))):
            bad.append(x.name)
    if bad:
        fail(f'{root}: {len(bad)} entr(ies) are not valid links into the library -> {bad[:5]}')
    else:
        n = len([x for x in root.iterdir() if x.is_symlink()])
        ok(f'{root.parent.name}/{root.name}: {n} valid links')

# ---------- cc-switch ----------
cc_file = HOME / '.cc-switch/settings.json'
if not cc_file.is_file():
    fail('cc-switch settings.json not found')
else:
    cc = json.loads(cc_file.read_text())
    if cc.get('skillSyncMethod') != 'symlink':
        fail(f"cc-switch skillSyncMethod = {cc.get('skillSyncMethod')!r}, expected 'symlink'")
    else:
        ok('cc-switch: skillSyncMethod = symlink')

# ---------- Hermes ----------
hermes_file = HOME / '.hermes/config.yaml'
if not hermes_file.is_file():
    fail('Hermes config.yaml not found')
else:
    hd = yaml.safe_load(hermes_file.read_text()).get('skills', {}).get('external_dirs', [])
    expected = MANIFEST['hermes']['external_dirs']
    if hd != expected:
        only_now = sorted(set(hd) - set(expected))
        only_exp = sorted(set(expected) - set(hd))
        fail(f'Hermes external_dirs differs from manifest '
             f'(have {len(hd)} / expect {len(expected)}; extra {only_now[:3]} missing {only_exp[:3]})')
    else:
        ok(f'Hermes: external_dirs matches manifest ({len(hd)} entries)')
    missing = [d for d in hd if not Path(d).is_dir()]
    if missing:
        fail(f'Hermes external_dirs: {len(missing)} path(s) do not exist -> {missing[:3]}')

# ---------- Kimi Desktop ----------
plugin_file = ROOT / 'kimi.plugin.json'
if not plugin_file.is_file():
    fail('kimi.plugin.json not found')
else:
    pp = json.loads(plugin_file.read_text())
    bad = [p for p in pp.get('skills', []) if not (ROOT / p).is_dir()]
    if bad:
        fail(f'kimi.plugin.json references missing paths -> {bad}')
    else:
        ok(f"kimi.plugin.json: {len(pp.get('skills', []))} shared path(s) valid")

registry = Path(MANIFEST['kimi_desktop']['registry'])
if not registry.is_file():
    fail(f'Kimi plugin registry not found: {registry}')
else:
    plugins = json.loads(registry.read_text()).get('plugins', [])
    hit = [p for p in plugins if p.get('id') == 'shared-skills-library']
    if not hit:
        fail('Kimi registry is missing the shared-skills-library plugin')
    elif hit[0].get('root') != str(ROOT):
        fail(f"Kimi plugin root = {hit[0].get('root')!r}, expected {str(ROOT)!r}")
    elif hit[0].get('enabled') is not True:
        fail('Kimi shared-skills-library plugin is not enabled')
    else:
        ok(f'Kimi Desktop: shared-skills-library enabled, root correct '
           f'({len(plugins)} plugins total)')

# ---------- Codex ----------
codex_cfg = HOME / '.codex/config.toml'
if not codex_cfg.is_file():
    fail('Codex config.toml not found')
else:
    text = codex_cfg.read_text(errors='replace')
    if 'skills' in text:
        info.append(('NOTE', 'Codex config.toml mentions "skills" — confirm manually'))
    ok('Codex: config.toml present (maintained by the Codex app; entry directory verified)')

# ---------- Output ----------
print('Harness integration check')
for tag, msg in info:
    print(f'  [{tag}] {msg}')
if errors:
    print()
    for e in errors:
        print(f'  [ERROR] {e}')
    print(f'\nResult: {len(errors)} problem(s)')
    raise SystemExit(1)
print(f'\nResult: all passed ({len(info)} checks)')
raise SystemExit(0)
