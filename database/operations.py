import datetime
from sqlalchemy import func
from database.db import get_session, init_db  # Re-export init_db for main.py
from database.models import Trade, UserSettings, TradeLog

def to_dict(obj):
    """Safely converts an SQLAlchemy model to a dictionary"""
    if not obj:
        return None
    d = obj.__dict__.copy()
    d.pop('_sa_instance_state', None)
    return d

def get_user_settings(user_id: int):
    with next(get_session()) as session:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not settings:
            settings = UserSettings(user_id=user_id)
            session.add(settings)
            session.commit()
            session.refresh(settings)
        return to_dict(settings)

def update_user_settings(user_id: int, field: str, value):
    with next(get_session()) as session:
        settings = session.query(UserSettings).filter(UserSettings.user_id == user_id).first()
        if not settings:
            settings = UserSettings(user_id=user_id)
            session.add(settings)
        setattr(settings, field, value)
        session.commit()

def get_daily_metrics(user_id: int):
    import datetime as _dt
    ist_offset = _dt.timedelta(hours=5, minutes=30)
    today = (_dt.datetime.utcnow() + ist_offset).date()

    with next(get_session()) as session:
        # trades taken today (exclude CANCELLED — they shouldn't count toward the daily limit)
        trades_today = session.query(func.count(Trade.id)).filter(
            Trade.user_id == user_id,
            Trade.status != 'CANCELLED',
            func.date(Trade.created_at) == today
        ).scalar() or 0
        
        # pnl today
        pnl_today = session.query(func.sum(Trade.pnl)).filter(
            Trade.user_id == user_id,
            Trade.status.in_(['CLOSED_SL', 'CLOSED_TARGET']),
            func.date(Trade.closed_at) == today
        ).scalar() or 0.0
        
        return {'trades_today': trades_today, 'pnl_today': float(pnl_today)}

def add_trade(user_id, symbol, entry_price, stop_loss, target_price, quantity):
    with next(get_session()) as session:
        trade = Trade(
            user_id=user_id,
            symbol=symbol.upper(),
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            quantity=quantity,
            status='PENDING'
        )
        session.add(trade)
        session.commit()
        session.refresh(trade)
        
        log = TradeLog(trade_id=trade.id, old_status='NONE', new_status='PENDING', message='Trade created')
        session.add(log)
        session.commit()
        return trade.id

def get_user_trades(user_id):
    with next(get_session()) as session:
        trades = session.query(Trade).filter(Trade.user_id == user_id).order_by(Trade.id.desc()).all()
        return [to_dict(t) for t in trades]

def get_trade_by_id(trade_id):
    with next(get_session()) as session:
        trade = session.query(Trade).filter(Trade.id == trade_id).first()
        return to_dict(trade)

def update_trade_execution(trade_id, status, current_price=None, pnl=None):
    with next(get_session()) as session:
        trade = session.query(Trade).filter(Trade.id == trade_id).first()
        if not trade: return
        
        old_status = trade.status
        trade.status = status
        
        if status == 'ACTIVE' and current_price is not None:
            trade.buy_price = current_price
        elif status in ['CLOSED_SL', 'CLOSED_TARGET'] and current_price is not None:
            trade.sell_price = current_price
            trade.pnl = pnl
            trade.closed_at = datetime.datetime.utcnow()
            
        log = TradeLog(trade_id=trade_id, old_status=old_status, new_status=status, message=f"Price: {current_price}, PnL: {pnl}")
        session.add(log)
        session.commit()

def update_trade_fields(trade_id: int, fields: dict):
    with next(get_session()) as session:
        trade = session.query(Trade).filter(Trade.id == trade_id).first()
        if not trade: return
        
        for key, value in fields.items():
            if hasattr(trade, key):
                setattr(trade, key, value)
        
        session.commit()

def get_trades_by_status(status_list: list):
    with next(get_session()) as session:
        trades = session.query(Trade).filter(Trade.status.in_(status_list)).all()
        return [to_dict(t) for t in trades]

def get_user_trade_history(user_id, limit=10):
    with next(get_session()) as session:
        trades = session.query(Trade).filter(
            Trade.user_id == user_id, 
            Trade.status.in_(['CLOSED_SL', 'CLOSED_TARGET'])
        ).order_by(Trade.id.desc()).limit(limit).all()
        return [to_dict(t) for t in trades]

def get_user_trade_stats(user_id):
    with next(get_session()) as session:
        trades = session.query(Trade).filter(Trade.user_id == user_id).all()
        stats = {'total_trades': len(trades), 'wins': 0, 'losses': 0, 'total_pnl': 0.0}
        
        for t in trades:
            if t.status in ['CLOSED_SL', 'CLOSED_TARGET']:
                stats['total_pnl'] += getattr(t, 'pnl', 0.0) or 0.0
                if (getattr(t, 'pnl', 0.0) or 0.0) > 0:
                    stats['wins'] += 1
                else:
                    stats['losses'] += 1
        
        total_closed = stats['wins'] + stats['losses']
        stats['win_rate'] = (stats['wins'] / total_closed * 100) if total_closed > 0 else 0.0
        return stats
