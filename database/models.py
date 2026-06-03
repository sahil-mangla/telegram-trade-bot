from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
import datetime
from database.db import Base

class UserSettings(Base):
    __tablename__ = "user_settings"
    
    user_id = Column(Integer, primary_key=True, index=True)
    account_size = Column(Float, default=0.0)
    risk_pct = Column(Float, default=1.0)
    max_daily_loss = Column(Float, default=0.0)

class Trade(Base):
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    symbol = Column(String, index=True)
    
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    initial_stop_loss = Column(Float, nullable=True)  # set once at creation, never trailed
    target_price = Column(Float, nullable=False)
    quantity = Column(Integer, default=1)
    
    status = Column(String, default="PENDING", index=True) 
    # PENDING, ORDER_PLACED, ACTIVE, CLOSED_SL, CLOSED_TARGET, CANCELLED
    
    entry_order_id = Column(String, nullable=True)
    exit_order_id = Column(String, nullable=True)
    product_type = Column(String(10), default="MIS", nullable=False)
    
    buy_price = Column(Float, nullable=True)
    sell_price = Column(Float, nullable=True)
    pnl = Column(Float, nullable=True)
    
    # New Autonomous Trading Columns
    initial_risk_amount = Column(Float, nullable=True)
    risk_per_share = Column(Float, nullable=True)
    max_mfe = Column(Float, nullable=True)
    max_mae = Column(Float, nullable=True)
    highest_price_reached = Column(Float, nullable=True)
    trailing_stop_events = Column(String, nullable=True) # JSON string
    exit_reason = Column(String(50), nullable=True)
    r_multiple_at_exit = Column(Float, nullable=True)
    signal_source = Column(String(50), default="manual")
    allocation_percentage = Column(Float, nullable=True)
    r_thresholds_crossed = Column(String, nullable=True) # JSON string
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)

class DailySummary(Base):
    __tablename__ = "daily_summary"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, unique=True, index=True)
    total_trades = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    sl_hits = Column(Integer, default=0)
    total_pnl = Column(Float, default=0.0)
    win_rate = Column(Float, default=0.0)
    avg_r_multiple = Column(Float, default=0.0)
    max_r_multiple_achieved = Column(Float, default=0.0)
    trades_reached_3r = Column(Integer, default=0)
    trades_reached_4r = Column(Integer, default=0)
    trades_reached_5r_plus = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

class SystemConfig(Base):
    __tablename__ = "system_config"
    
    key = Column(String(100), primary_key=True)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)

class TradeLog(Base):
    __tablename__ = "trade_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    trade_id = Column(Integer, ForeignKey("trades.id"))
    old_status = Column(String, nullable=False)
    new_status = Column(String, nullable=False)
    message = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    trade = relationship("Trade", backref="logs")
