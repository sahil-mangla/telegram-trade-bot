from interfaces.data_provider import DataProvider
from services.zerodha_service import ZerodhaService
from typing import List, Optional
import datetime

class ZerodhaDataProvider(DataProvider):
    def __init__(self):
        self.service = ZerodhaService()
        self.service.load_session()

    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            # symbol format: "NSE:SYMBOL"
            quote = self.service.kite.quote(f"NSE:{symbol}")
            return quote.get(f"NSE:{symbol}", {}).get("last_price")
        except:
            return None

    def get_historical_data(self, symbol: str, days: int) -> List[dict]:
        try:
            # Implementation for historical data if needed
            to_date = datetime.datetime.now()
            from_date = to_date - datetime.timedelta(days=days)
            # Need instrument_token for historical data, which requires loading instruments list
            # For now, this is a placeholder
            return []
        except:
            return []
