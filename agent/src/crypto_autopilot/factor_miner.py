"""LLM-driven factor mining engine for the crypto autopilot.

:class:`FactorMiner` asks DeepSeek (via the existing LangChain provider in
:mod:`src.providers.llm`) to generate factor source code that conforms to
the Alpha Zoo protocol, then runs two independent safety gates before
materialising a candidate:

1. **AST scrubber** — reuses the forbidden-operation constants and node
   checks from :mod:`backtest.runner` (network / process / exec / file
   writes are rejected).  We re-implement the walk rather than import the
   runner's ``_scan_runtime_reachable`` because that function is scoped to
   ``SignalEngine`` classes; here the entry point is the module-level
   ``compute()`` function.
2. **Protocol validator** — the parsed ``__alpha_meta__`` dict must
   construct a valid :class:`~src.factors.registry.AlphaMeta`, the
   ``compute`` symbol must be a callable accepting ``panel``, and every
   declared ``columns_required`` must be present in the supplied panel.

Candidates that fail either gate are logged and dropped; the miner never
raises on an LLM failure — it returns an empty list so the autopilot loop
keeps ticking.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import logging
import re
import time
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import ValidationError

from src.crypto_autopilot.llm_budget import LLMBudget
from src.crypto_autopilot.types import FactorCandidate
from src.factors.base import (
    decay_linear,
    delta,
    rank,
    safe_div,
    scale,
    signed_power,
    ts_argmax,
    ts_argmin,
    ts_corr,
    ts_cov,
    ts_max,
    ts_mean,
    ts_min,
    ts_rank,
    ts_std,
    zscore,
)
from src.factors.registry import AlphaMeta, RegistryError, load_alpha_meta_from_py

__all__ = ["FactorMiner"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AST safety — mirrors backtest/runner.py forbidden sets.
# ---------------------------------------------------------------------------

#: Modules whose import is always forbidden inside a mined factor.
_FORBIDDEN_IMPORT_MODULES: frozenset[str] = frozenset(
    {
        "socket",
        "socketserver",
        "subprocess",
        "urllib",
        "urllib2",
        "urllib3",
        "http",
        "requests",
        "httpx",
        "aiohttp",
        "ftplib",
        "smtplib",
        "telnetlib",
        "multiprocessing",
        "ctypes",
    }
)

#: ``os`` attributes that shell out, spawn, or read the environment.
_FORBIDDEN_OS_ATTRS: frozenset[str] = frozenset(
    {
        "system",
        "popen",
        "popen2",
        "popen3",
        "popen4",
        "fork",
        "forkpty",
        "putenv",
        "unsetenv",
        "getenv",
        "environ",
        "environb",
        "startfile",
    }
)

#: Builtins that execute code dynamically.
_FORBIDDEN_BUILTINS: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__", "globals", "locals", "vars", "breakpoint"}
)

#: ``open()`` mode characters that write/append — read-only is allowed.
_OPEN_WRITE_MODE_CHARS: frozenset[str] = frozenset("wax+")

#: ``getattr``/``setattr``/``delattr`` indirection onto os/forbidden modules.
_GETATTR_INDIRECTION: frozenset[str] = frozenset({"getattr", "setattr", "delattr"})

#: Operators exposed to the LLM — injected into the prompt so it uses these
#: rather than reinventing rolling statistics.
_AVAILABLE_OPERATORS: tuple[str, ...] = (
    "rank", "zscore", "scale",
    "ts_rank", "ts_corr", "ts_cov", "ts_mean", "ts_std",
    "ts_max", "ts_min", "ts_argmax", "ts_argmin",
    "delta", "decay_linear", "signed_power", "safe_div",
)

#: Theme tags the LLM may assign (mirrors AlphaMeta.theme Literal).
_THEMES: tuple[str, ...] = (
    "momentum", "reversal", "volume", "volatility", "quality", "value",
    "liquidity", "microstructure", "sentiment", "growth", "leverage", "carry",
)

#: Price columns the panel is allowed to declare.
_PRICE_COLS: frozenset[str] = frozenset(
    {"open", "high", "low", "close", "volume", "vwap", "amount", "funding_rate", "oi"}
)

#: id pattern from AlphaMeta (^[a-z][a-z0-9]+_[a-z0-9_]+$).
_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9]+_[a-z0-9_]+$")


# ---------------------------------------------------------------------------
# AST scrubber — re-implements backtest/runner.py checks for module-level code.
# ---------------------------------------------------------------------------


def _attribute_root_name(node: ast.Attribute) -> str | None:
    """Return the leftmost ``Name`` id of an attribute chain (``a.b.c`` → ``a``)."""
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _is_forbidden_os_attr(attr: str) -> bool:
    """Return whether ``os.<attr>`` shells out, spawns, execs, or reads env."""
    return attr in _FORBIDDEN_OS_ATTRS or attr.startswith(("spawn", "exec"))


def _reject_forbidden_open(node: ast.Call) -> None:
    """Reject ``open()`` used to write files or read a non-relative-literal path."""
    func = node.func
    is_builtin_open = isinstance(func, ast.Name) and func.id == "open"
    is_io_os_open = (
        isinstance(func, ast.Attribute)
        and func.attr == "open"
        and isinstance(func.value, ast.Name)
        and func.value.id in {"io", "os"}
    )
    if not (is_builtin_open or is_io_os_open):
        return

    mode_node: ast.AST | None = node.args[1] if len(node.args) >= 2 else None
    for kw in node.keywords:
        if kw.arg == "mode":
            mode_node = kw.value
    if mode_node is not None:
        if not (isinstance(mode_node, ast.Constant) and isinstance(mode_node.value, str)):
            raise ValueError("open() with a non-literal mode is not allowed")
        if any(ch in _OPEN_WRITE_MODE_CHARS for ch in mode_node.value):
            raise ValueError(
                f"Writing files via open(mode={mode_node.value!r}) is not allowed"
            )

    path_node = node.args[0] if node.args else None
    if not (isinstance(path_node, ast.Constant) and isinstance(path_node.value, str)):
        raise ValueError("open() with a non-literal path is not allowed")
    path = path_node.value
    if path.startswith(("/", "~", "\\")) or ".." in path or (len(path) > 1 and path[1] == ":"):
        raise ValueError(f"open() with a non-relative path {path!r} is not allowed")


def _reject_forbidden_getattr(node: ast.Call) -> None:
    """Reject getattr/setattr/delattr indirection onto os/forbidden modules."""
    func = node.func
    if not (isinstance(func, ast.Name) and func.id in _GETATTR_INDIRECTION):
        return
    if not node.args:
        return
    target = node.args[0]
    if isinstance(target, ast.Name):
        root: str | None = target.id
    elif isinstance(target, ast.Attribute):
        root = _attribute_root_name(target)
    else:
        root = None
    if root == "os" or root in _FORBIDDEN_IMPORT_MODULES:
        raise ValueError(f"{func.id}() indirection onto {root!r} is not allowed")


def _reject_forbidden_node(node: ast.AST) -> None:
    """Raise ``ValueError`` if a single AST node performs a forbidden operation."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] in _FORBIDDEN_IMPORT_MODULES:
                raise ValueError(f"Import of {alias.name!r} is not allowed")
    elif isinstance(node, ast.ImportFrom):
        root = (node.module or "").split(".")[0]
        if root in _FORBIDDEN_IMPORT_MODULES:
            raise ValueError(f"Import from {node.module!r} is not allowed")
        if root == "os":
            for alias in node.names:
                if _is_forbidden_os_attr(alias.name):
                    raise ValueError(f"Import of os.{alias.name} is not allowed")
    elif isinstance(node, ast.Attribute):
        root = _attribute_root_name(node)
        if root in _FORBIDDEN_IMPORT_MODULES:
            raise ValueError(f"Use of {root}.{node.attr} is not allowed")
        if root == "os" and _is_forbidden_os_attr(node.attr):
            raise ValueError(f"Use of os.{node.attr} is not allowed")
    elif isinstance(node, ast.Name):
        if node.id in _FORBIDDEN_BUILTINS:
            raise ValueError(f"Use of {node.id!r} is not allowed")
    elif isinstance(node, ast.Call):
        _reject_forbidden_open(node)
        _reject_forbidden_getattr(node)


