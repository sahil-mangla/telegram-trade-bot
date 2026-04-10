import logging
from kiteconnect import KiteTicker
from services.market_data import LIVE_TICK_DATA
from services.zerodha_service import ZerodhaService
from utils.instrument_manager import InstrumentManager
from utils.logger import log_system

class ZerodhaTicker:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ZerodhaTicker, cls).__new__(cls)
            cls._instance.kws = None
            cls._instance.tokens = []
        return cls._instance

    def start(self):
        zerodha = ZerodhaService()
        if not zerodha.load_session():
            log_system("Ticker: Cannot start, Zerodha session not found.", level=30)
            return

        api_key = zerodha.api_key
        access_token = zerodha.kite.access_token

        self.kws = KiteTicker(api_key, access_token)

        def on_ticks(ws, ticks):
            mgr = InstrumentManager()
            for tick in ticks:
                token = tick.get('instrument_token')
                price = tick.get('last_price')
                symbol = mgr.get_symbol(token)
                if symbol and price:
                    import time
                    LIVE_TICK_DATA[symbol] = (price, time.time())

        def on_connect(ws, response):
            log_system("Ticker connected successfully.")
            if self.tokens:
                ws.subscribe(self.tokens)
                ws.set_mode(ws.MODE_FULL, self.tokens)

        def on_close(ws, code, reason):
            log_system(f"Ticker connection closed: {reason}", level=30)

        def on_error(ws, code, reason):
            log_system(f"Ticker error: {code} - {reason}", level=40)

        self.kws.on_ticks = on_ticks
        self.kws.on_connect = on_connect
        self.kws.on_close = on_close
        self.kws.on_error = on_error

        # Run in a separate thread (connect() is blocking)
        self.kws.connect(threaded=True)

    def subscribe_symbols(self, symbols: list):
        if not self.kws:
            log_system("Ticker not started. Cannot subscribe.")
            # Cache tokens for when it starts
            mgr = InstrumentManager()
            new_tokens = list(mgr.get_tokens(symbols).values())
            self.tokens = list(set(self.tokens + new_tokens))
            return

        mgr = InstrumentManager()
        token_map = mgr.get_tokens(symbols)
        new_tokens = [t for t in token_map.values() if t not in self.tokens]
        
        if new_tokens:
            self.kws.subscribe(new_tokens)
            self.kws.set_mode(self.kws.MODE_FULL, new_tokens)
            self.tokens.extend(new_tokens)
            log_system(f"Ticker subscribed to: {list(token_map.keys())}")

    def stop(self):
        if self.kws:
            self.kws.close()
            self.kws = None
            log_system("Ticker stopped.")
