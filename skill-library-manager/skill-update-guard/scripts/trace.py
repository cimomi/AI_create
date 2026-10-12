#!/usr/bin/env python3
"""溯源：查清每个 skill 的安装来源、是否正规、上游是否有更新。

全程只读。不 clone、不 fetch 到工作目录、不下载任何内容。
（git ls-remote 只读远端引用；GitHub API 只读元数据。二者都不写入本地仓库。）

对无法溯源的 skill，会尝试自行寻找来源（查 SKILL.md 里的仓库链接、查 manifest 的
原始来源记录、必要时按名字查 GitHub），但**只找不装**——结果汇总后交用户决定。

用法：
    python3 trace.py                      # 扫描全机并溯源
    python3 trace.py --root DIR           # 限定范围（可重复）
    python3 trace.py --json
    python3 trace.py --no-api             # 不调用 GitHub API（省额度/离线）
    python3 trace.py --timeout 30

exit code：0 = 全部最新且无待处理；1 = 有可更新项或需要人工介入；2 = 出错。
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sources as S  # noqa: E402

CACHE_FILE = S.ROOT / '.maintenance/.skill-guard-github-cache.json'

# 可被追踪上游的类型
TRACEABLE = {'git', 'copy'}


def remote_head(path, branch, url, timeout):
    """只读获取远端分支 HEAD。branch 为空时尝试 HEAD。"""
    if branch:
        code, out, err = S.run(['git', '-C', str(path), 'ls-remote', '--heads', url, branch],
                               timeout=timeout)
        if code == 0 and out:
            return out.split()[0], None
    code, out, err = S.run(['git', 'ls-remote', url, 'HEAD'], timeout=timeout)
    if code != 0:
        return None, err or 'ls-remote 失败'
    if not out:
        return None, '远端没有返回任何引用'
    return out.split()[0], None


def is_ancestor(path, old, new, timeout=30):
    """判断 old 是否为 new 的祖先。

    返回 True/False/None。None 表示**无法判断**：远端 commit 还没 fetch 到本地，
    此时绝不能当成「已分叉」——那会把正常可快进的更新误报成冲突。
    """
    code, _, _ = S.run(['git', '-C', str(path), 'cat-file', '-e', f'{new}^{{commit}}'],
                       timeout=timeout)
    if code != 0:
        return None
    code, _, _ = S.run(['git', '-C', str(path), 'merge-base', '--is-ancestor', old, new],
                       timeout=timeout)
    return code == 0


def find_source_hints(path):
    """无法溯源时自行寻找线索：只在本地文件里找，不联网、不下载。

    必须区分可信度。实测教训：只看「URL + 名字相同 + 出现 install」会把 skill
    文档里引用的**依赖库主页**误判成 skill 来源（matplotlib / polars / sympy /
    markitdown / exa-py 全部中招），进而在报告里给出错误的溯源结论。

    因此只有满足下列之一才计为 high：
      - 出现在安装元数据文件（.huashu-skill-meta.json / plugin.json / package.json 的
        repository 字段）里；
      - 仓库路径带 skill/claude/agent/plugin 等特征词，且同一行是出处语义。
    其余一律 low，并在报告里单独标注「疑似依赖库或参考资料」，不当作来源。
    """
    PROVENANCE_FILES = {'.huashu-skill-meta.json', 'plugin.json'}
    SKILLISH = re.compile(r'skill|claude|agent|plugin|prompt|awesome|persona', re.I)
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
                conf = 'low'          # 依赖安装语句，不是 skill 出处
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
    """嵌套在某个可溯源 skill 目录内的 skill，继承其来源。

    实测：15 个人物 skill 位于 common/nuwa-skill/examples/ 下，本身没有 git 或
    安装元数据，但它们是 nuwa-skill 这个复制安装包的一部分，来源应当继承。
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
    """查 manifest 里记录的原始来源（迁移前的本地路径）。"""
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
    """返回该 skill 的溯源与更新状态。"""
    kind = e['kind']
    src = e.get('source')
    inherited = False
    if not src and e.get('inherited_source'):
        src = e['inherited_source']
        inherited = True
        kind = 'copy'          # 按父级安装方式处理
        e = dict(e, kind=kind, source=src)

    out = dict(e)
    out['update'] = 'not-applicable'
    out['detail'] = ''
    out['legit'] = None
    if inherited:
        out['inherited_from'] = e.get('inherited_from')

    if kind not in TRACEABLE:
        reason = {
            'library': '库内自建/归并内容，未见上游记录',
            'bundled': '应用内置，随该应用升级',
            'harness-native': '框架自带技能，随该框架升级',
            'unknown': '无 git 仓库也无安装元数据',
        }.get(kind, '不可追踪的类型')
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
        out['detail'] = '安装元数据里没有可用的仓库地址'
        out['hints'] = find_source_hints(e['path'])
        return out

    # ---- 来源正规性 ----
    legit_notes = []
    if e['risk']:
        legit_notes.append(e['risk'])
    if use_api and src.get('owner_repo'):
        meta = S.github_meta(src['owner_repo'], api_cache, timeout)
        out['repo_meta'] = meta
        if meta.get('checked'):
            if meta.get('archived'):
                legit_notes.append('上游仓库已归档（archived），不再维护')
            if meta.get('stars') is not None and meta['stars'] < 3:
                legit_notes.append(f"上游仓库星数很低（{meta['stars']}），请确认是否是正主")
            out['legit'] = 'ok' if not legit_notes else 'review'
        else:
            out['legit'] = 'unknown'
    else:
        out['legit'] = 'ok' if not legit_notes else 'review'
    out['legit_notes'] = legit_notes

    # ---- 上游更新 ----
    remote, err = remote_head(e['path'], src.get('branch'), url, timeout)
    if not remote:
        out['update'] = 'unknown'
        out['detail'] = f'无法访问上游：{err}'
        return out
    out['remote_commit'] = remote
    local = src.get('commit')

    if not local:
        out['update'] = 'unknown'
        out['detail'] = '本地没有记录安装版本，无法比较'
        return out

    if local == remote:
        out['update'] = 'current'
        out['detail'] = f'已是最新（{local[:7]}）'
        return out

    out['update'] = 'outdated'
    out['detail'] = f'本地 {local[:7]} → 远端 {remote[:7]}'
    if kind == 'git':
        ff = is_ancestor(e['path'], local, remote, timeout)
        out['fast_forward'] = ff
        if ff is False:
            out['detail'] += '（已分叉，需人工处理）'
        elif ff is None:
            out['detail'] += '（未 fetch，快进可行性在更新时判定）'
    return out


