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

# Product types that support GTT (server-side orders that persist across sessions)
GTT_SUPPORTED_PRODUCTS = ('CNC', 'NRML')

# Global variable to throttle minute-by-minute GTT verification check
last_gtt_verify_time = 0

def parse_gtt_id(gtt_val):
    if not gtt_val:
        return None
    gtt_str = str(gtt_val).strip()
    if gtt_str.startswith('{'):
        import ast
        try:
            d = ast.literal_eval(gtt_str)
            if isinstance(d, dict) and 'trigger_id' in d:
                return int(d['trigger_id'])
        except Exception as e:
            log_system(f"Failed to parse gtt_id dictionary: {e}", level=30)
    try:
        return int(gtt_str)
    except ValueError:
        return None

def get_default_user_id(context=None):
    if context and hasattr(context, 'data') and isinstance(context.data, dict) and 'chat_id' in context.data:
        return context.data['chat_id']
    env_id = os.environ.get("TELEGRAM_CHAT_ID")
    if env_id:
        try:
            return int(env_id)
        except ValueError:
            pass
    # Fallback to first user in UserSettings table
    try:
        from database.db import get_session
        from database.models import UserSettings
        with next(get_session()) as session:
            first_user = session.query(UserSettings).first()
            if first_user:
                return first_user.user_id
    except Exception as e:
        log_system(f"Failed to fetch user from DB in get_default_user_id: {e}", level=30)
    return 99999999 # fallback default

def sync_zerodha_orders(zerodha: ZerodhaService, user_id: int):
    """
    Synchronize manually placed orders from Zerodha with the local database.
    Imports new completed BUY orders as ACTIVE trades.
    Closes ACTIVE trades if a completed manual SELL order is detected.
    """
    if not zerodha.kite.access_token:
        return
    
    try:
        orders = zerodha.kite.orders()
    except Exception as e:
        log_system(f"Failed to fetch orders from Zerodha for syncing: {e}", level=30)
        return
    
    # 1. Handle completed BUY orders (Imports)
    buy_orders = [
        o for o in orders 
        if o.get('transaction_type') == 'BUY' 
        and o.get('status') == 'COMPLETE'
        and o.get('product') in ('CNC', 'NRML', 'MIS')
    ]
    
    sl_pct = float(os.environ.get("MANUAL_TRADE_SL_PCT", "1.5"))
    target_pct = float(os.environ.get("MANUAL_TRADE_TARGET_PCT", "3.0"))
    auto_import = os.environ.get("AUTO_IMPORT_MANUAL_TRADES", "True").lower() == "true"
    
    if auto_import:
        for order in buy_orders:
            order_id = str(order['order_id'])
            # Check if already imported
            from database.db import get_session
            from database.models import Trade
            with next(get_session()) as session:
                exists = session.query(Trade).filter(Trade.entry_order_id == order_id).first()
                if exists:
                    continue
            
            symbol = order['tradingsymbol']
            qty = int(order['filled_quantity'])
            entry_price = float(order['average_price'])
            product_type = order['product']
            
            # Entry price > SL for LONG positions
            stop_loss = round(entry_price * (1 - sl_pct / 100.0), 2)
            target_price = round(entry_price * (1 + target_pct / 100.0), 2)
            
            from database.operations import add_manually_placed_trade
            trade_id = add_manually_placed_trade(
                user_id=user_id,
                symbol=symbol,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_price=target_price,
                quantity=qty,
                product_type=product_type,
                order_id=order_id
            )
            
            log_system(f"Auto-imported Zerodha buy order: {symbol} (Qty: {qty}, Entry: {entry_price}, SL: {stop_loss}, Trade ID: {trade_id})")
            
            # Subscribe symbol to ticker
            try:
                ZerodhaTicker().subscribe_symbols([symbol])
            except Exception as e:
                log_system(f"Failed to subscribe imported symbol {symbol} to ticker: {e}", level=30)
                
    # 2. Handle completed SELL orders (Manual Exits)
    sell_orders = [
        o for o in orders 
        if o.get('transaction_type') == 'SELL' 
        and o.get('status') == 'COMPLETE'
        and o.get('product') in ('CNC', 'NRML', 'MIS')
    ]
    
    for order in sell_orders:
        order_id = str(order['order_id'])
        symbol = order['tradingsymbol'].upper()
        sell_price = float(order['average_price'])
        
        from database.db import get_session
        from database.models import Trade
        with next(get_session()) as session:
            # Find active trade for this symbol
            trade = session.query(Trade).filter(
                Trade.symbol == symbol,
                Trade.status == 'ACTIVE'
            ).first()
            
            if trade and trade.exit_order_id != order_id:
                trade_id = trade.id
                entry = trade.entry_price
                qty = trade.quantity
                is_long = entry > trade.stop_loss
                
                # Calculate actual PnL
                multiplier = 1 if is_long else -1
                pnl = (sell_price - entry) * qty * multiplier
                
                # Delete the GTT SL on Zerodha if it exists
                gtt_id_val = trade.gtt_id
                gtt_id = parse_gtt_id(gtt_id_val)
                if gtt_id and trade.product_type in GTT_SUPPORTED_PRODUCTS:
                    try:
                        zerodha.delete_gtt(gtt_id)
                        log_system(f"GTT #{gtt_id} deleted for manually exited trade {trade_id}")
                    except Exception as ex:
                        log_system(f"Failed to delete GTT for manual exit: {ex}", level=30)
                
                # Close the trade in DB
                trade.status = 'CLOSED_TARGET' if pnl >= 0 else 'CLOSED_SL'
                trade.sell_price = sell_price
                trade.pnl = pnl
                trade.closed_at = datetime.utcnow()
                trade.exit_order_id = order_id
                trade.exit_reason = 'manual_exit'
                
                from database.models import TradeLog
                log = TradeLog(
                    trade_id=trade_id, 
                    old_status='ACTIVE', 
                    new_status=trade.status, 
                    message=f"Manual exit detected on Zerodha. Order #{order_id} filled at avg price {sell_price}."
                )
                session.add(log)
                session.commit()
                log_system(f"Closed trade {trade_id} ({symbol}) due to manual Zerodha exit order #{order_id}")

