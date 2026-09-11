"""
Hybrid Strategy wrapper.
Combines multiple strategies (e.g., Macro + Micro) using configured allocation ratios.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any, Dict, List

from src.core.models import Candle, PortfolioSnapshot

logger = logging.getLogger("bot.strategy.hybrid")

class HybridStrategy:
    """
    Wraps Macro and Micro strategies.
    Allocates portfolio weights based on the core_ratio.
    """

    def __init__(
        self,
        macro_strategy: Any,
        micro_strategy: Any,
        core_ratio: float = 0.80,
    ):
        """
        Args:
            macro_strategy: The main macro trend strategy.
            micro_strategy: The short-term satellite strategy.
            core_ratio: The percentage of capital allocated to the macro strategy (e.g., 0.80).
        """
        self._macro = macro_strategy
        self._micro = micro_strategy
        self._core_ratio = max(0.0, min(1.0, core_ratio))
        self._satellite_ratio = 1.0 - self._core_ratio

        logger.info(
            "HybridStrategy initialized: %.0f%% Macro / %.0f%% Micro",
            self._core_ratio * 100,
            self._satellite_ratio * 100,
        )

    def export_state(self) -> Dict[str, Any]:
        """Export state for both strategies."""
        return {
            "macro_state": self._macro.export_state(),
            "micro_state": self._micro.export_state(),
        }

    def import_state(self, state_dict: Dict[str, Any]) -> None:
        """Import state for both strategies with fallback for legacy state structure."""
        if not isinstance(state_dict, dict):
            return
            
        macro_state = state_dict.get("macro_state")
        if macro_state:
            self._macro.import_state(macro_state)
        elif "positions" in state_dict or "bull_peak" in state_dict:
            # Fallback for legacy state where macro state was directly in strategy_state
            self._macro.import_state(state_dict)
            
        micro_state = state_dict.get("micro_state")
        if micro_state:
            self._micro.import_state(micro_state)

    def compute_signals(
        self,
        candles_by_asset: Dict[str, List[Candle]],
        portfolio: PortfolioSnapshot,
    ) -> Any:
        """
        Compute targets and signals.
        Returns a StrategyDecision object.
        """
        macro_decision = self._macro.compute_signals(candles_by_asset, portfolio)
        
        if self._satellite_ratio > 0.0:
            micro_decision = self._micro.compute_signals(candles_by_asset, portfolio)
        else:
            from src.core.models import StrategyDecision, TargetAllocation
            micro_decision = StrategyDecision(
                regime=macro_decision.regime,
                target_allocation=TargetAllocation(
                    weights={"USDT": 1.0},
                    regime=macro_decision.regime,
                    timestamp_ms=macro_decision.timestamp_ms,
                ),
                signals=[],
                timestamp_ms=macro_decision.timestamp_ms,
            )

        combined_weights: Dict[str, float] = {}

        # In Bear Regime, macro strategy controls 100% of capital (short hedge + cash).
        # Do NOT dilute bear hedge with the micro satellite split.
        from src.core.enums import Regime
        if macro_decision.regime == Regime.BEAR:
            # Synchronize micro state: all positions must be closed/inactive in Bear regime
            if hasattr(self._micro, "_positions") and isinstance(self._micro._positions, dict):
                for pos in self._micro._positions.values():
                    if isinstance(pos, dict):
                        pos["active"] = False
            combined_weights = dict(macro_decision.target_allocation.weights)
            all_signals = list(macro_decision.signals)
        elif macro_decision.metadata and macro_decision.metadata.get("safe_haven_active"):
            # Institutional Crash Shield Safe Haven:
            # Macro spot is 30% (safe_spot_weight), Micro satellite is up to 10% (safe_micro_weight),
            # Cash is >= 60% (safe_cash_weight). Parity with engine.py lines 875-876.
            safe_micro_w = float(macro_decision.metadata.get("safe_micro_weight", 0.10))

            all_assets = set(macro_decision.target_allocation.weights.keys()).union(
                set(micro_decision.target_allocation.weights.keys())
            )

            total_crypto = 0.0
            for asset in all_assets:
                if asset in ("USDT", "USD", "BUSD", "USDC"):
                    continue
                w_macro = macro_decision.target_allocation.weights.get(asset, 0.0)
                w_micro = micro_decision.target_allocation.weights.get(asset, 0.0)
                # Macro target weights are already scaled to safe_spot_weight (30%); micro scaled to safe_micro_weight (10%)
                combined = w_macro + (w_micro * safe_micro_w)
                combined_weights[asset] = combined
                total_crypto += combined

            combined_weights["USDT"] = max(0.0, 1.0 - total_crypto)

            all_signals = []
            for sig in macro_decision.signals:
                all_signals.append(dataclasses.replace(sig, reason=f"[MACRO-SAFE] {sig.reason}"))
            for sig in micro_decision.signals:
                all_signals.append(dataclasses.replace(sig, reason=f"[MICRO-SAFE] {sig.reason}"))
        else:
            all_assets = set(macro_decision.target_allocation.weights.keys()).union(
                set(micro_decision.target_allocation.weights.keys())
            )

            for asset in all_assets:
                w_macro = macro_decision.target_allocation.weights.get(asset, 0.0)
                w_micro = micro_decision.target_allocation.weights.get(asset, 0.0)
                
                combined = (w_macro * self._core_ratio) + (w_micro * self._satellite_ratio)
                combined_weights[asset] = combined

            all_signals = []
            
            for sig in macro_decision.signals:
                all_signals.append(dataclasses.replace(sig, reason=f"[MACRO] {sig.reason}"))
                
            for sig in micro_decision.signals:
                all_signals.append(dataclasses.replace(sig, reason=f"[MICRO] {sig.reason}"))

        # We must also replace the target_allocation since it is frozen
        macro_decision = dataclasses.replace(
            macro_decision,
            target_allocation=dataclasses.replace(
                macro_decision.target_allocation,
                weights=combined_weights,
            ),
            signals=all_signals
        )
        
        return macro_decision
