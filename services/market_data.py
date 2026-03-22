import yfinance as yf

def get_current_price(symbol: str) -> float:
    """Fetches the current fast last_price for a given symbol."""
    try:
        ticker = yf.Ticker(symbol)
        return ticker.fast_info['last_price']
    except Exception as e:
        print(f"Error fetching price for {symbol}: {e}")
        return None

def get_multiple_prices(symbols: list) -> dict:
    """Fetches fast prices for a list of symbols."""
    prices = {}
    for symbol in set(symbols):
        price = get_current_price(symbol)
        if price is not None:
            prices[symbol] = price
    return prices
