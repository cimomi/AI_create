"""Render the local provenance manifest as a readable catalogue."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import HOME, ROOT  # noqa: E402

manifest = json.loads((ROOT / 'manifest.json').read_text())
lines = [
    '# Skills catalogue and provenance',
    '',
    'Consolidated from the versions already present on this machine. This does not '
    'claim that every upstream repository was refreshed during the migration. '
    'Full file fingerprints and candidate sources live in `manifest.json`.',
    '',
    'Where the same skill existed in several places, the recently maintained copy '
    'from the shared Codex / DeepSeek directories was preferred; platform-specific '
    'adaptations were kept separately.',
    '',
    'The 15 flat persona skills are byte-identical to the versions inside the nuwa '
    'bundle, so they all reference the nuwa copy. Historical extras from the old '
    'alias packages are preserved in the recovery snapshot but are no longer '
    'registered as active skills.',
    '',
]
labels = {'common': 'Common', 'codex': 'Codex-only',
          'claude': 'Claude-only', 'hermes': 'Hermes-managed reference'}
for scope, label in labels.items():
    entries = [e for e in manifest['entries'] if e['scope'] == scope]
    lines += [f'## {label} ({len(entries)})', '',
              '| Skill | Location in library | Selected source |',
              '| --- | --- | --- |']
    for entry in entries:
        destination = Path(entry['destination']).relative_to(ROOT)
        source = entry['selected']['path'].replace(str(HOME) + '/', '~/')
        lines.append(f'| {entry["name"]} | `{destination}` | `{source}` |')
    lines.append('')
lines += [
    '## Compatibility and retention boundaries',
    '',
    '- The Karpathy skill used a custom top-level `type: perspective`; it was moved '
    'into `metadata.type` for Kimi Code compatibility. The body is unchanged.',
    '- `platforms/claude/deep-research.md` is a pre-existing standalone Markdown '
    'entry point; its original link is preserved.',
    '- Some shared directories referenced by the legacy Claude academic series were '
    'already missing before the migration. The content was kept as-is; no claim is '
    'made that those end-to-end workflows were exercised.',
    '- Application-bundled skills and plugin caches stay under their own software\'s '
    'control; `managed/` only provides reference entry points.',
    '- Four skill bundles (guizang-ppt-skill, huashu-design, darwin-skill, '
    'guizang-social-card-skill) keep their own upstream git repositories so that '
    'update checks can run against them.',
    '- The Obsidian originals are retained. `~/.claude/skills/.git` was deleted on '
    '2026-10-12 (stale, inconsistent working tree); its history is no longer kept.',
    '',
]
(ROOT / 'CATALOG.md').write_text('\n'.join(lines))
print(f'Catalogue written: {len(manifest["entries"])} skills')
