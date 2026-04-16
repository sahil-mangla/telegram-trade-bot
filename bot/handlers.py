from telegram import Update
from telegram.ext import ContextTypes
import os
import csv
import io
from database.operations import (add_trade, get_user_trades, 
                                 update_trade_execution, get_trade_by_id,
                                 get_user_trade_history, get_user_trade_stats,
                                 get_user_settings, update_user_settings, get_daily_metrics,
                                 update_trade_fields)
from sizers.fixed_percentage_sizer import FixedPercentageSizer
from engine.daily_limit_manager import DailyLimitManager
from utils.logger import log_trade_event, log_system
from services.zerodha_service import ZerodhaService
from services.zerodha_ticker import ZerodhaTicker

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    file = await update.message.document.get_file()
    
    if not update.message.document.file_name.endswith('.csv'):
        await update.message.reply_text("❌ Please upload a CSV file.")
        return

    # Download file content
    byte_array = await file.download_as_bytearray()
    try:
        content = byte_array.decode('utf-8-sig') # Safely handles BOM from Excel
    except UnicodeDecodeError:
        content = byte_array.decode('cp1252', errors='replace')
        
    f = io.StringIO(content)
    reader = csv.DictReader(f)

    # Validate headers (Case-insensitive)
    if not reader.fieldnames:
        await update.message.reply_text("❌ CSV file appears to be empty.")
        return
        
    headers = [h.strip().lower() for h in reader.fieldnames if h]
    required = ['symbol', 'entry', 'sl']
    if not all(r in headers for r in required):
        await update.message.reply_text(f"❌ Invalid CSV format. Required headers: {', '.join(required)}")
        return

    # Map headers to canonical names
    header_map = {h: h.strip().lower() for h in reader.fieldnames if h}
    
    added_count = 0
    errors = 0
    
    # Check daily limits (skip market hours check for manual CSV uploads)
    can_trade, reason = DailyLimitManager.can_create_trade(check_market_hours=False)
    if not can_trade:
        await update.message.reply_text(f"🛑 Cannot process CSV: {reason}")
        return

    settings = get_user_settings(user_id)
    if not settings or settings.get('account_size', 0) <= 0:
        await update.message.reply_text("❌ Your account size is $0.\nPlease set your account balance using `/account <amount>` first!\nThe bot needs this to calculate the 10% allocation for your CSV trades.")
        return

    sizer = FixedPercentageSizer(allocation_pct=0.10)
    
    existing_trades = get_user_trades(user_id)
    pending_symbols = [t['symbol'] for t in existing_trades if t['status'] in ['PENDING', 'ACTIVE']]

    try:
        key_symbol = next(k for k, v in header_map.items() if v == 'symbol')
        key_entry = next(k for k, v in header_map.items() if v == 'entry')
        key_sl = next(k for k, v in header_map.items() if v == 'sl')
    except StopIteration:
        await update.message.reply_text("❌ Could not map CSV headers correctly.")
        return

    skipped_qty = 0
    skipped_duplicate = 0
    
    for row in reader:
        try:
            raw_symbol = row.get(key_symbol)
            if not raw_symbol or not raw_symbol.strip():
                continue
                
            symbol = raw_symbol.strip().upper().split('.')[0]
            entry = float(row.get(key_entry))
            sl = float(row.get(key_sl))
            
            if symbol in pending_symbols:
                skipped_duplicate += 1
                continue
                
            qty = sizer.calculate_quantity(symbol, entry, sl, settings['account_size'])
            if qty < 1:
                skipped_qty += 1
                continue
                
            target = entry + 2 * (entry - sl)
            trade_id = add_trade(user_id, symbol, entry, sl, target, qty)
            update_trade_fields(trade_id, {
                'signal_source': 'csv_upload',
                'allocation_percentage': 10.0
            })
            added_count += 1
        except Exception as e:
            errors += 1
            log_system(f"CSV Parse error: {e}", level=30)

    # Auto-subscribe added symbols to WebSocket
    if added_count > 0:
        active_trades = get_user_trades(user_id)
        symbols = [t['symbol'] for t in active_trades if t['status'] in ['PENDING', 'ACTIVE']]
        ZerodhaTicker().subscribe_symbols(symbols)

    msg = f"✅ CSV parsing complete!\n📈 Added: {added_count} trades\n❌ Errors: {errors}"
    if skipped_duplicate > 0:
        msg += f"\n⏭️ Skipped (already active): {skipped_duplicate}"
    if skipped_qty > 0:
        msg += f"\n⚠️ Skipped (calculated qty < 1): {skipped_qty}\n(Hint: Increase your /account size or allocation %)"
        
    await update.message.reply_text(msg)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    text = (
        "Welcome to the Autonomous Trade Bot! 📈\n\n"
        "Manual Commands:\n"
        "/trade <symbol> <entry> <stop_loss> [quantity]\n"
        "/account [size] | /risk [pct]\n"
        "/list | /history | /stats | /pnl\n"
        "/cancel <trade_id>\n\n"
        "Autonomous Commands:\n"
        "/status - Daily summary & halt status\n"
        "/activetrades - View active trades with R-multiple\n"
        "/forcesync - Refresh local state\n"
        "/resetdaily - Reset daily SL counter\n\n"
        "Zerodha Commands:\n"
        "/login - Get Zerodha login URL\n"
        "/settoken <request_token> - Complete login\n\n"
        "💡 Tip: Drag and drop a CSV file with 'symbol', 'entry', 'sl' headers to bulk-add trades."
    )
    await update.message.reply_text(text)

