from database.operations import get_trades_by_status, update_trade_execution
from services.market_data import get_multiple_prices
from telegram.ext import ContextTypes
import logging
from utils.logger import log_trade_event, log_system

async def check_trades(context: ContextTypes.DEFAULT_TYPE):
    # Fetch pending and active trades
    pending_trades = get_trades_by_status(['PENDING'])
    active_trades = get_trades_by_status(['ACTIVE'])
    
    all_trades = pending_trades + active_trades
    if not all_trades:
        return
        
    # Get unique symbols
    symbols = list(set([t['symbol'] for t in all_trades]))
    
    log_system(f"Running price check for symbols: {symbols}")
    
    # Fetch current prices for all symbols using the centralized service
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
        
        # Determine if long or short position
        is_long = target > entry
        
        if status == 'PENDING':
            # For long, entry condition met if current price is around entry or drops to it
            # We trigger if current price crosses the entry price
            triggered = False
            if is_long and current_price <= entry:
                triggered = True
            elif not is_long and current_price >= entry:
                triggered = True
                
            if triggered:
                update_trade_execution(trade_id, 'ACTIVE', current_price=current_price)
                log_trade_event('ENTRY_HIT', trade_id=trade_id, symbol=symbol, price=current_price, status='ACTIVE', message=f"SL:{sl} TGT:{target}")
                
                msg = (f"🟢 TRADE ACTIVE!\n\nSymbol: {symbol}\nFilled Entry: {current_price:.2f}\n"
                       f"Stop Loss: {sl}\nTarget: {target}\nQty: {qty}")
                await context.bot.send_message(chat_id=user_id, text=msg)
                
        elif status == 'ACTIVE':
            # Need to get buy_price from DB (it's in the trade dictionary now)
            buy_price = trade.get('buy_price', entry) # Fallback to entry if None
            if buy_price is None:
                buy_price = entry

            # Check Stop Loss
            sl_hit = False
            if is_long and current_price <= sl:
                sl_hit = True
            elif not is_long and current_price >= sl:
                sl_hit = True
                
            # Check Target
            target_hit = False
            if is_long and current_price >= target:
                target_hit = True
            elif not is_long and current_price <= target:
                target_hit = True
                
            if sl_hit or target_hit:
                new_status = 'CLOSED_TARGET' if target_hit else 'CLOSED_SL'
                
                # Calculate PnL
                multiplier = 1 if is_long else -1
                pnl = (current_price - buy_price) * qty * multiplier
                
                # Update DB
                update_trade_execution(trade_id, new_status, current_price=current_price, pnl=pnl)
                log_trade_event('EXIT_HIT', trade_id=trade_id, symbol=symbol, price=current_price, status=new_status, message=f"PnL: {pnl:.2f}")
                
                icon = "🎯 TARGET REACHED" if target_hit else "🔴 STOP LOSS HIT"
                msg = (f"{icon}!\n\nSymbol: {symbol}\n"
                       f"Closed at: {current_price:.2f}\n"
                       f"PnL: ${pnl:.2f}\nQty: {qty}")
                await context.bot.send_message(chat_id=user_id, text=msg)
