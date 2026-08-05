# OKX check_status Tracer-Bullet Tickets

**关联 Spec**: [2026-07-25-okx-checkstatus-field-consistency.md](../specs/2026-07-25-okx-checkstatus-field-consistency.md)
**关联 ADR**: [ADR-001](../../docs/adr/adr-001-okx-checkstatus-credential-source-error-branch.md)

---

## 依赖关系图

```
Ticket 1 (无依赖) ───→ Ticket 2 (依赖 Ticket 1)
```

---

## Ticket 1: credential_source 修正 + _status_error() helper + 配置级错误分支

**依赖**: 无
**文件**: `agent/src/trading/connectors/okx/sdk.py`, `agent/tests/test_sdk_connectors.py`

### 实现内容

#### 1.1 credential_source 值修正
- `check_status()` L195: `credential_source = "runtime_file" if cfg.api_key else None`
  - 原: `credential_source = "okx_json" if cfg.api_key else None`

#### 1.2 新增 `_status_error()` helper（参照 longbridge/sdk.py:292-301）
```python
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
```
- 插入位置: `_missing_fields()` 之前（约 L609），与其他 helper 放在一起

#### 1.3 重连三个配置级错误分支
- **invalid flag 分支**（L208-211）: `return _status_error(report, "credentials_missing", f"invalid OKX flag ...")` 
  - 移除手动 `report["status"] = "error"` / `report["error"] = ...`
- **missing fields 分支**（L214-217）: `return _status_error(report, "credentials_missing", f"OKX connector not configured: missing {', '.join(missing)}.")`
  - 移除手动赋值
- **SDK not installed 分支**（L219-222）: `return _status_error(report, "sdk_missing", "Optional dependency missing: install with `pip install python-okx`.")`
  - 移除手动赋值

#### 1.4 修改 `check_status()` 初始化
- L200: 移除初始 `connection_state` 计算，改为固定值 `"connected"`（成功路径默认值）
  - 原: `"connection_state": "connected" if configured and okx_available() else "not_configured"`
  - 新: `"connection_state": "connected"`

### 测试用例
在 `test_sdk_connectors.py` OKX 段（L454-484 之后）新增：

| 测试 | 验证 |
|------|------|
| `test_okx_check_status_unconfigured` | `configured=False`, `credential_source=None`, `connection_state="not_configured"`, `error_code="credentials_missing"`, `status="error"` |
| `test_okx_check_status_credential_source_runtime_file` | 有 `api_key` → `credential_source="runtime_file"` |
| `test_okx_check_status_credential_source_none` | 无 `api_key` → `credential_source=None` |
| `test_okx_check_status_invalid_flag` | `flag` 非法 → `connection_state="error"`, `error_code="credentials_missing"` (非 `"connected"`) |
| `test_okx_check_status_sdk_missing` | SDK 未安装 + 凭证已配 → `connection_state="error"`, `error_code="sdk_missing"` |

### 验证命令
```bash
cd /Users/laohu/Vibe-Trading && python -m pytest agent/tests/test_sdk_connectors.py -k okx -v
```

### 验收标准
- [ ] `credential_source` = `"runtime_file"` 时通过 API 白名单（`_CREDENTIAL_SOURCES`）
- [ ] 三个配置级错误分支的 `connection_state` 正确
- [ ] 所有新增测试通过

---

## Ticket 2: _connection_error_code() + 运行时错误分支

**依赖**: Ticket 1（需要 `_status_error()` 已就位）

**文件**: `agent/src/trading/connectors/okx/sdk.py`, `agent/tests/test_sdk_connectors.py`

### 实现内容

#### 2.1 新增 helper 函数
在 `_status_error()` 之后新增（参照 longbridge/sdk.py:304-318）:

```python
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

#### 2.2 重连运行时错误分支
- **account snapshot 异常分支**（L225-229）: 
  ```python
  except Exception as exc:  # noqa: BLE001
      code = _connection_error_code(exc)
      return _status_error(report, code, _connection_error_message(code))
  ```
  移除手动 `report["status"] = "error"` / `report["error"] = str(exc)` — 这会导致 secret 泄露
- **UID mismatch 分支**（L238-241）: 
  ```python
  return _status_error(report, "broker_error", f"UID mismatch: expected {cfg.expected_uid}, broker returned {uid}.")
  ```
  移除手动赋值

### 测试用例

| 测试 | 验证 | Mock 策略 |
|------|------|----------|
| `test_okx_check_status_network_error` | `error_code="network_unreachable"` | mock `get_account_snapshot` raise `ConnectionError` |
| `test_okx_check_status_auth_error` | `error_code="authentication_failed"`, `connection_state="error"` | mock raise 含 "auth" 的异常 |
| `test_okx_check_status_broker_error` | `error_code="broker_error"` | mock raise 普通 `RuntimeError` |
| `test_okx_check_status_uid_mismatch` | `error_code="broker_error"`, `connection_state="error"` | mock `_account_client` 返回不匹配 uid |
| `test_okx_check_status_error_message_redacted` | error message 不含 secret | mock raise 含 secret 的异常 → error message 应为固定消息 |

### 验证命令
```bash
cd /Users/laohu/Vibe-Trading && python -m pytest agent/tests/test_sdk_connectors.py -k okx -v
```

### 验收标准
- [ ] `_connection_error_code()` 正确分类 network/auth/broker 异常
- [ ] 错误消息中不含凭证原文（secret 不泄露）
- [ ] UID mismatch → `error_code="broker_error"`, `connection_state="error"`
- [ ] account snapshot 异常不泄露原始异常字符串
- [ ] 所有 error_code 值在 `_ERROR_CODES` 白名单内
- [ ] 所有新增测试通过
- [ ] 原有 OKX 测试不受影响