def _scan_factor_source(source: str) -> None:
    """Walk the whole module tree and reject any forbidden operation.

    Unlike ``backtest.runner._scan_runtime_reachable`` (which scopes to
    ``SignalEngine`` methods), a mined factor module is tiny and the whole
    file is reachable via ``compute(panel)``, so we walk every node.

    Args:
        source: Python source text of the mined factor module.

    Raises:
        ValueError: If any forbidden node is found.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"mined factor has invalid Python syntax: {exc}") from exc
    for node in ast.walk(tree):
        _reject_forbidden_node(node)


# ---------------------------------------------------------------------------
# Module parsing — extract __alpha_meta__ + compute source.
# ---------------------------------------------------------------------------


def _extract_meta_and_compute(source: str) -> tuple[dict[str, Any], str]:
    """Parse the LLM output into a ``(meta_dict, compute_source)`` tuple.

    The LLM is asked to return a complete module; we AST-scan it to find
    the ``__alpha_meta__`` assignment and the ``compute`` function, then
    return the meta dict and the ``compute`` function's source span.

    Args:
        source: The full module source returned by the LLM.

    Returns:
        Tuple of (meta dict, compute function source text).

    Raises:
        ValueError: If ``__alpha_meta__`` or ``compute`` is missing or
            malformed.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"LLM output is not valid Python: {exc}") from exc

    meta_dict: dict[str, Any] | None = None
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        targets = [t for t in stmt.targets if isinstance(t, ast.Name)]
        if any(t.id == "__alpha_meta__" for t in targets):
            try:
                raw = ast.literal_eval(stmt.value)
            except (ValueError, SyntaxError) as exc:
                raise ValueError(f"__alpha_meta__ is not a literal: {exc}") from exc
            if not isinstance(raw, dict):
                raise ValueError("__alpha_meta__ must be a dict literal")
            meta_dict = raw
            break

    if meta_dict is None:
        raise ValueError("__alpha_meta__ assignment not found in LLM output")

    compute_node: ast.FunctionDef | None = None
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "compute":
            compute_node = stmt
            break

    if compute_node is None:
        raise ValueError("compute() function not found in LLM output")

    # Re-emit the compute function source from the AST segment.
    compute_source = ast.get_source_segment(source, compute_node)
    if compute_source is None:
        # Fallback: reconstruct from the function body lines.
        compute_source = "\n".join(source.splitlines()[compute_node.lineno - 1:compute_node.end_lineno])
    return meta_dict, compute_source


