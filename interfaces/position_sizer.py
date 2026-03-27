from abc import ABC, abstractmethod

class PositionSizer(ABC):
    """Abstract for position sizing logic"""
    @abstractmethod
    def calculate_quantity(self, symbol: str, entry_price: float, stop_loss: float, account_balance: float) -> int:
        pass
