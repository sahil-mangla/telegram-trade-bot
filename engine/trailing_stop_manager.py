import json
from datetime import datetime

class TrailingStopManager:
    @staticmethod
    def calculate_trailing_stop(entry_price: float, initial_sl: float, current_price: float, highest_price: float):
        """
        Calculates the new stop loss based on the 3R trailing stop logic.
        Returns (new_sl, r_multiple, thresholds_crossed)
        """
        risk_per_share = abs(entry_price - initial_sl)
        if risk_per_share == 0:
            return initial_sl, 0, []

        is_long = entry_price > initial_sl
        
        # Current profit in terms of R
        if is_long:
            current_r = (current_price - entry_price) / risk_per_share
            highest_r = (highest_price - entry_price) / risk_per_share
        else:
            current_r = (entry_price - current_price) / risk_per_share
            highest_r = (entry_price - highest_price) / risk_per_share

        # Determine thresholds crossed based on highest price reached
        thresholds = []
        new_sl = initial_sl
        
        # Loop from 3R upwards
        n = 3
        while highest_r >= n:
            thresholds.append(f"{n}R")
            # For 3R, lock entry (0R profit). For 4R, lock 1R.
            locked_r = n - 3
            if is_long:
                new_sl = entry_price + (locked_r * risk_per_share)
            else:
                new_sl = entry_price - (locked_r * risk_per_share)
            n += 1
            
        return new_sl, current_r, thresholds

    @staticmethod
    def format_event(timestamp, price, r_multiple, threshold=None):
        return {
            "timestamp": timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp,
            "price": price,
            "r_multiple": r_multiple,
            "threshold": threshold
        }