async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    service = ZerodhaService()
    url = service.get_login_url()
    if url:
        await update.message.reply_text(f"🔗 Login to Zerodha here:\n{url}\n\nAfter logging in, copy the 'request_token' from the URL bar of the page you are redirected to, and use /settoken <token>")
    else:
        await update.message.reply_text("❌ Error generating login URL. Check your Zerodha keys in .env.")

async def settoken_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /settoken <request_token>")
        return
    
    token = context.args[0]
    service = ZerodhaService()
    success, msg = service.set_access_token(token)
    if success:
        ticker = ZerodhaTicker()
        ticker.stop()
        ticker.start()
        await update.message.reply_text(f"✅ {msg}\nWebSocket Ticker reconnected.")
    else:
        await update.message.reply_text(f"❌ Error: {msg}")

async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    user_id = update.message.chat_id
    
    if len(args) < 3:
        await update.message.reply_text("Usage: /trade <symbol> <entry> <stop_loss> [quantity]")
        return
        
    # Skip market hours check for manual trades — only enforce the daily SL halt
    can_trade, reason = DailyLimitManager.can_create_trade(check_market_hours=False)
    if not can_trade:
        await update.message.reply_text(f"🛑 {reason}")
        return

    symbol = args[0].upper().split('.')[0]
    try:
        entry = float(args[1])
        sl = float(args[2])
    except ValueError:
        await update.message.reply_text("Invalid numbers provided.")
        return
        
    settings = get_user_settings(user_id)
    metrics = get_daily_metrics(user_id)
    
        
    if len(args) > 3:
        try:
            quantity = int(args[3])
        except ValueError:
            await update.message.reply_text("Quantity must be an integer.")
            return
    else:
        risk_amount = settings['account_size'] * (settings['risk_pct'] / 100.0)
        risk_per_share = abs(entry - sl)
        if risk_per_share == 0:
            await update.message.reply_text("Entry and SL cannot be same.")
            return
        quantity = int(risk_amount // risk_per_share)
        if quantity < 1:
            await update.message.reply_text("Calculated qty < 1.")
            return

    target = entry + 2 * (entry - sl)
    trade_id = add_trade(user_id, symbol, entry, sl, target, quantity)
    update_trade_fields(trade_id, {'signal_source': 'manual'})
    
    # Auto-subscribe to WebSocket
    ZerodhaTicker().subscribe_symbols([symbol])
    
    await update.message.reply_text(f"✅ Manual trade {trade_id} created.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    metrics = get_daily_metrics(user_id)
    sl_hits = DailyLimitManager.get_sl_hits_today()
    halted = DailyLimitManager.is_trading_halted()
    
    status_icon = "🛑 HALTED" if halted else "✅ ACTIVE"
    msg = (f"📊 Daily Status ({status_icon})\n\n"
           f"Trades Taken: {metrics['trades_today']}\n"
           f"PnL Today: ${metrics['pnl_today']:.2f}\n"
           f"SL Hits: {sl_hits}/{os.environ.get('MAX_DAILY_SL', 3)}")
    await update.message.reply_text(msg)

async def activetrades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = [t for t in get_user_trades(update.message.chat_id) if t['status'] == 'ACTIVE']
    if not trades:
        await update.message.reply_text("No active trades.")
        return
        
    text = "🚀 Active Trades:\n\n"
    for t in trades:
        text += (f"ID: {t['id']} | {t['symbol']} | Qty: {t['quantity']}\n"
                 f"Entry: {t['entry_price']} | SL: {t['stop_loss']}\n"
                 f"Source: {t.get('signal_source', 'manual')}\n"
                 f"----------------------\n")
    await update.message.reply_text(text)

async def forcesync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("ℹ️ Remote sync disabled. Bot is in CSV/Manual mode.")

async def resetdaily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    DailyLimitManager.set_trading_halt(False)
    await update.message.reply_text("✅ Daily limits/halt status reset.")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = get_user_trades(update.message.chat_id)
    active_or_pending = [t for t in trades if t['status'] in ['PENDING', 'ACTIVE']]
    
    if not active_or_pending:
        await update.message.reply_text("No pending or active trades.")
        return
        
    text = "📊 All Trades:\n\n"
    for t in active_or_pending:
        text += (f"ID: {t['id']} | {t['symbol']} | {t['status']}\n"
                 f"Entry: {t['entry_price']} | SL: {t['stop_loss']}\n"
                 f"----------------------\n")
    await update.message.reply_text(text)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args: return
    try:
        trade_id = int(args[0])
    except (ValueError, IndexError):
        await update.message.reply_text("Invalid Trade ID.")
        return

    from database.operations import get_trade_by_id
    trade = get_trade_by_id(trade_id)
    if not trade:
        await update.message.reply_text(f"Trade #{trade_id} not found.")
        return

    if trade['status'] == 'ACTIVE':
        # Must close the real position in Zerodha before cancelling in DB
        zerodha = ZerodhaService()
        if zerodha.load_session():
            symbol = trade['symbol']
            qty = trade['quantity']
            is_long = trade['target_price'] > trade['entry_price'] if trade['target_price'] else True
            tx_type = "SELL" if is_long else "BUY"
            exit_order_id, exit_error = zerodha.place_order(symbol, tx_type, qty)
            if not exit_order_id:
                await update.message.reply_text(
                    f"🚨 Cannot cancel Trade #{trade_id} — exit order FAILED.\n"
                    f"Please close {symbol} manually in Zerodha before cancelling here."
                )
                return
            await update.message.reply_text(f"✅ Zerodha exit order placed (#{exit_order_id}). Closing trade.")
        else:
            # No Zerodha session — warn but allow cancel (paper trade)
            await update.message.reply_text(
                f"⚠️ No Zerodha session. Trade #{trade_id} will be cancelled in bot DB only.\n"
                f"If this was a real trade, close it manually in Zerodha!"
            )

    update_trade_execution(trade_id, 'CANCELLED')
    await update.message.reply_text(f"✅ Trade #{trade_id} cancelled.")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = get_user_trade_history(update.message.chat_id, limit=10)
    if not trades:
        await update.message.reply_text("No history.")
        return
    text = "📜 History:\n\n"
    for t in trades:
        text += f"{t['symbol']} | PnL: ${t.get('pnl', 0):.2f} | Reason: {t.get('exit_reason', 'N/A')}\n"
    await update.message.reply_text(text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_user_trade_stats(update.message.chat_id)
    text = (f"📈 Stats:\n\nTotal: {stats['total_trades']}\nWins: {stats['wins']} | Losses: {stats['losses']}\n"
            f"WR: {stats['win_rate']:.1f}% | PnL: ${stats['total_pnl']:.2f}")
    await update.message.reply_text(text)

async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_user_trade_stats(update.message.chat_id)
    await update.message.reply_text(f"💰 Total PnL: ${stats['total_pnl']:.2f}")

async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    args = context.args
    if args:
        update_user_settings(user_id, 'account_size', float(args[0]))
        await update.message.reply_text(f"✅ Account: ${float(args[0])}")
    else:
        settings = get_user_settings(user_id)
        await update.message.reply_text(f"🏦 Account: ${settings['account_size']}")

async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    args = context.args
    if args:
        update_user_settings(user_id, 'risk_pct', float(args[0]))
        await update.message.reply_text(f"⚠️ Risk: {float(args[0])}%")
    else:
        settings = get_user_settings(user_id)
        await update.message.reply_text(f"⚠️ Risk: {settings['risk_pct']}%")
