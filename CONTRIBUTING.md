# 贡献指南

感谢参与。以下是这个仓库的约定。

## 提交前

```bash
scripts/install-hooks.sh    # 只需执行一次
```

钩子会在提交与推送时自动运行安全审计和密钥扫描。**不要用 `--no-verify` 绕过**——
本仓库公开，一次泄漏的代价远高于一次麻烦。

## 目录约定

- 每个作品一个顶层目录，自带 `README.md`
- 新作品需要在根 `README.md` 的「项目」一节登记
- 项目自己的运行产物、密钥、绝对路径必须在**项目级 `.gitignore`** 里排除

## 代码约定

- Python 3.9+，只用标准库，避免给使用者增加依赖
- 工具必须**路径可移植**：用 `SKILLS_ROOT` / `SKILLS_HOME` 环境变量，不写死用户名
- 任何"检查类"工具都必须能给出三态结论（通过 / 不通过 / **无法判断**），
  不允许把"不知道"压成"没问题"——见 [工程记录](skill-library-manager/docs/ENGINEERING.md)

## 提交信息

用 Conventional Commits 前缀：`feat:` `fix:` `docs:` `refactor:` `harden:` `chore:`

说明**为什么改**，而不只是改了什么。
