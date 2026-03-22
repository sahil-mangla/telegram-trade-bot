import os
import logging
import threading
from flask import Flask
from telegram.ext import ApplicationBuilder, CommandHandler
from bot.handlers import (start_command, trade_command, list_command, cancel_command,
                          history_command, stats_command, pnl_command,
                          account_command, risk_command)
from database.operations import init_db
from engine.price_checker import check_trades
from utils.logger import log_system

# Initialize Flask server for Web Service Health Checks
web_app = Flask(__name__)

@web_app.route('/health')
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    log_system(f"Starting Flask health server on port {port}...")
    # use_reloader=False is critical when running inside a thread
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)

def main():
    # Initialize the SQLite database
    init_db()

    # Get the token from environment variables
    # For local testing, you can rename `.env` to load automatically with python-dotenv
    # Or export it directly in your shell: `export TELEGRAM_BOT_TOKEN="your_token"`
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "your_bot_token_here")
    
    if token == "your_bot_token_here" or not token:
        log_system("TELEGRAM_BOT_TOKEN is not set. Please set it as an environment variable or in the .env file.", level=logging.ERROR)
        
    try:
        application = ApplicationBuilder().token(token).build()
    except Exception as e:
        log_system(f"Failed to build Application: {e}", level=logging.ERROR)
        return

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("trade", trade_command))
    application.add_handler(CommandHandler("list", list_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("pnl", pnl_command))
    application.add_handler(CommandHandler("account", account_command))
    application.add_handler(CommandHandler("risk", risk_command))

    # Set up the background job
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_trades, interval=60, first=10)
    else:
        log_system("JobQueue not initialized. Make sure APScheduler is installed.", level=logging.ERROR)

    # Start Flask in a background daemon thread so it doesn't block the Bot
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start the Bot
    log_system("Bot is starting via long polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
