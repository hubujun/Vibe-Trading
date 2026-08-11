# agent/cli — 命令行界面

## 模块边界

本目录包含 Vibe-Trading 的 CLI 工具，共 41 个 Python 源文件，提供交互式命令行和自动化入口。

| 文件 | 职责 |
|------|------|
| `main.py` | CLI 主入口（命令注册、交互循环） |
| `__main__.py` | `python -m agent.cli` 入口 |
| `completer.py` | 命令补全 |
| `input.py` | 用户输入处理 |
| `intro.py` | 启动横幅 |
| `onboard.py` | 首次使用引导 |
| `stream.py` | 流式输出 |
| `_legacy.py` | 遗留命令兼容（含 `cmd_provider_doctor`） |
| `commands/` | 命令实现子目录 |

## 关键约定

- 新增命令在 `commands/` 下创建，并在 `main.py` 中注册
- 命令应有 `--help` 输出，help 文档变更需同步更新
- `_legacy.py` 中的命令仅做兼容，不应扩展新功能

## 聚焦测试命令

```bash
# CLI 语法检查
cd agent && python -m compileall -q cli

# 通用回归
pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q
```
