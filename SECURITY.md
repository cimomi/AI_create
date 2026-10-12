# 安全策略

## 报告问题

发现安全问题请**不要开公开 issue**。使用 GitHub 的
[私密报告](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
（仓库 → Security → Report a vulnerability），或直接联系仓库所有者。

请附上：复现步骤、影响范围、可能的修复方向。

## 本仓库自己怎么防泄漏

这个仓库是公开的，所以**推送前的安全闸门是强制的**，分三层：

| 层 | 位置 | 覆盖 | 可否绕过 |
| --- | --- | --- | --- |
| 1 | `.githooks/pre-commit` | 暂存区密钥 | 可以（`--no-verify`） |
| 2 | `.githooks/pre-push` | 全部历史密钥 + 绝对路径等 | 可以（`--no-verify`） |
| 3 | `.github/workflows/security.yml` | 同上，服务端 | **不可以** |

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
本仓库的 CI 是补充，不是替代。