# ---------------------------------------------------------------------------
# Prompt construction.
# ---------------------------------------------------------------------------

#: Example crypto factor source injected into the prompt as a template.
_EXAMPLE_FACTOR_SOURCE = '''"""crypto SENTIMENT: daily open interest change rate."""

from __future__ import annotations

import pandas as pd

from src.factors.base import safe_div

__alpha_meta__ = {
    "id": "crypto_oi_change",
    "nickname": "OI变动率",
    "theme": ["sentiment"],
    "formula_latex": "\\\\frac{\\\\mathrm{OI}_t - \\\\mathrm{OI}_{t-1}}{\\\\mathrm{OI}_{t-1}}",
    "columns_required": ["oi"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 2,
    "notes": "Open interest day-over-day change.",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the day-over-day OI change percentage, aligned to close index."""
    oi = panel["oi"].astype(float)
    close = panel["close"]
    aligned = oi.reindex(index=close.index, method="ffill")
    oi_change = aligned.diff()
    return safe_div(oi_change, aligned.shift(1) + 1e-12)
'''


def _build_prompt(
    panel: dict[str, pd.DataFrame],
    n_candidates: int,
    theme_hints: list[str] | None,
) -> str:
    """Construct the DeepSeek prompt for factor generation.

    Args:
        panel: The factor panel the generated code will run against.
        n_candidates: Number of distinct factors to generate.
        theme_hints: Optional theme tags to bias generation.

    Returns:
        The prompt string.
    """
    panel_cols = sorted(panel.keys())
    close_shape = panel["close"].shape if "close" in panel else (0, 0)
    close_cols = (
        list(panel["close"].columns)[:10] if "close" in panel else []
    )

    themes_str = ", ".join(theme_hints) if theme_hints else "any"
    operators_str = ", ".join(_AVAILABLE_OPERATORS)
    themes_allowed = ", ".join(_THEMES)

    return f"""You are a quantitative researcher generating crypto trading factors.

Generate exactly {n_candidates} distinct, novel crypto trading factors. Each factor
must be a complete, self-contained Python module conforming to the Alpha Zoo protocol.

## Strict requirements

1. Each factor module MUST define:
   - `__alpha_meta__`: a dict literal with keys: id, nickname, theme,
     formula_latex, columns_required, universe, frequency, decay_horizon,
     min_warmup_bars, notes. Optional: extras_required, requires_sector.
   - `compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame`: the factor
     calculation function.

2. `id` MUST match `^[a-z][a-z0-9]+_[a-z0-9_]+$` and start with `crypto_mined_`.
   Example: `crypto_mined_momentum_volume_accel`.

3. `theme` MUST be a non-empty list from: [{themes_allowed}].
   Use the theme hints: [{themes_str}].

4. `columns_required`: each entry must be one of the price columns
   ({', '.join(sorted(_PRICE_COLS))}) OR prefixed with `fund:` or `onchain:`.

5. `universe` MUST include `"crypto"`.

6. `decay_horizon` MUST be an int in [0, 512].
   `min_warmup_bars` MUST be a non-negative int.

7. `compute()` MUST:
   - Accept a single `panel: dict[str, pd.DataFrame]` argument.
   - Return a `pd.DataFrame` with the SAME shape as `panel["close"]`.
   - Use ONLY these operators from `src.factors.base` (do NOT reinvent
     rolling stats): {operators_str}
   - NOT call any network, subprocess, file I/O, eval, exec, or __import__.
   - NOT import anything except `pandas`, `numpy`, and `src.factors.base`.
   - Propagate NaN (no silent fillna(0)); never produce +/- inf.

## Available operators (import from src.factors.base)

- rank(df): cross-sectional percentile rank per row.
- zscore(df): cross-sectional z-score per row.
- scale(df, a=1.0): per-row L1 normalise.
- ts_rank(df, n): rolling rank per column, warmup → NaN.
- ts_corr(x, y, n): rolling Pearson correlation per column.
- ts_cov(x, y, n): rolling covariance per column.
- ts_mean(df, n): rolling mean per column.
- ts_std(df, n): rolling std (ddof=1) per column.
- ts_max(df, n) / ts_min(df, n): rolling max / min per column.
- ts_argmax(df, n) / ts_argmin(df, n): rolling argmax / argmin.
- delta(df, d): first difference at lag d (d >= 1, lookahead ban).
- decay_linear(df, n): linear decay-weighted moving average.
- signed_power(df, p): sign(df) * |df|**p.
- safe_div(a, b, eps=1e-12): safe division, b==0 → NaN.

## Panel context (the factors will run against this panel)

- Available panel columns: {panel_cols}
- close shape: {close_shape} (rows × instruments)
- Sample instrument columns: {close_cols}

## Example factor module (follow this structure exactly)

```python
{_EXAMPLE_FACTOR_SOURCE}
```

## Output format

Return each factor as a fenced ```python code block. Separate multiple
factors with a blank line between blocks. Do NOT include any commentary
outside the code blocks. Each block must be a complete, importable module.
"""


