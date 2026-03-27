import os
import datetime
from database.operations import get_session, Trade
from database.models import SystemConfig, Trade
from sqlalchemy import func

class DailyLimitManager:
    @staticmethod
    def get_sl_hits_today():
        with next(get_session()) as session:
            today = datetime.date.today()
            hits = session.query(func.count(Trade.id)).filter(
                Trade.status == 'CLOSED_SL',
                func.date(Trade.closed_at) == today
            ).scalar() or 0
            return hits

    @staticmethod
    def is_trading_halted():
        # Check current SL hits
        max_sl = int(os.environ.get("MAX_DAILY_SL", 3))
        if DailyLimitManager.get_sl_hits_today() >= max_sl:
            return True
        
        # Check explicit flag in system_config
        with next(get_session()) as session:
            config = session.query(SystemConfig).filter(SystemConfig.key == 'trading_halted_today').first()
            if config and config.value == 'True':
                # Check if it was set today
                if config.updated_at.date() == datetime.date.today():
                    return True
        return False

    @staticmethod
    def set_trading_halt(halt: bool):
        value = 'True' if halt else 'False'
        with next(get_session()) as session:
            config = session.query(SystemConfig).filter(SystemConfig.key == 'trading_halted_today').first()
            if not config:
                config = SystemConfig(key='trading_halted_today', value=value)
                session.add(config)
            else:
                config.value = value
                config.updated_at = datetime.datetime.utcnow()
            session.commit()
            
    @staticmethod
    def can_create_trade():
        # Check time limits (no trades after market close - 30 mins)
        market_close_str = os.environ.get("MARKET_CLOSE_TIME", "15:30")
        try:
            hour, minute = map(int, market_close_str.split(':'))
            # Calculate 30 mins before
            total_mins = hour * 60 + minute - 30
            h = total_mins // 60
            m = total_mins % 60
            limit_time = datetime.time(h, m)
        except:
            limit_time = datetime.time(15, 0)

        if datetime.now().time() >= limit_time:
            return False, "Market close approaching (no new trades 30 mins before close)."

        if DailyLimitManager.is_trading_halted():
            return False, f"Trading halted for the day (Daily SL limit hit)."
            
        return True, ""
