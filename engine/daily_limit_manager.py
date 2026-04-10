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
        # updated_at is stored as UTC — compare against IST today (same as zerodha_service.py)
        ist_offset = datetime.timedelta(hours=5, minutes=30)
        ist_today = (datetime.datetime.utcnow() + ist_offset).date()

        with next(get_session()) as session:
            config = session.query(SystemConfig).filter(SystemConfig.key == 'trading_halted_today').first()
            if config and config.value == 'True':
                stored_ist = config.updated_at + ist_offset
                if stored_ist.date() == ist_today:
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
    def can_create_trade(check_market_hours: bool = True):
        """Check whether a new trade can be created.
        
        Args:
            check_market_hours: If True (default), blocks trades 30 mins before
                                market close. Set to False to allow manual/paper
                                trades outside live market hours.
        """
        if check_market_hours:
            # Use IST explicitly for market-hours check
            ist_offset = datetime.timedelta(hours=5, minutes=30)
            ist_now = datetime.datetime.utcnow() + ist_offset

            market_close_str = os.environ.get("MARKET_CLOSE_TIME", "15:30")
            try:
                hour, minute = map(int, market_close_str.split(':'))
                # Block trades 30 mins before market close
                total_mins = hour * 60 + minute - 30
                h = total_mins // 60
                m = total_mins % 60
                limit_time = datetime.time(h, m)
            except:
                limit_time = datetime.time(15, 0)

            if ist_now.time() >= limit_time:
                return False, "Market close approaching – no new trades in the last 30 mins before close (15:00 IST). Use /resetdaily to override."

        if DailyLimitManager.is_trading_halted():
            max_sl = int(os.environ.get("MAX_DAILY_SL", 3))
            return False, f"Daily SL limit hit ({max_sl} stop-losses today). Trading halted for the day. Use /resetdaily to reset."

        return True, ""
