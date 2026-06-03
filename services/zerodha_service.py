import os
from kiteconnect import KiteConnect
from database.db import get_session
from database.models import SystemConfig
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
            if not config:
                log_system("Zerodha: No access token found in DB. Use /login and /settoken to authenticate.", level=30)
                return False
            
            # updated_at is stored as UTC; compare against today in UTC to avoid IST/UTC mismatch
            ist_offset = datetime.timedelta(hours=5, minutes=30)
            utc_now = datetime.datetime.utcnow()
            ist_now = utc_now + ist_offset
            ist_today = ist_now.date()
            
            # Convert stored UTC time to IST for comparison
            stored_ist = config.updated_at + ist_offset
            token_date = stored_ist.date()
            
            if token_date == ist_today:
                self.kite.set_access_token(config.value)
                log_system(f"Zerodha: Session loaded (token set on {token_date}).")
                return True
            else:
                log_system(f"Zerodha: Token is stale (set on {token_date}, today is {ist_today}). Re-login required.", level=30)
                return False

    def place_order(self, symbol: str, transaction_type: str, quantity: int, order_type: str = "MARKET", price: float = None, trigger_price: float = None, product_type: str = None):
        if not self.kite.access_token:
            if not self.load_session():
                log_system("Zerodha session expired or not set.", level=40)
                return None, "Session expired or not configured. Use /login."
            
        try:
            # Clean symbol (remove .NS, .BO or any suffix)
            clean_symbol = symbol.split('.')[0].strip().upper()
            
            if not product_type:
                product_type = os.environ.get("ZERODHA_PRODUCT", "MIS").upper()
            else:
                product_type = product_type.upper()
                
            if product_type == "CNC":
                kite_product = self.kite.PRODUCT_CNC
            elif product_type == "NRML":
                kite_product = self.kite.PRODUCT_NRML
            else:
                kite_product = self.kite.PRODUCT_MIS
 
            if trigger_price and float(trigger_price) > 0:
                # Calculate protection percentage to yield exactly 1 absolute point deviation
                market_protection = round((1.0 / float(trigger_price)) * 100.0, 4)
            else:
                market_protection = 2.0

            # Note: Zerodha requires exchange info, e.g., "NSE:INFY"
            order_id = self.kite.place_order(
                variety=self.kite.VARIETY_REGULAR,
                exchange=self.kite.EXCHANGE_NSE,
                tradingsymbol=clean_symbol,
                transaction_type=transaction_type, # BUY/SELL
                quantity=quantity,
                product=kite_product,
                order_type=order_type,
                price=price,
                trigger_price=trigger_price,
                market_protection=market_protection
            )
            log_system(f"Zerodha Order Placed: {order_id} ({transaction_type} {clean_symbol}, type={order_type}, product={product_type}, trigger={trigger_price}, market_protection={market_protection})")
            return order_id, None
        except Exception as e:
            error_msg = str(e)
            log_system(f"Zerodha Order Error ({symbol}): {error_msg}", level=40)
            return None, error_msg
 
    def place_slm_entry_order(self, symbol: str, transaction_type: str, quantity: int, trigger_price: float, product_type: str = None):
        log_system(f"Zerodha: Placing SL-M entry order for {symbol} at trigger: {trigger_price}.")
        return self.place_order(symbol, transaction_type, quantity, order_type="SL-M", trigger_price=trigger_price, product_type=product_type)

    def get_order_status(self, order_id: str):
        if not self.kite.access_token:
            if not self.load_session():
                log_system("Zerodha session expired or not set.", level=40)
                return "FAILED", 0.0
        try:
            history = self.kite.order_history(order_id)
            if not history:
                return "UNKNOWN", 0.0
            
            # The order_history returns a list of status updates. The last element is the latest status.
            latest = history[-1]
            status = latest.get("status")
            avg_price = float(latest.get("average_price", 0.0) or 0.0)
            return status, avg_price
        except Exception as e:
            log_system(f"Error fetching status for order {order_id}: {e}", level=40)
            return "ERROR", 0.0

    def cancel_order(self, order_id: str):
        if not self.kite.access_token:
            if not self.load_session():
                log_system("Zerodha session expired or not set.", level=40)
                return False, "Session expired or not configured. Use /login."
        try:
            self.kite.cancel_order(variety=self.kite.VARIETY_REGULAR, order_id=order_id)
            log_system(f"Zerodha Order Cancelled: {order_id}")
            return True, None
        except Exception as e:
            error_msg = str(e)
            log_system(f"Zerodha Order Cancel Error ({order_id}): {error_msg}", level=40)
            return False, error_msg

    def place_gtt_sl(self, symbol: str, qty: int, sl_price: float,
                     last_price: float, product_type: str = "CNC"):
        """Place a single-leg GTT SL (stop loss) order for an active LONG position."""
        if not self.kite.access_token:
            if not self.load_session():
                return None, "Session expired or not configured."
        try:
            clean_symbol = symbol.split('.')[0].strip().upper()
            sl_limit = round(sl_price - 1.0, 2)    # 1 point below trigger for guaranteed fill

            orders = [
                {
                    "exchange": "NSE",
                    "tradingsymbol": clean_symbol,
                    "transaction_type": "SELL",
                    "quantity": qty,
                    "order_type": "LIMIT",
                    "product": product_type,
                    "price": sl_limit,
                }
            ]

            gtt_id_resp = self.kite.place_gtt(
                trigger_type=self.kite.GTT_TYPE_SINGLE,
                tradingsymbol=clean_symbol,
                exchange=self.kite.EXCHANGE_NSE,
                trigger_values=[round(sl_price, 2)],
                last_price=round(last_price, 2),
                orders=orders,
            )
            gtt_id = gtt_id_resp
            if isinstance(gtt_id_resp, dict) and "trigger_id" in gtt_id_resp:
                gtt_id = gtt_id_resp["trigger_id"]
            log_system(f"Zerodha GTT SL Placed: #{gtt_id} ({clean_symbol}, SL={sl_price}, product={product_type})")
            return gtt_id, None
        except Exception as e:
            error_msg = str(e)
            log_system(f"Zerodha GTT Place Error ({symbol}): {error_msg}", level=40)
            return None, error_msg

    def modify_gtt_sl(self, gtt_id: int, symbol: str, qty: int, new_sl_price: float,
                       last_price: float, product_type: str = "CNC"):
        """Modify the SL trigger + limit price of an existing single-leg GTT when trailing stop advances."""
        if not self.kite.access_token:
            if not self.load_session():
                return None, "Session expired or not configured."
        try:
            clean_symbol = symbol.split('.')[0].strip().upper()
            sl_limit = round(new_sl_price - 1.0, 2)

            orders = [
                {
                    "exchange": "NSE",
                    "tradingsymbol": clean_symbol,
                    "transaction_type": "SELL",
                    "quantity": qty,
                    "order_type": "LIMIT",
                    "product": product_type,
                    "price": sl_limit,
                }
            ]

            result = self.kite.modify_gtt(
                trigger_id=int(gtt_id),
                trigger_type=self.kite.GTT_TYPE_SINGLE,
                tradingsymbol=clean_symbol,
                exchange=self.kite.EXCHANGE_NSE,
                trigger_values=[round(new_sl_price, 2)],
                last_price=round(last_price, 2),
                orders=orders,
            )
            log_system(f"Zerodha GTT Modified: #{gtt_id} ({clean_symbol}, new SL={new_sl_price})")
            return result, None
        except Exception as e:
            error_msg = str(e)
            log_system(f"Zerodha GTT Modify Error (#{gtt_id}, {symbol}): {error_msg}", level=40)
            return None, error_msg

    def delete_gtt(self, gtt_id: int):
        """Delete a GTT by its ID — used on position close, EOD, or manual cancel."""
        if not self.kite.access_token:
            if not self.load_session():
                return False, "Session expired or not configured."
        try:
            self.kite.delete_gtt(trigger_id=int(gtt_id))
            log_system(f"Zerodha GTT Deleted: #{gtt_id}")
            return True, None
        except Exception as e:
            error_msg = str(e)
            log_system(f"Zerodha GTT Delete Error (#{gtt_id}): {error_msg}", level=40)
            return False, error_msg

