#!/usr/bin/env python3
"""Shared layer: skill provenance detection, clustering, and safety scanning.

Reused by trace.py and update.py. Provides functions only; never reads or
writes library content on its own.

Design notes, all derived from the actual distribution on this machine rather
than assumptions:
  - Entry directories (~/.agents and friends) are entirely symlinks, so
    entities must be de-duplicated by realpath or the same skill is counted
    several times.
  - The 15 persona skills live inside common/nuwa-skill/examples/ and are only
    visible one level deep through the entry links.
  - Hermes uses a category layout (apple/apple-notes) and ships its own
    .bundled_manifest; those skills are managed by Hermes itself.
  - Application-bundled directories (DeepSeek office-skills, Codex .system)
    are outside the user-updatable scope.
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

# Portable paths: no username is hard-coded; override with environment variables.
HOME = Path(os.environ.get('SKILLS_HOME') or Path.home())
ROOT = Path(os.environ.get('SKILLS_ROOT') or (HOME / 'Skills'))

# Directories holding flat SKILL.md bundles: (path, owner label)
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

# Application/framework bundles: recorded but never updated by this tool.
BUNDLED_ROOTS = [
    (Path('/Applications/DeepSeek Harness.app/Contents/Resources/runtime/office-skills'),
     'DeepSeek Harness'),
    (HOME / '.codex/skills/.system', 'Codex'),
    (HOME / '.codex/plugins/cache', 'Codex plugins'),
]

# Framework bundles using a category layout, enumerated from a shipped manifest.
NESTED_ROOTS = [
    (HOME / '.hermes/skills', 'Hermes', '.bundled_manifest'),
]

TRUSTED_HOSTS = {'github.com', 'gitlab.com', 'codeberg.org', 'bitbucket.org', 'gitee.com'}
SKIP_DIRS = {'.git', 'node_modules', '__pycache__', '.venv', '.system'}

# ---- Safety scanning: patterns that get flagged inside incoming updates ----
# These must stay tight or they produce mass false positives. Real cases hit
# while building this:
#   \bexec\( matched JavaScript's RegExp method .exec(font), which is everywhere
#   \.credentials matched a legitimate c.credentials(config) test assertion
#   NUL-byte detection treated ordinary png/woff assets as suspicious binaries
# NOTE: two patterns below are intentionally written in Chinese characters.
# They detect Chinese-language prompt injection and are not documentation —
# translating them would silently stop detecting that class of attack.
RISK_PATTERNS = [
    (r'\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b',
     'pipes a download straight into a shell', 'high'),
    (r'\bbase64\s+(-d|--decode)\b[^\n|]*\|',
     'base64-decodes into a pipe', 'high'),
    (r'rm\s+-rf\s+/(?!\S*\w)', 'recursive delete of root', 'high'),
    (r'(id_rsa|\.ssh/|\.aws/credentials|~/\.credentials|\.netrc|\.npmrc)',
     'reads credential or key files', 'high'),
    (r'(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+instructions',
     'instruction-override phrasing (English)', 'high'),
    (r'忽略(之前|以上|上述|前面).{0,6}(指令|要求|规则)',
     'instruction-override phrasing (Chinese)', 'high'),
    (r'you\s+are\s+now\s+(a|an)\s+', 'identity-replacement phrasing', 'medium'),
    (r'(?<![.\w])eval\s*\(', 'dynamic evaluation', 'medium'),
    (r'(?<![.\w])exec\s*\(', 'dynamic evaluation', 'medium'),
    (r'chmod\s+\+x\s+', 'grants execute permission', 'medium'),
    (r'\bnc\s+-[a-z]*e\b|/dev/tcp/', 'reverse shell', 'high'),
    (r'(powershell|cmd\.exe)\s+.*-(enc|e)\b', 'encoded execution (Windows)', 'high'),
]

# Only these extensions count as suspicious executable content. Images, fonts
# and archives are ordinary assets and are merely counted.
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
        return 124, '', f'timed out (>{timeout}s)'
    except FileNotFoundError as e:
        return 127, '', str(e)


def git(path, *args, **kw):
    return run(['git', '-C', str(path), *args], **kw)


# ---------- Provenance parsing ----------

def parse_origin(url):
    """Return (host, owner_repo, risk). risk is None when the source checks out."""
    if not url:
        return None, None, 'no origin configured'
    if re.search(r'://[^/]*:[^/]*@', url):
        return None, None, 'origin URL embeds credentials'
    m = re.match(r'^https?://([^/]+)/(.+?)(?:\.git)?/?$', url)
    if not m:
        return None, None, f'unrecognised source format: {url}'
    host, repo = m.group(1).lower(), m.group(2)
    if not url.startswith('https://'):
        return host, repo, f'source is not https: {url}'
    if host not in TRUSTED_HOSTS:
        return host, repo, f'source host not in the trusted list ({host})'
    return host, repo, None


def github_meta(owner_repo, cache, timeout=15):
    """Fetch GitHub repository metadata (archived / fork / stars). Cached.

    The unauthenticated API allows only 60 requests per hour, so every call is
    cached on disk and callers are expected to skip this when offline.
    """
    if owner_repo in cache:
        return cache[owner_repo]
    import urllib.request
    info = {'checked': False}
    try:
        req = urllib.request.Request(
            f'https://api.github.com/repos/{owner_repo}',
            headers={'Accept': 'application/vnd.github+json',
                     'User-Agent': 'skill-update-guard'})
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


# ---------- Single-skill detection ----------

def detect_source(path):
    """Determine how a skill was installed. Returns (kind, source, manager, risk)."""
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
            return 'copy', None, None, f'corrupt metadata: {e}'
        repo = d.get('repo')
        url = f'https://github.com/{repo}.git' if repo and '/' in repo else None
        host, owner_repo, risk = parse_origin(url)
        subdir = d.get('subdir')
        if subdir in (None, '', '.'):
            # Missing subdir: infer from the repository name and flag it.
            inferred = (repo or '').split('/')[-1]
            subdir = '.' if inferred == path.name else inferred
            note = f'subdir missing, inferred {subdir!r} from the repository name'
            risk = note if not risk else f'{risk}; {note}'
        src = {'type': 'copy', 'url': url, 'host': host, 'owner_repo': owner_repo,
               'commit': d.get('commit'), 'branch': None,
               'subdir': subdir, 'installed_at': d.get('installed_at'),
               'evidence': '.huashu-skill-meta.json'}
        return 'copy', src, 'skill-update-guard', risk

    return None, None, None, None


def classify(path, flat_owner):
    """Determine the overall entity kind."""
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
        # User-level skills shipped with Hermes, managed by Hermes' own lifecycle.
        return 'harness-native', None, 'harness', None
    if real.startswith(str(ROOT)):
        return 'library', None, None, None
    return 'unknown', None, None, None


# ---------- Machine-wide discovery ----------

def discover(extra_roots=None, include_bundled=True):
    """Scan the machine for skills, de-duplicated by realpath.

    Returns (entities, bundled_groups).
    """
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
            names = [p.parent.name for p in root.rglob('SKILL.md')]
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
            if not names:  # fall back to directory enumeration
                for cat in root.iterdir():
                    if cat.is_dir() and not cat.name.startswith('.'):
                        names.extend(d.name for d in cat.iterdir()
                                     if d.is_dir() and not d.name.startswith('.'))
            groups.append({'label': label, 'root': str(root),
                           'count': len(names), 'names': sorted(names[:200])})

    return list(entities.values()), groups


def cluster(entities):
    """Group entities by source. Returns clusters ordered by size, descending."""
    buckets = {}
    for e in entities:
        src = e.get('source')
        if src and src.get('owner_repo'):
            key = ('github', src['owner_repo'].split('/')[0])
            label = f"github.com/{key[1]}"
        elif e['kind'] == 'library':
            key, label = ('library', 'library'), 'authored in-library, no upstream recorded'
        elif e['kind'] == 'bundled':
            key, label = ('bundled', 'bundled'), 'bundled with an application (self-managed)'
        elif e['kind'] == 'harness-native':
            key, label = ('harness-native', 'harness'), 'shipped with a harness (self-managed)'
        elif e['kind'] == 'unknown':
            key, label = ('unknown', 'unknown'), 'unidentified source'
        else:
            key, label = ('other', 'other'), 'other'
        buckets.setdefault(key, {'label': label, 'kind': key[0], 'items': []})
        buckets[key]['items'].append(e)

    out = sorted(buckets.values(), key=lambda b: (-len(b['items']), b['label']))
    for b in out:
        kinds = {}
        for i in b['items']:
            kinds[i['kind']] = kinds.get(i['kind'], 0) + 1
        b['kinds'] = kinds
    return out


# ---------- Safety scanning ----------

def scan_text_risks(text, where):
    hits = []
    lines = text.splitlines()
    for pat, label, sev in RISK_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            line = text[:m.start()].count('\n') + 1
            snippet = lines[line - 1].strip()[:140] if line - 1 < len(lines) else ''
            hits.append({'where': where, 'label': label, 'severity': sev,
                         'line': line, 'text': snippet})
    return hits


def scan_tree(path, max_files=4000):
    """Scan a directory for risk patterns and suspicious executables.

    Returns (hits, suspicious, asset_count, truncated). Ordinary assets such as
    images and fonts are counted, never reported.
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
                # Binary with an unknown extension: could be an asset or an
                # executable payload, so it gets its own bucket.
                suspicious.append(rel + ' (unknown binary)')
                continue
            hits.extend(scan_text_risks(raw.decode('utf-8', errors='ignore'), rel))
    return hits, suspicious, assets, False


def fingerprint(path):
    """Content fingerprint. Kept identical to tools/inventory.py."""
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
