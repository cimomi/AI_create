#!/usr/bin/env bash
# Detect drift between the local working library and the English translations
# published in this repository.
#
# The local library under ~/Skills is maintained in Chinese; the copies in this
# repository are English translations of it. They are therefore NOT synced by
# copying — a copy would overwrite the translations with Chinese.
#
# Instead this records the hash of each Chinese source at translation time and
# reports when a source has changed since, so the translation can be refreshed.
#
# Usage:
#   ./scripts/check-translation-drift.sh            # report drift
#   ./scripts/check-translation-drift.sh --accept   # accept current sources as the new baseline
#
# Exit code: 0 = no drift (or baseline just accepted); 1 = drift detected.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="${SKILLS_ROOT:-$HOME/Skills}"
BASELINE="$ROOT/.translation-baseline.json"

command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }

python3 - "$LIB" "$BASELINE" "$ROOT" "${1:-}" <<'PY'
import hashlib, json, os, sys
from pathlib import Path

lib, baseline_path, repo, action = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]

# Logical repository path  ->  source path inside the local library
MAPPING = {
    'tools/_paths.py':                                   '.maintenance/_paths.py',
    'tools/inventory.py':                                '.maintenance/inventory.py',
    'tools/health.py':                                   '.maintenance/health.py',
    'tools/check_integrations.py':                       '.maintenance/check_integrations.py',
    'tools/register_skill.py':                           '.maintenance/register_skill.py',
    'tools/build_catalog.py':                            '.maintenance/build_catalog.py',
    'skill-update-guard/SKILL.md':                       'common/skill-update-guard/SKILL.md',
    'skill-update-guard/scripts/sources.py':             'common/skill-update-guard/scripts/sources.py',
    'skill-update-guard/scripts/trace.py':               'common/skill-update-guard/scripts/trace.py',
    'skill-update-guard/scripts/update.py':              'common/skill-update-guard/scripts/update.py',
}

def sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()

current = {}
missing = []
for logical, rel in MAPPING.items():
    src = Path(lib) / rel
    if src.is_file():
        current[logical] = sha(src)
    else:
        missing.append(rel)

previous = {}
if baseline_path.is_file():
    try:
        previous = json.loads(baseline_path.read_text(encoding='utf-8')).get('sources', {})
    except Exception:
        previous = {}

if action == '--accept':
    baseline_path.write_text(json.dumps(
        {'note': 'sha256 of the local source file at the time its English translation was made',
         'sources': current}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Baseline updated: {len(current)} source file(s) recorded.')
    if missing:
        print('Missing sources (skipped): ' + ', '.join(missing))
    raise SystemExit(0)

drift = [k for k in current if k in previous and previous[k] != current[k]]
new = [k for k in current if k not in previous]

print(f'Local library: {lib}')
if not previous:
    print('No baseline recorded yet. Run with --accept once the translations are current.')
    raise SystemExit(1)

if not drift and not new:
    print(f'No drift: all {len(current)} translated file(s) match their source.')
    raise SystemExit(0)

if drift:
    print(f'\nDRIFT: {len(drift)} local source file(s) changed since translation:')
    for k in drift:
        print(f'  - {k}')
    print('  The published English copy may now be stale; re-translate these.')
if new:
    print(f'\nNEW: {len(new)} file(s) not yet in the baseline:')
    for k in new:
        print(f'  - {k}')
if missing:
    print('\nMissing locally (cannot verify): ' + ', '.join(missing))
print('\nAfter refreshing the translations, run: ./scripts/check-translation-drift.sh --accept')
raise SystemExit(1)
PY
