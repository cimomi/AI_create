#!/usr/bin/env python3
"""公开推送前的强制安全审计。发现任何问题即返回非零，阻止推送。

为什么要扫历史：一次泄漏哪怕后来删掉了，只要进过提交，就仍然在 git 对象里、
仍然能通过 API 取到。所以审计必须覆盖「当前文件」和「全部历史 blob」两部分。

扫描项：
  1. 绝对路径（/Users/<名字>、/home/<名字>）——暴露用户名与目录结构
  2. 密钥（各类 token、API key、私钥、JWT、通用密码赋值）
  3. 禁止入库的文件（.env / *.pem / *.key / 凭证文件 / 备份 / 缓存）
  4. 过大的文件与归档
  5. 非根目录的 LICENSE（提示可能存在第三方内容）

用法：
    python3 scripts/audit.py              # 当前文件 + 全部历史
    python3 scripts/audit.py --no-history # 只查当前文件（快）
    python3 scripts/audit.py --json

退出码：0 = 通过；1 = 发现问题；2 = 用法/环境错误。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ---- 文档里合理出现的示例路径，不算泄漏 ----
PLACEHOLDER_USERS = {'me', 'user', 'you', 'your', 'username', 'yourname',
                     'example', 'someone', 'name', 'test'}

PATH_RE = re.compile(r'/(?:Users|home)/([A-Za-z0-9_.-]+)')

SECRET_PATTERNS = [
    ('GitHub PAT (classic)', re.compile(r'gh[pousr]_[A-Za-z0-9]{36,}')),
    ('GitHub PAT (fine-grained)', re.compile(r'github_pat_[A-Za-z0-9_]{60,}')),
    ('Anthropic API key', re.compile(r'sk-ant-[A-Za-z0-9_\-]{30,}')),
    ('OpenAI API key', re.compile(r'sk-[A-Za-z0-9]{32,}')),
    ('AWS access key', re.compile(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b')),
    ('Slack token', re.compile(r'xox[baprs]-[A-Za-z0-9\-]{10,}')),
    ('Google API key', re.compile(r'AIza[0-9A-Za-z_\-]{35}')),
    ('私钥块', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('JWT', re.compile(r'eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}')),
    ('通用密钥赋值', re.compile(
        r'(?i)\b(password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)'
        r'\s*[:=]\s*["\']([^"\'\s]{16,})["\']')),
]

# 通用赋值模式的豁免：占位符、测试值、变量引用
BENIGN_VALUE = re.compile(
    r'(?i)^(your|my|test|dummy|fake|sample|example|placeholder|changeme|xxx|todo|'
    r'<|\{\{|\$\{|%\(|none|null|empty|redacted|\*{3,}|sk-your|sk-test|test-)')

FORBIDDEN_PATH = [
    (re.compile(r'(^|/)\.env(\.|$)'), '.env 环境文件'),
    (re.compile(r'(^|/)\.envrc$'), '.envrc'),
    (re.compile(r'\.(pem|key|p12|pfx|jks|keystore)$', re.I), '密钥/证书文件'),
    (re.compile(r'(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$'), 'SSH 私钥'),
    (re.compile(r'(^|/)\.netrc$|(^|/)\.git-credentials$'), '凭证文件'),
    (re.compile(r'(^|/)\.(aws|ssh|kube|docker)/'), '凭证目录'),
    (re.compile(r'(^|/)node_modules/'), 'node_modules'),
    (re.compile(r'(^|/)__pycache__/|\.pyc$'), 'Python 缓存'),
    (re.compile(r'(^|/)(update-backups|\.recovery|\.venv|venv)/'), '备份/虚拟环境'),
    (re.compile(r'(^|/)\.DS_Store$'), '.DS_Store'),
    (re.compile(r'(^|/)health-report\.json$'), '工具运行产物'),
]

MAX_FILE_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 5_000_000
ARCHIVE_EXT = {'.tar', '.gz', '.tgz', '.zip', '.7z', '.rar', '.dmg', '.iso'}


def git(*args, cwd=None):
    r = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout, r.stderr


def scan_text(text, where, findings):
    for m in PATH_RE.finditer(text):
        if m.group(1) not in PLACEHOLDER_USERS:
            line = text[:m.start()].count('\n') + 1
            findings.append({'type': '绝对路径', 'where': where, 'line': line,
                             'detail': m.group(0)})
    for name, pat in SECRET_PATTERNS:
        for m in pat.finditer(text):
            if name == '通用密钥赋值' and BENIGN_VALUE.match(m.group(2) or ''):
                continue
            line = text[:m.start()].count('\n') + 1
            hit = m.group(0)
            findings.append({'type': f'疑似密钥（{name}）', 'where': where, 'line': line,
                             'detail': (hit[:60] + '…') if len(hit) > 60 else hit})


def check_path_listing(paths, findings):
    for p in paths:
        for pat, label in FORBIDDEN_PATH:
            if pat.search(p):
                findings.append({'type': '禁止入库的文件', 'where': p, 'line': 0, 'detail': label})
                break


def scan_worktree(root, findings):
    code, out, _ = git('ls-files', '-z', cwd=root)
    files = [f for f in out.split('\0') if f]
    check_path_listing(files, findings)
    for rel in files:
        p = Path(root) / rel
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
            raw = p.read_bytes()
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            findings.append({'type': '文件过大', 'where': rel, 'line': 0,
                             'detail': f'{size/1048576:.1f} MB'})
        if p.suffix.lower() in ARCHIVE_EXT and size > MAX_ARCHIVE_BYTES:
            findings.append({'type': '归档过大', 'where': rel, 'line': 0,
                             'detail': f'{size/1048576:.1f} MB'})
        if b'\x00' in raw[:1024]:
            continue
        if not Path(rel).name.lower().startswith('license'):
            scan_text(raw.decode('utf-8', errors='ignore'), rel, findings)
    for rel in files:
        if Path(rel).name.lower().startswith('license') and '/' in rel:
            findings.append({'type': '提示：子目录含 LICENSE', 'where': rel, 'line': 0,
                             'detail': '确认不是第三方内容随仓库分发'})


def scan_history(root, findings, limit=20000):
    """扫描全部历史中的唯一 blob（去重，避免重复扫同一份内容）。"""
    code, out, _ = git('rev-list', '--objects', '--all', cwd=root)
    if code != 0:
        findings.append({'type': '审计异常', 'where': '-', 'line': 0,
                         'detail': '无法列出历史对象'})
        return 0
    seen, blobs = set(), []
    for line in out.splitlines():
        parts = line.split(' ', 1)
        if len(parts) < 2:
            continue
        sha, path = parts
        if sha in seen:
            continue
        seen.add(sha)
        blobs.append((sha, path))
        if len(blobs) >= limit:
            break

    scanned = 0
    for sha, path in blobs:
        code, content, _ = git('cat-file', '-p', sha, cwd=root)
        if code != 0 or '\x00' in content[:1024]:
            continue
        scanned += 1
        scan_text(content, f'{path}（历史 {sha[:7]}）', findings)
    return scanned


def main():
    ap = argparse.ArgumentParser(description='公开推送前的强制安全审计')
    ap.add_argument('--no-history', action='store_true', help='跳过历史扫描（更快）')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--root', default=None)
    args = ap.parse_args()

    root = args.root or subprocess.run(['git', 'rev-parse', '--show-toplevel'],
                                       capture_output=True, text=True).stdout.strip()
    if not root or not Path(root).is_dir():
        print('不在 git 仓库中', file=sys.stderr)
        return 2

    findings = []
    scan_worktree(root, findings)
    n_hist = 0
    if not args.no_history:
        n_hist = scan_history(root, findings)

    uniq, seen = [], set()
    for f in findings:
        key = (f['type'], f['where'], f['line'], f['detail'])
        if key not in seen:
            seen.add(key)
            uniq.append(f)

    hard = [f for f in uniq if not f['type'].startswith('提示')]
    soft = [f for f in uniq if f['type'].startswith('提示')]

    if args.json:
        print(json.dumps({'ok': not hard, 'hard': hard, 'soft': soft,
                          'history_blobs_scanned': n_hist}, ensure_ascii=False, indent=2))
    else:
        print(f'安全审计：{root}')
        print(f'  历史 blob 已扫 {n_hist} 个' if not args.no_history else '  已跳过历史扫描')
        if hard:
            print(f'\n  ✗ 发现 {len(hard)} 项阻断问题：')
            for f in hard[:40]:
                loc = f"{f['where']}:{f['line']}" if f['line'] else f['where']
                print(f"     [{f['type']}] {loc}")
                print(f"         {f['detail']}")
            if len(hard) > 40:
                print(f'     … 另有 {len(hard) - 40} 项')
        else:
            print('  ✓ 无阻断问题')
        if soft:
            print(f'\n  ⚠️  {len(soft)} 项提示：')
            for f in soft[:10]:
                print(f"     {f['where']} — {f['detail']}")
        print('\n结论：' + ('不通过，已阻止本次操作' if hard else '通过'))

    return 1 if hard else 0


if __name__ == '__main__':
    raise SystemExit(main())
