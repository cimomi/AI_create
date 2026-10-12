#!/usr/bin/env python3
"""安全地把 skill 更新到上游：无污染、无病毒、可回滚。

只处理「已溯源且上游有更新」的 skill。无法溯源的 skill 本脚本一律不动。

安全保证：
- 默认演练（dry-run），加 --confirm 才写入；
- 更新前做安全扫描，命中高危模式直接拒绝（除非 --force）；
- git 类只允许快进（merge --ff-only），分叉不动；
- 复制类先在临时目录克隆并扫描，确认后才用「干净替换」覆盖（rsync --delete），
  避免旧版本残留文件污染新版本；
- 覆盖前打包备份到 .maintenance/update-backups/；
- 更新后重新对齐 manifest.json 指纹并跑库体检。

用法：
    python3 update.py --name huashu-art-motion --dry-run
    python3 update.py --name huashu-art-motion --confirm
    python3 update.py --all --confirm

exit code：0 = 成功或演练完成；1 = 未应用（有需人工处理的状况）；2 = 用法错误。
"""
import argparse
import datetime
import json
import os
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
# 这些是本库自己的管理文件，干净替换时必须保留、不能被上游内容覆盖或删除
KEEP_LOCAL = ['.huashu-skill-meta.json', '.last-update-check']
# 干净替换时要排除的项：本库自己的管理文件，以及临时克隆带来的 .git。
# 不加 .git 会把临时克隆的仓库塞进 skill 目录，让复制安装类被误判成 git 类，
# 而且那是 --depth 1 的浅克隆，后续 ff-only 会很不稳。（实测踩过）
RSYNC_EXCLUDES = ['.git'] + KEEP_LOCAL


def git(path, *args, timeout=120):
    return S.run(['git', '-C', str(path), *args], timeout=timeout)


