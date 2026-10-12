#!/usr/bin/env python3
"""Skills 库一键体检。

只读检查，不修改任何 Skill 内容。覆盖：
  A 内容完整性   destination 存在、SKILL.md 存在、文件指纹与 manifest 一致
  B 入口链接     311 条链接均为指向库内的有效符号链接
  C 入口一致性   各 harness 入口目录的实际条目与 manifest 完全对应
  D 元数据       frontmatter 的 name/description 合法且无重名
  E 平台隔离     Codex 专用 Skill 未泄漏到公共入口
  F 杂物         入口目录中无 .git / .DS_Store / __pycache__
  G 编目覆盖     库内目录与 manifest 双向对应，无漏编
  H 内容重复     无同哈希的重复 Skill

用法:
    python3 .maintenance/health.py            # 人读报告
    python3 .maintenance/health.py --json     # 仅输出 JSON
    python3 .maintenance/health.py --quiet    # 仅输出结论行
退出码: 0 = 全部通过, 1 = 存在 error。
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


def check():
    if not MANIFEST.is_file():
        print(f'找不到 manifest：{MANIFEST}\n'
              f'请把 SKILLS_ROOT 指向一个已初始化的 Skill 库，'
              f'或先运行 .maintenance/inventory.py 生成清单。', file=sys.stderr)
        raise SystemExit(2)
    manifest = json.loads(MANIFEST.read_text())
    entries, links = manifest['entries'], manifest['links']
    findings = collections.defaultdict(list)
    stats = {'skills': len(entries), 'links': len(links)}

    # ---------- A 内容完整性 ----------
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

    # ---------- B 入口链接 ----------
    for src, dst in links.items():
        p, t = Path(src), Path(dst)
        if not p.is_symlink():
            findings['B_not_symlink'].append(src)
        elif not p.exists():
            findings['B_dangling'].append(f'{src} -> {os.readlink(src)}')
        elif os.path.realpath(src) != os.path.realpath(dst):
            findings['B_wrong_target'].append(f'{src} -> {os.readlink(src)}')

    # ---------- C 入口一致性 ----------
    for root in ENTRY_ROOTS:
        rp = Path(root)
        if not rp.is_dir():
            findings['C_entry_root_absent'].append(root)
            continue
        actual = {x.name for x in rp.iterdir() if x.name not in IGNORED_ENTRIES}
        expected = {Path(s).name for s in links if Path(s).parent == rp}
        for name in sorted(actual - expected):
            findings['C_unexpected_entry'].append(f'{root}/{name}')
        for name in sorted(expected - actual):
            findings['C_missing_entry'].append(f'{root}/{name}')

    # ---------- D 元数据 ----------
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
            findings['H_duplicate_content'].append(f'{h[:12]}: {dupes}')

    # ---------- E 平台隔离 ----------
    private = {Path(e['destination']).name for e in entries if e['scope'] == 'codex'}
    for rel in PUBLIC_ROOTS:
        base = HOME / rel
        if not base.is_dir():
            continue
        for name in private:
            cand = base / name
            if cand.is_symlink() or cand.exists():
                findings['E_private_leak'].append(str(cand))

    # ---------- F 杂物 ----------
    # 入口目录里的 .git / .DS_Store / __pycache__ 一律视为杂物（单一实体设计的破坏）。
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
    # 库根只查顶层，不递归进 Skill 包内部。
    for n in CRUFT:
        if (ROOT / n).exists():
            findings['F_entry_cruft'].append(str(ROOT / n))
    if (ROOT / '.maintenance/__pycache__').exists():
        findings['F_entry_cruft'].append(str(ROOT / '.maintenance/__pycache__'))

    # 库内 Skill 自带的 git 仓库是上游更新通道（huashu-skill-updater 依赖），
    # 不属于杂物，仅作信息记录。
    info = collections.defaultdict(list)
    for scope, base in [('common', ROOT / 'common'),
                        ('codex', ROOT / 'platforms/codex'),
                        ('claude', ROOT / 'platforms/claude')]:
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if d.is_dir() and (d / '.git').is_dir():
                info['nested_git'].append(str(d))

    # ---------- G 编目覆盖 ----------
    catalogued = {e['destination'] for e in entries}
    for scope, base in [('common', ROOT / 'common'),
                        ('codex', ROOT / 'platforms/codex'),
                        ('claude', ROOT / 'platforms/claude')]:
        if not base.is_dir():
            continue
        for d in base.iterdir():
            if not d.is_dir() or d.name == 'nuwa-skill':
                continue
            if str(d) not in catalogued:
                findings['G_uncatalogued'].append(str(d))
    for e in entries:
        if not Path(e['destination']).is_dir() and e['destination'] not in findings['A_missing_destination']:
            findings['G_broken_catalog'].append(e['name'])

    stats['scope_counts'] = dict(collections.Counter(e['scope'] for e in entries))
    errors = {k: v for k, v in findings.items() if v}
    return {'ok': not errors, 'stats': stats, 'findings': errors,
            'info': {k: v for k, v in info.items() if v},
            'counts': {k: len(v) for k, v in sorted(findings.items()) if v}}


def main():
    ap = argparse.ArgumentParser(description='Skills 库一键体检')
    ap.add_argument('--json', action='store_true', help='仅输出 JSON')
    ap.add_argument('--quiet', action='store_true', help='仅输出结论行')
    ap.add_argument('--no-write', action='store_true', help='不写 health-report.json')
    args = ap.parse_args()

    result = check()
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
    print('Skills 库体检报告')
    print(f"  逻辑 Skill  {s['skills']}  "
          f"({', '.join(f'{k} {v}' for k, v in sorted(s['scope_counts'].items()))})")
    print(f"  入口链接    {s['links']}")
    print()
    if result['info']:
        for k, v in sorted(result['info'].items()):
            if k == 'nested_git':
                print(f"  信息: {len(v)} 个 Skill 自带上游 git 仓库（供更新检查，非杂物）")
                for item in v:
                    print(f'    - {item}')
        print()
    if result['ok']:
        print('  结论: 全部通过，无需处理')
    else:
        print('  结论: 发现以下问题')
        for k, v in sorted(result['findings'].items()):
            print(f"\n  [{k}] {len(v)} 项")
            for item in v[:12]:
                print(f'    - {item}')
            if len(v) > 12:
                print(f'    ... 另有 {len(v) - 12} 项，详见 {REPORT}')
    print(f"\n  报告: {REPORT}")
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