async def check_trades(context: ContextTypes.DEFAULT_TYPE):
    # Initialize Zerodha Service for execution
    zerodha = ZerodhaService()
    has_zerodha = zerodha.load_session()
    
    user_id = get_default_user_id(context)
    
    # Sync manual orders first if Zerodha is available
    if has_zerodha:
        sync_zerodha_orders(zerodha, user_id)
        
        # Check if WebSocket Ticker needs to be started or refreshed
        ticker = ZerodhaTicker()
        current_db_token = zerodha.kite.access_token
        if not ticker.kws or not getattr(ticker.kws, 'ws', None) or getattr(ticker, 'current_token', None) != current_db_token:
            log_system("Ticker: Connecting or refreshing WebSocket Ticker connection...")
            ticker.stop()
            ticker.start()

        # GTT Verification (runs every 60 seconds)
        global last_gtt_verify_time
        import time
        now_ts = time.time()
        if now_ts - last_gtt_verify_time >= 60:
            last_gtt_verify_time = now_ts
            try:
                await verify_broker_gtts(zerodha)
            except Exception as e:
                log_system(f"GTT Verification failed: {e}", level=logging.ERROR)

    # Time configuration
    ist_offset = timedelta(hours=5, minutes=30)
    ist_now = datetime.utcnow() + ist_offset
    ist_today = ist_now.date()
    current_time = ist_now.time()

    # Gap-up opening report (runs after 09:15:00 IST on weekdays)
    if ist_today.weekday() < 5 and current_time >= time(9, 15):
        run_key = "gap_up_report_last_run"
        already_run = False
        
        from database.db import get_session
        from database.models import SystemConfig
        with next(get_session()) as session:
            config = session.query(SystemConfig).filter(SystemConfig.key == run_key).first()
            if config and config.value == str(ist_today):
                already_run = True
                
        if not already_run:
            logged_in_early = False
            if has_zerodha:
                with next(get_session()) as session:
                    token_config = session.query(SystemConfig).filter(SystemConfig.key == 'zerodha_access_token').first()
                    if token_config:
                        stored_ist = token_config.updated_at + ist_offset
                        # User logged in before 9:15 AM
                        if stored_ist.date() == ist_today and stored_ist.time() < time(9, 15):
                            logged_in_early = True
            
            # Mark as run first
            with next(get_session()) as session:
                config = session.query(SystemConfig).filter(SystemConfig.key == run_key).first()
                if not config:
                    config = SystemConfig(key=run_key, value=str(ist_today))
                    session.add(config)
                else:
                    config.value = str(ist_today)
                    config.updated_at = datetime.utcnow()
                session.commit()
                
            if logged_in_early:
                try:
                    await compile_and_send_gap_up_report(zerodha, user_id, context)
                except Exception as e:
                    log_system(f"Gap-up report failed: {e}", level=logging.ERROR)
            else:
                log_system("Gap-up report: User did not log in before 9:15 AM today. Skipping report compilation.")

    # Daily Post-Market Job (runs after 15:40 IST on weekdays)
    if ist_today.weekday() < 5 and current_time >= time(15, 40):
        # Check if we already ran today
        from database.db import get_session
        from database.models import SystemConfig
        run_key = "daily_job_last_run"
        
        already_run = False
        with next(get_session()) as session:
            config = session.query(SystemConfig).filter(SystemConfig.key == run_key).first()
            if config and config.value == str(ist_today):
                already_run = True
                
        if not already_run:
            try:
                # Mark as run first to prevent concurrent execution/loops
                with next(get_session()) as session:
                    config = session.query(SystemConfig).filter(SystemConfig.key == run_key).first()
                    if not config:
                        config = SystemConfig(key=run_key, value=str(ist_today))
                        session.add(config)
                    else:
                        config.value = str(ist_today)
                        config.updated_at = datetime.utcnow()
                    session.commit()
                
                await run_daily_post_market_job(zerodha, context)
            except Exception as e:
                log_system(f"Daily post-market job failed: {e}", level=logging.ERROR)
    
    async def send_telegram_alert(chat_id, text):
        if chat_id and context and hasattr(context, 'bot') and context.bot:
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                log_system(f"Telegram alert failed: {e}", level=30)
        else:
            log_system(f"NOTIFICATION (Telegram Offline): {text}")


    def _cancel_trade_gtt(trade):
        """Helper: delete the GTT for a trade (if any) and clear the stored gtt_id."""
        gtt_id_val = trade.get('gtt_id')
        gtt_id = parse_gtt_id(gtt_id_val)
        product = trade.get('product_type', 'MIS')
        if gtt_id and product in GTT_SUPPORTED_PRODUCTS and has_zerodha:
            ok, err = zerodha.delete_gtt(gtt_id)
            if ok:
                log_system(f"GTT #{gtt_id} deleted for trade {trade['id']} ({trade['symbol']})")
            else:
                log_system(f"GTT #{gtt_id} delete failed for {trade['symbol']}: {err}", level=30)
            update_trade_fields(trade['id'], {'gtt_id': None})
    
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
    
    # Update active trades cache in ZerodhaTicker for instant WebSocket target hits
    try:
        ZerodhaTicker().update_active_trades_cache(active_trades)
    except Exception as e:
        log_system(f"Failed to update Ticker active trades cache: {e}", level=30)
        
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
        product = trade.get('product_type', 'MIS')
        
        entry = trade['entry_price']
        sl = trade['stop_loss']
        target = trade['target_price']
        is_long = entry > sl
        
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

                    # ----- Place GTT SL sell (CNC/NRML only) -----
                    if product in GTT_SUPPORTED_PRODUCTS:
                        gtt_id, gtt_err = zerodha.place_gtt_sl(
                            symbol, qty, sl, exec_price, product_type=product
                        )
                        if gtt_id:
                            update_trade_fields(trade_id, {'gtt_id': str(gtt_id)})
                            log_trade_event("GTT_PLACED", trade_id=trade_id, symbol=symbol,
                                            price=exec_price, status="ACTIVE",
                                            message=f"GTT SL #{gtt_id} placed (SL={sl}).")
                            target_str = f" | Target: ₹{target}" if target else ""
                            await send_telegram_alert(trade.get('user_id'),
                                                      f"🚀 ENTRY FILLED: {symbol}\n"
                                                      f"Avg Price: ₹{exec_price} | Direction: {'LONG' if is_long else 'SHORT'}\n"
                                                      f"Qty: {qty} | SL: ₹{sl}{target_str}\n"
                                                      f"Order #{order_id} filled.\n"
                                                      f"🛡️ GTT SL Set: SL=₹{sl} (GTT #{gtt_id})")
                        else:
                            log_system(f"GTT SL placement FAILED for {symbol}: {gtt_err}", level=40)
                            target_str = f" | Target: ₹{target}" if target else ""
                            await send_telegram_alert(trade.get('user_id'),
                                                      f"🚀 ENTRY FILLED: {symbol}\n"
                                                      f"Avg Price: ₹{exec_price} | Direction: {'LONG' if is_long else 'SHORT'}\n"
                                                      f"Qty: {qty} | SL: ₹{sl}{target_str}\n"
                                                      f"Order #{order_id} filled.\n"
                                                      f"⚠️ GTT PLACEMENT FAILED: {gtt_err}\n"
                                                      f"Bot will monitor via price checker.")
                    else:
                        # MIS or other product — no GTT, notify normally
                        target_str = f" | Target: ₹{target}" if target else ""
                        await send_telegram_alert(trade.get('user_id'),
                                                  f"🚀 ENTRY FILLED: {symbol}\n"
                                                  f"Avg Price: ₹{exec_price} | Direction: {'LONG' if is_long else 'SHORT'}\n"
                                                  f"Qty: {qty} | SL: ₹{sl}{target_str}\n"
                                                  f"Order #{order_id} filled. (MIS — monitoring via price checker)")

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
            # Retroactively place GTT SL if missing for CNC/NRML
            if not is_eod and product in GTT_SUPPORTED_PRODUCTS and not trade.get('gtt_id') and has_zerodha:
                log_system(f"Retroactively placing GTT SL for already ACTIVE trade {symbol} (ID: {trade_id})")
                gtt_id, gtt_err = zerodha.place_gtt_sl(
                    symbol, qty, sl, current_price, product_type=product
                )
                if gtt_id:
                    trade['gtt_id'] = str(gtt_id)
                    update_trade_fields(trade_id, {'gtt_id': str(gtt_id)})
                    log_trade_event("GTT_PLACED_RETROACTIVE", trade_id=trade_id, symbol=symbol,
                                    price=current_price, status="ACTIVE",
                                    message=f"Retroactive GTT SL #{gtt_id} placed (SL={sl}).")
                    await send_telegram_alert(trade.get('user_id'),
                                              f"🛡️ RETROACTIVE GTT SL SET: {symbol}\n"
                                              f"SL: ₹{sl}\n"
                                              f"GTT ID: #{gtt_id}")
                else:
                    log_system(f"Retroactive GTT placement FAILED for {symbol}: {gtt_err}", level=40)
                    await send_telegram_alert(trade.get('user_id'),
                                              f"⚠️ RETROACTIVE GTT PLACEMENT FAILED: {symbol}\n"
                                              f"Error: {gtt_err}\n"
                                              f"Bot will monitor via price checker.")

            # 1. EOD Check — close at market, record actual P&L direction
            if is_eod:
                buy_price = trade.get('buy_price') or entry
                multiplier = 1 if is_long else -1
                pnl = (current_price - buy_price) * qty * multiplier
                
                if has_zerodha:
                    # Delete GTT first so it doesn't fire after we manually exit
                    _cancel_trade_gtt(trade)
                    tx_type = "SELL" if is_long else "BUY"
                    zerodha.place_order(symbol, tx_type, qty, product_type=trade.get('product_type'))

                # Use CLOSED_SL if EOD exit is a loss so SL-hit counter stays accurate
                eod_status = 'CLOSED_TARGET' if pnl >= 0 else 'CLOSED_SL'
                update_trade_execution(trade_id, eod_status, current_price=current_price, pnl=pnl)
                update_trade_fields(trade_id, {'exit_reason': 'EOD_CLOSE'})
                continue

            # 2. Risk Check (SL/Target) — for MIS or if GTT somehow wasn't placed
            sl_hit = (is_long and current_price <= sl) or (not is_long and current_price >= sl)
            target_hit = target and ((is_long and current_price >= target) or (not is_long and current_price <= target))
            
            if sl_hit or target_hit:
                new_status = 'CLOSED_TARGET' if target_hit else 'CLOSED_SL'
                buy_price = trade.get('buy_price') or entry
                multiplier = 1 if is_long else -1
                pnl = (current_price - buy_price) * qty * multiplier
                
                exit_ok = True
                if has_zerodha:
                    # Delete GTT first to avoid double-fill on CNC/NRML
                    _cancel_trade_gtt(trade)
                    # For MIS OR when target is hit on CNC/NRML, fire an immediate market exit
                    if product not in GTT_SUPPORTED_PRODUCTS or target_hit:
                        tx_type = "SELL" if is_long else "BUY"
                        exit_order_id, exit_error = zerodha.place_order(symbol, tx_type, qty, product_type=product)
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

                reason = "🎯 TARGET HIT" if target_hit else "🛑 STOP LOSS HIT"
                await send_telegram_alert(trade.get('user_id'),
                                          f"{reason}: {symbol}\n"
                                          f"Exit Price: ₹{current_price:.2f} | PnL: ₹{pnl:.2f}\n"
                                          f"{'GTT SL has executed the sell order on Zerodha.' if (product in GTT_SUPPORTED_PRODUCTS and sl_hit) else 'Market exit order placed.'}")
                continue

            # 3. Trailing Stop Update (1R Logic)
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

                # ----- Modify GTT SL leg for CNC/NRML -----
                gtt_id_val = trade.get('gtt_id')
                gtt_id = parse_gtt_id(gtt_id_val)
                if gtt_id and product in GTT_SUPPORTED_PRODUCTS and has_zerodha:
                    _, gtt_err = zerodha.modify_gtt_sl(
                        gtt_id, symbol, qty, new_sl,
                        current_price, product_type=product
                    )
                    if gtt_err:
                        log_system(f"GTT modify FAILED for {symbol} GTT#{gtt_id}: {gtt_err}", level=40)
                        await send_telegram_alert(trade.get('user_id'),
                                                  f"⚠️ GTT SL UPDATE FAILED: {symbol}\n"
                                                  f"New SL: ₹{new_sl:.2f} | GTT #{gtt_id}\n"
                                                  f"Error: {gtt_err}\n"
                                                  f"Please update GTT manually in Kite.")
                    else:
                        await send_telegram_alert(trade.get('user_id'),
                                                  f"📈 TRAILING SL UPDATED: {symbol}\n"
                                                  f"New SL: ₹{new_sl:.2f}\n"
                                                  f"GTT #{gtt_id} modified on Zerodha. ✅\n"
                                                  f"({event_msg})")
                else:
                    # MIS or no GTT — just notify
                    await send_telegram_alert(trade.get('user_id'),
                                              f"📈 TRAILING SL UPDATED: {symbol}\n"
                                              f"New SL: ₹{new_sl:.2f}\n"
                                              f"({event_msg})")

