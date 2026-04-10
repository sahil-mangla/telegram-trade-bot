import yfinance as yf

import time

# Global storage for live ticks from WebSocket
# Format: { 'SYMBOL': (float_price, timestamp_seconds) }
LIVE_TICK_DATA = {}

def get_live_price(symbol: str):
    """Helper to get a non-stale live price."""
    if symbol in LIVE_TICK_DATA:
        val = LIVE_TICK_DATA[symbol]
        # In case some old code pushed just a float before reload
        if isinstance(val, tuple) and len(val) == 2:
            price, timestamp = val
            if time.time() - timestamp < 60:
                return price
            else:
                del LIVE_TICK_DATA[symbol]
        else:
            # Stale format, clear it
            del LIVE_TICK_DATA[symbol]
    return None

def _extract_price(ticker) -> float:
    """Robustly extract current price from a yfinance Ticker object.
    
    Tries multiple methods in order of speed and reliability:
    1. fast_info['lastPrice'] or fast_info['regularMarketPrice'] (camelCase - correct keys)
    2. ticker.info['currentPrice'] or ['regularMarketPrice'] (slower but reliable fallback)
    """
    # Method 1: fast_info (camelCase keys)
    try:
        fi = ticker.fast_info
        for key in ('lastPrice', 'regularMarketPrice'):
            try:
                val = fi[key]
                if val and val > 0:
                    return float(val)
            except (KeyError, TypeError):
                pass
    except Exception:
        pass

    # Method 2: ticker.info (more reliable, slightly slower)
    try:
        info = ticker.info
        for key in ('currentPrice', 'regularMarketPrice', 'previousClose'):
            val = info.get(key)
            if val and val > 0:
                return float(val)
    except Exception:
        pass

    return None


def get_current_price(symbol: str) -> float:
    """Fetches the current price for an NSE-listed symbol.
    
    Prioritizes LIVE_TICK_DATA (WebSocket), then falls back to yfinance.
    """
    # Check live cache first
    live_price = get_live_price(symbol)
    if live_price is not None:
        return live_price

    # Always try .NS suffix first (NSE-listed Indian stocks)
    nse_symbol = symbol if symbol.endswith('.NS') else f"{symbol}.NS"
    
    try:
        ticker = yf.Ticker(nse_symbol)
        price = _extract_price(ticker)
        if price:
            return price
    except Exception as e:
        print(f"[market_data] Error fetching {nse_symbol}: {e}")

    # Fallback: try raw symbol (BSE or already has suffix)
    if nse_symbol != symbol:
        try:
            ticker = yf.Ticker(symbol)
            price = _extract_price(ticker)
            if price:
                return price
        except Exception as e:
            print(f"[market_data] Error fetching {symbol}: {e}")

    print(f"[market_data] Could not fetch price for {symbol}")
    return None


def get_multiple_prices(symbols: list) -> dict:
    """Fetches current prices for a list of NSE symbols."""
    prices = {}
    for symbol in set(symbols):
        price = get_current_price(symbol)
        if price is not None:
            prices[symbol] = price
    return prices

