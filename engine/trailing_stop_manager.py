import json
from datetime import datetime


class TrailingStopManager:
    """
    Manages the trailing stop logic for an active trade.
    Uses a 1R-based trailing stop: once a trade reaches 1R profit,
    the SL is locked to entry (break-even). At 2R it locks 1R profit, etc.
    
    Usage (as in price_checker.py):
        ts_manager = TrailingStopManager(trade)
        updated, new_sl, event_msg = ts_manager.check_and_update(current_price)
        if updated:
            # persist ts_manager.events and new_sl
    """

    def __init__(self, trade: dict):
        self.trade = trade
        self.entry = trade['entry_price']
        # CRITICAL: use the ORIGINAL stop loss, not the current (possibly trailed) one.
        # initial_stop_loss is set once at trade creation and never updated.
        # Falls back to current stop_loss only for trades created before this fix.
        self.initial_sl = trade.get('initial_stop_loss') or trade['stop_loss']
        self.qty = trade.get('quantity', 1)
        self.is_long = self.entry > self.initial_sl

        # Load existing events from DB (JSON string) or start fresh
        raw = trade.get('trailing_stop_events')
        try:
            self.events = json.loads(raw) if raw else []
        except (TypeError, json.JSONDecodeError):
            self.events = []

        # Thresholds already crossed (from DB) so we don't re-trigger them
        raw_thresholds = trade.get('r_thresholds_crossed')
        try:
            self.thresholds_crossed = json.loads(raw_thresholds) if raw_thresholds else []
        except (TypeError, json.JSONDecodeError):
            self.thresholds_crossed = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _risk(self) -> float:
        return abs(self.entry - self.initial_sl)

    def _current_r(self, price: float) -> float:
        risk = self._risk()
        if risk == 0:
            return 0.0
        if self.is_long:
            return (price - self.entry) / risk
        return (self.entry - price) / risk

    def _highest_r(self) -> float:
        highest = self.trade.get('highest_price_reached') or self.entry
        return self._current_r(highest)

    def _new_sl_for_locked_r(self, locked_r: float) -> float:
        """Return the SL price that locks in `locked_r` profit."""
        risk = self._risk()
        if self.is_long:
            return self.entry + locked_r * risk
        return self.entry - locked_r * risk

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check_and_update(self, current_price: float):
        """
        Check whether any new trailing-stop thresholds have been crossed.

        Returns:
            (updated: bool, new_sl: float, event_msg: str)
        """
        risk = self._risk()
        if risk == 0:
            return False, self.initial_sl, ""

        highest_price = max(self.trade.get('highest_price_reached') or self.entry, current_price)
        highest_r = self._current_r(highest_price)

        new_sl = self.trade['stop_loss']   # current SL (may already have been updated)
        new_events = []
        n = 1
        while highest_r >= n:
            label = f"{n}R"
            if label not in self.thresholds_crossed:
                locked_r = n - 1          # 1R→lock 0R (entry), 2R→lock 1R, …
                candidate_sl = self._new_sl_for_locked_r(locked_r)

                # Only move SL in the protective direction (never widen it)
                if self.is_long and candidate_sl > new_sl:
                    new_sl = candidate_sl
                elif not self.is_long and candidate_sl < new_sl:
                    new_sl = candidate_sl

                self.thresholds_crossed.append(label)
                event = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "price": current_price,
                    "r_multiple": round(highest_r, 2),
                    "threshold": label,
                    "new_sl": round(new_sl, 2),
                }
                new_events.append(event)
            n += 1

        if new_events:
            self.events.extend(new_events)
            msg = f"Trailing SL → {new_sl:.2f} (thresholds: {[e['threshold'] for e in new_events]})"
            return True, new_sl, msg

        return False, new_sl, ""

    @property
    def thresholds_crossed_json(self) -> str:
        return json.dumps(self.thresholds_crossed)
