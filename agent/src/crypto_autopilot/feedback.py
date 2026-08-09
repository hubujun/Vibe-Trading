"""Feedback analysis for the crypto autopilot — LLM-driven reflection loop.

:class:`FeedbackAnalyzer` uses DeepSeek (via :func:`src.providers.llm.build_llm`)
to analyze successful vs. failed factor patterns, extract theme insights,
and produce mining hints for the next mining cycle. This closes the
self-improving loop: mine → evaluate → trade → **reflect** → mine better.

The LLM call follows the same pattern as :class:`FactorMiner`:
- Wrapped in try/except — never crashes on LLM failure.
- Uses :class:`LLMBudget` for token management.
- Degrades gracefully when the budget is exhausted.

Performance records are persisted to a local JSON file (simple approach)
so feedback accumulates across restarts without a database dependency.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.crypto_autopilot.llm_budget import LLMBudget

logger = logging.getLogger(__name__)

__all__ = ["FeedbackAnalyzer"]

#: Default file name for performance records.
_PERFORMANCE_FILE = "feedback_performance.json"

#: Maximum number of performance records to retain (FIFO).
_MAX_RECORDS = 500

#: Maximum characters in the analysis prompt.
_MAX_PROMPT_CHARS = 8000


def _default_feedback_dir() -> Path:
    """Return the default directory for feedback artifacts.

    Returns:
        ``<agent>/runs/autopilot/``
    """
    return Path(__file__).resolve().parents[2] / "runs" / "autopilot"


class FeedbackAnalyzer:
    """LLM-driven feedback analyzer for the crypto autopilot reflection loop.

    Analyzes factor performance data to extract patterns, theme insights,
    and mining hints that guide the next mining cycle. The analyzer is
    intentionally resilient: any LLM failure is logged and the caller
    receives an empty/default result rather than an exception.

    Attributes:
        config: Autopilot tuning knobs.
        budget: Token budget tracker shared with the miner.
    """

    def __init__(
        self,
        config: AutopilotConfig | None = None,
        llm_provider: Any = None,
    ) -> None:
        """Initialize the feedback analyzer.

        Args:
            config: Autopilot config; loaded from env when ``None``.
            llm_provider: Optional injected LLM callable
                (``prompt -> response``).  When ``None``, one is lazily
                built via :func:`src.providers.llm.build_llm` on first use.
        """
        self.config: AutopilotConfig = config or load_autopilot_config()
        self._llm_provider = llm_provider
        self._budget: LLMBudget = LLMBudget()
        self._feedback_dir: Path = _default_feedback_dir()
        self._feedback_dir.mkdir(parents=True, exist_ok=True)
        self._performance_path: Path = self._feedback_dir / _PERFORMANCE_FILE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(self, factor_results: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze factor performance using DeepSeek and return structured insights.

        Sends a prompt with factor metadata (alpha_id, lifecycle, metrics)
        to the LLM and parses the response into a structured dict. On any
        LLM failure, returns a default empty analysis.

        Args:
            factor_results: List of dicts, each with keys:
                - ``alpha_id``: Factor identifier.
                - ``lifecycle``: Current lifecycle stage string.
                - ``metrics``: Performance metrics dict (Sharpe, drawdown, etc.).

        Returns:
            Dict with keys:

            - ``successful_themes``: List of theme strings from successful factors.
            - ``failed_patterns``: List of failure pattern descriptions.
            - ``prompt_adjustments``: List of suggested prompt modifications.
            - ``parameters_to_tune``: List of parameter names to adjust.
            - ``analysis_text``: Raw LLM response text (for debugging).
            - ``ts``: ISO-8601 UTC timestamp.
        """
        if not factor_results:
            return self._empty_analysis()

        if self._budget.should_degrade():
            logger.info("FeedbackAnalyzer: budget degraded; skipping analysis")
            return self._empty_analysis()

        prompt = self._build_analysis_prompt(factor_results)
        estimated_tokens = len(prompt) // 3 + 500

        if not self._budget.check_budget(estimated_tokens):
            logger.info("FeedbackAnalyzer: budget insufficient; skipping")
            return self._empty_analysis()

        try:
            raw_response = self._call_llm(prompt)
        except Exception as exc:  # noqa: BLE001 — LLM must not crash the loop
            logger.warning("FeedbackAnalyzer: LLM call failed: %s", exc)
            self._budget.record_failure()
            return self._empty_analysis()

        # Record the performance data locally.
        for result in factor_results:
            alpha_id = result.get("alpha_id", "")
            metrics = result.get("metrics", {})
            if alpha_id:
                self.record_performance(alpha_id, metrics)

        # Parse the response into structured insights.
        return self._parse_analysis(raw_response)

    def record_performance(self, alpha_id: str, metrics: dict[str, Any]) -> None:
        """Record factor performance to a local JSON file.

        Appends a timestamped record. When the file exceeds
        :data:`_MAX_RECORDS`, the oldest entries are trimmed (FIFO).

        Args:
            alpha_id: Factor identifier.
            metrics: Performance metrics dict.
        """
        records = self._load_records()
        records.append({
            "alpha_id": alpha_id,
            "metrics": metrics,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        # Trim to max records.
        if len(records) > _MAX_RECORDS:
            records = records[-_MAX_RECORDS:]

        try:
            self._performance_path.write_text(
                json.dumps(records, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 — persistence is best-effort
            logger.warning(
                "FeedbackAnalyzer: failed to write performance records: %s", exc,
            )

    def get_mining_hints(self) -> list[str]:
        """Return theme hints for the next mining cycle based on accumulated feedback.

        Reads the local performance records and extracts themes from
        successful factors (those with positive Sharpe or passing lifecycle).
        Falls back to the last LLM analysis's ``prompt_adjustments`` if
        available.

        Returns:
            List of theme hint strings (may be empty).
        """
        records = self._load_records()
        if not records:
            return []

        hints: list[str] = []
        successful_count = 0
        failed_count = 0

        for record in records[-50:]:  # Last 50 records.
            metrics = record.get("metrics", {})
            sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe", 0)
            lifecycle = record.get("lifecycle", "")

            try:
                sharpe_val = float(sharpe)
            except (TypeError, ValueError):
                sharpe_val = 0.0

            if sharpe_val > 0 or lifecycle in ("paper_validated", "live_deployed"):
                successful_count += 1
                # Extract themes from metrics if available.
                themes = metrics.get("themes") or metrics.get("theme", [])
                if isinstance(themes, str):
                    themes = [themes]
                if isinstance(themes, list):
                    hints.extend(themes)
            else:
                failed_count += 1

        # Deduplicate hints.
        seen: set[str] = set()
        unique_hints: list[str] = []
        for hint in hints:
            h = str(hint).strip().lower()
            if h and h not in seen:
                seen.add(h)
                unique_hints.append(h)

        # Add a meta-hint based on success/failure ratio.
        if successful_count > 0:
            unique_hints.append("momentum")  # Bias toward momentum when things work.
        if failed_count > successful_count * 2:
            unique_hints.append("volume")  # Try volume-based when many fail.

        logger.info(
            "FeedbackAnalyzer: %d hints from %d successful / %d failed",
            len(unique_hints), successful_count, failed_count,
        )
        return unique_hints[:10]  # Cap at 10 hints.

    # ------------------------------------------------------------------
    # Internal: prompt construction
    # ------------------------------------------------------------------

    def _build_analysis_prompt(self, factor_results: list[dict[str, Any]]) -> str:
        """Construct the DeepSeek prompt for factor performance analysis.

        Args:
            factor_results: List of factor result dicts.

        Returns:
            The prompt string, capped at :data:`_MAX_PROMPT_CHARS`.
        """
        # Build a compact summary of each factor.
        summaries: list[str] = []
        for result in factor_results:
            alpha_id = result.get("alpha_id", "unknown")
            lifecycle = result.get("lifecycle", "unknown")
            metrics = result.get("metrics", {})

            sharpe = metrics.get("sharpe_ratio") or metrics.get("sharpe", "N/A")
            max_dd = metrics.get("max_drawdown") or metrics.get("drawdown", "N/A")
            total_return = metrics.get("total_return") or metrics.get("return", "N/A")
            n_trades = metrics.get("n_trades") or metrics.get("num_trades", "N/A")

            summaries.append(
                f"- {alpha_id}: lifecycle={lifecycle}, sharpe={sharpe}, "
                f"max_dd={max_dd}, return={total_return}, trades={n_trades}"
            )

        summary_text = "\n".join(summaries[:50])  # Cap at 50 factors.

        prompt = f"""You are a quantitative research analyst reviewing crypto trading factor performance.

## Factor Performance Summary

{summary_text}

## Analysis Request

Analyze the above factor performance data and provide:

1. **Successful Themes**: Which factor themes/strategies performed well? (e.g., momentum, reversal, volume)
2. **Failed Patterns**: What common patterns led to poor performance?
3. **Prompt Adjustments**: How should the factor mining prompt be adjusted to generate better factors?
4. **Parameters to Tune**: Which backtest/trading parameters should be adjusted?

## Output Format

Respond in JSON format with these exact keys:
```json
{{
  "successful_themes": ["theme1", "theme2"],
  "failed_patterns": ["pattern description 1", "pattern description 2"],
  "prompt_adjustments": ["adjustment 1", "adjustment 2"],
  "parameters_to_tune": ["param1", "param2"]
}}
```

Be concise. Focus on actionable insights for the next mining cycle.
"""
        # Cap prompt length.
        if len(prompt) > _MAX_PROMPT_CHARS:
            prompt = prompt[:_MAX_PROMPT_CHARS]

        return prompt

    # ------------------------------------------------------------------
    # Internal: LLM invocation
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str) -> str:
        """Invoke the LLM provider with budget tracking and backoff.

        Follows the same pattern as :meth:`FactorMiner._call_llm`:
        retries with exponential backoff, records token usage, and raises
        after all retries are exhausted.

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
                # Record usage.
                usage = self._extract_usage(response)
                if usage is not None:
                    self._budget.record_usage(*usage)
                else:
                    self._budget.record_usage(
                        len(prompt) // 4,
                        max(len(str(response)) // 4, 100),
                    )
                return self._extract_text(response)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < self._budget.max_retries:
                    delay = self._budget.get_backoff_delay(attempt)
                    logger.info(
                        "FeedbackAnalyzer: LLM attempt %d failed (%s); "
                        "retrying in %.1fs",
                        attempt, exc, delay,
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

        from src.providers.llm import build_llm

        chat = build_llm(model_name=self.config.deepseek_model)

        def _invoke(prompt: str) -> Any:
            return chat.invoke(prompt)

        self._llm_provider = _invoke
        return self._llm_provider

    # ------------------------------------------------------------------
    # Internal: response parsing
    # ------------------------------------------------------------------

    def _parse_analysis(self, raw_response: str) -> dict[str, Any]:
        """Parse the LLM response into a structured analysis dict.

        Attempts to extract JSON from the response. Falls back to
        returning the raw text when parsing fails.

        Args:
            raw_response: The raw LLM text response.

        Returns:
            Structured analysis dict.
        """
        result = self._empty_analysis()
        result["analysis_text"] = raw_response

        # Try to extract JSON from the response.
        try:
            # Look for a JSON block in the response.
            json_text = self._extract_json_block(raw_response)
            if json_text:
                parsed = json.loads(json_text)
                if isinstance(parsed, dict):
                    result["successful_themes"] = parsed.get(
                        "successful_themes", [],
                    )
                    result["failed_patterns"] = parsed.get(
                        "failed_patterns", [],
                    )
                    result["prompt_adjustments"] = parsed.get(
                        "prompt_adjustments", [],
                    )
                    result["parameters_to_tune"] = parsed.get(
                        "parameters_to_tune", [],
                    )
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.debug(
                "FeedbackAnalyzer: JSON parse failed: %s", exc,
            )

        return result

    @staticmethod
    def _extract_json_block(text: str) -> str | None:
        """Extract a JSON code block from the LLM response.

        Looks for ```json ... ``` or { ... } patterns.

        Args:
            text: Raw LLM response text.

        Returns:
            The JSON string, or ``None`` if not found.
        """
        import re

        # Try fenced code block first.
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Try to find a JSON object literal.
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            return match.group(0)

        return None

    @staticmethod
    def _extract_text(response: Any) -> str:
        """Normalize an LLM response into a plain string."""
        if isinstance(response, str):
            return response
        content = getattr(response, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            ]
            return "".join(parts)
        return str(response) if response is not None else ""

    @staticmethod
    def _extract_usage(response: Any) -> tuple[int, int] | None:
        """Extract (prompt_tokens, completion_tokens) from an LLM response."""
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

    # ------------------------------------------------------------------
    # Internal: persistence
    # ------------------------------------------------------------------

    def _load_records(self) -> list[dict[str, Any]]:
        """Load performance records from the local JSON file.

        Returns:
            List of record dicts (empty on error or missing file).
        """
        if not self._performance_path.is_file():
            return []
        try:
            raw = self._performance_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug(
                "FeedbackAnalyzer: failed to load performance records: %s", exc,
            )
        return []

    @staticmethod
    def _empty_analysis() -> dict[str, Any]:
        """Return an empty/default analysis dict."""
        return {
            "successful_themes": [],
            "failed_patterns": [],
            "prompt_adjustments": [],
            "parameters_to_tune": [],
            "analysis_text": "",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
