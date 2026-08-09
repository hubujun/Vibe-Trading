"""Token budget management and degradation for LLM calls.

Tracks daily token usage and enforces a hard budget limit.  When the
budget is exhausted (or consecutive failures accumulate), callers should
degrade gracefully — e.g. fall back to cached factors or skip the mining
phase until the next UTC day.

Design notes
------------
The budget resets at **UTC midnight** (not local time) to stay aligned with
provider billing cycles.  The reset is lazy: it fires on the next
``check_budget`` / ``record_usage`` call after the date rolls over, not via
a background timer.

Backoff follows ``2^attempt`` seconds capped at 60 s so a transient 429 from
DeepSeek does not burn the whole daily budget in retries.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

__all__ = ["LLMBudget"]

logger = logging.getLogger(__name__)

#: Absolute cap on a single backoff delay (seconds).
_MAX_BACKOFF_S: float = 60.0

#: Consecutive-failure count that triggers degradation even when under budget.
_CONSECUTIVE_FAILURE_THRESHOLD: int = 3


class LLMBudget:
    """Daily token budget tracker with exponential-backoff degradation.

    Attributes:
        daily_token_limit: Maximum total tokens (prompt + completion) per
            UTC day.  Default 500 000.
        max_retries: Maximum LLM retry attempts before giving up on a single
            call.  Default 2.
        timeout_s: Per-call timeout in seconds.  Default 120.
    """

    def __init__(
        self,
        daily_token_limit: int = 500_000,
        max_retries: int = 2,
        timeout_s: float = 120.0,
    ) -> None:
        """Initialise the budget tracker.

        Args:
            daily_token_limit: Max tokens per UTC day.
            max_retries: Max retries per LLM call.
            timeout_s: Per-call timeout (seconds).
        """
        self.daily_token_limit = daily_token_limit
        self.max_retries = max_retries
        self.timeout_s = timeout_s

        self._used_tokens: int = 0
        self._consecutive_failures: int = 0
        self._current_day: datetime = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_budget(self, estimated_tokens: int) -> bool:
        """Return ``True`` if *estimated_tokens* fits within the remaining budget.

        Triggers a daily reset if the UTC date has rolled over.

        Args:
            estimated_tokens: Expected token cost of the next call.

        Returns:
            ``True`` when the call is within budget, ``False`` otherwise.
        """
        self.daily_reset_if_needed()
        return (self._used_tokens + estimated_tokens) <= self.daily_token_limit

    def record_usage(self, prompt_tokens: int, completion_tokens: int) -> None:
        """Record actual token consumption and reset the failure counter.

        A successful call resets consecutive failures to zero so the
        degradation flag clears.

        Args:
            prompt_tokens: Tokens consumed by the prompt.
            completion_tokens: Tokens consumed by the completion.
        """
        self.daily_reset_if_needed()
        total = prompt_tokens + completion_tokens
        self._used_tokens += total
        self._consecutive_failures = 0
        logger.debug(
            "LLM usage recorded: prompt=%d completion=%d daily_total=%d/%d",
            prompt_tokens,
            completion_tokens,
            self._used_tokens,
            self.daily_token_limit,
        )

    def record_failure(self) -> None:
        """Increment the consecutive-failure counter.

        Call this when an LLM invocation raises (timeout, 429, network
        error, etc.) so that :meth:`should_degrade` can trip after a
        sustained failure streak.
        """
        self._consecutive_failures += 1
        logger.warning(
            "LLM failure recorded (consecutive=%d)", self._consecutive_failures
        )

    def get_backoff_delay(self, attempt: int) -> float:
        """Exponential backoff delay for retry *attempt* (0-indexed).

        ``delay = min(2^attempt, 60)`` seconds.

        Args:
            attempt: Zero-based retry attempt index.

        Returns:
            Delay in seconds before the next retry.
        """
        return min(2.0 ** max(attempt, 0), _MAX_BACKOFF_S)

    def should_degrade(self) -> bool:
        """Return ``True`` when the miner should stop calling the LLM.

        Degradation triggers:
        - Daily token budget is exhausted.
        - Consecutive failures exceed the threshold.

        Returns:
            ``True`` if the caller should skip the LLM and degrade.
        """
        self.daily_reset_if_needed()
        if self._used_tokens >= self.daily_token_limit:
            logger.info(
                "LLM budget exhausted (%d/%d tokens); degrading",
                self._used_tokens,
                self.daily_token_limit,
            )
            return True
        if self._consecutive_failures >= _CONSECUTIVE_FAILURE_THRESHOLD:
            logger.info(
                "LLM consecutive failures (%d >= %d); degrading",
                self._consecutive_failures,
                _CONSECUTIVE_FAILURE_THRESHOLD,
            )
            return True
        return False

    def daily_reset_if_needed(self) -> None:
        """Reset counters if the UTC date has rolled over.

        Idempotent — safe to call on every budget check.
        """
        today = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        if today > self._current_day:
            logger.info(
                "LLM budget daily reset: %d tokens used on %s",
                self._used_tokens,
                self._current_day.strftime("%Y-%m-%d"),
            )
            self._used_tokens = 0
            self._consecutive_failures = 0
            self._current_day = today

    @property
    def used_tokens(self) -> int:
        """Tokens consumed so far today."""
        return self._used_tokens

    @property
    def remaining_tokens(self) -> int:
        """Tokens remaining in the daily budget (never negative)."""
        return max(0, self.daily_token_limit - self._used_tokens)
