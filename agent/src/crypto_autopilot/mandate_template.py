"""Pre-built mandate YAML generator for the crypto autopilot loop.

Produces a mandate dict (and its YAML serialization) that is structurally
compatible with :func:`src.live.mandate.store.load_mandate` — i.e. the
nested ``hard_caps`` / ``universe`` / ``consent`` shape the frozen
:class:`~src.live.mandate.model.Mandate` dataclass expects.

The autopilot mandate is the **strictest** profile in the pipeline: small
capital (200 USDT aggregate), tiny per-order notional (50 USDT), no leverage
(``max_leverage == 1.0`` → cash-only spot), and flatten-on-halt enabled so a
kill-switch trip immediately unwinds positions. This mirrors the
:mod:`~src.crypto_autopilot.paper_engine` sizing but pins the OKX *live*
profile (``flag="0"``) instead of the demo flag.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from src.crypto_autopilot.config import AutopilotConfig, load_autopilot_config
from src.live.mandate.model import MANDATE_SCHEMA_VERSION

logger = logging.getLogger(__name__)

__all__ = ["MandateTemplate"]

#: Broker key used by the autopilot live channel — matches the OKX connector.
_BROKER_KEY = "okx"

#: Opaque account reference stamped into the mandate's consent provenance.
_ACCOUNT_REF = "crypto-autopilot"

#: Mandate lifetime in days. A live mandate must not live forever (SPEC §9
#: decision 2); 30 days keeps the autopilot bounded and forces periodic
#: re-authorization via the consent UX.
_MANDATE_LIFETIME_DAYS = 30

#: Default file name for a generated autopilot mandate.
_MANDATE_FILENAME = "autopilot_mandate.yaml"


class MandateTemplate:
    """Pre-built mandate generator tuned for the crypto autopilot live profile.

    All methods are static — the template is a pure factory with no mutable
    state, mirroring the immutable mandate model it produces dicts for.

    The generated dict uses the exact field names expected by
    :func:`src.live.mandate.store._parse_mandate` so it round-trips through
    ``load_mandate`` after being written to a JSON file. When serialized to
    YAML (via :meth:`to_yaml`) it serves as a human-readable contract the
    operator can inspect before committing to the protected store.
    """

    @staticmethod
    def autopilot_mandate(config: AutopilotConfig | None = None) -> dict[str, Any]:
        """Build the strictest autopilot mandate dict.

        Maps :class:`AutopilotConfig` knobs onto the nested mandate-model
        structure (``hard_caps`` / ``universe`` / ``consent``). Every field
        name and type matches :class:`~src.live.mandate.model.Mandate` so the
        dict is parseable by :func:`src.live.mandate.store.load_mandate`.

        Extra autopilot metadata (``pairs``, ``profile``) is included at the
        top level; the mandate parser ignores unknown keys, so these do not
        break structural compatibility.

        Args:
            config: Autopilot config; loaded from env when ``None``.

        Returns:
            A mandate dict with:

            - ``schema_version``: :data:`MANDATE_SCHEMA_VERSION`
            - ``hard_caps``: small-capital, no-leverage ceilings
            - ``universe``: crypto-only, no structural floors
            - ``consent``: 30-day expiry, autopilot provenance
            - ``flatten_on_halt``: ``True``
            - ``pairs`` / ``profile``: autopilot reference metadata
        """
        cfg = config or load_autopilot_config()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=_MANDATE_LIFETIME_DAYS)

        # Deterministic consent token — binds this mandate to the autopilot
        # config at generation time (not a real consent-artifact hash, but a
        # stable fingerprint so the provenance field is non-empty and
        # attributable).
        token_seed = (
            f"{cfg.live_profile}:{cfg.max_total_exposure_usd}:"
            f"{cfg.max_order_notional_usd}:{','.join(cfg.pairs)}"
        )
        consent_token = hashlib.sha256(token_seed.encode()).hexdigest()

        return {
            "schema_version": MANDATE_SCHEMA_VERSION,
            "hard_caps": {
                "account_funding_usd": float(cfg.max_total_exposure_usd),
                "max_order_notional_usd": float(cfg.max_order_notional_usd),
                "max_total_exposure_usd": float(cfg.max_total_exposure_usd),
                # Cash-only spot — no leverage, no liquidation risk. This is
                # the strictest possible setting and matches the paper engine.
                "max_leverage": 1.0,
                # InstrumentType.CRYPTO == "crypto" — spot crypto spot pairs.
                "allowed_instruments": ["crypto"],
                "max_trades_per_day": int(cfg.max_trades_per_day),
            },
            "universe": {
                # AssetClass.CRYPTO == "crypto"
                "asset_classes": ["crypto"],
                "min_market_cap_usd": None,
                "min_avg_daily_volume_usd": None,
                "exclude_symbols": [],
            },
            "consent": {
                "created_at": now.isoformat(timespec="seconds"),
                "consent_token_sha256": consent_token,
                "broker": _BROKER_KEY,
                "account_ref": _ACCOUNT_REF,
                "expires_at": expires.isoformat(timespec="seconds"),
            },
            "flatten_on_halt": True,
            # --- Autopilot reference metadata (ignored by the mandate parser) ---
            "pairs": list(cfg.pairs),
            "profile": cfg.live_profile,
        }

    @staticmethod
    def to_yaml(mandate: dict[str, Any]) -> str:
        """Serialize a mandate dict to a human-readable YAML string.

        Uses ``yaml.safe_dump`` with ``sort_keys=False`` so the field order
        follows the dict insertion order (matching the mandate-model
        hierarchy) rather than alphabetical, making the output readable as a
        contract document.

        Args:
            mandate: A mandate dict (typically from :meth:`autopilot_mandate`).

        Returns:
            A YAML string with a trailing ``---`` document terminator.
        """
        return yaml.safe_dump(
            mandate,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )

    @staticmethod
    def save_mandate_file(
        path: Path | None = None,
        config: AutopilotConfig | None = None,
    ) -> Path:
        """Generate the autopilot mandate and persist it as a YAML file.

        Args:
            path: Destination file path. When ``None``, defaults to
                ``<runtime_root>/live/okx/autopilot_mandate.yaml`` so it sits
                alongside the broker's other live-channel state.
            config: Autopilot config; loaded from env when ``None``.

        Returns:
            The resolved file path the mandate was written to. The parent
            directory is created with ``0700`` perms (owner-only) matching
            the live channel's security posture.
        """
        mandate = MandateTemplate.autopilot_mandate(config)
        yaml_text = MandateTemplate.to_yaml(mandate)

        if path is None:
            # Late import to avoid a circular dependency at module load time.
            from src.live.paths import broker_dir

            path = broker_dir(_BROKER_KEY) / _MANDATE_FILENAME

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(yaml_text, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            # Best-effort permission pinning; the parent dir is already 0700.
            pass

        logger.info(
            "autopilot mandate written to %s (expires %s)",
            path,
            mandate["consent"]["expires_at"],
        )
        return path
