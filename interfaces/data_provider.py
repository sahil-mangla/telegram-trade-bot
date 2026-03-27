from abc import ABC, abstractmethod
from typing import List, Optional

class DataProvider(ABC):
    """Abstract for market data (currently yfinance, later Zerodha)"""
    @abstractmethod
    def get_current_price(self, symbol: str) -> Optional[float]:
        pass
    
    @abstractmethod
    def get_historical_data(self, symbol: str, days: int) -> List[dict]:
        pass
