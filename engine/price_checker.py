import os
import json
import logging
from datetime import datetime, time, timedelta
from telegram.ext import ContextTypes
from database.operations import get_trades_by_status, update_trade_execution, update_trade_fields
from services.market_data import get_multiple_prices, get_live_price
from engine.trailing_stop_manager import TrailingStopManager
from services.zerodha_service import ZerodhaService
from services.zerodha_ticker import ZerodhaTicker
from utils.logger import log_trade_event, log_system

async def check_trades(context: ContextTypes.DEFAULT_TYPE):
    # Initialize Zerodha Service for execution
    zerodha = ZerodhaService()
    has_zerodha = zerodha.load_session()
    
    async def send_telegram_alert(chat_id, text):
        if chat_id:
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                log_system(f"Telegram alert failed: {e}", level=30)
    
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

    # Fetch pending, order_placed, and active trades
    pending_trades = get_trades_by_status(['PENDING'])
    order_placed_trades = get_trades_by_status(['ORDER_PLACED'])
    active_trades = get_trades_by_status(['ACTIVE'])
    
    all_trades = pending_trades + order_placed_trades + active_trades
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
        live = get_live_price(s)
        if live is not None:
            prices[s] = live
            
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
            if has_zerodha:
                # Place SL-M/Market entry order direct on exchange if market is open (>= 09:15)
                try:
                    market_open_str = os.environ.get("MARKET_OPEN_TIME", "09:15")
                    oh, om = map(int, market_open_str.split(':'))
                    market_open_time = time(oh, om)
                except:
                    market_open_time = time(9, 15)

                if current_time >= market_open_time:
                    # Check for gap-up/gap-down to avoid chasing (only active in first 15 mins: e.g., 9:15 - 9:30)
                    try:
                        open_dt = datetime.combine(ist_now.date(), market_open_time)
                        gap_limit_dt = open_dt + timedelta(minutes=15)
                        gap_limit_time = gap_limit_dt.time()
                    except:
                        gap_limit_time = time(9, 30)

                    if market_open_time <= current_time <= gap_limit_time:
                        is_gap_up = current_price >= entry if is_long else current_price <= entry
                        if is_gap_up:
                            log_system(f"Gap-up/down detected for {symbol} (current: {current_price}, entry: {entry}). Skipping and cancelling trade to avoid chasing.")
                            update_trade_execution(trade_id, 'CANCELLED')
                            update_trade_fields(trade_id, {'exit_reason': 'GAP_UP_CANCEL'})
                            # Telegram Alert
                            await send_telegram_alert(trade.get('user_id'), 
                                                      f"⏭️ GAP-UP/DOWN CANCELLED: {symbol}\n"
                                                      f"Current Price ₹{current_price} is beyond entry level ₹{entry}.\n"
                                                      f"Trade cancelled to avoid chasing.")
                            continue

                    tx_type = "BUY" if is_long else "SELL"
                    order_id, error_msg = zerodha.place_slm_entry_order(symbol, tx_type, qty, entry, product_type=trade.get('product_type'))
                    if order_id:
                        log_system(f"Zerodha entry order placed: #{order_id} ({tx_type} {symbol} x{qty}, product={trade.get('product_type')})")
                        update_trade_execution(trade_id, 'ORDER_PLACED', order_id=order_id)
                        log_trade_event("ENTRY_PLACED", trade_id=trade_id, symbol=symbol, price=entry, status="ORDER_PLACED", message=f"Order #{order_id} placed on exchange.")
                        # Telegram Alert
                        await send_telegram_alert(trade.get('user_id'),
                                                  f"🔍 ENTRY ORDER PLACED (SL-M): {symbol}\n"
                                                  f"Entry: ₹{entry} | Current: ₹{current_price}\n"
                                                  f"Qty: {qty} | Order ID: #{order_id}")
                    else:
                        log_system(f"Zerodha entry order placement FAILED for {symbol}: {error_msg}", level=40)
                        await send_telegram_alert(trade.get('user_id'),
                                                  f"⚠️ ORDER FAILED: {symbol}\n"
                                                  f"Error: {error_msg}\n"
                                                  f"Trade remains PENDING.")
            else:
                # Paper trading (no Zerodha session)
                entry_hit = (is_long and current_price >= entry) or (not is_long and current_price <= entry)
                if entry_hit:
                    log_system(f"Paper Entry Hit: {symbol} at {current_price}")
                    update_trade_execution(trade_id, 'ACTIVE', current_price=current_price)
                    log_trade_event("ENTRY_TRIGGERED", trade_id=trade_id, symbol=symbol, price=current_price, status="ACTIVE", message="Paper entry hit.")
                    # Telegram Alert
                    await send_telegram_alert(trade.get('user_id'),
                                              f"🚀 PAPER ENTRY HIT: {symbol}\n"
                                              f"Price: ₹{current_price} | Direction: {'LONG' if is_long else 'SHORT'}\n"
                                              f"Qty: {qty} | SL: {sl}\n"
                                              f"Paper trade (no Zerodha session)")

        # --- CASE: ORDER_PLACED ---
        elif status == 'ORDER_PLACED':
            if has_zerodha and trade.get('entry_order_id'):
                order_id = trade['entry_order_id']
                order_status, avg_price = zerodha.get_order_status(order_id)
                log_system(f"Checking order status for {symbol} (#{order_id}): {order_status}")
                
                if order_status == "COMPLETE":
                    exec_price = avg_price if avg_price > 0 else current_price
                    log_system(f"Entry Filled: {symbol} at avg price {exec_price}")
                    update_trade_execution(trade_id, 'ACTIVE', current_price=exec_price, order_id=order_id)
                    log_trade_event("ENTRY_FILLED", trade_id=trade_id, symbol=symbol, price=exec_price, status="ACTIVE", message=f"Order #{order_id} filled.")
                    # Telegram Alert
                    await send_telegram_alert(trade.get('user_id'),
                                              f"🚀 ENTRY FILLED: {symbol}\n"
                                              f"Avg Price: ₹{exec_price} | Direction: {'LONG' if is_long else 'SHORT'}\n"
                                              f"Qty: {qty} | SL: {sl}\n"
                                              f"Order #{order_id} filled.")
                elif order_status in ["CANCELLED", "REJECTED", "FAILED"]:
                    log_system(f"Entry order {order_id} was {order_status} for {symbol}. Reverting to PENDING.")
                    update_trade_execution(trade_id, 'PENDING')
                    update_trade_fields(trade_id, {'entry_order_id': None})
                    log_trade_event("ORDER_CANCELLED", trade_id=trade_id, symbol=symbol, price=current_price, status="PENDING", message=f"Order #{order_id} was {order_status}. Reverted to PENDING.")
                    # Telegram Alert
                    await send_telegram_alert(trade.get('user_id'),
                                              f"⚠️ ENTRY ORDER {order_status}: {symbol}\n"
                                              f"Order #{order_id} is no longer active. Reverted trade to PENDING.")
            else:
                # If no session or no order ID, revert to PENDING for safety
                log_system(f"Reverting ORDER_PLACED trade {trade_id} to PENDING due to missing session/order ID.", level=30)
                update_trade_execution(trade_id, 'PENDING')
                update_trade_fields(trade_id, {'entry_order_id': None})

        # --- CASE: ACTIVE ---
        elif status == 'ACTIVE':
            # 1. EOD Check — close at market, record actual P&L direction
            if is_eod:
                buy_price = trade.get('buy_price') or entry
                multiplier = 1 if is_long else -1
                pnl = (current_price - buy_price) * qty * multiplier
                
                if has_zerodha:
                    tx_type = "SELL" if is_long else "BUY"
                    zerodha.place_order(symbol, tx_type, qty, product_type=trade.get('product_type'))

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
                
                exit_ok = True
                if has_zerodha:
                    tx_type = "SELL" if is_long else "BUY"
                    exit_order_id, exit_error = zerodha.place_order(symbol, tx_type, qty, product_type=trade.get('product_type'))
                    if not exit_order_id:
                        exit_ok = False
                        log_system(f"EXIT ORDER FAILED for {symbol} (trade {trade_id}): {exit_error}", level=40)
                        await send_telegram_alert(trade.get('user_id'),
                                                  f"🚨 EXIT ORDER FAILED: {symbol}\n"
                                                  f"Error: {exit_error}\n"
                                                  f"MANUAL ACTION REQUIRED in Zerodha!")

                if not exit_ok:
                    continue  # CRITICAL: do not mark closed if Zerodha exit failed

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
