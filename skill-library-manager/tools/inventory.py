"""Read local skill bundles and record content identities without executing them."""
import hashlib
import json
import os
from pathlib import Path
import yaml

from _paths import HOME, ROOT  # noqa: E402
SOURCES = {
    'agents': HOME / '.agents/skills',
    'codex': HOME / '.codex/skills',
    'deepseek': HOME / '.dsh/skills',
    'claude': HOME / '.claude/skills',
    'cc-switch': HOME / '.cc-switch/skills',
}
SKIP = {'.git', '.DS_Store', '__pycache__', '.pytest_cache', 'node_modules', '.venv'}


def fingerprint(folder):
    records = []
    for base, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in sorted(files):
            if name in SKIP or name.endswith('.pyc'):
                continue
            path = Path(base) / name
            relative = str(path.relative_to(folder))
            if path.is_symlink():
                records.append((relative, 'link:' + os.readlink(path)))
            else:
                records.append((relative, hashlib.sha256(path.read_bytes()).hexdigest()))
        for name in dirs:
            path = Path(base) / name
            if path.is_symlink():
                records.append((str(path.relative_to(folder)), 'link:' + os.readlink(path)))
    raw = json.dumps(sorted(records), separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest(), len(records)


def inspect(source, folder):
    entry = folder / 'SKILL.md'
    text = entry.read_text()
    error = None
    try:
        meta = yaml.safe_load(text.split('---', 2)[1]) if text.startswith('---') else {}
        meta = meta if isinstance(meta, dict) else {}
    except yaml.YAMLError as exc:
        meta, error = {}, str(exc)
    supplemental = {}
    for name in ('.huashu-skill-meta.json', 'manifest.json', 'package.json', 'VERSION'):
        path = folder / name
        if path.is_file():
            try:
                supplemental[name] = json.loads(path.read_text()) if name.endswith('.json') else path.read_text().strip()
            except (ValueError, UnicodeError):
                pass
    digest, count = fingerprint(folder)
    return dict(source=source, path=str(folder), realpath=str(folder.resolve()),
                folder=folder.name, name=meta.get('name', folder.name),
                metadata=meta, supplemental=supplemental, parse_error=error,
                sha256=digest, files=count,
                skill_sha256=hashlib.sha256(entry.read_bytes()).hexdigest(),
                entry_mtime=entry.stat().st_mtime, symlink=folder.is_symlink())


if __name__ == '__main__':
    records = []
    for source, root in SOURCES.items():
        if not root.exists():
            continue
        for folder in sorted(root.iterdir()):
            if not folder.name.startswith('.') and (folder / 'SKILL.md').is_file():
                records.append(inspect(source, folder))
    output = ROOT / '.maintenance/inventory.json'
    output.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str) + '\n')
    print(json.dumps({'counts': {s: sum(r['source'] == s for r in records) for s in SOURCES},
                      'bundles': len(records), 'unique_names': len({r['name'] for r in records}),
                      'parse_errors': [r['path'] for r in records if r['parse_error']]}, indent=2))
