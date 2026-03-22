import logging
import os
from logging.handlers import RotatingFileHandler

LOGS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOGS_DIR, 'trade_bot.log')

# Setup logger
logger = logging.getLogger("TradeBot")
logger.setLevel(logging.INFO)

# Formatter
formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')

# File handler (rotates file when it reaches 5MB, keeps 3 backups)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5*1024*1024, backupCount=3)
file_handler.setFormatter(formatter)

# Console handler
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

def log_trade_event(event, trade_id="N/A", symbol="N/A", price="N/A", status="N/A", message="", level=logging.INFO):
    """
    Logs trading events in a structured format:
    timestamp (added by formatter) | level | TradeID:X | Symbol:Y | Event:Z | Price:W | Status:S | Message
    """
    if isinstance(price, float):
        price = f"{price:.2f}"
        
    msg = f"TradeID:{trade_id} | Symbol:{symbol} | Event:{event} | Price:{price} | Status:{status} | {message}"
    logger.log(level, msg)
    
def log_system(message, level=logging.INFO):
    """General system logs (errors, bootups, polling checks)"""
    logger.log(level, message)
