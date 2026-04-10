from interfaces.position_sizer import PositionSizer
import math


class FixedPercentageSizer(PositionSizer):
    """
    Sizes position so that the risk per trade equals `risk_pct` of the account balance.
    
    Bug fixed: Previously calculated qty = allocation / entry_price, which ignored
    stop distance and led to wildly different actual risk amounts per trade.
    Now: qty = (account * risk_pct) / risk_per_share  — true fixed-risk sizing.
    
    The `allocation_pct` parameter is kept for backward compatibility but now represents
    the fraction of account balance to risk per trade (not the total allocation).
    """

    def __init__(self, allocation_pct: float = 0.10):
        # Treat allocation_pct as max risk per trade (10% of account by default)
        self.risk_pct = allocation_pct

    def calculate_quantity(self, symbol: str, entry_price: float, stop_loss: float, account_balance: float) -> int:
        if entry_price <= 0 or account_balance <= 0:
            return 0

        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share == 0:
            return 0  # Cannot size — entry == SL

        risk_amount = account_balance * self.risk_pct
        quantity = int(risk_amount // risk_per_share)
        return max(0, quantity)