async def verify_broker_gtts(zerodha: ZerodhaService):
    """
    Every minute verify broker GTT matches expected stop and delete orphans.
    """
    log_system("GTT Verification: Starting verification...")
    if not zerodha.kite.access_token:
        log_system("GTT Verification: No access token, skipping.")
        return

    try:
        # Fetch active GTTs from broker
        gtt_list = zerodha.kite.gtts()
        active_gtts = {g['id']: g for g in gtt_list if g['status'] == 'active'}
        log_system(f"GTT Verification: Found {len(active_gtts)} active GTTs on Zerodha.")
    except Exception as e:
        log_system(f"GTT Verification: Failed to fetch GTTs from Zerodha: {e}", level=30)
        return

    # Get active CNC/NRML trades from DB
    from database.operations import get_trades_by_status, update_trade_fields
    active_trades = get_trades_by_status(['ACTIVE'])
    active_cnc_nrml = [t for t in active_trades if t.get('product_type') in GTT_SUPPORTED_PRODUCTS]

    # Verify and update GTTs for active trades
    for trade in active_cnc_nrml:
        trade_id = trade['id']
        symbol = trade['symbol']
        expected_sl = round(trade['stop_loss'], 2)
        qty = trade['quantity']
        product = trade['product_type']
        
        gtt_id_val = trade.get('gtt_id')
        gtt_id = parse_gtt_id(gtt_id_val)
        
        gtt_exists = False
        if gtt_id and gtt_id in active_gtts:
            gtt_exists = True
            g_item = active_gtts[gtt_id]
            triggers = g_item.get('condition', {}).get('trigger_values', [])
            if triggers:
                actual_sl = round(float(triggers[0]), 2)
                # Apply 1% GTT optimization comparison
                pct_diff = abs(expected_sl - actual_sl) / actual_sl
                if pct_diff >= 0.01:
                    log_system(f"GTT Verification: Mismatch for {symbol} (GTT #{gtt_id}). Expected: {expected_sl}, Actual: {actual_sl} (diff: {pct_diff:.2%}). Modifying GTT...")
                    from services.market_data import get_current_price
                    current_price = get_current_price(symbol) or expected_sl + 5.0
                    _, err = zerodha.modify_gtt_sl(gtt_id, symbol, qty, expected_sl, current_price, product_type=product)
                    if err:
                        log_system(f"GTT Verification: Failed to modify GTT {gtt_id}: {err}", level=30)
            else:
                gtt_exists = False

        if not gtt_exists:
            log_system(f"GTT Verification: Missing GTT for active trade {symbol} (ID: {trade_id}). Placing new GTT...")
            from services.market_data import get_current_price
            current_price = get_current_price(symbol) or expected_sl + 5.0
            new_gtt_id, err = zerodha.place_gtt_sl(symbol, qty, expected_sl, current_price, product_type=product)
            if new_gtt_id:
                update_trade_fields(trade_id, {'gtt_id': str(new_gtt_id)})
                log_system(f"GTT Verification: Placed new GTT #{new_gtt_id} for {symbol} (SL: {expected_sl})")
            else:
                log_system(f"GTT Verification: Failed to place GTT for {symbol}: {err}", level=40)

    # Detect and cancel orphan GTTs where quantity owned is 0
    try:
        holdings = zerodha.kite.holdings()
        positions = zerodha.kite.positions()
        
        owned_qtys = {}
        for h in holdings:
            sym = h.get('tradingsymbol', '').upper()
            if sym:
                owned_qtys[sym] = owned_qtys.get(sym, 0) + int(h.get('quantity', 0))
                
        for p in positions.get('net', []):
            sym = p.get('tradingsymbol', '').upper()
            if sym:
                owned_qtys[sym] = owned_qtys.get(sym, 0) + int(p.get('quantity', 0))

        # Check each active GTT to see if it is an orphan
        for g_id, g_item in active_gtts.items():
            sym = g_item.get('condition', {}).get('tradingsymbol', '').upper()
            if not sym:
                continue
                
            qty_owned = owned_qtys.get(sym, 0)
            if qty_owned <= 0:
                log_system(f"GTT Verification: Orphan GTT #{g_id} detected for {sym} (Owned: {qty_owned}). Cancelling GTT...")
                zerodha.delete_gtt(g_id)
                # If any trade in our DB points to this gtt_id, clear it
                for t in active_trades:
                    if parse_gtt_id(t.get('gtt_id')) == g_id:
                        update_trade_fields(t['id'], {'gtt_id': None})
    except Exception as e:
        log_system(f"GTT Verification: Error checking for orphan GTTs: {e}", level=30)

