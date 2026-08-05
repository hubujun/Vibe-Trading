# Spec: OKX check_status 字段一致性与错误分支修复

**日期**: 2026-07-25
**状态**: Draft
**ADR**: [ADR-001](../docs/adr/adr-001-okx-checkstatus-credential-source-error-branch.md)
**问题**: LAO-11

---

## 1. 问题陈述 (Problem Statement)

OKX broker connector 的运行时页面一直显示"状态不可用"，根因是 `check_status()` 函数存在两个缺陷：

### 缺陷 A：`credential_source` 值不在 API 白名单内
- `check_status()` 返回 `credential_source = "okx_json"`
- `live_routes.py:222` 的白名单 `_CREDENTIAL_SOURCES = frozenset({"environment", "runtime_file"})` 不接受此值
- `_closed_vocabulary()` 将其过滤为 `None` → 前端渲染"凭证来源: 未知"

### 缺陷 B：错误分支中 `connection_state` / `error_code` 不一致
- `connection_state` 在报告初始化时一次性计算：`"connected" if configured and okx_available() else "not_configured"`
- 后续 5 个错误分支（invalid flag、missing fields、SDK not installed、account snapshot 异常、UID mismatch）均未更新 `connection_state` 或 `error_code`
- 导致"invalid flag 但 configured=True"等场景下 `connection_state` 误报为 `"connected"`（应为 `"error"`）

### 期望行为（由 ADR-001 决策）
1. `credential_source` → `"runtime_file"`（OKX 凭证来自 `~/.vibe-trading/okx.json`，属于运行时配置文件类别）
2. 所有错误分支统一通过 `_status_error()` helper 更新 `connection_state` 和 `error_code`，参照 Longbridge 模式

---

## 2. 验收标准 (Acceptance Criteria)

### AC-1: credential_source 正确通过 API 白名单
- **Given** OKX 凭证已配置（`api_key` 非空）
- **When** 调用 `check_status()`
- **Then** `credential_source` = `"runtime_file"`（非 `"okx_json"`，非 `None`）

### AC-2: 无凭证时 credential_source 为 None
- **Given** OKX 凭证未配置（`api_key` 为空）
- **When** 调用 `check_status()`
- **Then** `credential_source` = `None`

### AC-3: invalid flag 错误分支
- **Given** `flag` 不为 `"0"` 或 `"1"`
- **When** 调用 `check_status()`
- **Then** `connection_state` = `"error"`（非 `"connected"`），`error_code` = `"credentials_missing"`

### AC-4: missing fields 错误分支
- **Given** `api_key` / `api_secret` / `passphrase` 有任一为空
- **When** 调用 `check_status()`
- **Then** `connection_state` = `"not_configured"`，`error_code` = `"credentials_missing"`

### AC-5: SDK not installed 错误分支
- **Given** `python-okx` 未安装
- **When** 调用 `check_status()` 且凭证已配置
- **Then** `connection_state` = `"error"`，`error_code` = `"sdk_missing"`

### AC-6: account snapshot 异常错误分支
- **Given** `get_account_snapshot()` 抛出网络/认证/broker 异常
- **When** 调用 `check_status()`
- **Then** `connection_state` = `"error"`，`error_code` 为 `"network_unreachable"` / `"authentication_failed"` / `"broker_error"` 之一（由 `_connection_error_code()` 映射）

### AC-7: UID mismatch 错误分支
- **Given** `expected_uid` 已设置且与 broker 返回的 `uid` 不匹配
- **When** 调用 `check_status()`
- **Then** `connection_state` = `"error"`，`error_code` = `"broker_error"`

### AC-8: 成功路径不受影响
- **Given** OKX 凭证完整、SDK 已安装、account snapshot 成功、无 UID mismatch
- **When** 调用 `check_status()`
- **Then** `connection_state` = `"connected"`，`error_code` = `None`，`status` = `"ok"`

### AC-9: 所有 error_code 值在 API 白名单内
- `_ERROR_CODES = frozenset({"authentication_failed", "broker_error", "credentials_missing", "credentials_partial", "network_unreachable", "sdk_missing"})`
- 任何 `check_status()` 返回的 `error_code` 必须在此集合中（或为 `None`）

### AC-10: 前端渲染正确
- 端到端：通过 `GET /live/status` 获取 OKX broker 状态时
- `credential_source` 不为 `None`（有凭证时）
- `connection_state` 正确反映实际状态
- `error_code` 为非 `None` 时对应合理错误

---

## 3. 接口 / Seams

