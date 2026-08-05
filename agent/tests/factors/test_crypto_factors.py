"""Unit tests for crypto-specific alpha factors (crypto zoo)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.factors.registry import Registry


def _build_crypto_panel(n_rows: int = 500) -> dict[str, pd.DataFrame]:
    """Build a synthetic crypto panel with OHLCV + funding_rate + oi + onchain:*.

    Uses seeded random data so outputs are deterministic and reproducible.
    n_rows defaults to 500 to satisfy the 365-day warmup requirement for
    MVRV Z-Score (needs 365 bars for rolling MA/std).
    """
    rng = np.random.RandomState(42)
    n_cols = 5
    codes = [f"CRYPTO{i:02d}" for i in range(n_cols)]
    dates = pd.date_range("2024-01-01", periods=n_rows, freq="D")

    # Price series: random walk from 100
    log_rets = rng.normal(0, 0.03, size=(n_rows, n_cols))
    close = pd.DataFrame(
        100 * np.exp(np.cumsum(log_rets, axis=0)), index=dates, columns=codes
    )
    high = close * (1 + rng.uniform(0.005, 0.05, size=(n_rows, n_cols)))
    low = close * (1 - rng.uniform(0.005, 0.05, size=(n_rows, n_cols)))
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.DataFrame(
        rng.uniform(1e5, 1e8, size=(n_rows, n_cols)), index=dates, columns=codes
    )

    # Funding rate: oscillates around zero (-0.1% to +0.1% typical range)
    funding_rate = pd.DataFrame(
        rng.normal(0, 0.0005, size=(n_rows, n_cols)), index=dates, columns=codes
    )

    # Open interest: random walk from 1e6
    oi_log_rets = rng.normal(0, 0.02, size=(n_rows, n_cols))
    oi = pd.DataFrame(
        1e6 * np.exp(np.cumsum(oi_log_rets, axis=0)), index=dates, columns=codes
    )

    # On-chain: MVRV (mean-reverting around 2.5, range 1-5)
    mvrv = pd.DataFrame(
        rng.normal(2.5, 1.0, size=(n_rows, n_cols)).clip(0.5, 8),
        index=dates, columns=codes,
    )

    # On-chain: exchange netflow (mean 0, range -1e6 to +1e6)
    exchange_netflow = pd.DataFrame(
        rng.normal(0, 5e5, size=(n_rows, n_cols)),
        index=dates, columns=codes,
    )

    # On-chain: active addresses (random walk from 500k)
    addr_log_rets = rng.normal(0, 0.01, size=(n_rows, n_cols))
    active_addresses = pd.DataFrame(
        500_000 * np.exp(np.cumsum(addr_log_rets, axis=0)),
        index=dates, columns=codes,
    )

    # On-chain: NVT ratio (range 30-200, median ~80)
    nvt = pd.DataFrame(
        rng.lognormal(mean=4.2, sigma=0.6, size=(n_rows, n_cols)).clip(10, 300),
        index=dates, columns=codes,
    )

    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "funding_rate": funding_rate,
        "oi": oi,
        "onchain:mvrv": mvrv,
        "onchain:exchange_netflow": exchange_netflow,
        "onchain:active_addresses": active_addresses,
        "onchain:nvt": nvt,
    }


class TestCryptoFundingRate:
    """Tests for crypto_funding_rate factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_funding_rate", panel)
        assert isinstance(result, pd.DataFrame)

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_funding_rate", panel)
        assert result.shape == panel["close"].shape

    def test_values_in_reasonable_range(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_funding_rate", panel)
        arr = result.to_numpy(dtype=np.float64)
        # Funding rate should be small (probably < 0.01 = 1%)
        assert np.nanmax(np.abs(arr)) < 0.1

    def test_registry_discovery(self) -> None:
        registry = Registry()
        alphas = registry.list(zoo="crypto")
        assert "crypto_funding_rate" in alphas
        alpha = registry.get("crypto_funding_rate")
        assert alpha.zoo == "crypto"
        assert "carry" in alpha.meta["theme"]


class TestCryptoOIChange:
    """Tests for crypto_oi_change factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_oi_change", panel)
        assert isinstance(result, pd.DataFrame)

    def test_first_row_is_nan(self) -> None:
        """First row should be NaN since OI diff needs t-1."""
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_oi_change", panel)
        assert result.iloc[0].isna().all()

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_oi_change", panel)
        assert result.shape == panel["close"].shape


class TestCryptoOIPriceDivergence:
    """Tests for crypto_oi_price_divergence factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_oi_price_divergence", panel)
        assert isinstance(result, pd.DataFrame)

    def test_output_non_negative(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_oi_price_divergence", panel)
        arr = result.to_numpy(dtype=np.float64)
        assert (np.nan_to_num(arr) >= 0).all()

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_oi_price_divergence", panel)
        assert result.shape == panel["close"].shape


class TestCryptoVolumeRatio:
    """Tests for crypto_volume_ratio factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_volume_ratio", panel)
        assert isinstance(result, pd.DataFrame)

    def test_first_6_rows_are_nan(self) -> None:
        """First 6 rows should be NaN (7-day MA needs 7 bars)."""
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_volume_ratio", panel)
        assert result.iloc[:6].isna().all().all()

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_volume_ratio", panel)
        assert result.shape == panel["close"].shape


class TestCryptoAmplitude:
    """Tests for crypto_amplitude factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_amplitude", panel)
        assert isinstance(result, pd.DataFrame)

    def test_output_non_negative(self) -> None:
        """Amplitude = (high-low)/open must be >= 0."""
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_amplitude", panel)
        arr = result.to_numpy(dtype=np.float64)
        assert (np.nan_to_num(arr) >= 0).all()

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_amplitude", panel)
        assert result.shape == panel["close"].shape


class TestCryptoRegistryIntegration:
    """Integration tests for the crypto zoo within the Registry."""

    def test_all_nine_factors_registered(self) -> None:
        registry = Registry()
        alphas = registry.list(zoo="crypto")
        expected = {
            "crypto_funding_rate",
            "crypto_oi_change",
            "crypto_oi_price_divergence",
            "crypto_volume_ratio",
            "crypto_amplitude",
            "crypto_mvrv_zscore",
            "crypto_exchange_netflow",
            "crypto_active_addresses",
            "crypto_nvt_ratio",
        }
        assert set(alphas) == expected

    def test_filter_by_carry_theme(self) -> None:
        registry = Registry()
        alphas = registry.list(theme="carry")
        assert "crypto_funding_rate" in alphas

    def test_filter_by_sentiment_theme(self) -> None:
        registry = Registry()
        alphas = registry.list(theme="sentiment")
        for aid in ("crypto_oi_change", "crypto_oi_price_divergence"):
            assert aid in alphas

    def test_filter_by_crypto_universe(self) -> None:
        registry = Registry()
        alphas = registry.list(universe="crypto")
        assert len(alphas) >= 9
        for aid in (
            "crypto_funding_rate",
            "crypto_oi_change",
            "crypto_oi_price_divergence",
            "crypto_volume_ratio",
            "crypto_amplitude",
            "crypto_mvrv_zscore",
            "crypto_exchange_netflow",
            "crypto_active_addresses",
            "crypto_nvt_ratio",
        ):
            assert aid in alphas

    def test_health_no_load_errors(self) -> None:
        registry = Registry()
        health = registry.health()
        # New crypto factors should load without errors
        crypto_errors = [
            e for e in health["errors"] if e["alpha_id"].startswith("crypto")
        ]
        assert len(crypto_errors) == 0, f"Unexpected crypto load errors: {crypto_errors}"

    def test_missing_funding_rate_skips_factor(self) -> None:
        """Factors requiring funding_rate should raise SkipAlpha when it's missing."""
        from src.factors.registry import Registry, SkipAlpha

        registry = Registry()
        panel = _build_crypto_panel()
        del panel["funding_rate"]

        with pytest.raises(SkipAlpha, match="funding_rate"):
            registry.compute("crypto_funding_rate", panel)

    def test_missing_oi_skips_factor(self) -> None:
        """Factors requiring oi should raise SkipAlpha when it's missing."""
        from src.factors.registry import Registry, SkipAlpha

        registry = Registry()
        panel = _build_crypto_panel()
        del panel["oi"]

        with pytest.raises(SkipAlpha, match="oi"):
            registry.compute("crypto_oi_change", panel)


class TestCryptoMVRVZScore:
    """Tests for crypto_mvrv_zscore on-chain factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_mvrv_zscore", panel)
        assert isinstance(result, pd.DataFrame)

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_mvrv_zscore", panel)
        assert result.shape == panel["close"].shape

    def test_first_364_rows_are_nan(self) -> None:
        """365-day rolling MA/std requires 365 bars warmup."""
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_mvrv_zscore", panel)
        assert result.iloc[:364].isna().all().all()

    def test_zscore_centered_around_zero(self) -> None:
        """After warmup, z-scores should be roughly centered around zero."""
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_mvrv_zscore", panel)
        arr = result.iloc[365:].to_numpy(dtype=np.float64)
        mean = np.nanmean(arr)
        assert abs(mean) < 1.0  # roughly zero-centered

    def test_missing_onchain_mvrv_skips(self) -> None:
        from src.factors.registry import Registry, SkipAlpha

        registry = Registry()
        panel = _build_crypto_panel()
        del panel["onchain:mvrv"]

        with pytest.raises(SkipAlpha, match="onchain:mvrv"):
            registry.compute("crypto_mvrv_zscore", panel)


class TestCryptoExchangeNetflow:
    """Tests for crypto_exchange_netflow on-chain factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_exchange_netflow", panel)
        assert isinstance(result, pd.DataFrame)

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_exchange_netflow", panel)
        assert result.shape == panel["close"].shape

    def test_raw_values_match_input(self) -> None:
        """Exchange netflow factor returns the raw onchain:exchange_netflow column."""
        registry = Registry()
        panel = _build_crypto_panel(n_rows=100)
        result = registry.compute("crypto_exchange_netflow", panel)
        expected = panel["onchain:exchange_netflow"].astype(float)
        pd.testing.assert_frame_equal(result, expected)

    def test_missing_onchain_field_skips(self) -> None:
        from src.factors.registry import Registry, SkipAlpha

        registry = Registry()
        panel = _build_crypto_panel()
        del panel["onchain:exchange_netflow"]

        with pytest.raises(SkipAlpha, match="onchain:exchange_netflow"):
            registry.compute("crypto_exchange_netflow", panel)


class TestCryptoActiveAddresses:
    """Tests for crypto_active_addresses on-chain factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_active_addresses", panel)
        assert isinstance(result, pd.DataFrame)

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_active_addresses", panel)
        assert result.shape == panel["close"].shape

    def test_first_60_rows_are_nan(self) -> None:
        """30-day MA requires 30 bars, then shifted 30 more = 60 warmup."""
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_active_addresses", panel)
        assert result.iloc[:59].isna().all().all()

    def test_values_in_reasonable_range(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_active_addresses", panel)
        arr = result.to_numpy(dtype=np.float64)
        # Active address momentum should be bounded (not extreme)
        finite = arr[np.isfinite(arr)]
        assert (np.abs(finite) < 2.0).all()

    def test_missing_onchain_field_skips(self) -> None:
        from src.factors.registry import Registry, SkipAlpha

        registry = Registry()
        panel = _build_crypto_panel()
        del panel["onchain:active_addresses"]

        with pytest.raises(SkipAlpha, match="onchain:active_addresses"):
            registry.compute("crypto_active_addresses", panel)


class TestCryptoNVTRatio:
    """Tests for crypto_nvt_ratio on-chain factor."""

    def test_compute_returns_dataframe(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_nvt_ratio", panel)
        assert isinstance(result, pd.DataFrame)

    def test_output_shape_matches_close(self) -> None:
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_nvt_ratio", panel)
        assert result.shape == panel["close"].shape

    def test_raw_values_match_input(self) -> None:
        """NVT ratio factor returns the raw onchain:nvt column."""
        registry = Registry()
        panel = _build_crypto_panel(n_rows=100)
        result = registry.compute("crypto_nvt_ratio", panel)
        expected = panel["onchain:nvt"].astype(float)
        pd.testing.assert_frame_equal(result, expected)

    def test_values_positive(self) -> None:
        """NVT should always be positive."""
        registry = Registry()
        panel = _build_crypto_panel()
        result = registry.compute("crypto_nvt_ratio", panel)
        arr = result.to_numpy(dtype=np.float64)
        finite = arr[np.isfinite(arr)]
        assert (finite > 0).all()

    def test_missing_onchain_field_skips(self) -> None:
        from src.factors.registry import Registry, SkipAlpha

        registry = Registry()
        panel = _build_crypto_panel()
        del panel["onchain:nvt"]

        with pytest.raises(SkipAlpha, match="onchain:nvt"):
            registry.compute("crypto_nvt_ratio", panel)
