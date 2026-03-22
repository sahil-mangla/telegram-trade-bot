from telegram import Update
from telegram.ext import ContextTypes
from database.operations import (add_trade, get_user_trades, 
                                 update_trade_execution, get_trade_by_id,
                                 get_user_trade_history, get_user_trade_stats,
                                 get_user_settings, update_user_settings, get_daily_metrics)
from utils.logger import log_trade_event

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Welcome to the Trade Bot! 📈\n\n"
        "Commands:\n"
        "/trade <symbol> <entry> <stop_loss> [quantity] - Create a new trade\n"
        "/account [size] - View or set account size\n"
        "/risk [pct] - View or set risk per trade percentage\n"
        "/list - View your pending and active trades\n"
        "/history - View your last 10 closed trades\n"
        "/stats - View your performance stats\n"
        "/pnl - View your total PnL\n"
        "/cancel <trade_id> - Cancel a pending trade"
    )
    await update.message.reply_text(text)

async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    user_id = update.message.chat_id
    
    if len(args) < 3:
        await update.message.reply_text("Usage: /trade <symbol> <entry> <stop_loss> [quantity]")
        return
        
    symbol = args[0].upper()
    try:
        entry = float(args[1])
        sl = float(args[2])
    except ValueError:
        await update.message.reply_text("Invalid numbers provided. Entry and Stop Loss must be numbers.")
        return
        
    # Get Risk Settings & Metrics
    settings = get_user_settings(user_id)
    metrics = get_daily_metrics(user_id)
    
    # 1. Daily Limits Validations
    if metrics['trades_today'] >= settings['max_daily_trades']:
        await update.message.reply_text(f"🛑 Trade rejected: You have reached your max daily trades limit ({settings['max_daily_trades']}).")
        return
        
    if metrics['pnl_today'] <= -settings['max_daily_loss']:
        await update.message.reply_text(f"🛑 Trade rejected: You have hit your max daily loss limit (${settings['max_daily_loss']}).")
        return
        
    # 2. Position Sizing
    if len(args) > 3:
        try:
            quantity = int(args[3])
        except ValueError:
            await update.message.reply_text("Quantity must be an integer.")
            return
    else:
        # Auto calculating quantity based on risk parameter
        risk_amount = settings['account_size'] * (settings['risk_pct'] / 100.0)
        risk_per_share = abs(entry - sl)
        if risk_per_share == 0:
            await update.message.reply_text("Entry and Stop Loss cannot be the same.")
            return
            
        quantity = int(risk_amount // risk_per_share)
        if quantity < 1:
            await update.message.reply_text(f"Risk per share (${risk_per_share:.2f}) is too high for your allowed risk (${risk_amount:.2f}). Calculated qty is < 1.")
            return

    # Calculate target (1:2 R:R)
    target = entry + 2 * (entry - sl)
    
    trade_id = add_trade(user_id, symbol, entry, sl, target, quantity)
    log_trade_event('CREATED', trade_id=trade_id, symbol=symbol, price=entry, status='PENDING', message=f"SL:{sl} TGT:{target} QTY:{quantity}")
    
    msg = (f"✅ Trade setup saved (ID: {trade_id})\n\n"
           f"Symbol: {symbol}\n"
           f"Entry: {entry}\n"
           f"Stop Loss: {sl}\n"
           f"Auto Target: {target}\n"
           f"Quantity: {quantity}\n\n"
           f"Status: PENDING")
    await update.message.reply_text(msg)

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = get_user_trades(update.message.chat_id)
    active_or_pending = [t for t in trades if t['status'] in ['PENDING', 'ACTIVE']]
    
    if not active_or_pending:
        await update.message.reply_text("You have no pending or active trades.")
        return
        
    text = "📊 Your Trades:\n\n"
    for t in active_or_pending:
        text += (f"ID: {t['id']} | {t['symbol']} | Qty: {t['quantity']}\n"
                 f"Entry: {t['entry_price']} | SL: {t['stop_loss']} | TGT: {t['target_price']}\n"
                 f"Status: {t['status']}\n"
                 f"----------------------\n")
                 
    await update.message.reply_text(text)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("Usage: /cancel <trade_id>")
        return
        
    try:
        trade_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Trade ID must be a number.")
        return
        
    trade = get_trade_by_id(trade_id)
    if not trade:
        await update.message.reply_text("Trade not found.")
        return
        
    if trade['user_id'] != update.message.chat_id:
        await update.message.reply_text("You do not have permission to cancel this trade.")
        return
        
    if trade['status'] not in ['PENDING', 'ACTIVE']:
        await update.message.reply_text(f"Cannot cancel a trade that is already {trade['status']}.")
        return
        
    update_trade_execution(trade_id, 'CANCELLED')
    log_trade_event('CANCELLED', trade_id=trade_id, symbol=trade['symbol'], status='CANCELLED')
    await update.message.reply_text(f"✅ Trade {trade_id} cancelled.")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    trades = get_user_trade_history(update.message.chat_id, limit=10)
    if not trades:
        await update.message.reply_text("No closed trades found.")
        return
        
    text = "📜 Trade History (Last 10):\n\n"
    for t in trades:
        icon = "🎯" if t['status'] == 'CLOSED_TARGET' else "🔴"
        pnl_val = t.get('pnl') or 0.0
        pnl_str = f"+${pnl_val:.2f}" if pnl_val >= 0 else f"-${abs(pnl_val):.2f}"
        
        buy_pr = f"{t.get('buy_price'):.2f}" if t.get('buy_price') else "N/A"
        sell_pr = f"{t.get('sell_price'):.2f}" if t.get('sell_price') else "N/A"
        
        text += (f"{icon} {t['symbol']} | Qty: {t['quantity']}\n"
                 f"Bought: {buy_pr} | Sold: {sell_pr}\n"
                 f"PnL: {pnl_str}\n"
                 f"----------------------\n")
    await update.message.reply_text(text)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_user_trade_stats(update.message.chat_id)
    if stats['total_trades'] == 0:
        await update.message.reply_text("No trading data available.")
        return
        
    text = (f"📈 Performance Stats:\n\n"
            f"Total Trades: {stats['total_trades']}\n"
            f"Wins: {stats['wins']} | Losses: {stats['losses']}\n"
            f"Win Rate: {stats['win_rate']:.1f}%\n"
            f"Total PnL: ${stats['total_pnl']:.2f}")
    await update.message.reply_text(text)

async def pnl_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_user_trade_stats(update.message.chat_id)
    pnl_val = stats.get('total_pnl', 0.0)
    emoji = "🚀" if pnl_val >= 0 else "📉"
    await update.message.reply_text(f"{emoji} Total Realized PnL: ${pnl_val:.2f}")

async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    args = context.args
    settings = get_user_settings(user_id)
    
    if not args:
        await update.message.reply_text(f"🏦 Account Size: ${settings['account_size']:,.2f}")
        return
        
    try:
        new_size = float(args[0])
    except ValueError:
        await update.message.reply_text("Account size must be a number.")
        return
        
    update_user_settings(user_id, 'account_size', new_size)
    await update.message.reply_text(f"✅ Account size updated to ${new_size:,.2f}")

async def risk_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat_id
    args = context.args
    settings = get_user_settings(user_id)
    
    if not args:
        await update.message.reply_text(f"⚠️ Risk per Trade: {settings['risk_pct']}%")
        return
        
    try:
        new_risk = float(args[0])
    except ValueError:
        await update.message.reply_text("Risk percentage must be a number.")
        return
        
    if new_risk <= 0 or new_risk > 100:
        await update.message.reply_text("Risk percentage must be between 0 and 100.")
        return
        
    update_user_settings(user_id, 'risk_pct', new_risk)
    await update.message.reply_text(f"✅ Risk per trade updated to {new_risk}%")