def backup(dest, name, keep=3):
    """打包备份到 .maintenance/update-backups/，并按 skill 只保留最近 keep 份。

    实测：huashu-art-motion 一个 skill 的备份就有 190MB（含 126 个素材文件），
    不设保留策略会随更新次数无限增长。返回 (路径, 大小, 被清理的旧备份)。
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
    """重新对齐 manifest 指纹。返回 ((old,new,count)|None, err|None)。"""
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
        print(f"      [可疑可执行] {b}")
    if len(suspicious) > limit:
        print(f"      ... 另有 {len(suspicious) - limit} 个可疑文件")
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
        return None, None, '无法生成差异'
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

    # fetch 只更新 .git 的对象与远端引用，不碰工作树；没有它本地没有可比对的 commit
    print(f"  获取远端对象（只写 .git，不动工作树）…")
    code, _, ferr = git(path, 'fetch', '--quiet', 'origin', branch or 'HEAD',
                        timeout=args.timeout)
    if code != 0:
        return False, f'fetch 失败：{ferr}'

    print(f"  变更文件：")
    _, changes, _ = git(path, 'diff', '--name-status', '--no-color', f'{old}..{new}',
                        timeout=args.timeout)
    for l in changes.splitlines()[:25]:
        print(f"    {l}")

    hits, suspicious, err = scan_git_diff(path, old, new, args.timeout)
    blocked = False
    if err:
        print(f"  ⚠️  {err}")
    elif hits or suspicious:
        print(f"  安全扫描命中：")
        print_hits(hits, suspicious)
        blocked = sev_rank(hits) >= 2 and not args.force
    else:
        print("  安全扫描：未命中")

    if blocked and args.confirm:
        return False, '安全扫描命中高危模式，已拒绝写入（确认可信后用 --force 强制）'

    if not args.confirm:
        note = '演练完成（安全扫描命中高危，正式写入前需人工确认）' if blocked else 'dry-run'
        return None, note

    bak, size, pruned = backup(path, e['name'])
    print(f"  已备份 {bak.name}（{size/1048576:.1f} MB）")
    for n, sz in pruned:
        print(f"  清理旧备份 {n}（{sz/1048576:.1f} MB，仅保留最近 3 份）")

    code, out, gerr = git(path, 'merge', '--ff-only', e['remote_commit'], timeout=args.timeout)
    if code != 0:
        return False, f'快进失败：{gerr or out}'

    res, rerr = repin(path)
    if rerr == 'NOT_IN_MANIFEST':
        pass
    elif rerr:
        return False, f'指纹对齐失败：{rerr}'
    _, applied, _ = git(path, 'rev-parse', 'HEAD')
    return True, f"{old[:7]} → {applied[:7]}（备份 {bak.name}）"


def update_copy(e, plan, args):
    path = Path(e['path'])
    src = e['source']
    url = src['url']
    subdir = src.get('subdir') or '.'
    old = src['commit']
    new = e['remote_commit']

    tmp = Path(tempfile.mkdtemp(prefix='skill-guard-'))
    try:
        print(f"  克隆到临时目录进行扫描（不会直接写入 skill）：{tmp}")
        code, _, err = S.run(['git', 'clone', '--quiet', '--depth', '1', url, str(tmp / 'repo')],
                             timeout=300)
        if code != 0:
            return False, f'克隆失败：{err}'

        content = (tmp / 'repo' / subdir).resolve() if subdir not in (None, '', '.') \
            else tmp / 'repo'
        if not content.is_dir():
            return False, f'仓库里找不到子目录 {subdir!r}，subdir 元数据可能有误'
        if not (content / 'SKILL.md').is_file():
            return False, f'{subdir!r} 下没有 SKILL.md，拒绝用非 skill 内容覆盖'

        hits, suspicious, assets, truncated = S.scan_tree(content)
        print(f"  安全扫描（{subdir}/）：{assets} 个普通素材文件已跳过")
        blocked = False
        if hits or suspicious:
            print_hits(hits, suspicious)
            blocked = sev_rank(hits) >= 2 and not args.force
        else:
            print("    未命中")
        if truncated:
            print("    ⚠️  文件数超过上限，扫描未覆盖全部内容")

        _, new_commit, _ = S.run(['git', 'rev-parse', 'HEAD'], cwd=str(tmp / 'repo'),
                                 timeout=30)
        if new_commit:
            new = new_commit

        if blocked and args.confirm:
            return False, '安全扫描命中高危模式，已拒绝写入（确认可信后用 --force 强制）'

        if not args.confirm:
            note = '演练完成（安全扫描命中高危，正式写入前需人工确认）' if blocked else 'dry-run'
            return None, note

        bak, size, pruned = backup(path, e['name'])
        print(f"  已备份 {bak.name}（{size/1048576:.1f} MB）")
        for n, sz in pruned:
            print(f"  清理旧备份 {n}（{sz/1048576:.1f} MB，仅保留最近 3 份）")

        # 干净替换：--delete 清掉旧版本残留，排除本库自己的管理文件
        cmd = ['rsync', '-a', '--delete']
        for k in RSYNC_EXCLUDES:
            cmd += ['--exclude', k]
        cmd += [str(content) + '/', str(path) + '/']
        code, out, rerr = S.run(cmd, timeout=300)
        if code != 0:
            return False, f'干净替换失败（备份在 {bak.name}）：{rerr or out}'

        # 回写安装元数据
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
                return False, f'元数据回写失败：{ex}'

        res, rerr = repin(path)
        if rerr and rerr != 'NOT_IN_MANIFEST':
            return False, f'指纹对齐失败：{rerr}'
        return True, f"{old[:7]} → {new[:7]}（{subdir}/ 干净替换，备份 {bak.name}）"
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
        r = T.trace_entity(e, args.timeout, False, api_cache)
        plans.append(r)
    return plans, missing


def main():
    ap = argparse.ArgumentParser(description='安全更新已溯源的 skill')
    ap.add_argument('--name', action='append', default=[])
    ap.add_argument('--all', action='store_true', help='更新所有可更新项')
    ap.add_argument('--confirm', action='store_true', help='真正写入（默认演练）')
    ap.add_argument('--dry-run', action='store_true', help='显式演练（默认行为，便于脚本自述）')
    ap.add_argument('--root', action='append', default=None, help='额外的扫描目录，可重复')
    ap.add_argument('--force', action='store_true', help='安全扫描命中高危时仍继续（慎用）')
    ap.add_argument('--timeout', type=int, default=120)
    args = ap.parse_args()

    if not args.name and not args.all:
        print('请用 --name <skill> 或 --all 指定范围', file=sys.stderr)
        return 2

    api_cache = T.load_cache()
    if args.all:
        entities, _ = S.discover(extra_roots=args.root)
        entities = T.apply_inheritance(entities)
        # 只挑真正需要更新的，避免逐个联网
        candidates = [e['name'] for e in entities if e.get('source')]
        plans, missing = build_plan(candidates, args, api_cache)
        targets = [p for p in plans if p['update'] == 'outdated']
    else:
        plans, missing = build_plan(args.name, args, api_cache)
        targets = [p for p in plans if p['update'] == 'outdated']
        for p in plans:
            if p['update'] == 'current':
                print(f"✅ {p['name']} 已是最新（{p['source']['commit'][:7]}），跳过")
            elif p['update'] == 'untraceable':
                print(f"🔍 {p['name']} 无法溯源，本工具不更新它")
            elif p['update'] == 'unknown':
                print(f"❓ {p['name']} 无法确认上游状态，跳过：{p['detail']}")

    for n in missing:
        print(f"⚠️  本机找不到名为 {n} 的 skill", file=sys.stderr)

    if not targets:
        print('\n没有需要更新的 skill。')
        T.save_cache(api_cache)
        return 0

    mode = '应用' if args.confirm else '演练'
    updated, failed = [], []
    print(f"\n=== {mode} {len(targets)} 个更新 ===")
    for p in targets:
        print(f"\n▶ {p['name']}  [{p['kind']}]")
        print(f"  来源 {p['source']['url']}")
        print(f"  {p['detail']}")
        if p['kind'] == 'git':
            ok, msg = update_git(p, p, args)
        else:
            ok, msg = update_copy(p, p, args)
        if ok is None:
            print(f"  → 演练结束（未写入）。{msg}")
        elif ok:
            print(f"  ✅ 已更新：{msg}")
            updated.append((p['name'], msg))
        else:
            print(f"  ❌ 未更新：{msg}")
            failed.append((p['name'], msg))

    T.save_cache(api_cache)

    if args.confirm and updated:
        print('\n运行库体检…')
        r = subprocess.run([sys.executable, str(ROOT / '.maintenance/health.py'), '--quiet'],
                           capture_output=True, text=True)
        print(f"  {r.stdout.strip()}")
        if r.returncode != 0:
            print('  ⚠️  体检未通过，请检查上面报告。')

    print(f"\n结果：成功 {len(updated)}，失败 {len(failed)}，"
          f"{'演练未写入' if not args.confirm else '已写入'}")
    for n, m in failed:
        print(f"  失败 {n}：{m}")
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
