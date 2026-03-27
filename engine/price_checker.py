from database.operations import get_trades_by_status, update_trade_execution, update_trade_fields
from services.market_data import get_multiple_prices
from engine.trailing_stop_manager import TrailingStopManager
from services.zerodha_service import ZerodhaService
from telegram.ext import ContextTypes
import logging
import os
import json
from datetime import datetime, time
from utils.logger import log_trade_event, log_system

async def check_trades(context: ContextTypes.DEFAULT_TYPE):
    # Initialize Zerodha Service for execution
    zerodha = ZerodhaService()
    has_zerodha = zerodha.load_session()
    # 1. Check for Same-Day Closure (EOD)
    market_close_str = os.environ.get("MARKET_CLOSE_TIME", "15:30")
    try:
        hour, minute = map(int, market_close_str.split(':'))
        market_close_time = time(hour, minute)
    except:
        market_close_time = time(15, 30)

    now = datetime.now()
    current_time = now.time()
    
    # Force close 10 mins before market close
    is_eod = False
    closing_minutes = (market_close_time.hour * 60 + market_close_time.minute) - (current_time.hour * 60 + current_time.minute)
    if 0 < closing_minutes <= 10:
        is_eod = True
        log_system(f"Market close approaching ({closing_minutes} mins left). Initiating EOD closure.")

    # Fetch pending and active trades
    pending_trades = get_trades_by_status(['PENDING'])
    active_trades = get_trades_by_status(['ACTIVE'])
    
    all_trades = pending_trades + active_trades
    if not all_trades:
        return
        
    # Get unique symbols
    symbols = list(set([t['symbol'] for t in all_trades]))
    log_system(f"Running price check for symbols: {symbols}")
    
    # Fetch current prices
    prices = get_multiple_prices(symbols)
            
    # Process trades
    for trade in all_trades:
        symbol = trade['symbol']
        if symbol not in prices:
            continue
            
        current_price = prices[symbol]
        status = trade['status']
        user_id = trade['user_id']
        trade_id = trade['id']
        
        entry = trade['entry_price']
        sl = trade['stop_loss']
        target = trade['target_price']
        qty = trade['quantity']
        is_long = target > entry if target else True # Default to long if no target

        if is_eod and status == 'ACTIVE':
            # Force close at EOD
            buy_price = trade.get('buy_price') or entry
            multiplier = 1 if is_long else -1
            pnl = (current_price - buy_price) * qty * multiplier
            
            # Zerodha Exit
            if has_zerodha:
                tx_type = "SELL" if is_long else "BUY"
                zerodha.place_order(symbol, tx_type, qty)

            update_trade_execution(trade_id, 'CLOSED_TARGET', current_price=current_price, pnl=pnl)
            # ... (rest of the block)
@@ -67,6 +75,10 @@
                 risk_per_share = abs(entry - sl)
                 initial_risk = risk_per_share * qty
                 
+                # Zerodha Entry
+                if has_zerodha:
+                    tx_type = "BUY" if is_long else "SELL"
+                    zerodha.place_order(symbol, tx_type, qty)
+
                 update_trade_execution(trade_id, 'ACTIVE', current_price=current_price)
                 # ... (rest of the block)
@@ -134,6 +146,11 @@
                 new_status = 'CLOSED_TARGET' if target_hit else 'CLOSED_SL'
                 multiplier = 1 if is_long else -1
                 pnl = (current_price - buy_price) * qty * multiplier
+                
+                # Zerodha Exit
+                if has_zerodha:
+                    tx_type = "SELL" if is_long else "BUY"
+                    zerodha.place_order(symbol, tx_type, qty)
                 
                 update_trade_execution(trade_id, new_status, current_price=current_price, pnl=pnl)
                 # ... (rest of the block)
