#!/usr/bin/env python3
"""把新建的 skill 正式登记进本库（manifest、入口链接、Hermes 清单、CATALOG）。

只处理「新增一个 skill」这一件事，不移动、不覆盖已有内容。
登记前会做前置校验，登记后自动跑体检；任一步失败都会回滚已写入的 manifest。

入口链接规则（按库内现有分布推导，不猜测）：
  common  → .agents/skills（全量根）+ .claude/skills（全量根）+ Hermes external_dirs
  claude  → .claude/skills
  codex   → .codex/skills
.cc-switch 与 .codex 的部分集合是迁移时保留的原有条目，不自动扩建。

用法：
    python3 register_skill.py --name skill-update-guard
    python3 register_skill.py --name foo --scope claude --reason "自研设计类技能"
    python3 register_skill.py --name foo --dry-run

exit code：0 = 成功；1 = 校验未通过或登记失败；2 = 用法错误。
"""
import argparse
import datetime
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

sys.path.insert(0, str(ROOT / '.maintenance'))
from inventory import fingerprint  # noqa: E402


def fail(msg):
    print(f'❌ {msg}', file=sys.stderr)
    raise SystemExit(1)


def parse_frontmatter(skill_md):
    text = skill_md.read_text(encoding='utf-8', errors='replace')
    m = re.match(r'^---\r?\n(.*?)\r?\n---', text, re.S)
    if not m:
        return None, 'SKILL.md 缺少 frontmatter'
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        return None, f'frontmatter 解析失败：{e}'
    if not isinstance(meta, dict):
        return None, 'frontmatter 不是映射'
    return meta, None


def hermes_add_external_dir(path_str):
    """在 ~/.hermes/config.yaml 的 skills.external_dirs 里按序插入一项。

    必须做文本级插入：该文件含 32 行注释，yaml.safe_dump 回写会把注释全部抹掉。
    只动 external_dirs 这一个列表，其余字节保持不变。
    返回 (changed, message)。changed=False 表示未改动文件。
    """
    text = HERMES_CONFIG.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)

    start = next((i for i, l in enumerate(lines)
                  if l.rstrip('\n') == '  external_dirs:'), None)
    if start is None:
        return False, '未找到 skills.external_dirs，未改动（请手动添加）'

    block = []
    j = start + 1
    while j < len(lines) and re.match(r'^  - /', lines[j]):
        block.append(j)
        j += 1
    if not block:
        return False, 'external_dirs 为空，无法定位插入点，未改动'

    want = '- ' + path_str
    if any(lines[i].strip() == want for i in block):
        return False, '已存在，无需改动'

    insert_at = block[-1] + 1
    for i in block:
        if lines[i].strip() > want:
            insert_at = i
            break
    lines.insert(insert_at, '  - ' + path_str + '\n')
    HERMES_CONFIG.write_text(''.join(lines), encoding='utf-8')
    return True, f'已按序插入（external_dirs 第 {insert_at - start} 项）'


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
    p = argparse.ArgumentParser(description='把新建的 skill 登记进本库')
    p.add_argument('--name', required=True)
    p.add_argument('--scope', choices=sorted(SCOPE_DIRS), default='common')
    p.add_argument('--reason', default=None)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    name, scope = args.name, args.scope
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', name):
        fail(f'skill 名不合法：{name}（只允许小写字母、数字、连字符）')

    dest = SCOPE_DIRS[scope] / name
    if not dest.is_dir():
        fail(f'目录不存在：{dest}')
    skill_md = dest / 'SKILL.md'
    if not skill_md.is_file():
        fail(f'缺少 SKILL.md：{dest}')

    meta, err = parse_frontmatter(skill_md)
    if err:
        fail(err)
    if meta.get('name') != name:
        fail(f"frontmatter name = {meta.get('name')!r}，与目录名 {name!r} 不一致")
    if not meta.get('description'):
        fail('frontmatter 缺少 description')
    desc = meta['description']
    if len(desc) > 500:
        print(f'⚠️  description 较长（{len(desc)} 字符），建议压缩到 500 以内以提升检索命中率')

    manifest = json.loads(MANIFEST.read_text(encoding='utf-8'))
    if any(e['name'] == name for e in manifest['entries']):
        fail(f'manifest 中已存在同名条目：{name}')
    if any(Path(e['destination']) == dest for e in manifest['entries']):
        fail(f'该目录已被其他条目占用：{dest}')

    link_roots = SCOPE_LINK_ROOTS[scope]
    for base in link_roots:
        target = base / name
        if target.exists() or target.is_symlink():
            fail(f'入口已存在，避免覆盖：{target}')

    in_hermes = bool(manifest['hermes']['external_dirs']) and \
        str(dest) in manifest['hermes']['external_dirs']
    will_hermes = scope == 'common'

    print(f'即将登记：{name}')
    print(f'  目录    {dest}')
    print(f'  范围    {scope}')
    print(f'  入口    {", ".join(str(b) for b in link_roots)}')
    print(f'  Hermes  {"加入 external_dirs" if will_hermes else "不涉及"}')
    print(f'  指纹    {fingerprint(dest)[0][:16]}（{fingerprint(dest)[1]} 个文件）')

    if args.dry_run:
        print('\n演练完成，未写入任何内容。')
        return 0

    entry = build_entry(name, scope, dest, meta)
    if args.reason:
        entry['reason'] = args.reason

    # 先建链接（可回滚）
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
        fail(f'创建入口链接失败，已回滚：{e}')

    # 再写 manifest
    backup = MANIFEST.read_text(encoding='utf-8')
    hermes_backup = HERMES_CONFIG.read_text(encoding='utf-8') if HERMES_CONFIG.is_file() else None
    try:
        # 先改 Hermes 配置，再让 manifest 的清单与它逐项一致（顺序也必须相同，
        # check_integrations.py 是逐项比对的强不变量）。
        if will_hermes and HERMES_CONFIG.is_file():
            changed, msg = hermes_add_external_dir(str(dest))
            print(f'   Hermes：{msg}')
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
        MANIFEST.write_text(backup, encoding='utf-8')
        if hermes_backup is not None:
            HERMES_CONFIG.write_text(hermes_backup, encoding='utf-8')
        for t in created:
            t.unlink(missing_ok=True)
        fail(f'写入 manifest/配置失败，已全部回滚：{e}')

    print(f'\n✅ 已登记：manifest 条目 + {len(created)} 个入口链接'
          + ('，Hermes external_dirs 已更新' if will_hermes else ''))

    print('重建 CATALOG…')
    r = subprocess.run([sys.executable, str(ROOT / '.maintenance/build_catalog.py')],
                       capture_output=True, text=True)
    print(f'   {r.stdout.strip() or r.stderr.strip()}')

    print('运行体检…')
    h = subprocess.run([sys.executable, str(ROOT / '.maintenance/health.py'), '--quiet'],
                       capture_output=True, text=True)
    print(f'   {h.stdout.strip()}')
    i = subprocess.run([sys.executable, str(ROOT / '.maintenance/check_integrations.py')],
                       capture_output=True, text=True)
    print(f'   {i.stdout.strip().splitlines()[-1] if i.stdout.strip() else i.stderr.strip()}')
    if h.returncode != 0 or i.returncode != 0:
        print('\n⚠️  体检未全通过，请按上面的报告处理。')
        return 1
    print('\n✅ 登记完成，体检与接入检查全部通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
