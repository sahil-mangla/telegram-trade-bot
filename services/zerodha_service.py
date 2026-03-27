import os
from kiteconnect import KiteConnect
from database.operations import get_session, SystemConfig
from utils.logger import log_system
import datetime

class ZerodhaService:
    def __init__(self):
        self.api_key = os.environ.get("ZERODHA_API_KEY")
        self.api_secret = os.environ.get("ZERODHA_API_SECRET")
        self.redirect_url = os.environ.get("ZERODHA_REDIRECT_URL")
        self.kite = KiteConnect(api_key=self.api_key)
        
    def get_login_url(self):
        return self.kite.login_url()

    def set_access_token(self, request_token: str):
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            access_token = data["access_token"]
            
            # Store access token in system_config
            with next(get_session()) as session:
                config = session.query(SystemConfig).filter(SystemConfig.key == 'zerodha_access_token').first()
                if not config:
                    config = SystemConfig(key='zerodha_access_token', value=access_token)
                    session.add(config)
                else:
                    config.value = access_token
                    config.updated_at = datetime.datetime.utcnow()
                session.commit()
            
            self.kite.set_access_token(access_token)
            return True, "Session generated successfully."
        except Exception as e:
            log_system(f"Zerodha session error: {e}", level=40)
            return False, str(e)

    def load_session(self):
        with next(get_session()) as session:
            config = session.query(SystemConfig).filter(SystemConfig.key == 'zerodha_access_token').first()
            if config:
                # Check if it was updated today (Kite tokens expire daily)
                if config.updated_at.date() == datetime.date.today():
                    self.kite.set_access_token(config.value)
                    return True
        return False

    def place_order(self, symbol: str, transaction_type: str, quantity: int, order_type: str = "MARKET", price: float = None):
        if not self.load_session():
            log_system("Zerodha session expired or not set.", level=40)
            return None
            
        try:
            # Note: Zerodha requires exchange info, e.g., "NSE:INFY"
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NSE,
                tradingsymbol=symbol,
                transaction_type=transaction_type, # BUY/SELL
                quantity=quantity,
                product=self.kite.PRODUCT_MIS, # Intraday
                order_type=order_type,
                price=price
            )
            log_system(f"Zerodha Order Placed: {order_id} ({transaction_type} {symbol})")
            return order_id
        except Exception as e:
            log_system(f"Zerodha Order Error ({symbol}): {e}", level=40)
            return None
