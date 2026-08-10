"""Tests for the LLM-driven factor mining engine.

Covers AST safety (_scan_factor_source), protocol compliance
(_extract_meta_and_compute), AlphaMeta validation, FactorCandidate
construction, and ID uniqueness (_ensure_unique_id).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from src.crypto_autopilot.factor_miner import (
    FactorMiner,
    _extract_meta_and_compute,
    _scan_factor_source,
)
from src.crypto_autopilot.types import FactorCandidate, validate_alpha_id
from src.factors.registry import AlphaMeta


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

#: A complete, valid factor module source used across multiple tests.
VALID_FACTOR_SOURCE = '''"""Test crypto factor: momentum via close rank."""

from __future__ import annotations

import pandas as pd

from src.factors.base import rank

__alpha_meta__ = {
    "id": "crypto_mined_test",
    "nickname": "Test Momentum",
    "theme": ["momentum"],
    "formula_latex": "\\\\mathrm{rank}(\\\\mathrm{close})",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 2,
    "notes": "test factor",
}


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return cross-sectional rank of close prices."""
    return rank(panel["close"])
'''

#: A valid meta dict matching the VALID_FACTOR_SOURCE.
VALID_META_DICT: dict = {
    "id": "crypto_mined_test",
    "nickname": "Test Momentum",
    "theme": ["momentum"],
    "formula_latex": r"\mathrm{rank}(\mathrm{close})",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 3,
    "min_warmup_bars": 2,
    "notes": "test factor",
}


# ---------------------------------------------------------------------------
# 1. AST safety tests — _scan_factor_source()
# ---------------------------------------------------------------------------


class TestASTSafety:
    """Verify the AST scrubber rejects forbidden operations."""

    def test_rejects_import_subprocess(self) -> None:
        with pytest.raises(ValueError, match="subprocess"):
            _scan_factor_source("import subprocess\n")

    def test_rejects_import_socket(self) -> None:
        with pytest.raises(ValueError, match="socket"):
            _scan_factor_source("import socket\n")

    def test_rejects_eval(self) -> None:
        with pytest.raises(ValueError, match="eval"):
            _scan_factor_source("x = eval('1+1')\n")

    def test_rejects_exec(self) -> None:
        with pytest.raises(ValueError, match="exec"):
            _scan_factor_source("exec('x = 1')\n")

    def test_rejects_compile(self) -> None:
        with pytest.raises(ValueError, match="compile"):
            _scan_factor_source("c = compile('x', '<s>', 'exec')\n")

    def test_rejects_open_write_mode(self) -> None:
        with pytest.raises(ValueError, match="open"):
            _scan_factor_source('f = open("out.txt", "w")\n')

    def test_rejects_os_system(self) -> None:
        with pytest.raises(ValueError, match="os.system"):
            _scan_factor_source("import os\nos.system('echo hi')\n")

    def test_rejects_os_popen(self) -> None:
        with pytest.raises(ValueError, match="os.popen"):
            _scan_factor_source("import os\nos.popen('echo hi')\n")

    def test_accepts_valid_factor_code(self) -> None:
        """Valid imports (pandas, numpy, src.factors.base) pass the scan."""
        source = (
            "import pandas as pd\n"
            "import numpy as np\n"
            "from src.factors.base import rank\n"
            "\n"
            "def compute(panel):\n"
            "    return rank(panel['close'])\n"
        )
        _scan_factor_source(source)  # should not raise

    def test_accepts_open_read_mode_relative_path(self) -> None:
        """open() in read mode with a relative path is allowed."""
        source = 'data = open("input.txt", "r").read()\n'
        _scan_factor_source(source)  # should not raise


# ---------------------------------------------------------------------------
# 2. Protocol compliance tests — _extract_meta_and_compute()
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    """Verify meta extraction and compute source parsing."""

    def test_extracts_alpha_meta_dict(self) -> None:
        meta, _ = _extract_meta_and_compute(VALID_FACTOR_SOURCE)
        assert isinstance(meta, dict)
        assert meta["id"] == "crypto_mined_test"
        assert meta["theme"] == ["momentum"]
        assert meta["columns_required"] == ["close"]

    def test_extracts_compute_source(self) -> None:
        _, compute_src = _extract_meta_and_compute(VALID_FACTOR_SOURCE)
        assert isinstance(compute_src, str)
        assert "def compute" in compute_src

    def test_rejects_module_without_alpha_meta(self) -> None:
        source = "def compute(panel):\n    return panel['close']\n"
        with pytest.raises(ValueError, match="__alpha_meta__"):
            _extract_meta_and_compute(source)

    def test_rejects_module_without_compute(self) -> None:
        source = "__alpha_meta__ = {'id': 'crypto_mined_test'}\n"
        with pytest.raises(ValueError, match="compute"):
            _extract_meta_and_compute(source)

    def test_rejects_non_literal_alpha_meta(self) -> None:
        """__alpha_meta__ assigned from a function call is rejected."""
        source = (
            "__alpha_meta__ = dict(id='crypto_mined_test')\n"
            "def compute(panel):\n"
            "    return panel['close']\n"
        )
        with pytest.raises(ValueError, match="not a literal"):
            _extract_meta_and_compute(source)


# ---------------------------------------------------------------------------
# 3. AlphaMeta validation
# ---------------------------------------------------------------------------


class TestAlphaMetaValidation:
    """Verify AlphaMeta schema enforcement."""

    def test_valid_meta_creates_alpha_meta(self) -> None:
        meta = AlphaMeta(**VALID_META_DICT)
        assert meta.id == "crypto_mined_test"
        assert meta.theme == ["momentum"]

    def test_invalid_id_pattern_raises(self) -> None:
        bad = {**VALID_META_DICT, "id": "123_bad"}
        with pytest.raises(ValidationError):
            AlphaMeta(**bad)

    def test_missing_required_field_raises(self) -> None:
        incomplete = {k: v for k, v in VALID_META_DICT.items() if k != "formula_latex"}
        with pytest.raises(ValidationError):
            AlphaMeta(**incomplete)


# ---------------------------------------------------------------------------
# 4. FactorCandidate construction
# ---------------------------------------------------------------------------


class TestFactorCandidateConstruction:
    """Verify FactorCandidate alpha_id validation on construction."""

    def test_valid_alpha_id_creates_candidate(self) -> None:
        candidate = FactorCandidate(
            alpha_id="crypto_mined_test",
            source_code="def compute(panel): pass",
            created_at=datetime.now(timezone.utc),
        )
        assert candidate.alpha_id == "crypto_mined_test"

    def test_invalid_alpha_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="invalid alpha_id"):
            FactorCandidate(
                alpha_id="bad",  # no underscore separator
                source_code="x",
                created_at=datetime.now(timezone.utc),
            )

    def test_validate_alpha_id_helper(self) -> None:
        """The standalone validate_alpha_id function works as expected."""
        validate_alpha_id("crypto_mined_test")  # should not raise
        with pytest.raises(ValueError):
            validate_alpha_id("Bad-ID!")


# ---------------------------------------------------------------------------
# 5. ID uniqueness — _ensure_unique_id()
# ---------------------------------------------------------------------------


class TestEnsureUniqueId:
    """Verify the ID fallback hashing logic."""

    @pytest.fixture
    def miner(self) -> FactorMiner:
        return FactorMiner(llm_provider=lambda _prompt: "")

    def test_valid_crypto_mined_id_passes_through(self, miner: FactorMiner) -> None:
        result = miner._ensure_unique_id("crypto_mined_test")
        assert result == "crypto_mined_test"

    def test_invalid_id_gets_hashed_fallback(self, miner: FactorMiner) -> None:
        result = miner._ensure_unique_id("bad_id")
        assert result.startswith("crypto_mined_")
        assert result != "bad_id"


# ---------------------------------------------------------------------------
# 5b. write_factor — short-id length cap
# ---------------------------------------------------------------------------


class TestWriteFactorShortId:
    """The zoo stem must satisfy the registry's 32-char token limit."""

    @pytest.fixture
    def miner(self, tmp_path) -> FactorMiner:
        return FactorMiner(llm_provider=lambda _prompt: "", zoo_root=tmp_path)

    def test_overlong_alpha_id_truncated_to_32_chars(
        self, miner: FactorMiner, tmp_path,
    ) -> None:
        from src.factors.registry import _ID_RE

        long_id = "crypto_mined_microstructure_volume_range_alignment"
        candidate = FactorCandidate(
            alpha_id=long_id,
            source_code="def compute(panel): pass",
            created_at=datetime.now(timezone.utc),
        )
        out = miner.write_factor(candidate)
        assert _ID_RE.fullmatch(out.stem), f"stem {out.stem!r} violates token rule"
        assert len(out.stem) <= 32
        # Truncation keeps the semantic prefix, not a random suffix.
        assert out.stem.startswith("microstructure_volume_range")

    def test_valid_short_id_kept_unchanged(self, miner: FactorMiner, tmp_path) -> None:
        candidate = FactorCandidate(
            alpha_id="crypto_mined_volume_momentum",
            source_code="def compute(panel): pass",
            created_at=datetime.now(timezone.utc),
        )
        out = miner.write_factor(candidate)
        assert out.stem == "volume_momentum"


# ---------------------------------------------------------------------------
# 6. End-to-end mine_factors with a stub LLM provider
# ---------------------------------------------------------------------------


class TestMineFactorsWithStub:
    """Verify the full mining pipeline with a stubbed LLM."""

    def test_mine_factors_returns_valid_candidate(self) -> None:
        stub_response = f"```python\n{VALID_FACTOR_SOURCE}\n```"
        miner = FactorMiner(llm_provider=lambda _prompt: stub_response)

        panel = {"close": pd.DataFrame({"BTC": [1.0, 2.0, 3.0]})}
        candidates = miner.mine_factors(panel, n_candidates=1)

        assert len(candidates) == 1
        assert candidates[0].alpha_id == "crypto_mined_test"
        assert candidates[0].zoo == "crypto_mined"

    def test_mine_factors_empty_response_returns_empty(self) -> None:
        miner = FactorMiner(llm_provider=lambda _prompt: "no code here")
        panel = {"close": pd.DataFrame({"BTC": [1.0]})}
        candidates = miner.mine_factors(panel, n_candidates=1)
        assert candidates == []

    def test_mine_factors_missing_close_returns_empty(self) -> None:
        miner = FactorMiner(llm_provider=lambda _prompt: VALID_FACTOR_SOURCE)
        candidates = miner.mine_factors({}, n_candidates=1)
        assert candidates == []
