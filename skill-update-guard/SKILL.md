---
name: skill-update-guard
description: Use when 用户要检查或更新本机的 skill（"检查skill更新""更新skill""skill是不是最新""skill落后了吗""升级skill"，或 skill update / outdated / 这个skill更新安全吗）。适用于从 GitHub 等克隆或复制安装的 skill、目录带 .git 或 .huashu-skill-meta.json、以及需要判断 skill 来源是否正规、更新会不会带入风险内容的场景。花叔系复制安装的 skill 也由本 skill 统一处理。不用于改写 SKILL.md 正文（走 darwin-skill）。
---

# skill-update-guard — Skill 更新与安全检索

Skill 是从网上来的代码和指令，更新它就是一次**供应链操作**：拉进来的内容会直接进入你的上下文，还可能带可执行脚本。所以顺序永远是**先问范围 → 再只读溯源 → 审查后才写入 → 最后分类汇报**。

## 第一步：确认范围（不能跳过）

**用户没指定范围时，先问，不要自己默认全机扫描。** 用 `ask_user_question` 提问：

- 选项一（推荐）：**检索本机全部 skill**（含各框架内置）
- 选项二：只处理本库 `~/Skills`
- 选项三：只处理用户指定的目录或名字

用户回答了范围就**严格按用户的范围来**，不要擅自扩大或缩小。用户直接给了名字/目录，就不要再问，直接做。

## 第二步：只读溯源（先看不改）

```bash
python3 ~/Skills/common/skill-update-guard/scripts/trace.py            # 全机
python3 ~/Skills/common/skill-update-guard/scripts/trace.py --root DIR # 限定范围，可重复
python3 ~/Skills/common/skill-update-guard/scripts/trace.py --json     # 机器可读
```

脚本会：按真实路径去重 → **聚类**出每个 skill 装自哪里 → 核验来源是否正规 → 只读比对上游（`git ls-remote`，不 fetch、不下载、不写对象）→ 对无法溯源的逐个自行寻找线索。

聚类结果形如：`github.com/alchaincyf — 13 个`、`本库自建/无上游记录 — 90 个`、`应用内置（由该应用自身管理）— 171 个`。

**来源正规性**看这几点，任何一条命中都要在汇报里点出来：来源不是 https、主机不在可信列表、URL 内嵌账号密码、上游仓库已归档、星数异常低。

三类特殊来源**不由本工具更新**，直接归入"找不到来源"：应用内置（DeepSeek office-skills、Codex `.system`、插件缓存）、框架自带（Hermes 技能，随框架升级）、嵌套在可溯源包内的子技能（继承父级来源）。

## 第三步：安全更新

只更新**已溯源 + 有更新**的项。无法溯源的一律不动。

```bash
python3 ~/Skills/common/skill-update-guard/scripts/update.py --name X --dry-run   # 先看
python3 ~/Skills/common/skill-update-guard/scripts/update.py --name X --confirm   # 再写
python3 ~/Skills/common/skill-update-guard/scripts/update.py --all --confirm      # 批量
```

- **git 类**：fetch（只写 `.git`）→ 工作树必须干净 → 只允许 `merge --ff-only` → 对齐 manifest 指纹
- **复制类**：克隆到临时目录 → 安全扫描 → 通过后 `rsync --delete` **干净替换**（不让旧版本残留文件污染新版本）→ 回写元数据 → 对齐指纹
- 两者写入前都打包备份到 `~/Skills/.maintenance/update-backups/`

### 无污染、无病毒是怎么保证的

- **无污染**：复制类用 `--delete` 全量替换；git 类走快进，git 自己处理删除的文件。本库自己的管理文件（`.huashu-skill-meta.json`、`.last-update-check`）在替换时显式排除
- **无病毒**：写入前扫新增内容。高危模式（`curl|bash`、base64 管道执行、`rm -rf /`、读 `~/.ssh/id_rsa`、指令覆盖话术、反弹 shell）命中即**拒绝写入**；普通素材（图片、字体）只计数不报警
- 确认是误报后用 `--force` 强制，但**必须先把命中的具体文件和行给用户看**

`--dry-run` 是默认行为，永远先跑一次。

## 第四步：分类汇报（顺序固定）

**必须按这个顺序汇报，不要打乱：**

1. **已更新** —— 逐个列出：skill 名、来源、`旧版本 → 新版本`、变更了什么、备份文件名
2. **无更新** —— 列出仍是最新的项及当前版本。继承父级来源的（如 15 个人物 skill 属 nuwa-skill）合并成一行，不要铺开
3. **找不到来源** —— 按类型归组说明；对**自行寻找后找到疑似来源**的，单独列出并注明置信度，说明"未下载、未更新"

汇报里要带上：哪些来源被标记为不正规、哪些项因为没有本地版本记录而无法比较。

## 快速参考

| 情况 | 处理 |
| --- | --- |
| 用户没给范围 | **先问**，默认选项是全机 |
| 已是最新 | 记入"无更新" |
| 远端有新提交 | 走 `--dry-run` → `--confirm` |
| 工作树脏 / 已分叉 | 停止，交人工；本工具不做 merge/rebase |
| 来源不是可信主机 | 标记风险，需用户确认 |
| 离线 / 认证失败 | 标"无法确认"，**不等于最新** |
| 无 git 也无安装元数据 | 只找来源，**不下载、不更新** |
| 应用内置 / 框架自带 | 不处理，随所属应用升级 |

## 红旗 — 停下来

- 用户没给范围，你没问就自己决定了范围
- 用 `git pull` 不加 `--ff-only`，或用 `reset --hard` / `clean` 让工作树"变干净"
- 没跑 `--dry-run` 就直接 `--confirm`
- 安全扫描命中高危却直接 `--force`，没给用户看命中内容
- 对无法溯源的 skill 擅自下载或更新
- 更新完没重新对齐 manifest 指纹就宣布完成

**以上任何一条出现，都说明你在把一次可回滚的更新变成不可回滚的破坏。**

## 常见错误

| 错误 | 后果 | 正确做法 |
| --- | --- | --- |
| 把文档里引用的依赖库主页当成 skill 来源 | 给出错误的溯源结论（`matplotlib` 会指向 `matplotlib/matplotlib`） | 脚本已按置信度分级，只有高可信才当来源 |
| 更新后不重对齐 manifest 指纹 | `health.py` 报内容不匹配，误以为库坏了 | `update.py` 自动对齐 |
| 把内置/框架自带 skill 当可更新项 | 与所属应用的管理冲突 | 归入"找不到来源" |
| 未 fetch 就断言"已分叉" | 把可快进的更新误报成冲突 | 无法判断时标"未确定"，不猜 |
