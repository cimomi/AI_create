# Skill Library Manager

**一套把散落在 10 个 AI 工具里的 Skill 收敛成单一可信来源的管理系统。**

本机 121 个 Skill 只维护一份实体文件，各工具通过符号链接读取；所有内容有指纹登记、有完整性校验、有安全更新通道。仓库里是**管理这套库的工具链**，不含任何第三方 Skill 内容。

> A single-source-of-truth manager for AI agent skills spread across 10 tools: one canonical copy, symlinked into every harness, fingerprinted, verified, and safely updatable.

---

## 为什么需要它

同一个 Skill 被装进 Codex、Claude Code、Gemini CLI、Kimi、Hermes……每个工具抄一份。很快就会出现：

- 同一 Skill 五个副本，版本各不相同，改了一处其余四处还是旧的
- 不知道哪个副本是正本，删哪个都心虚
- 更新时不知道会不会把某个工具的本地改动覆盖掉
- 谁从哪装的、能不能更新、更新安不安全，全无记录

这套系统把"**单一实体 + 链接分发 + 指纹登记 + 可验证**"作为唯一结构，并用工具链把维护动作固化下来。

## 结构

```
~/Skills/
├── common/            通用 Skill（全工具共享，单一实体）
├── platforms/         平台专用 Skill（claude / codex，不污染公共入口）
├── managed/           对应用内置目录的引用，不复制内容
├── manifest.json      121 条记录：来源、候选版本、文件指纹、平台分配、入口链接
├── CATALOG.md         人可读的清单（由 manifest 生成）
└── .maintenance/      工具链 + 验证记录 + 恢复快照
```

各工具的 skills 目录**全部是符号链接**，指回 `~/Skills`。改一处，10 个工具同时生效。

| 工具 | 接入方式 |
| --- | --- |
| Codex / ChatGPT 本地 Codex 引擎 | 公共入口 + Codex 专用入口 |
| DeepSeek Harness | 公共入口 |
| Claude Code | 公共入口 + Claude 专用链接 |
| VS Code Claude Code 扩展 | 复用同一 Claude 用户库 |
| Gemini CLI | 公共入口 |
| Kimi Code / Kimi 桌面版 | 公共入口 + 原生本地插件 |
| Pi | 公共入口 |
| Hermes | 原生 `skills.external_dirs` |
| CC Switch | 目录改为链接，同步方式 `symlink` |

## 工具链

### 完整性：`tools/health.py`

八类只读检查，退出码即结论，可直接接 CI 或 cron。

```bash
python3 tools/health.py           # 人读报告
python3 tools/health.py --quiet   # 仅结论行
python3 tools/health.py --json    # 机器可读
```

| 代号 | 检查 |
| --- | --- |
| A | destination 存在、有 SKILL.md、文件指纹与 manifest 一致 |
| B | 每条入口链接都是指向库内的有效符号链接 |
| C | 各工具入口目录的实际条目与 manifest 完全对应 |
| D | frontmatter 的 `name`/`description` 合法、无重名 |
| E | 平台专用 Skill 未泄漏到公共入口 |
| F | 入口目录无 `.git` / `.DS_Store` / `__pycache__` |
| G | 库内目录与 manifest 双向对应，无漏编 |
| H | 无同哈希的重复 Skill |

### 接入：`tools/check_integrations.py`

`health.py` 只看库内是否自洽，这个脚本看外部工具是否仍正确指向本库：四个入口根目录、cc-switch 同步方式、Hermes 外部目录清单、Kimi 插件注册表、`kimi.plugin.json` 共享路径。

### 登记：`tools/register_skill.py`

新增 Skill 不再是手工改 manifest。脚本先校验目录与 frontmatter，再写 manifest、建入口链接、同步 Hermes 清单、重建 CATALOG，最后自动跑两项检查——任一步失败全部回滚。

```bash
python3 tools/register_skill.py --name <skill> --dry-run
python3 tools/register_skill.py --name <skill>
```

### 溯源与安全更新：`skill-update-guard/`

```bash
python3 skill-update-guard/scripts/trace.py                      # 只读溯源
python3 skill-update-guard/scripts/update.py --name X --dry-run  # 演练
python3 skill-update-guard/scripts/update.py --name X --confirm  # 写入
```

`trace.py` 全机发现 → 按真实路径去重 → 按来源聚类 → 核验来源 → 只读比对上游。无法溯源的会自行寻找线索但**只找不装**。

`update.py` 只更新已溯源且有更新的项：git 类只允许 `merge --ff-only`；复制类克隆到临时目录扫描后 `rsync --delete` 干净替换。两者写入前都备份，写入后自动重新对齐 manifest 指纹。

## 安全模型

Skill 是从网上来的代码和指令，更新它是一次**供应链操作**。四条不可协商的原则：

1. **只读检索不碰本地仓库** —— `git ls-remote` 不 fetch、不写 object
2. **本地改动一律不动** —— 工作树脏就停下报告，绝不 `reset --hard` / `checkout` / `clean`
3. **只允许快进** —— 分叉交给人，不做 merge、不做 rebase
4. **更新后必须重对齐指纹** —— 否则体检会报内容不匹配，看起来像库坏了

写入前扫描高危模式（`curl|bash`、base64 管道执行、`rm -rf /`、读 `~/.ssh/id_rsa`、指令覆盖话术、反弹 shell），命中即拒绝。

## 工程记录

真正花时间的不是写出功能，是让工具**不说假话**。见 [`docs/ENGINEERING.md`](docs/ENGINEERING.md)，其中包括：

- 溯源时未 fetch 就断言"已分叉"，把可快进的更新误报成冲突
- 文档里引用的依赖库主页被当成 Skill 来源（`matplotlib` → `matplotlib/matplotlib`）
- `\bexec\(` 命中 JavaScript 的正则方法 `.exec(font)`
- NUL 字节检测把 png/woff 素材当成可疑二进制
- `rsync` 时把临时克隆的 `.git` 一并复制进 Skill 目录，让复制安装类被误判成 git 类
- YAML 库回写 `~/.hermes/config.yaml` 会抹掉全部 32 行注释

## 快速开始

需要 Python 3.9+；登记与接入检查额外需要 PyYAML。

```bash
python3 tools/health.py
```

路径默认为 `~/Skills`，可用环境变量覆盖：

```bash
SKILLS_ROOT=/path/to/Skills python3 tools/health.py
```

## 说明

本仓库只包含管理系统的代码与文档。被管理的 Skill 各自属于其原作者，持有各自的许可证，**不随本仓库分发**——`examples/` 下只有脱敏节选，用于展示数据结构。

## License

MIT，见 [LICENSE](LICENSE)。
