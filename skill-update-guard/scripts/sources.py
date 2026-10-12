#!/usr/bin/env python3
"""共享层：Skill 来源识别、聚类与安全扫描。

被 scan.py / trace.py / update.py 复用。只提供函数，不直接读写库内容。

设计要点（来自本机实测分布，不是猜测）：
- 入口目录（~/.agents 等）全是符号链接，必须按 realpath 去重，否则同一实体被重复计数；
- 女娲人物 skill 嵌在 common/nuwa-skill/examples/ 下，只有通过入口链接才能单层看到；
- Hermes 用分类目录结构（apple/apple-notes）且自带 .bundled_manifest，由 Hermes 自身管理；
- 应用内置目录（DeepSeek office-skills、Codex .system）不属于用户可更新范围。
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

# 路径可移植：默认取当前用户主目录，可用环境变量覆盖，不写死任何用户名
HOME = Path(os.environ.get('SKILLS_HOME') or Path.home())
ROOT = Path(os.environ.get('SKILLS_ROOT') or (HOME / 'Skills'))

# 平铺 SKILL.md 的目录：(路径, 归属)
FLAT_ROOTS = [
    (ROOT / 'common', 'library'),
    (ROOT / 'platforms/codex', 'library'),
    (ROOT / 'platforms/claude', 'library'),
    (ROOT / 'common/nuwa-skill/examples', 'library'),
    (HOME / '.agents/skills', 'entry'),
    (HOME / '.claude/skills', 'entry'),
    (HOME / '.codex/skills', 'entry'),
    (HOME / '.cc-switch/skills', 'entry'),
]

# 应用/框架内置，用户不可更新，只登记不追踪
BUNDLED_ROOTS = [
    (Path('/Applications/DeepSeek Harness.app/Contents/Resources/runtime/office-skills'),
     'DeepSeek Harness'),
    (HOME / '.codex/skills/.system', 'Codex'),
    (HOME / '.codex/plugins/cache', 'Codex 插件'),
]

# 分类目录结构的框架内置技能（用自带清单枚举）
NESTED_ROOTS = [
    (HOME / '.hermes/skills', 'Hermes', '.bundled_manifest'),
]

TRUSTED_HOSTS = {'github.com', 'gitlab.com', 'codeberg.org', 'bitbucket.org', 'gitee.com'}
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', '.system'}

# ---- 安全扫描：更新内容里出现这些模式就点名 ----
# 注意：模式必须收紧，否则会大面积误报。实测踩过的坑：
#   \bexec\( 会命中 JavaScript 的正则方法 .exec(font)（极为常见）
#   \.credentials 会命中测试里正常的 c.credentials(config) 断言
#   NUL 字节检测会把 png/woff 等正常素材当成可疑二进制
RISK_PATTERNS = [
    (r'\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b', '从网络直接管道执行 shell', 'high'),
    (r'\bbase64\s+(-d|--decode)\b[^\n|]*\|', 'base64 解码后管道执行', 'high'),
    (r'rm\s+-rf\s+/(?!\S*\w)', '递归删除根目录', 'high'),
    (r'(id_rsa|\.ssh/|\.aws/credentials|~/\.credentials|\.netrc|\.npmrc)',
     '读取凭证或密钥文件', 'high'),
    (r'(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+instructions',
     '指令覆盖话术（英文）', 'high'),
    (r'忽略(之前|以上|上述|前面).{0,6}(指令|要求|规则)', '指令覆盖话术（中文）', 'high'),
    (r'you\s+are\s+now\s+(a|an)\s+', '身份替换话术', 'medium'),
    (r'(?<![.\w])eval\s*\(', '动态求值执行', 'medium'),
    (r'(?<![.\w])exec\s*\(', '动态求值执行', 'medium'),
    (r'chmod\s+\+x\s+', '赋予可执行权限', 'medium'),
    (r'\bnc\s+-[a-z]*e\b|/dev/tcp/', '反弹 shell', 'high'),
    (r'(powershell|cmd\.exe)\s+.*-(enc|e)\b', '编码执行（Windows）', 'high'),
]

# 只把这些扩展名当作「可疑可执行内容」；图片/字体/压缩包属于正常素材，另行计数
BINARY_EXT = {'.exe', '.dll', '.so', '.dylib', '.bin', '.scr', '.msi', '.bat', '.cmd',
              '.jar', '.apk', '.dmg', '.pkg', '.com', '.vbs', '.ps1', '.sh'}
ASSET_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.woff', '.woff2',
             '.ttf', '.otf', '.eot', '.mp3', '.mp4', '.mov', '.webm', '.wav', '.pdf',
             '.zip', '.gz', '.tar', '.whl'}


def run(cmd, cwd=None, timeout=60):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, '', f'超时（>{timeout}s）'
    except FileNotFoundError as e:
        return 127, '', str(e)


def git(path, *args, **kw):
    return run(['git', '-C', str(path), *args], **kw)


# ---------- 来源解析 ----------

def parse_origin(url):
    """返回 (host, owner_repo, risk)。risk 为 None 表示通过核验。"""
    if not url:
        return None, None, '没有配置 origin'
    if re.search(r'://[^/]*:[^/]*@', url):
        return None, None, 'origin URL 内嵌账号密码，属凭证泄漏风险'
    m = re.match(r'^https?://([^/]+)/(.+?)(?:\.git)?/?$', url)
    if not m:
        return None, None, f'无法识别的来源形式：{url}'
    host, repo = m.group(1).lower(), m.group(2)
    if not url.startswith('https://'):
        return host, repo, f'来源不是 https：{url}'
    if host not in TRUSTED_HOSTS:
        return host, repo, f'来源主机不在可信列表（{host}）'
    return host, repo, None


def github_meta(owner_repo, cache, timeout=15):
    """查询 GitHub 仓库元数据（了解是否归档/是否 fork/星数）。带本地缓存，限量使用。"""
    if owner_repo in cache:
        return cache[owner_repo]
    import urllib.request
    info = {'checked': False}
    try:
        req = urllib.request.Request(
            f'https://api.github.com/repos/{owner_repo}',
            headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'skill-update-guard'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        info = {'checked': True, 'stars': d.get('stargazers_count'),
                'archived': d.get('archived'), 'fork': d.get('fork'),
                'license': (d.get('license') or {}).get('spdx_id'),
                'pushed_at': d.get('pushed_at'), 'description': d.get('description')}
    except Exception as e:
        info = {'checked': False, 'error': str(e)[:120]}
    cache[owner_repo] = info
    return info


# ---------- 单个 skill 的识别 ----------

def detect_source(path):
    """判断安装来源。返回 (kind, source|None, manager|None, risk|None)。"""
    path = Path(path)

    if (path / '.git').exists():
        _, url, _ = git(path, 'config', '--get', 'remote.origin.url')
        host, owner_repo, risk = parse_origin(url)
        _, commit, _ = git(path, 'rev-parse', 'HEAD')
        _, branch, _ = git(path, 'rev-parse', '--abbrev-ref', 'HEAD')
        src = {'type': 'git', 'url': url, 'host': host, 'owner_repo': owner_repo,
               'commit': commit or None, 'branch': branch or None,
               'subdir': '.', 'evidence': '.git/config'}
        return 'git', src, 'skill-update-guard', risk

    meta_file = path / '.huashu-skill-meta.json'
    if meta_file.is_file():
        try:
            d = json.loads(meta_file.read_text(encoding='utf-8'))
        except Exception as e:
            return 'copy', None, None, f'元数据损坏：{e}'
        repo = d.get('repo')
        url = f'https://github.com/{repo}.git' if repo and '/' in repo else None
        host, owner_repo, risk = parse_origin(url)
        subdir = d.get('subdir')
        if subdir in (None, '', '.'):
            # 缺失 subdir 时按仓库名推断，标记待确认
            inferred = (repo or '').split('/')[-1]
            subdir = '.' if inferred == path.name else inferred
            note = f'subdir 缺失，按仓库名推断为 {subdir!r}'
            risk = note if not risk else f'{risk}；{note}'
        src = {'type': 'copy', 'url': url, 'host': host, 'owner_repo': owner_repo,
               'commit': d.get('commit'), 'branch': None,
               'subdir': subdir, 'installed_at': d.get('installed_at'),
               'evidence': '.huashu-skill-meta.json'}
        return 'copy', src, 'skill-update-guard', risk

    return None, None, None, None


def classify(path, flat_owner):
    """综合判定实体类型。"""
    path = Path(path)
    kind, src, manager, risk = detect_source(path)

    if kind:
        return kind, src, manager, risk

    real = str(path)
    if str(path).startswith('/Applications/'):
        return 'bundled', None, 'app', None
    if '/.codex/skills/.system' in real or '/.codex/plugins/' in real:
        return 'bundled', None, 'app', None
    if real.startswith(str(HOME / '.hermes/skills')):
        # Hermes 自带的用户级技能，由 Hermes 自身生命周期管理
        return 'harness-native', None, 'harness', None
    if real.startswith(str(ROOT)):
        return 'library', None, None, None
    return 'unknown', None, None, None


# ---------- 全机发现 ----------

def discover(extra_roots=None, include_bundled=True):
    """扫描本机 skill，按 realpath 去重。返回 (entities, bundled_groups)。"""
    entities = {}

    def add(path, seen_via):
        real = Path(os.path.realpath(path))
        key = str(real)
        if key not in entities:
            kind, src, manager, risk = classify(real, seen_via)
            entities[key] = {
                'name': real.name, 'path': str(real),
                'seen_via': [], 'kind': kind, 'source': src,
                'manager': manager, 'risk': risk,
            }
        if seen_via not in entities[key]['seen_via']:
            entities[key]['seen_via'].append(seen_via)

    roots = list(FLAT_ROOTS)
    for r in (extra_roots or []):
        roots.append((Path(os.path.expanduser(r)), 'user'))

    for root, owner in roots:
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.name.startswith('.'):
                continue
            if (e / 'SKILL.md').is_file():
                add(e, owner)

    groups = []
    if include_bundled:
        for root, label in BUNDLED_ROOTS:
            if not root.is_dir():
                continue
            names = []
            for p in root.rglob('SKILL.md'):
                names.append(p.parent.name)
            if names:
                groups.append({'label': label, 'root': str(root),
                               'count': len(names), 'names': sorted(names)})

        for root, label, manifest_name in NESTED_ROOTS:
            if not root.is_dir():
                continue
            mf = root / manifest_name
            names = []
            if mf.is_file():
                for line in mf.read_text(encoding='utf-8', errors='replace').splitlines():
                    if ':' in line:
                        names.append(line.split(':', 1)[0])
            if not names:  # 退化为目录枚举
                for cat in root.iterdir():
                    if cat.is_dir() and not cat.name.startswith('.'):
                        names.extend(d.name for d in cat.iterdir()
                                     if d.is_dir() and not d.name.startswith('.'))
            groups.append({'label': label, 'root': str(root),
                           'count': len(names), 'names': sorted(names[:200])})

    return list(entities.values()), groups


def cluster(entities):
    """按来源聚类。返回按规模降序的簇列表。"""
    buckets = {}
    for e in entities:
        src = e.get('source')
        if src and src.get('owner_repo'):
            key = ('github', src['owner_repo'].split('/')[0])
            label = f"github.com/{key[1]}"
        elif e['kind'] == 'library':
            key, label = ('library', 'library'), '本库自建/无上游记录'
        elif e['kind'] == 'bundled':
            key, label = ('bundled', 'bundled'), '应用内置（由该应用自身管理）'
        elif e['kind'] == 'harness-native':
            key, label = ('harness-native', 'harness'), '框架自带用户技能（由该框架自身管理）'
        elif e['kind'] == 'unknown':
            key, label = ('unknown', 'unknown'), '未能识别的来源'
        else:
            key, label = ('other', 'other'), '其他'
        buckets.setdefault(key, {'label': label, 'kind': key[0], 'items': []})
        buckets[key]['items'].append(e)

    out = sorted(buckets.values(), key=lambda b: (-len(b['items']), b['label']))
    for b in out:
        kinds = {}
        for i in b['items']:
            kinds[i['kind']] = kinds.get(i['kind'], 0) + 1
        b['kinds'] = kinds
    return out


# ---------- 安全扫描 ----------

def scan_text_risks(text, where):
    hits = []
    for pat, label, sev in RISK_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            line = text[:m.start()].count('\n') + 1
            snippet = text.splitlines()[line - 1].strip()[:140] if line - 1 < len(text.splitlines()) else ''
            hits.append({'where': where, 'label': label, 'severity': sev,
                         'line': line, 'text': snippet})
    return hits


def scan_tree(path, max_files=4000):
    """扫描目录内所有文本文件的风险模式，并找出可疑可执行文件。

    返回 (hits, suspicious, asset_count, truncated)。
    图片/字体等正常素材只计数，不报警。
    """
    path = Path(path)
    hits, suspicious, assets, n = [], [], 0, 0
    for base, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            n += 1
            if n > max_files:
                return hits, suspicious, assets, True
            p = Path(base) / f
            ext = p.suffix.lower()
            rel = str(p.relative_to(path))
            if ext in ASSET_EXT:
                assets += 1
                continue
            if ext in BINARY_EXT:
                suspicious.append(rel)
                continue
            try:
                if p.stat().st_size > 512 * 1024:
                    continue
                raw = p.read_bytes()
            except OSError:
                continue
            if b'\x00' in raw[:1024]:
                # 未知扩展名的二进制，可能是素材也可能是可执行内容，单独归类
                suspicious.append(rel + '（未知二进制）')
                continue
            text = raw.decode('utf-8', errors='ignore')
            hits.extend(scan_text_risks(text, rel))
    return hits, suspicious, assets, False


def fingerprint(path):
    """内容指纹，与 .maintenance/inventory.py 保持一致。"""
    records = []
    for base, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if name in SKIP_DIRS or name.endswith('.pyc'):
                continue
            p = Path(base) / name
            rel = str(p.relative_to(path))
            if p.is_symlink():
                records.append((rel, 'link:' + os.readlink(p)))
            else:
                records.append((rel, hashlib.sha256(p.read_bytes()).hexdigest()))
        for name in dirs:
            p = Path(base) / name
            if p.is_symlink():
                records.append((str(p.relative_to(path)), 'link:' + os.readlink(p)))
    raw = json.dumps(sorted(records), separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest(), len(records)
