#!/usr/bin/env python3
"""各 harness 接入本库的实时检查（可重复运行）。

与 health.py 的分工：
  health.py            检查库内内容、链接、元数据是否自洽
  check_integrations.py 检查外部软件是否仍正确指向本库

覆盖 cc-switch、Hermes、Kimi 桌面版、Codex 与四个入口根目录。
只读，不修改任何配置。退出码: 0 = 全部通过, 1 = 存在 error。

注意：Codex `config.toml` 由 Codex 应用自身维护，应用升级会改写版本号、
浏览器服务路径和界面偏好。本脚本只检查其中的 Skill 相关事实（入口目录），
不对整份文件做逐字断言——那是一次性迁移记录（verify_config.py）的职责。
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


# ---------- 入口根目录 ----------
# 判定标准：条目的“直连目标”必须写在本库路径下。像 .cc-switch/skills/dogfood
# 指向库内 managed/hermes/dogfood（再由 managed/hermes 中转回 ~/.hermes）是合法设计，
# 因此不能用完全解析后的路径来判断。
for root in ENTRY_ROOTS:
    if not root.is_dir():
        fail(f'入口根目录缺失: {root}')
        continue
    bad = []
    for x in root.iterdir():
        if x.name in ('.DS_Store', '.system'):
            continue
        if not (x.is_symlink() and x.exists() and os.readlink(x).startswith(str(ROOT))):
            bad.append(x.name)
    if bad:
        fail(f'{root}: {len(bad)} 项不是指向本库的有效链接 -> {bad[:5]}')
    else:
        n = len([x for x in root.iterdir() if x.is_symlink()])
        ok(f'{root.parent.name}/{root.name}: {n} 项链接有效')

# ---------- cc-switch ----------
cc_file = HOME / '.cc-switch/settings.json'
if not cc_file.is_file():
    fail('cc-switch settings.json 不存在')
else:
    cc = json.loads(cc_file.read_text())
    if cc.get('skillSyncMethod') != 'symlink':
        fail(f"cc-switch skillSyncMethod = {cc.get('skillSyncMethod')!r}，应为 'symlink'")
    else:
        ok("cc-switch: skillSyncMethod = symlink")

# ---------- Hermes ----------
hermes_file = HOME / '.hermes/config.yaml'
if not hermes_file.is_file():
    fail('Hermes config.yaml 不存在')
else:
    hd = yaml.safe_load(hermes_file.read_text()).get('skills', {}).get('external_dirs', [])
    expected = MANIFEST['hermes']['external_dirs']
    if hd != expected:
        only_now = sorted(set(hd) - set(expected))
        only_exp = sorted(set(expected) - set(hd))
        fail(f'Hermes external_dirs 与 manifest 不一致 '
             f'(当前 {len(hd)} / 预期 {len(expected)}; 多 {only_now[:3]} 缺 {only_exp[:3]})')
    else:
        ok(f'Hermes: external_dirs {len(hd)} 项与 manifest 一致')
    missing = [d for d in hd if not Path(d).is_dir()]
    if missing:
        fail(f'Hermes external_dirs 中 {len(missing)} 项目录不存在 -> {missing[:3]}')

# ---------- Kimi 桌面版 ----------
plugin_file = ROOT / 'kimi.plugin.json'
if not plugin_file.is_file():
    fail('kimi.plugin.json 不存在')
else:
    pp = json.loads(plugin_file.read_text())
    bad = [p for p in pp.get('skills', []) if not (ROOT / p).is_dir()]
    if bad:
        fail(f'kimi.plugin.json 中路径不存在 -> {bad}')
    else:
        ok(f"kimi.plugin.json: {len(pp.get('skills', []))} 条共享路径有效")

registry = Path(MANIFEST['kimi_desktop']['registry'])
if not registry.is_file():
    fail(f'Kimi 插件注册表不存在: {registry}')
else:
    plugins = json.loads(registry.read_text()).get('plugins', [])
    hit = [p for p in plugins if p.get('id') == 'shared-skills-library']
    if not hit:
        fail('Kimi 注册表缺少 shared-skills-library 插件')
    elif hit[0].get('root') != str(ROOT):
        fail(f"Kimi 插件 root = {hit[0].get('root')!r}，应为 {str(ROOT)!r}")
    elif hit[0].get('enabled') is not True:
        fail('Kimi shared-skills-library 插件未启用')
    else:
        ok(f'Kimi 桌面版: shared-skills-library 已启用，root 正确（共 {len(plugins)} 插件）')

# ---------- Codex ----------
codex_cfg = HOME / '.codex/config.toml'
if not codex_cfg.is_file():
    fail('Codex config.toml 不存在')
else:
    text = codex_cfg.read_text(errors='replace')
    if 'skills' in text:
        info.append(('注意', 'Codex config.toml 出现 skills 字样，请人工确认是否有 Skill 配置'))
    ok('Codex: config.toml 存在（内容由 Codex 应用维护，Skill 入口目录已验证）')

# ---------- 输出 ----------
print('各 harness 接入检查')
for tag, msg in info:
    print(f'  [{tag}] {msg}')
if errors:
    print()
    for e in errors:
        print(f'  [错误] {e}')
    print(f'\n结论: {len(errors)} 项异常')
    raise SystemExit(1)
print(f'\n结论: 全部通过（{len(info)} 项）')
raise SystemExit(0)
