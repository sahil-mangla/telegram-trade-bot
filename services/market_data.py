import yfinance as yf

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
    
    Symbols should be stored without suffix (e.g. 'ATHERENERG').
    Automatically appends '.NS' for NSE stocks on yfinance.
    """
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

