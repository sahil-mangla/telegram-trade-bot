import logging
import threading
from kiteconnect import KiteTicker
from services.market_data import LIVE_TICK_DATA
from services.zerodha_service import ZerodhaService
from utils.instrument_manager import InstrumentManager
from utils.logger import log_system

class ZerodhaTicker:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ZerodhaTicker, cls).__new__(cls)
            cls._instance.kws = None
            cls._instance.tokens = []
            cls._instance.active_trades = {}
            cls._instance._lock = threading.Lock()
        return cls._instance

    def start(self):
        zerodha = ZerodhaService()
        if not zerodha.load_session():
            log_system("Ticker: Cannot start, Zerodha session not found.", level=30)
            return

        api_key = zerodha.api_key
        access_token = zerodha.kite.access_token
        self.current_token = access_token

        self.kws = KiteTicker(api_key, access_token)

        def on_ticks(ws, ticks):
            mgr = InstrumentManager()
            for tick in ticks:
                token = tick.get('instrument_token')
                price = tick.get('last_price')
                symbol = mgr.get_symbol(token)
                if symbol and price:
                    import time
                    LIVE_TICK_DATA[symbol] = (price, time.time())
                    self.check_instant_target_hit(symbol, price)
                    self.check_instant_blow_off(symbol, price, tick)

        def on_connect(ws, response):
            log_system("Ticker connected successfully.")
            if self.tokens:
                ws.subscribe(self.tokens)
                ws.set_mode(ws.MODE_FULL, self.tokens)

        def on_close(ws, code, reason):
            log_system(f"Ticker connection closed: {reason}", level=30)

        def on_error(ws, code, reason):
            log_system(f"Ticker error: {code} - {reason}", level=40)

        self.kws.on_ticks = on_ticks
        self.kws.on_connect = on_connect
        self.kws.on_close = on_close
        self.kws.on_error = on_error

        # Run in a separate thread (connect() is blocking)
        self.kws.connect(threaded=True)

    def subscribe_symbols(self, symbols: list):
        if not self.kws:
            log_system("Ticker not started. Cannot subscribe.")
            # Cache tokens for when it starts
            mgr = InstrumentManager()
            new_tokens = list(mgr.get_tokens(symbols).values())
            self.tokens = list(set(self.tokens + new_tokens))
            return

        mgr = InstrumentManager()
        token_map = mgr.get_tokens(symbols)
        new_tokens = [t for t in token_map.values() if t not in self.tokens]
        
        if new_tokens:
            self.kws.subscribe(new_tokens)
            self.kws.set_mode(self.kws.MODE_FULL, new_tokens)
            self.tokens.extend(new_tokens)
            log_system(f"Ticker subscribed to: {list(token_map.keys())}")

    def stop(self):
        if self.kws:
            self.kws.close()
            self.kws = None
            log_system("Ticker stopped.")

    def update_active_trades_cache(self, trades: list):
        cache = {}
        for t in trades:
            symbol = t['symbol']
            entry = t['entry_price']
            target = t['target_price']
            is_long = entry > t['stop_loss']
            
            trade_info = {
                'id': t['id'],
                'symbol': symbol,
                'target_price': target,
                'stop_loss': t['stop_loss'],
                'quantity': t['quantity'],
                'product_type': t.get('product_type', 'MIS'),
                'gtt_id': t.get('gtt_id'),
                'user_id': t.get('user_id'),
                'is_long': is_long,
                'atr': t.get('atr'),
                'yesterday_close': t.get('yesterday_close'),
                'average_price_20d': t.get('average_price_20d'),
                'average_volume_20d': t.get('average_volume_20d')
            }
            if symbol not in cache:
                cache[symbol] = []
            cache[symbol].append(trade_info)
        with self._lock:
            self.active_trades = cache
        log_system(f"Ticker: Active trades cache updated for symbols: {list(cache.keys())}")

    def check_instant_target_hit(self, symbol: str, price: float):
        with self._lock:
            trades_for_symbol = self.active_trades.get(symbol)
            if not trades_for_symbol:
                return

            remaining_trades = []
            trades_to_exit = []
            for trade in trades_for_symbol:
                target = trade.get('target_price')
                if not target:
                    remaining_trades.append(trade)
                    continue

                is_long = trade['is_long']
                target_hit = (is_long and price >= target) or (not is_long and price <= target)
                if target_hit:
                    trades_to_exit.append(trade)
                else:
                    remaining_trades.append(trade)

            # Evict triggered trades from cache BEFORE releasing lock to prevent double-trigger
            if remaining_trades:
                self.active_trades[symbol] = remaining_trades
            elif trades_to_exit:
                self.active_trades.pop(symbol, None)

        # Spawn exit threads OUTSIDE the lock so we don't block the WebSocket thread
        for trade in trades_to_exit:
            log_system(f"Ticker: Instant target hit detected for {symbol} at {price} (Target: {trade.get('target_price')}). Spawning exit thread.")
            threading.Thread(
                target=self._execute_target_exit,
                args=(trade, price),
                daemon=True
            ).start()

    def _execute_target_exit(self, trade, hit_price):
        import asyncio
        try:
            asyncio.run(self._execute_target_exit_async(trade, hit_price))
        except Exception as e:
            log_system(f"Ticker exit thread error: {e}", level=40)

    async def _execute_target_exit_async(self, trade, hit_price):
        symbol = trade['symbol']
        qty = trade['quantity']
        product = trade['product_type']
        user_id = trade['user_id']
        trade_id = trade['id']
        is_long = trade['is_long']
        gtt_id = trade.get('gtt_id')

        # 1. Initialize Zerodha
        zerodha = ZerodhaService()
        has_zerodha = zerodha.load_session()

        # 2. Delete GTT first to avoid double-fill on SL leg
        if gtt_id and product in ('CNC', 'NRML') and has_zerodha:
            try:
                from engine.price_checker import parse_gtt_id
                parsed_gtt = parse_gtt_id(gtt_id)
                if parsed_gtt:
                    zerodha.delete_gtt(parsed_gtt)
            except Exception as e:
                log_system(f"Ticker: Error deleting GTT #{gtt_id}: {e}", level=30)

        # 3. Fire immediate market exit order on Zerodha
        exit_ok = True
        exit_order_id = None
        if has_zerodha:
            tx_type = "SELL" if is_long else "BUY"
            exit_order_id, exit_error = zerodha.place_order(symbol, tx_type, qty, product_type=product)
            if not exit_order_id:
                exit_ok = False
                log_system(f"WebSocket instant target exit FAILED for {symbol}: {exit_error}", level=40)
                await self._send_telegram_alert(user_id,
                    f"🚨 WebSocket TARGET EXIT FAILED: {symbol}\n"
                    f"Price crossed target: ₹{hit_price}\n"
                    f"Error: {exit_error}\n"
                    f"MANUAL ACTION REQUIRED in Zerodha!")

        if not exit_ok:
            return

        # 4. Calculate P&L and update DB
        from database.operations import update_trade_execution, update_trade_fields, get_trade_by_id
        trade_db = get_trade_by_id(trade_id)
        buy_price = trade_db.get('buy_price') if trade_db else None
        if buy_price is None:
            buy_price = trade_db.get('entry_price') if trade_db else trade['target_price'] # fallback

        multiplier = 1 if is_long else -1
        pnl = (hit_price - buy_price) * qty * multiplier

        update_trade_execution(trade_id, 'CLOSED_TARGET', current_price=hit_price, pnl=pnl, order_id=exit_order_id)
        update_trade_fields(trade_id, {'exit_reason': 'TARGET', 'gtt_id': None})

        # 5. Telegram Alert
        await self._send_telegram_alert(user_id,
            f"🎯 TARGET HIT (WebSocket Instant): {symbol}\n"
            f"Exit Price: ₹{hit_price:.2f} | PnL: ₹{pnl:.2f}\n"
            f"Market exit order placed: #{exit_order_id}")

    async def _send_telegram_alert(self, chat_id, text):
        import os
        from telegram import Bot
        token = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        if token and chat_id:
            try:
                bot = Bot(token=token)
                await bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                log_system(f"Telegram alert failed from Ticker: {e}", level=30)

    def check_instant_blow_off(self, symbol: str, price: float, tick: dict):
        with self._lock:
            trades_for_symbol = self.active_trades.get(symbol)
            if not trades_for_symbol:
                return

            remaining_trades = []
            trades_to_blow_off = []
            
            for trade in trades_for_symbol:
                atr = trade.get('atr')
                y_close = trade.get('yesterday_close')
                avg_price = trade.get('average_price_20d')
                
                # If indicators aren't loaded yet, skip check
                if atr is None or y_close is None or avg_price is None:
                    remaining_trades.append(trade)
                    continue
                
                is_long = trade['is_long']
                
                # Condition 1: Gain above yesterday's close > 2 * ATR
                cond1 = False
                if is_long:
                    gain = price - y_close
                    if gain > 2.0 * atr:
                        cond1 = True
                else:
                    gain = y_close - price
                    if gain > 2.0 * atr:
                        cond1 = True
                
                # Option to require heavy volume for Condition 1
                import os
                req_heavy_vol = os.environ.get("BLOW_OFF_REQUIRE_HEAVY_VOLUME", "False").lower() == "true"
                if cond1 and req_heavy_vol:
                    avg_vol = trade.get('average_volume_20d') or 0.0
                    tick_vol = tick.get('volume_traded') or tick.get('volume') or 0.0
                    if tick_vol <= avg_vol:
                        cond1 = False # don't trigger if volume is not heavy
                
                # Condition 2: Current Price > 20d Avg + 3.5 * ATR (for long)
                cond2 = False
                if is_long:
                    if price > avg_price + (3.5 * atr):
                        cond2 = True
                else:
                    if price < avg_price - (3.5 * atr):
                        cond2 = True
                
                if cond1 or cond2:
                    trades_to_blow_off.append((trade, price))
                else:
                    remaining_trades.append(trade)

            # Evict triggered trades from cache BEFORE releasing lock
            if remaining_trades:
                self.active_trades[symbol] = remaining_trades
            elif trades_to_blow_off:
                self.active_trades.pop(symbol, None)

        # Spawn blow-off exit threads OUTSIDE the lock
        for trade, hit_price in trades_to_blow_off:
            log_system(f"Ticker: Blow-off condition hit for {symbol} at {hit_price} (ATR: {trade.get('atr')}, Yesterday Close: {trade.get('yesterday_close')}). Spawning execution thread.")
            threading.Thread(
                target=self._execute_blow_off,
                args=(trade, hit_price),
                daemon=True
            ).start()

    def _execute_blow_off(self, trade, hit_price):
        import asyncio
        try:
            asyncio.run(self._execute_blow_off_async(trade, hit_price))
        except Exception as e:
            log_system(f"Ticker blow-off execution error: {e}", level=40)

    async def _execute_blow_off_async(self, trade, hit_price):
        symbol = trade['symbol']
        qty = trade['quantity']
        product = trade['product_type']
        user_id = trade['user_id']
        trade_id = trade['id']
        is_long = trade['is_long']
        gtt_id = trade.get('gtt_id')

        zerodha = ZerodhaService()
        has_zerodha = zerodha.load_session()
        
        # Read blow-off config
        import os
        action = os.environ.get("BLOW_OFF_ACTION", "EXIT").upper()
        
        if action == "EXIT" or qty <= 1:
            # 1. Cancel GTT SL if CNC/NRML
            if gtt_id and product in ('CNC', 'NRML') and has_zerodha:
                try:
                    from engine.price_checker import parse_gtt_id
                    parsed_gtt = parse_gtt_id(gtt_id)
                    if parsed_gtt:
                        zerodha.delete_gtt(parsed_gtt)
                except Exception as e:
                    log_system(f"Ticker: Error deleting GTT #{gtt_id}: {e}", level=30)
            
            # 2. Fire immediate market exit order on Zerodha
            exit_ok = True
            exit_order_id = None
            if has_zerodha:
                tx_type = "SELL" if is_long else "BUY"
                exit_order_id, exit_error = zerodha.place_order(symbol, tx_type, qty, product_type=product)
                if not exit_order_id:
                    exit_ok = False
                    log_system(f"WebSocket blow-off exit FAILED for {symbol}: {exit_error}", level=40)
                    await self._send_telegram_alert(user_id,
                        f"🚨 Blow-Off EXIT FAILED: {symbol}\n"
                        f"Price: ₹{hit_price}\n"
                        f"Error: {exit_error}\n"
                        f"MANUAL ACTION REQUIRED in Zerodha!")
            
            if not exit_ok:
                return

            # 3. Calculate P&L and update DB
            from database.operations import update_trade_execution, update_trade_fields, get_trade_by_id
            trade_db = get_trade_by_id(trade_id)
            buy_price = trade_db.get('buy_price') if trade_db else None
            if buy_price is None:
                buy_price = trade_db.get('entry_price') if trade_db else hit_price
            
            multiplier = 1 if is_long else -1
            pnl = (hit_price - buy_price) * qty * multiplier
            
            update_trade_execution(trade_id, 'CLOSED_TARGET', current_price=hit_price, pnl=pnl, order_id=exit_order_id)
            update_trade_fields(trade_id, {'exit_reason': 'BLOW_OFF_EXIT', 'gtt_id': None})
            
            await self._send_telegram_alert(user_id,
                f"💥 BLOW-OFF EXIT TRIGGERED: {symbol}\n"
                f"Exit Price: ₹{hit_price:.2f} | PnL: ₹{pnl:.2f}\n"
                f"Position closed completely.")

        elif action == "PARTIAL":
            partial_pct = int(os.environ.get("BLOW_OFF_PARTIAL_PCT", "50"))
            sell_qty = max(1, int(qty * (partial_pct / 100.0)))
            remaining_qty = qty - sell_qty
            
            if remaining_qty <= 0:
                await self._execute_blow_off_async(trade, hit_price)
                return

            # 1. Fire partial market exit order on Zerodha
            exit_ok = True
            exit_order_id = None
            if has_zerodha:
                tx_type = "SELL" if is_long else "BUY"
                exit_order_id, exit_error = zerodha.place_order(symbol, tx_type, sell_qty, product_type=product)
                if not exit_order_id:
                    exit_ok = False
                    log_system(f"WebSocket blow-off partial sell FAILED for {symbol}: {exit_error}", level=40)
                    await self._send_telegram_alert(user_id,
                        f"🚨 Blow-Off Partial Sell FAILED: {symbol}\n"
                        f"Qty: {sell_qty}\n"
                        f"Error: {exit_error}\n"
                        f"MANUAL ACTION REQUIRED!")
            
            if not exit_ok:
                return

            # 2. Tighten stop to entry price
            from database.operations import get_trade_by_id, update_trade_fields
            trade_db = get_trade_by_id(trade_id)
            entry_price = trade_db.get('entry_price')
            new_stop = entry_price

            # Update DB remaining quantity and stop loss
            update_trade_fields(trade_id, {
                'quantity': remaining_qty,
                'stop_loss': new_stop,
            })

            # 3. Cancel or reduce GTT SL
            if gtt_id and product in ('CNC', 'NRML') and has_zerodha:
                _, gtt_err = zerodha.modify_gtt_sl(gtt_id, symbol, remaining_qty, new_stop, hit_price, product_type=product)
                if gtt_err:
                    log_system(f"GTT modify FAILED for {symbol} after partial blow-off: {gtt_err}", level=40)
                    await self._send_telegram_alert(user_id,
                        f"⚠️ GTT SL UPDATE FAILED after partial blow-off: {symbol}\n"
                        f"New SL: ₹{new_stop:.2f} | Remaining Qty: {remaining_qty}\n"
                        f"Error: {gtt_err}\n"
                        f"Please update GTT manually in Kite.")
                else:
                    await self._send_telegram_alert(user_id,
                        f"🛡️ GTT SL Modified (Partial Blow-off): {symbol}\n"
                        f"New SL: ₹{new_stop:.2f} | Qty: {remaining_qty}")
            
            await self._send_telegram_alert(user_id,
                f"💥 BLOW-OFF PARTIAL TRIGGERED: {symbol}\n"
                f"Sold Qty: {sell_qty} at ₹{hit_price:.2f}\n"
                f"Remaining Qty: {remaining_qty} | Stop loss tightened to Entry (₹{new_stop:.2f})")