# ---------------------------------------------------------------------------
# Main class.
# ---------------------------------------------------------------------------


class FactorMiner:
    """Generate, validate, and persist crypto factor candidates via DeepSeek.

    The miner is intentionally resilient: any LLM failure (timeout, 429,
    unparseable output, AST/protocol violation) is logged and the offending
    candidate is dropped — :meth:`mine_factors` never raises on LLM errors.
    """

    def __init__(
        self,
        llm_provider: Any = None,
        model_name: str = "deepseek-chat",
        zoo_root: Path | None = None,
        budget: LLMBudget | None = None,
    ) -> None:
        """Initialise the miner.

        Args:
            llm_provider: A callable that takes a prompt string and returns
                the LLM text response.  When ``None``, the miner lazily
                builds one via :func:`src.providers.llm.build_llm` on first
                use.  Tests can inject a stub here.
            model_name: DeepSeek model name.  Default ``"deepseek-chat"``.
            zoo_root: Root directory for the mined-factor zoo.  Defaults to
                ``<agent>/src/factors/zoo/crypto_mined/``.
            budget: Optional pre-built :class:`LLMBudget`.  A fresh one is
                created when ``None``.
        """
        self._llm_provider = llm_provider
        self.model_name = model_name
        self._zoo_root = (
            zoo_root
            if zoo_root is not None
            else Path(__file__).resolve().parent.parent / "factors" / "zoo" / "crypto_mined"
        )
        self._budget = budget if budget is not None else LLMBudget()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def mine_factors(
        self,
        panel: dict[str, pd.DataFrame],
        n_candidates: int = 3,
        theme_hints: list[str] | None = None,
    ) -> list[FactorCandidate]:
        """Generate N factor candidates via DeepSeek and validate each.

        Args:
            panel: Factor panel keyed by column name.  Must contain
                ``"close"``.
            n_candidates: Number of candidates to request from the LLM.
            theme_hints: Optional theme tags to bias generation.

        Returns:
            List of validated :class:`FactorCandidate` objects.  May be
            shorter than ``n_candidates`` (or empty) if the LLM fails or
            candidates fail validation.
        """
        if "close" not in panel:
            logger.warning("FactorMiner: panel missing 'close'; skipping mining")
            return []

        if self._budget.should_degrade():
            logger.info("FactorMiner: budget degraded; skipping mining")
            return []

        prompt = _build_prompt(panel, n_candidates, theme_hints)
        estimated_tokens = len(prompt) // 3 + 500 * n_candidates
        if not self._budget.check_budget(estimated_tokens):
            logger.info("FactorMiner: budget insufficient for mining run")
            return []

        try:
            raw_response = self._call_llm(prompt)
        except Exception as exc:  # noqa: BLE001 — LLM must not crash the loop
            logger.warning("FactorMiner: LLM call failed: %s", exc)
            self._budget.record_failure()
            return []

        candidates: list[FactorCandidate] = []
        code_blocks = _extract_code_blocks(raw_response)
        if not code_blocks:
            logger.warning("FactorMiner: no code blocks in LLM response")
            self._budget.record_failure()
            return []

        for block in code_blocks:
            candidate = self._validate_block(block, panel)
            if candidate is not None:
                candidates.append(candidate)

        logger.info(
            "FactorMiner: mined %d/%d candidates (requested %d, blocks %d)",
            len(candidates),
            n_candidates,
            n_candidates,
            len(code_blocks),
        )
        return candidates

    def write_factor(self, candidate: FactorCandidate) -> Path:
        """Persist a candidate to ``zoo/crypto_mined/<short_id>.py``.

        The file name stem is the part of ``alpha_id`` after the
        ``crypto_mined_`` prefix (or the full id if no such prefix).

        Args:
            candidate: The validated factor candidate.

        Returns:
            Path to the written file.
        """
        self._zoo_root.mkdir(parents=True, exist_ok=True)
        short_id = candidate.alpha_id
        if short_id.startswith("crypto_mined_"):
            short_id = short_id[len("crypto_mined_"):]
        # Ensure the stem is a valid zoo id token (lowercase, digits, _).
        if not re.fullmatch(r"[a-z][a-z0-9_]*", short_id):
            short_id = candidate.alpha_id.replace("crypto_mined_", "cm_")
            short_id = re.sub(r"[^a-z0-9_]", "_", short_id)
        # The registry only accepts stems up to 32 chars
        # (``^[a-z][a-z0-9_]{0,31}$``) — truncate without leaving a stray `_`.
        short_id = short_id[:32].rstrip("_")

        out_path = self._zoo_root / f"{short_id}.py"
        source = _assemble_module_source(candidate)
        out_path.write_text(source, encoding="utf-8")
        logger.info("FactorMiner: wrote %s → %s", candidate.alpha_id, out_path)
        return out_path

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        """Invoke the LLM provider with backoff and budget tracking.

        Args:
            prompt: The prompt string.

        Returns:
            The raw LLM text response.

        Raises:
            Exception: After all retries are exhausted.
        """
        provider = self._get_provider()
        last_exc: Exception | None = None
        for attempt in range(self._budget.max_retries + 1):
            try:
                response = provider(prompt)
                usage = _extract_usage(response)
                if usage is not None:
                    self._budget.record_usage(*usage)
                else:
                    self._budget.record_usage(len(prompt) // 4, max(len(str(response)) // 4, 100))
                self._budget.record_usage  # noqa: B018 — touch for clarity
                return _extract_text(response)
            except Exception as exc:  # noqa: BLE001 — retry once
                last_exc = exc
                if attempt < self._budget.max_retries:
                    delay = self._budget.get_backoff_delay(attempt)
                    logger.info(
                        "FactorMiner: LLM attempt %d failed (%s); retrying in %.1fs",
                        attempt,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def _get_provider(self) -> Any:
        """Lazily build the LLM provider via :func:`build_llm`.

        Returns:
            A callable ``prompt -> response``.
        """
        if self._llm_provider is not None:
            return self._llm_provider

        from src.providers.llm import build_llm  # local import; heavy

        chat = build_llm(model_name=self.model_name)

        def _invoke(prompt: str) -> Any:
            # LangChain chat models accept a string and return an AIMessage.
            return chat.invoke(prompt)

        self._llm_provider = _invoke
        return self._llm_provider

    def _validate_block(
        self,
        source: str,
        panel: dict[str, pd.DataFrame],
    ) -> FactorCandidate | None:
        """Run AST + protocol validation on a single code block.

        Args:
            source: The mined factor module source.
            panel: The panel the factor will run against.

        Returns:
            A :class:`FactorCandidate` if all gates pass, else ``None``.
        """
        # Gate 1: AST safety scan.
        try:
            _scan_factor_source(source)
        except ValueError as exc:
            logger.warning("FactorMiner: AST safety reject — %s", exc)
            return None

        # Gate 2: extract __alpha_meta__ + compute source.
        try:
            meta_dict, compute_source = _extract_meta_and_compute(source)
        except ValueError as exc:
            logger.warning("FactorMiner: parse reject — %s", exc)
            return None

        # Gate 3: AlphaMeta schema validation.
        try:
            meta = AlphaMeta(**meta_dict)
        except ValidationError as exc:
            logger.warning("FactorMiner: AlphaMeta reject — %s", exc)
            return None

        # Gate 4: columns_required must be a subset of the panel.
        panel_cols = set(panel.keys())
        missing = [c for c in meta.columns_required if c not in panel_cols]
        if missing:
            logger.warning(
                "FactorMiner: columns_required %s not in panel %s",
                missing,
                sorted(panel_cols),
            )
            return None

        # Gate 5: compute is callable and accepts (panel,).
        if not _validate_compute_signature(source):
            logger.warning("FactorMiner: compute() signature invalid")
            return None

        # Generate a unique alpha_id if the LLM-supplied one collides.
        alpha_id = self._ensure_unique_id(meta.id)

        # Re-serialise the meta dict so the written file is self-consistent.
        final_meta = {**meta_dict, "id": alpha_id}

        candidate = FactorCandidate(
            alpha_id=alpha_id,
            source_code=compute_source,
            created_at=datetime.now(timezone.utc),
            zoo="crypto_mined",
            meta={
                "alpha_meta": final_meta,
                "full_module_source": source,
                "model": self.model_name,
            },
        )
        return candidate

    def _ensure_unique_id(self, base_id: str) -> str:
        """Return *base_id* if it matches the id pattern, else a hashed fallback.

        The LLM occasionally produces ids that violate the
        ``^[a-z][a-z0-9]+_[a-z0-9_]+$`` rule (e.g. starting with a digit or
        lacking the underscore separator).  In that case we substitute a
        deterministic hash-suffixed id rooted in ``crypto_mined``.

        Args:
            base_id: The LLM-proposed id.

        Returns:
            A zoo-valid id.
        """
        if _ID_PATTERN.fullmatch(base_id) and base_id.startswith("crypto_mined"):
            return base_id
        # Build a deterministic fallback from the base id hash.
        digest = hashlib.sha256(base_id.encode("utf-8")).hexdigest()[:8]
        fallback = f"crypto_mined_h{digest}"
        # Sanity: the hash fallback always matches (h + hex).
        if not _ID_PATTERN.fullmatch(fallback):
            fallback = f"crypto_mined_{uuid.uuid4().hex[:8]}"
        return fallback


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


_CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def _extract_code_blocks(text: str) -> list[str]:
    """Extract fenced ```python code blocks from the LLM response.

    Args:
        text: Raw LLM text.

    Returns:
        List of code block contents (stripped).
    """
    return [m.strip() for m in _CODE_BLOCK_RE.findall(text) if m.strip()]


def _extract_text(response: Any) -> str:
    """Normalise an LLM response into a plain string."""
    if isinstance(response, str):
        return response
    # LangChain AIMessage — content is the text.
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Some providers return a list of content blocks.
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ]
        return "".join(parts)
    return str(response) if response is not None else ""


def _extract_usage(response: Any) -> tuple[int, int] | None:
    """Extract ``(prompt_tokens, completion_tokens)`` from an LLM response.

    Returns:
        Tuple of token counts, or ``None`` if usage metadata is absent.
    """
    # LangChain v0.2+ stashes usage under response_metadata.
    metadata = getattr(response, "response_metadata", None)
    if isinstance(metadata, dict):
        usage = metadata.get("token_usage") or metadata.get("usage")
        if isinstance(usage, dict):
            pt = int(usage.get("prompt_tokens", 0) or 0)
            ct = int(usage.get("completion_tokens", 0) or 0)
            return pt, ct
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict):
        pt = int(usage.get("input_tokens", 0) or 0)
        ct = int(usage.get("output_tokens", 0) or 0)
        return pt, ct
    return None


