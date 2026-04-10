import os
import json
import logging
from datetime import datetime, time, timedelta
from telegram.ext import ContextTypes
from database.operations import get_trades_by_status, update_trade_execution, update_trade_fields
from services.market_data import get_multiple_prices, LIVE_TICK_DATA
from engine.trailing_stop_manager import TrailingStopManager
from services.zerodha_service import ZerodhaService
from services.zerodha_ticker import ZerodhaTicker
from utils.logger import log_trade_event, log_system

async def check_trades(context: ContextTypes.DEFAULT_TYPE):
    # Initialize Zerodha Service for execution
    zerodha = ZerodhaService()
    has_zerodha = zerodha.load_session()
    
    # 1. Check for Same-Day Closure (EOD) — use IST explicitly (server may run in UTC)
    market_close_str = os.environ.get("MARKET_CLOSE_TIME", "15:30")
    try:
        hour, minute = map(int, market_close_str.split(':'))
        market_close_time = time(hour, minute)
    except:
        market_close_time = time(15, 30)

    ist_offset = timedelta(hours=5, minutes=30)
    ist_now = datetime.utcnow() + ist_offset
    current_time = ist_now.time()
    
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
    
    # Auto-subscribe symbols to WebSocket Ticker
    ticker = ZerodhaTicker()
    ticker.subscribe_symbols(symbols)
    
    # Fetch current prices (yfinance fallback)
    prices = get_multiple_prices(symbols)
    
    # Merge with Live Tick Data (Real-time data takes precedence)
    for s in symbols:
        if s in LIVE_TICK_DATA:
            prices[s] = LIVE_TICK_DATA[s]
            
    # Process trades
    for trade in all_trades:
        symbol = trade['symbol']
        if symbol not in prices:
            continue
            
        current_price = prices[symbol]
        status = trade['status']
        trade_id = trade['id']
        qty = trade['quantity']
        
        entry = trade['entry_price']
        sl = trade['stop_loss']
        target = trade['target_price']
        is_long = target > entry if target else True 

        # --- CASE: PENDING ---
        if status == 'PENDING':
            # Check for Entry
            entry_hit = (is_long and current_price >= entry) or (not is_long and current_price <= entry)
            if entry_hit:
                log_system(f"Entry Hit: {symbol} at {current_price}")
                
                order_id = None  # default — avoids UnboundLocalError if Zerodha is not connected
                # Zerodha Entry
                if has_zerodha:
                    tx_type = "BUY" if is_long else "SELL"
                    order_id = zerodha.place_order(symbol, tx_type, qty)
                    if order_id:
                        log_system(f"Zerodha order placed: #{order_id} ({tx_type} {symbol} x{qty})")
                    else:
                        log_system(f"Zerodha order FAILED for {symbol}. Check logs.", level=40)
                else:
                    log_system(f"Zerodha not connected — trade {trade_id} ({symbol}) marked ACTIVE but NO real order placed.", level=30)

                update_trade_execution(trade_id, 'ACTIVE', current_price=current_price)
                log_trade_event(trade_id, "PENDING", "ACTIVE", f"Entry triggered at {current_price}")
                
                # Telegram Alert
                try:
                    zerodha_note = f"Order #{order_id}" if order_id else "No Zerodha session — paper trade only"
                    await context.bot.send_message(
                        chat_id=context.job.data.get('chat_id') if context.job and context.job.data else None,
                        text=(f"🚀 ENTRY HIT: {symbol}\n"
                              f"Price: ₹{current_price} | Direction: {'LONG' if is_long else 'SHORT'}\n"
                              f"Qty: {qty} | SL: {sl}\n"
                              f"{zerodha_note}")
                    ) if context.job and context.job.data and context.job.data.get('chat_id') else None
                except Exception as tg_err:
                    log_system(f"Telegram alert failed: {tg_err}", level=30)

        # --- CASE: ACTIVE ---
        elif status == 'ACTIVE':
            # 1. EOD Check — close at market, record actual P&L direction
            if is_eod:
                buy_price = trade.get('buy_price') or entry
                multiplier = 1 if is_long else -1
                pnl = (current_price - buy_price) * qty * multiplier
                
                if has_zerodha:
                    tx_type = "SELL" if is_long else "BUY"
                    zerodha.place_order(symbol, tx_type, qty)

                # Use CLOSED_SL if EOD exit is a loss so SL-hit counter stays accurate
                eod_status = 'CLOSED_TARGET' if pnl >= 0 else 'CLOSED_SL'
                update_trade_execution(trade_id, eod_status, current_price=current_price, pnl=pnl)
                update_trade_fields(trade_id, {'exit_reason': 'EOD_CLOSE'})
                continue

            # 2. Risk Check (SL/Target)
            sl_hit = (is_long and current_price <= sl) or (not is_long and current_price >= sl)
            target_hit = target and ((is_long and current_price >= target) or (not is_long and current_price <= target))
            
            if sl_hit or target_hit:
                new_status = 'CLOSED_TARGET' if target_hit else 'CLOSED_SL'
                buy_price = trade.get('buy_price') or entry
                multiplier = 1 if is_long else -1
                pnl = (current_price - buy_price) * qty * multiplier
                
                if has_zerodha:
                    tx_type = "SELL" if is_long else "BUY"
                    zerodha.place_order(symbol, tx_type, qty)
                
                update_trade_execution(trade_id, new_status, current_price=current_price, pnl=pnl)
                update_trade_fields(trade_id, {'exit_reason': 'TARGET' if target_hit else 'STOP_LOSS'})
                continue

            # 3. Trailing Stop Update (3R Logic)
            ts_manager = TrailingStopManager(trade)
            updated, new_sl, event_msg = ts_manager.check_and_update(current_price)
            if updated:
                update_trade_fields(trade_id, {
                    'stop_loss': new_sl,
                    'highest_price_reached': max(trade.get('highest_price_reached') or 0, current_price),
                    'trailing_stop_events': json.dumps(ts_manager.events),
                    'r_thresholds_crossed': ts_manager.thresholds_crossed_json,
                })
                log_system(f"Trailing SL Update ({symbol}): {event_msg}")
