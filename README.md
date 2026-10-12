# AI_create

我的 AI 相关作品集。**每个项目一个独立子目录**，可单独阅读与运行。

---

## 项目

### [skill-library-manager](skill-library-manager/) · Skill 库管理系统

把散落在 **10 个 AI 工具**（Codex、Claude Code、Gemini CLI、Kimi、Hermes、Pi 等）里的 Skill
收敛成**单一可信来源**：一份实体文件，各工具通过符号链接读取，所有内容有指纹登记、
有完整性校验、有可验证的安全更新通道。

| | |
| --- | --- |
| 管理规模 | 121 个 Skill、313 条入口链接、9 类接入方式 |
| 校验能力 | 8 类完整性检查 + 9 项跨工具接入检查 |
| 更新安全 | 只读溯源、来源核验、快进限定、干净替换、风险扫描、自动回滚 |
| 技术栈 | Python 3 · git · 符号链接 · 内容指纹 |

**核心特点**：不是"功能多"，而是**工具说的话可信**。项目里的
[工程记录](skill-library-manager/docs/ENGINEERING.md) 记录了九条真实踩坑——每一条都是
"工具给出了看起来合理但错误的结论"，以及如何修正。

`Python` `git` `Claude Code` `Codex` `Gemini CLI` `Kimi` `Hermes`

---

## 仓库约定

### 推送前强制安全审计

本仓库公开。**任何推送前都会自动运行安全审计**，不通过即拒绝：

```bash
scripts/install-hooks.sh    # 新克隆的机器先执行一次
```

| 钩子 | 时机 | 范围 |
| --- | --- | --- |
| `pre-commit` | 提交前 | 当前文件（快） |
| `pre-push` | **推送前** | 当前文件 + **全部历史 blob** |

审计项：绝对路径（用户名/目录结构）、各类密钥与私钥、凭证类文件、超大文件、
疑似第三方内容。

**为什么必须扫历史**：一次泄漏哪怕后来删掉了，只要进过提交，就仍然留在 git 对象里、
仍然能通过 API 取到。只检查当前工作区是不够的。

也可以随时手动运行：

```bash
python3 scripts/audit.py            # 完整审计
python3 scripts/audit.py --no-history
```

### 新增项目

1. 在仓库根新建项目目录，写自己的 `README.md`
2. 在本文件的「项目」一节登记
3. 确认项目内含绝对路径、密钥、第三方内容的文件已被自己的 `.gitignore` 排除
4. 提交时会自动触发安全审计

---

## License

MIT，见 [LICENSE](LICENSE)。各子目录若另有 LICENSE，以该目录为准。