def main():
    ap = argparse.ArgumentParser(description='溯源 skill 来源与上游更新（只读）')
    ap.add_argument('--root', action='append', default=None, help='限定扫描目录，可重复')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--no-api', action='store_true', help='不调用 GitHub API')
    ap.add_argument('--no-cache', action='store_true', help='忽略 API 缓存')
    ap.add_argument('--timeout', type=int, default=30)
    args = ap.parse_args()

    entities, groups = S.discover(extra_roots=args.root)
    entities = apply_inheritance(entities)
    api_cache = {} if args.no_cache else load_cache()

    results = []
    for e in sorted(entities, key=lambda x: x['name']):
        results.append(trace_entity(e, args.timeout, not args.no_api, api_cache))
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
        print(f'本机唯一 skill 实体：{len(results)} 个（已按真实路径去重）\n')
        print('来源聚类：')
        for c in clusters:
            print(f"  ● {c['label']} — {len(c['items'])} 个")
        if groups:
            print('\n框架/应用内置（不由本工具更新）：')
            for g in groups:
                print(f"  ○ {g['label']} — {g['count']} 个")

        traceable = [r for r in results if r['update'] in ('current', 'outdated', 'unknown')]
        untraceable = [r for r in results if r['update'] == 'untraceable']

        print(f'\n可溯源并已核对上游：{len(traceable)} 个')
        for r in traceable:
            mark = {'current': '✅ 无更新', 'outdated': '⬆️  有更新',
                    'unknown': '❓ 无法确认'}[r['update']]
            extra = f"（继承自 {r['inherited_from']}）" if r.get('inherited_from') else ''
            print(f"  {mark}  {r['name']}{extra}")
            print(f"          {r['detail']}  来源 {r['source'].get('url')}")
            if r.get('legit') == 'review':
                for n in r.get('legit_notes', []):
                    print(f"          ⚠️  {n}")

        print(f'\n找不到来源：{len(untraceable)} 个')
        by_kind = {}
        for r in untraceable:
            by_kind.setdefault(r['kind'], []).append(r)
        for k, items in sorted(by_kind.items()):
            print(f"  · {k} — {len(items)} 个（{items[0]['detail']}）")
        with_hints = [r for r in untraceable
                      if any(h['confidence'] == 'high' for h in r.get('hints', []))]
        if with_hints:
            print(f'\n  自行寻找后找到【高可信】疑似来源的 {len(with_hints)} 个（未下载、未更新）：')
            for r in with_hints:
                repos = sorted({h['repo'] for h in r['hints'] if h['confidence'] == 'high'})
                print(f"    🔎 {r['name']} → {', '.join(repos)}")

        print('\n统计：' + '，'.join(f'{k} {v}' for k, v in sorted(counts.items())))

    need = counts.get('outdated', 0) or counts.get('unknown', 0)
    return 1 if need else 0


if __name__ == '__main__':
    raise SystemExit(main())