def _validate_compute_signature(source: str) -> bool:
    """Check that the module defines a ``compute(panel)`` function.

    This is a lightweight AST check — it does not execute the code.  The
    full safety scan already ran; here we only confirm the function exists
    and accepts at least one positional argument.

    Args:
        source: Module source.

    Returns:
        ``True`` if ``compute`` is a function def with ≥1 arg.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef) and stmt.name == "compute":
            args = stmt.args
            n_pos = len(args.args)
            # Must accept at least `panel` (could be pos-only or regular).
            n_pos_only = len(args.posonlyargs)
            total_pos = n_pos + n_pos_only
            return total_pos >= 1
    return False


def _assemble_module_source(candidate: FactorCandidate) -> str:
    """Reassemble the full module source for a candidate.

    The candidate's ``meta`` dict stores the LLM-generated full module
    source.  If absent (e.g. test-constructed candidate), we synthesise a
    minimal module from the ``__alpha_meta__`` dict + compute source.

    Args:
        candidate: The factor candidate.

    Returns:
        Complete, importable Python module source.
    """
    full = candidate.meta.get("full_module_source")
    if isinstance(full, str) and full.strip():
        return full

    meta_dict = candidate.meta.get("alpha_meta", {})
    # Pretty-print the meta dict as a Python literal.
    meta_repr = _dict_to_python_literal(meta_dict)
    compute_src = candidate.source_code
    header = '''"""crypto_mined factor (auto-generated by FactorMiner)."""

from __future__ import annotations

'''
    return f"{header}__alpha_meta__ = {meta_repr}\n\n\n{compute_src}\n"


def _dict_to_python_literal(d: dict[str, Any], indent: int = 0) -> str:
    """Render a dict as a Python literal (for source emission).

    Args:
        d: The dict to render.
        indent: Current indentation level.

    Returns:
        A string representation that ``ast.literal_eval`` can parse.
    """
    pad = "    " * indent
    inner_pad = "    " * (indent + 1)
    if not d:
        return "{}"
    lines = ["{"]
    for key, value in d.items():
        lines.append(f"{inner_pad}{key!r}: {_value_to_python_literal(value, indent + 1)},")
    lines.append(f"{pad}}}")
    return "\n".join(lines)


def _value_to_python_literal(value: Any, indent: int) -> str:
    """Render a value as a Python literal."""
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        inner = ", ".join(_value_to_python_literal(v, indent) for v in value)
        return f"[{inner}]"
    if isinstance(value, dict):
        return _dict_to_python_literal(value, indent)
    if value is None:
        return "None"
    return repr(value)