async def run_daily_post_market_job(zerodha: ZerodhaService, context=None):
    """
    Daily Job (After Market Close)
    1. Recalculate ATR
    2. Calculate Step = 3 * ATR
    3. Compute New Stop
    4. Raise GTT if required
    """
    log_system("Daily Job: Starting daily post-market trailing stop updates...")
    from database.operations import get_trades_by_status, update_trade_fields
    active_trades = get_trades_by_status(['ACTIVE'])
    if not active_trades:
        log_system("Daily Job: No active trades to update.")
        return

    from utils.indicators import fetch_historical_candles, calculate_indicators
    for trade in active_trades:
        trade_id = trade['id']
        symbol = trade['symbol']
        current_sl = trade['stop_loss']
        is_long = trade['entry_price'] > (trade.get('initial_stop_loss') or current_sl)
        qty = trade['quantity']
        product = trade.get('product_type', 'MIS')
        gtt_id_val = trade.get('gtt_id')
        gtt_id = parse_gtt_id(gtt_id_val)

        # Fetch candles and recalculate indicators (last 50 days)
        candles = fetch_historical_candles(symbol, days=50)
        indicators = calculate_indicators(candles)
        if not indicators:
            log_system(f"Daily Job: Failed to fetch candles/calculate indicators for {symbol}. Skipping.", level=30)
            continue

        atr = indicators['atr']
        yesterday_close = indicators['yesterday_close']
        avg_price = indicators['average_price_20d']
        avg_vol = indicators['average_volume_20d']

        # Get today's high/low
        today_candle = candles[-1]
        today_high = today_candle['high']
        today_low = today_candle['low']

        # Compute New Stop
        step = 3.0 * atr
        if is_long:
            candidate_stop = round(today_high - step, 2)
            # Stop never moves down (only increases)
            new_sl = round(max(current_sl, candidate_stop), 2)
        else:
            candidate_stop = round(today_low + step, 2)
            # Stop never moves up (only decreases/tightens)
            new_sl = round(min(current_sl, candidate_stop), 2)

        # Update DB fields
        update_fields = {
            'atr': atr,
            'yesterday_close': yesterday_close,
            'average_price_20d': avg_price,
            'average_volume_20d': avg_vol,
            'highest_price_reached': round(max(trade.get('highest_price_reached') or 0, today_high), 2) if is_long else round(min(trade.get('highest_price_reached') or 999999, today_low), 2)
        }

        # If SL changed, update it
        sl_changed = abs(new_sl - current_sl) > 0.01
        if sl_changed:
            update_fields['stop_loss'] = new_sl
            log_system(f"Daily Job: Trailing Stop for {symbol} updated from {current_sl} to {new_sl}.")
            
            # Record a log event
            from database.db import get_session
            from database.models import TradeLog
            with next(get_session()) as session:
                log_entry = TradeLog(
                    trade_id=trade_id,
                    old_status='ACTIVE',
                    new_status='ACTIVE',
                    message=f"Daily ATR trail update: stop moved from {current_sl} to {new_sl} (ATR: {atr})."
                )
                session.add(log_entry)
                session.commit()

        update_trade_fields(trade_id, update_fields)

        # Modify GTT if required (respecting 1% optimization)
        if sl_changed and gtt_id and product in GTT_SUPPORTED_PRODUCTS and zerodha.kite.access_token:
            pct_change = abs(new_sl - current_sl) / current_sl
            if pct_change >= 0.01:
                log_system(f"Daily Job: Modifying GTT #{gtt_id} for {symbol} to new stop {new_sl} (change: {pct_change:.2%}).")
                from services.market_data import get_current_price
                current_price = get_current_price(symbol) or new_sl + 5.0
                _, err = zerodha.modify_gtt_sl(gtt_id, symbol, qty, new_sl, current_price, product_type=product)
                if err:
                    log_system(f"Daily Job: GTT modify FAILED for {symbol}: {err}", level=40)
                    if context and hasattr(context, 'bot') and context.bot:
                        try:
                            await context.bot.send_message(
                                chat_id=trade.get('user_id'),
                                text=f"⚠️ Daily GTT Modify FAILED for {symbol}: {err}\nPlease update GTT #{gtt_id} to SL={new_sl} manually."
                            )
                        except:
                            pass
                else:
                    if context and hasattr(context, 'bot') and context.bot:
                        try:
                            await context.bot.send_message(
                                chat_id=trade.get('user_id'),
                                text=f"📈 Daily Trail: Stop loss for {symbol} updated to ₹{new_sl:.2f} (GTT modified) ✅"
                            )
                        except:
                            pass
            else:
                log_system(f"Daily Job: GTT modify skipped for {symbol} (change {pct_change:.2%} is < 1% optimization threshold).")

    log_system("Daily Job: Post-market updates completed successfully.")

