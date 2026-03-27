from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class TradeSignal:
    symbol: str
    entry_price: float
    initial_stop_loss: float
    signal_source: str
    metadata: dict = None

class SignalSource(ABC):
    """Abstract for trade signal sources"""
    @abstractmethod
    def get_trades_for_day(self) -> List[TradeSignal]:
        pass
