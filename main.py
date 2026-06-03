import os
import time
import logging
import threading
from flask import Flask
from dotenv import load_dotenv
from telegram.error import NetworkError
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from bot.handlers import (start_command, trade_command, list_command, cancel_command,
                          history_command, stats_command, pnl_command,
                          account_command, risk_command,
                          status_command, activetrades_command, forcesync_command, resetdaily_command,
                          login_command, settoken_command, handle_document, myip_command)
from database.operations import init_db
from engine.price_checker import check_trades
from services.zerodha_ticker import ZerodhaTicker
from utils.logger import log_system

load_dotenv()

# Flask for Health Check and Redirect URL
app = Flask(__name__)

@app.route('/health')
def health():
    return "OK", 200

@app.route('/login')
def login_redirect():
    return "Login Successful! Please return to your Telegram bot and use /settoken with the request_token from the URL bar.", 200

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

def build_and_run():
    """Build the Telegram application and start polling."""
    token = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log_system("TELEGRAM_TOKEN or TELEGRAM_BOT_TOKEN not found in environment variables", level=logging.ERROR)
        raise RuntimeError("Missing Telegram token")

    # Build Telegram Bot with extended timeouts to prevent Render crashes
    application = (
        ApplicationBuilder()
        .token(token)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .connect_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    # Register Command Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("trade", trade_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("pnl", pnl_command))
    application.add_handler(CommandHandler("account", account_command))
    application.add_handler(CommandHandler("risk", risk_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("activetrades", activetrades_command))
    application.add_handler(CommandHandler("forcesync", forcesync_command))
    application.add_handler(CommandHandler("resetdaily", resetdaily_command))
    application.add_handler(CommandHandler("login", login_command))
    application.add_handler(CommandHandler("settoken", settoken_command))
    application.add_handler(CommandHandler("myip", myip_command))
    
    # Register File Upload Handler (CSV)
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Set up Background Price Checker Job
    job_queue = application.job_queue
    if job_queue:
        default_chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        job_queue.run_repeating(check_trades, interval=15, first=10, data={'chat_id': default_chat_id})
        
        # Start Zerodha WebSocket Ticker
        ticker = ZerodhaTicker()
        ticker.start()
        log_system("Zerodha Ticker background thread started.")
    else:
        log_system("JobQueue not initialized. Make sure python-telegram-bot[job-queue] is installed.", level=logging.ERROR)

    log_system("TradeBot started successfully.")
    application.run_polling()

def main():
    # Initialize DB
    init_db()

    # Start Flask Server in background (before retry loop so health checks pass on Render)
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Retry loop — handles transient DNS failures on Render cold start
    max_retries = 10
    for attempt in range(1, max_retries + 1):
        try:
            log_system(f"Starting bot (attempt {attempt}/{max_retries})...")
            build_and_run()
            break  # clean exit
        except NetworkError as e:
            wait = min(2 ** attempt, 60)  # exponential backoff, capped at 60s
            log_system(f"NetworkError on startup (attempt {attempt}): {e}. Retrying in {wait}s...", level=logging.WARNING)
            time.sleep(wait)
        except RuntimeError:
            break  # Config error, no point retrying
        except Exception as e:
            wait = min(2 ** attempt, 60)
            log_system(f"Unexpected error on startup (attempt {attempt}): {e}. Retrying in {wait}s...", level=logging.ERROR)
            time.sleep(wait)
    else:
        log_system("Max retries reached. Bot failed to start.", level=logging.ERROR)

if __name__ == '__main__':
    main()
