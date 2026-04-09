import logging
from services.zerodha_service import ZerodhaService
from utils.logger import log_system

class InstrumentManager:
    _instance = None
    _token_map = {} # symbol -> token
    _symbol_map = {} # token -> symbol

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(InstrumentManager, cls).__new__(cls)
        return cls._instance

    def get_tokens(self, symbols: list) -> dict:
        """
        Takes a list of symbols and returns a mapping of symbol -> token.
        Fetches missing tokens from Zerodha API.
        """
        missing = [s for s in symbols if s not in self._token_map]
        
        if missing:
            log_system(f"Fetching instrument tokens for: {missing}")
            zerodha = ZerodhaService()
            if zerodha.load_session():
                try:
                    # Query tokens using quote (fetches only needed data)
                    quotes = zerodha.kite.quote([f"NSE:{s}" for s in missing])
                    for nse_sym, data in quotes.items():
                        symbol = nse_sym.split(":")[1]
                        token = data['instrument_token']
                        self._token_map[symbol] = token
                        self._symbol_map[token] = symbol
                except Exception as e:
                    log_system(f"Error fetching instrument tokens: {e}", level=logging.ERROR)
            else:
                log_system("Zerodha session not available for fetching tokens.", level=30)

        # Return mapping for requested symbols
        return {s: self._token_map[s] for s in symbols if s in self._token_map}

    def get_symbol(self, token: int) -> str:
        return self._symbol_map.get(token)