async def send_telegram_message(chat_id, text, context=None):
    if chat_id and context and hasattr(context, 'bot') and context.bot:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception as e:
            log_system(f"Telegram message failed: {e}", level=30)
    else:
        log_system(f"NOTIFICATION (Telegram Offline): {text}")

async def compile_and_send_gap_up_report(zerodha: ZerodhaService, user_id: int, context=None):
    """
    Fetch opening prices of top stocks and compile the top 25 gap-ups of >= 0.5%.
    """
    log_system("Gap-up Report: Compiling opening gap-ups...")
    
    TOP_STOCKS = [
        "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "SBIN", "ITC", "HINDUNILVR",
        "LT", "BAJFINANCE", "HCLTECH", "MARUTI", "SUNPHARMA", "ADANIENT", "KOTAKBANK", "TATAMOTORS", "AXISBANK", "ONGC",
        "NTPC", "COALINDIA", "JSWSTEEL", "POWERGRID", "TITAN", "ADANIPORTS", "ULTRACEMCO", "BPCL", "GRASIM", "BAJAJFINSV",
        "DRREDDY", "NESTLEIND", "CIPLA", "APOLLOHOSP", "M&M", "EICHERMOT", "TATACONSUM", "HEROMOTOCO", "SBILIFE", "WIPRO",
        "TECHM", "DIVISLAB", "HDFCLIFE", "BRITANNIA", "SHRIRAMFIN", "INDUSINDBK", "BAJAJ-AUTO", "HINDALCO"
    ]
    symbols = sorted(list(set(TOP_STOCKS)))
    instruments = [f"NSE:{s}" for s in symbols]
    
    try:
        quotes = zerodha.kite.quote(instruments)
    except Exception as e:
        log_system(f"Gap-up Report: Failed to fetch quotes from Zerodha: {e}", level=40)
        return

    gap_ups = []
    for inst, quote in quotes.items():
        symbol = inst.replace("NSE:", "")
        ohlc = quote.get('ohlc', {})
        open_price = ohlc.get('open')
        prev_close = ohlc.get('close')
        
        if open_price and prev_close and prev_close > 0:
            gap_pct = ((open_price - prev_close) / prev_close) * 100.0
            if gap_pct >= 0.5:
                gap_ups.append({
                    'symbol': symbol,
                    'open': open_price,
                    'close': prev_close,
                    'gap_pct': gap_pct
                })

    # Sort in descending order of gap percentage
    gap_ups.sort(key=lambda x: x['gap_pct'], reverse=True)
    
    # Get top 25
    top_25 = gap_ups[:25]
    
    # Format message
    if not top_25:
        msg = "🔔 *Market Opening Report*\nNo stocks from the top list opened gap-up by >= 0.5% today."
    else:
        msg = f"🔔 *Top Gap-Up Openings (>= 0.5%)*\n\n"
        for i, item in enumerate(top_25, 1):
            msg += f"{i}. *{item['symbol']}*: +{item['gap_pct']:.2f}% (Open: ₹{item['open']:.2f} | Prev Close: ₹{item['close']:.2f})\n"
            
    await send_telegram_message(user_id, msg, context)
    log_system(f"Gap-up Report: Sent report with {len(top_25)} stocks to Telegram.")
