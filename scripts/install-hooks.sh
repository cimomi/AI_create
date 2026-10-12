#!/usr/bin/env bash
# 启用仓库自带的安全钩子。core.hooksPath 是本地配置，不会随仓库分发，
# 所以在每台新克隆的机器上执行一次。
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh scripts/*.py 2>/dev/null || true
echo "已启用安全钩子：core.hooksPath = .githooks"
echo "  pre-commit  提交前快检（不扫历史）"
echo "  pre-push    推送前完整审计（含全部历史）"
