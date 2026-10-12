# 更新日志

本文件记录本仓库的显著变更。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

## [0.2.0] - 2026-10-12

### 新增
- 分层安全闸门：本地 `pre-commit` / `pre-push` 钩子 + 服务端 GitHub Actions CI
- `scripts/audit.py`：仓库审计（绝对路径、凭证文件、超大文件、疑似第三方内容），
  同时覆盖当前文件与全部历史 blob
- `.gitleaks.toml` 与 gitleaks 集成，负责密钥扫描
- `scripts/install-hooks.sh`：一键启用钩子
- `SECURITY.md`、`CONTRIBUTING.md`、`CHANGELOG.md`

### 变更
- 仓库根改为作品集容器，`skill-library-manager` 下沉为顶层项目目录
- `LICENSE` 提到仓库根，全仓库共用
- 根 `README.md` 改为作品索引 + 仓库约定

## [0.1.0] - 2026-10-12

### 新增
- `skill-library-manager`：跨 10 个 AI 工具的 Skill 统一库管理工具链
  - 八类完整性检查、九项跨工具接入检查
  - 新 Skill 登记流程，失败自动回滚
  - `skill-update-guard`：Skill 溯源与安全更新
  - 工程记录 `docs/ENGINEERING.md`
