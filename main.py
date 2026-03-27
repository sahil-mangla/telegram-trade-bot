import os
import logging
import threading
from flask import Flask
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters
from bot.handlers import (start_command, trade_command, list_command, cancel_command,
                          history_command, stats_command, pnl_command,
                          account_command, risk_command,
                          status_command, activetrades_command, forcesync_command, resetdaily_command,
                          login_command, settoken_command, handle_document)
from database.operations import init_db
from engine.price_checker import check_trades
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

def main():
    # Initialize DB
    init_db()
    
    token = os.environ.get("TELEGRAM_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log_system("TELEGRAM_TOKEN or TELEGRAM_BOT_TOKEN not found in environment variables", level=logging.ERROR)
        return

    # Build Telegram Bot
    application = ApplicationBuilder().token(token).build()

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
    
    # Register File Upload Handler (CSV)
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Set up Background Price Checker Job
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_trades, interval=60, first=10)
    else:
        log_system("JobQueue not initialized. Make sure python-telegram-bot[job-queue] is installed.", level=logging.ERROR)

    # Start Flask Server in Background
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start Telegram Bot Polling
    log_system("TradeBot started successfully.")
    application.run_polling()

if __name__ == '__main__':
    main()
