# 贡献指南

感谢你考虑为 AlphaAgents-CN 贡献代码。

## 快速开始

```bash
git clone https://github.com/hebjt2024/AlphaAgents-CN.git
cd AlphaAgents-CN

python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest ruff

pytest tests/ -v
ruff check tradingagents/ cli/ webui/ tests/
```

## 分支与提交

```bash
git checkout -b feat/your-feature
# ... 开发 ...
ruff check . && pytest tests/ -v      # 提交前务必自检
git commit -m "feat: 简要描述你的改动"
git push origin feat/your-feature
```

提交信息使用 Conventional Commits 前缀：`feat` / `fix` / `docs` / `refactor` / `test` / `chore`。

## 同步上游

本项目基于 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)。
`upstream` remote 指向官方仓库，定期同步可以拿到最新的模型支持与 bug 修复：

```bash
git fetch upstream
git rebase upstream/main      # 或 git merge upstream/main
```

**冲突高发区域**：`tradingagents/graph/`、`tradingagents/agents/`。
A 股相关的改动请尽量集中在 `tradingagents/dataflows/` 内，可显著减少冲突。

## 代码风格

- Python 3.10+，行宽 100
- `ruff` 检查必须通过（配置见 `pyproject.toml`）
- **公开函数必须有 docstring**，说明参数、返回值、异常
- A 股特有的业务逻辑请补充中文注释；通用逻辑用英文注释即可
- 保持上游的教学式注释风格——本项目的一大特色是面向零基础学习者

## 测试要求

新增功能必须附带测试：

```bash
pytest tests/ -v --tb=short
```

测试**不应触网**。如需验证网络行为，用 `unittest.mock.patch` 替换数据源。

## 提交 Issue

请附上：

- Python 与依赖版本（`pip list`）
- 完整报错栈
- 复现步骤
- 涉及的数据源（AkShare / BaoStock / yfinance）与标的代码

## 许可

提交即表示你同意你的贡献以 **Apache License 2.0** 授权。

特别提醒：本项目**不引入任何非商业限制（Noncommercial）代码**。
如果你的改动参考了非 Apache 许可的代码，请在 PR 中说明来源与许可，
否则 CI 的许可合规检查会失败。

## 致谢

本项目站在 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
的肩膀上，感谢原作者 Yijia-Xiao 与 TauricResearch 团队。
