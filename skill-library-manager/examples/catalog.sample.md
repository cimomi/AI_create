# Skills 目录与版本取舍

按本机现有版本归并，没有将本次迁移表述为全部上游最新版更新。完整文件指纹和候选来源见 `manifest.json`。

同名比较优先采用已核对的 Codex / DeepSeek 公共目录近期维护版本；平台专用适配分别保留。

人物类的 15 个平铺副本与女娲库内新版完全一致，统一引用女娲库内文件。旧别名包的历史附加材料在恢复快照中保留，不再作为活动 Skill 注册。

## 通用（84）

| Skill | 库内位置 | 选用来源 |
| --- | --- | --- |

> 以下为节选，完整目录由 `tools/build_catalog.py` 在本机生成。

| Skill | 库内位置 | 选用来源 |
| --- | --- | --- |
| algorithmic-art | `common/algorithmic-art` | `~/.agents/skills/algorithmic-art` |
| andrej-karpathy-perspective | `common/nuwa-skill/examples/andrej-karpathy-perspective` | `~/.codex/skills/nuwa-skill/examples/andrej-karpathy-perspective` |
| architecture-designer | `common/architecture-designer` | `~/.agents/skills/architecture-designer` |
| brainstorming | `common/brainstorming` | `~/.codex/skills/brainstorming` |
| canvas-design | `common/canvas-design` | `~/.agents/skills/canvas-design` |
| code-documenter | `common/code-documenter` | `~/.agents/skills/code-documenter` |
| code-reviewer | `common/code-reviewer` | `~/.agents/skills/code-reviewer` |
