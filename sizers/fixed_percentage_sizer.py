from interfaces.position_sizer import PositionSizer
import math

class FixedPercentageSizer(PositionSizer):
    def __init__(self, allocation_pct: float = 0.10):
        self.allocation_pct = allocation_pct
        
    def calculate_quantity(self, symbol: str, entry_price: float, stop_loss: float, account_balance: float) -> int:
        if entry_price <= 0:
            return 0
        
        # Calculate dollar amount to allocate
        allocation_amount = account_balance * self.allocation_pct
        
        # Quantity = allocation / entry_price
        quantity = int(allocation_amount // entry_price)
        return max(0, quantity)