### 修改的文件
| 文件 | 变更 |
|------|------|
| `agent/src/trading/connectors/okx/sdk.py` | `check_status()` 及相关 helper |
| `agent/tests/test_sdk_connectors.py` | 新增 OKX 测试用例 |

### 新增函数
```python
# sdk.py 新增 (参照 longbridge/sdk.py:292-301)

def _status_error(report: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    """Update a status report for a known error code, fixing connection_state."""
    report.update(
        status="error",
        connection_state=(
            "not_configured" if code in ("credentials_missing", "sdk_missing")
            else "error"
        ),
        error_code=code,
        error=message,
    )
    return report


def _connection_error_code(exc: Exception) -> str:
    """Map a broker/network exception to a stable, redaction-safe error code."""
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "network_unreachable"
    text = type(exc).__name__.lower() + " " + str(exc).lower()
    if any(token in text for token in ("auth", "token", "permission", "unauthorized")):
        return "authentication_failed"
    return "broker_error"


def _connection_error_message(code: str) -> str:
    return {
        "authentication_failed": "OKX authentication failed.",
        "network_unreachable": "OKX network is unreachable.",
        "broker_error": "OKX broker request failed.",
    }[code]
```

### `check_status()` 变更点（伪代码）
```
check_status(cfg):
    configured = not _missing_fields(cfg)
    credential_source = "runtime_file" if cfg.api_key else None   # ← was "okx_json"
    report = {status="ok", configured, credential_source, connection_state="connected", error_code=None, ...}

    if flag invalid:
        return _status_error(report, "credentials_missing", "invalid flag ...")

    if missing_fields:
        return _status_error(report, "credentials_missing", "missing ...")

    if sdk not installed:
        return _status_error(report, "sdk_missing", "pip install ...")

    try:
        snapshot = get_account_snapshot(cfg)
    except Exception:
        code = _connection_error_code(exc)
        return _status_error(report, code, _connection_error_message(code))

    if UID mismatch:
        return _status_error(report, "broker_error", "UID mismatch ...")

    report["account"] = {...}
    return report
```

### 不修改的文件
- `agent/src/api/live_routes.py` — 白名单不变
- `agent/src/trading/connectors/longbridge/sdk.py` — 参考实现，不修改
- `agent/src/trading/service.py` — 中间层不受影响

---

## 4. 边界条件与异常处理

| 场景 | 预期行为 |
|------|---------|
| `flag` 为 `"2"` 或其他非法值 | `connection_state="error"`, `error_code="credentials_missing"` — invalid flag 视为配置错误 |
| `api_key` 有值但 `api_secret` 为空 | `configured=False`, `credential_source="runtime_file"`, `connection_state="not_configured"`, `error_code="credentials_missing"` |
| 所有凭证字段为空 | `configured=False`, `credential_source=None`, `connection_state="not_configured"`, `error_code="credentials_missing"` |
| SDK 未安装 + 凭证已配置 | `connection_state="error"`, `error_code="sdk_missing"` |
| `get_account_snapshot()` 抛 `ConnectionError` | `error_code="network_unreachable"` |
| `get_account_snapshot()` 抛认证异常 | `error_code="authentication_failed"` |
| `get_account_snapshot()` 抛其他异常 | `error_code="broker_error"` |
| `expected_uid` 为空 | 跳过 UID 检查，正常返回 |
| `expected_uid` 设置但 broker 返回无 uid 字段 | 不触发 UID mismatch 错误（`uid` 为 `None` → 不等于 `expected_uid` → 触发？不对，应该看代码） |

**关于 UID mismatch 的边界**：当前代码仅在 `uid is not None and str(uid) != cfg.expected_uid` 时触发错误。`uid` 为 `None`（broker 未返回 uid 字段）不会触发。此行为保持不变。

---

## 5. 明确排除范围 (Out of Scope)

- ❌ 不修改 `live_routes.py` 的 `_CREDENTIAL_SOURCES` 白名单（ADR 决策）
- ❌ 不修改 `longbridge/sdk.py` 的参考实现
- ❌ 不修改 OKX 配置文件结构或凭证加载逻辑
- ❌ 不在 `check_status()` 的 `config` 字段中添加新信息
- ❌ 不迁移其他 connector（alpaca/binance/futu 等）的错误处理模式（仅 OKX）
- ❌ 不处理 `check_status()` 的 `uid_check` 子字段错误路径的 `error_code`（`uid_check` 是 best-effort 旁路，不改变主报告状态）
- ❌ 不添加集成测试（需要真实 OKX 凭证）— 仅单元测试
