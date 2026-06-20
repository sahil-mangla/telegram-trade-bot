import datetime
import yfinance as yf
from services.zerodha_service import ZerodhaService
from utils.instrument_manager import InstrumentManager
from utils.logger import log_system

def fetch_historical_candles(symbol: str, days: int = 50) -> list:
    """
    Fetches daily candles for the given symbol.
    Tries Zerodha API first, falls back to yfinance.
    Returns:
        List of dicts: [{'date': datetime.date, 'open': float, 'high': float, 'low': float, 'close': float, 'volume': float}]
    """
    # Clean symbol (remove any trailing suffixes for Zerodha)
    clean_symbol = symbol.split('.')[0].strip().upper()
    
    # Try Zerodha first
    zerodha = ZerodhaService()
    if zerodha.load_session():
        try:
            mgr = InstrumentManager()
            token_map = mgr.get_tokens([clean_symbol])
            token = token_map.get(clean_symbol)
            if token:
                to_date = datetime.datetime.now()
                from_date = to_date - datetime.timedelta(days=days)
                records = zerodha.kite.historical_data(
                    instrument_token=token,
                    from_date=from_date,
                    to_date=to_date,
                    interval="day"
                )
                if records:
                    candles = []
                    for r in records:
                        dt = r['date']
                        d = dt.date() if isinstance(dt, datetime.datetime) else dt
                        candles.append({
                            'date': d,
                            'open': float(r['open']),
                            'high': float(r['high']),
                            'low': float(r['low']),
                            'close': float(r['close']),
                            'volume': float(r['volume'])
                        })
                    log_system(f"Indicators: Fetched {len(candles)} candles from Zerodha for {clean_symbol}.")
                    return candles
        except Exception as e:
            log_system(f"Indicators: Zerodha fetch failed for {clean_symbol}: {e}. Trying yfinance fallback.", level=30)
            
    # yfinance Fallback
    try:
        nse_symbol = clean_symbol if clean_symbol.endswith('.NS') else f"{clean_symbol}.NS"
        ticker = yf.Ticker(nse_symbol)
        df = ticker.history(period=f"{days}d")
        if df.empty and nse_symbol != clean_symbol:
            ticker = yf.Ticker(clean_symbol)
            df = ticker.history(period=f"{days}d")
            
        if not df.empty:
            candles = []
            for dt, row in df.iterrows():
                candles.append({
                    'date': dt.date(),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                    'volume': float(row['Volume'])
                })
            log_system(f"Indicators: Fetched {len(candles)} candles from yfinance fallback for {clean_symbol}.")
            return candles
    except Exception as e:
        log_system(f"Indicators: yfinance fetch failed for {clean_symbol}: {e}", level=40)
        
    return []

def calculate_indicators(candles: list, period_atr: int = 14, period_sma: int = 20):
    """
    Calculates 14-day daily ATR, 20-day SMA of close, 20-day SMA of volume, and yesterday's close.
    Assumes candles are sorted by date ascending.
    Returns:
        dict: {
            'atr': float,
            'yesterday_close': float,
            'average_price_20d': float,
            'average_volume_20d': float
        } or None
    """
    # Create a local copy to avoid modifying the original list
    candles_copy = list(candles)
    
    # Exclude today's partial candle if running during market hours
    ist_offset = datetime.timedelta(hours=5, minutes=30)
    ist_now = datetime.datetime.utcnow() + ist_offset
    ist_today = ist_now.date()
    current_time_ist = ist_now.time()
    
    if candles_copy and candles_copy[-1]['date'] == ist_today:
        # If we are before 15:40 IST (still market hours or close to it), today's candle is not fully completed/settled
        if current_time_ist < datetime.time(15, 40):
            candles_copy.pop()
            
    if len(candles_copy) < max(period_atr + 1, period_sma):
        return None
        
    # 1. Calculate True Ranges
    trs = []
    for i in range(1, len(candles_copy)):
        high = candles_copy[i]['high']
        low = candles_copy[i]['low']
        prev_close = candles_copy[i-1]['close']
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        
    if len(trs) < period_atr:
        return None
        
    # Calculate Wilder's ATR
    # First value is SMA of first 14 TRs
    current_atr = sum(trs[:period_atr]) / period_atr
    for i in range(period_atr, len(trs)):
        current_atr = (current_atr * (period_atr - 1) + trs[i]) / period_atr
        
    # 2. Calculate 20-day SMA price and volume
    closes = [c['close'] for c in candles_copy[-period_sma:]]
    volumes = [c['volume'] for c in candles_copy[-period_sma:]]
    
    avg_price = sum(closes) / period_sma
    avg_vol = sum(volumes) / period_sma
    
    # 3. Yesterday's Close is the close of the last candle in our list
    yesterday_close = candles_copy[-1]['close']
    
    return {
        'atr': round(current_atr, 2),
        'yesterday_close': round(yesterday_close, 2),
        'average_price_20d': round(avg_price, 2),
        'average_volume_20d': round(avg_vol, 2)
    }
