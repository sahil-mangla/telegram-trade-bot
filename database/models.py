from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
import datetime
from database.db import Base

class UserSettings(Base):
    __tablename__ = "user_settings"
    
    user_id = Column(Integer, primary_key=True, index=True)
    account_size = Column(Float, default=10000.0)
    risk_pct = Column(Float, default=1.0)
    max_daily_loss = Column(Float, default=500.0)
    max_daily_trades = Column(Integer, default=5)

class Trade(Base):
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    symbol = Column(String, index=True)
    
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    target_price = Column(Float, nullable=False)
    quantity = Column(Integer, default=1)
    
    status = Column(String, default="PENDING", index=True) 
    # PENDING, ACTIVE, CLOSED_SL, CLOSED_TARGET, CANCELLED
    
    buy_price = Column(Float, nullable=True)
    sell_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)

class TradeLog(Base):
    __tablename__ = "trade_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"))
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    message = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    trade = relationship("Trade", backref="logs")
