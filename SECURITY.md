# 安全策略

## 报告问题

发现安全问题请**不要开公开 issue**。使用 GitHub 的
[私密报告](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
（仓库 → Security → Report a vulnerability），或直接联系仓库所有者。

请附上：复现步骤、影响范围、可能的修复方向。

## 本仓库自己怎么防泄漏

这个仓库是公开的，所以**推送前的安全闸门是强制的**，分三层：

| 层 | 位置 | 时机 | 覆盖 | 能否阻止泄漏 |
| --- | --- | --- | --- | --- |
| 1 | `.githooks/pre-commit` | 提交**前** | 暂存区密钥 | 能，但可 `--no-verify` 绕过 |
| 2 | `.githooks/pre-push` | 推送**前** | 全部历史密钥 + 绝对路径等 | 能，但可 `--no-verify` 绕过 |
| 3 | `.github/workflows/security.yml` | 推送**后** | 同上，服务端 | **不能**——它在推送之后运行，只能发现与告警 |
| 4 | GitHub Push Protection | 推送**时** | 平台已知凭证格式 | **能，且不可绕过** |

**必须说清楚的一点**：第 3 层的 CI 虽然无法被 `--no-verify` 跳过，但
**GitHub Actions 在推送完成之后才运行，所以它拦不住泄漏本身**。它能做的是
发现、告警，以及（配合分支保护）阻止问题代码被合并进 `main`。

真正能在推送**当时**拒绝的是第 4 层。

关键点：**必须扫描全部历史**。一次泄漏哪怕后来删掉了，只要进过提交，
它就仍然留在 git 对象里、仍然能通过 API 取到。只检查当前工作区是不够的。

```bash
scripts/install-hooks.sh    # 新克隆的机器执行一次
```

## 建议同时开启 GitHub 原生防护

仓库管理员可在 **Settings → Code security** 开启：

- **Secret scanning** —— 自动扫描已推送内容中的已知凭证格式
- **Push protection** —— 在服务端**直接拒绝**含凭证的推送

这两项对公开仓库免费，且位于平台层，比任何客户端手段都更早生效。
**本仓库的本地钩子与 CI 都是补充，不是替代**——只有 Push Protection 能在泄漏发生前拦住它。
