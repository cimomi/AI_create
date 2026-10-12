#!/usr/bin/env bash
# 从本机 Skill 库同步工具链到本仓库（只导出白名单文件，并做脱敏校验）。
#
# 用法：
#   ./sync-from-library.sh            # 同步并显示变更
#   ./sync-from-library.sh --commit   # 同步后自动提交
#
# 默认库路径 ~/Skills，可用 SKILLS_ROOT 覆盖。
#
# 安全设计：先复制到临时暂存区、校验通过后才落地。
# 反过来先落地再校验，一旦发现泄漏，文件已经进仓库了。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="${SKILLS_ROOT:-$HOME/Skills}"

[ -d "$LIB" ] || { echo "库目录不存在：$LIB" >&2; exit 1; }

# 白名单：只有这些文件会被导出。绝不整目录复制——
# 库里有第三方 Skill、恢复快照、更新备份，都不能进仓库。
TOOL_FILES=(
  _paths.py inventory.py health.py check_integrations.py
  register_skill.py build_catalog.py
)
SKILL_FILES=(SKILL.md scripts/sources.py scripts/trace.py scripts/update.py)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

echo "从 $LIB 同步 → $REPO"

mkdir -p "$STAGE/tools" "$STAGE/skill-update-guard/scripts"
for f in "${TOOL_FILES[@]}"; do
  cp "$LIB/.maintenance/$f" "$STAGE/tools/$f"
done
for f in "${SKILL_FILES[@]}"; do
  cp "$LIB/common/skill-update-guard/$f" "$STAGE/skill-update-guard/$f"
done

# ---- 脱敏校验（在暂存区做，未通过绝不落地）----
echo "脱敏校验…"
FAIL=0
if grep -rIn -- "$HOME" "$STAGE" >/dev/null 2>&1; then
  echo "  ✗ 含本机主目录绝对路径："
  grep -rIn -- "$HOME" "$STAGE" | sed "s|^$STAGE/|     |" | head -5
  FAIL=1
fi

# 其他绝对路径只警告：文档里的示例路径（/home/me）是合理的
WARN="$(grep -rIn -E '/Users/[A-Za-z0-9_.-]+|/home/[A-Za-z0-9_.-]+' "$STAGE" 2>/dev/null || true)"
if [ -n "$WARN" ]; then
  echo "  ⚠️  其他绝对路径（请确认是文档示例而非真实路径）："
  echo "$WARN" | sed "s|^$STAGE/|     |" | head -10
fi

if [ "$FAIL" -ne 0 ]; then
  echo "同步已中止，仓库内容未改动。" >&2
  exit 1
fi
echo "  ✓ 通过"

# ---- 校验通过，才落地 ----
cp "$STAGE"/tools/*.py "$REPO/tools/"
cp "$STAGE"/skill-update-guard/SKILL.md "$REPO/skill-update-guard/SKILL.md"
cp "$STAGE"/skill-update-guard/scripts/*.py "$REPO/skill-update-guard/scripts/"

echo
echo "=== 变更 ==="
git -C "$REPO" status --short || true

if [ "${1:-}" = "--commit" ]; then
  if [ -z "$(git -C "$REPO" status --porcelain)" ]; then
    echo "没有变更，无需提交。"
    exit 0
  fi
  git -C "$REPO" add -A
  git -C "$REPO" commit -m "sync: 从本机库同步工具链 $(date '+%Y-%m-%d %H:%M')"
  echo "已提交。推送：git -C \"$REPO\" push"
fi
