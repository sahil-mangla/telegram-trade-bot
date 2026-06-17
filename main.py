import os
import time
import logging
import threading
import asyncio
from flask import Flask, request, render_template_string
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

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>TradeBot Zerodha Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #0e1117;
            color: #e0e6ed;
            margin: 0;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
        }
        .card {
            background-color: #1a1c23;
            border-radius: 12px;
            padding: 30px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
            max-width: 450px;
            width: 100%;
            text-align: center;
        }
        h1 {
            color: #ff4b4b;
            margin-bottom: 10px;
            font-size: 24px;
        }
        p {
            color: #a0aec0;
            font-size: 15px;
            line-height: 1.5;
            margin-bottom: 25px;
        }
        .btn {
            display: inline-block;
            background-color: #ff4b4b;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 8px;
            font-weight: bold;
            font-size: 16px;
            transition: background-color 0.2s;
            border: none;
            cursor: pointer;
            width: 100%;
            box-sizing: border-box;
        }
        .btn:hover {
            background-color: #e03e3e;
        }
        .status-badge {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: bold;
            margin-top: 15px;
        }
        .active {
            background-color: rgba(46, 204, 113, 0.2);
            color: #2ecc71;
        }
        .inactive {
            background-color: rgba(231, 76, 60, 0.2);
            color: #e74c3c;
        }
        .success-msg {
            color: #2ecc71;
            font-weight: bold;
            margin-top: 15px;
            background-color: rgba(46, 204, 113, 0.1);
            padding: 10px;
            border-radius: 6px;
        }
        .error-msg {
            color: #e74c3c;
            font-weight: bold;
            margin-top: 15px;
            background-color: rgba(231, 76, 60, 0.1);
            padding: 10px;
            border-radius: 6px;
        }
        .form-group {
            margin-top: 25px;
            text-align: left;
            border-top: 1px solid #2d3748;
            padding-top: 20px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            font-size: 14px;
            color: #a0aec0;
        }
        input[type="text"] {
            width: 100%;
            padding: 10px;
            border-radius: 6px;
            border: 1px solid #4a5568;
            background-color: #2d3748;
            color: white;
            box-sizing: border-box;
            margin-bottom: 12px;
        }
    </style>
</head>
<body>
    <div class="card">
        <h1>📈 TradeBot Zerodha Login</h1>
        
        {% if success_msg %}
            <div class="success-msg">{{ success_msg }}</div>
            <p style="margin-top: 10px; font-size: 13px;">The background price checker service will automatically pick up this session and start/restart the ticker loop within 15 seconds.</p>
        {% elif error_msg %}
            <div class="error-msg">Error: {{ error_msg }}</div>
        {% endif %}

        <p>Zerodha requires daily login verification to refresh the API token. Authenticate your account below to activate autonomous trading and trailing.</p>

        {% if is_active %}
            <div class="status-badge active">Session Active ✅</div>
            <p style="margin-top: 10px; font-size: 13px;">(Saved today in IST)</p>
        {% else %}
            <div class="status-badge inactive">Session Expired/Inactive ❌</div>
        {% endif %}

        <div style="margin-top: 30px;">
            <a class="btn" href="{{ login_url }}" target="_blank">🔑 Click to Log In on Kite</a>
        </div>

        <div class="form-group">
            <form action="/login" method="GET">
                <label for="request_token">Or Paste Request Token manually:</label>
                <input type="text" id="request_token" name="request_token" placeholder="Paste request_token from URL bar here..." required>
                <button type="submit" class="btn" style="background-color: #4a5568;">Submit Token</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

@app.route('/health')
def health():
    return "OK", 200

@app.route('/')
@app.route('/login')
def login_redirect():
    from services.zerodha_service import ZerodhaService
    zerodha = ZerodhaService()
    
    # Check if request_token or token is passed in query parameters
    request_token = request.args.get('request_token') or request.args.get('token')
    success_msg = None
    error_msg = None
    
    if request_token:
        # Authenticate and set access token
        success, msg = zerodha.set_access_token(request_token)
        if success:
            success_msg = "Successfully Authenticated with Zerodha!"
        else:
            error_msg = msg
            
    is_active = zerodha.load_session()
    login_url = zerodha.get_login_url()
    
    return render_template_string(
        HTML_TEMPLATE,
        is_active=is_active,
        login_url=login_url,
        success_msg=success_msg,
        error_msg=error_msg
    )

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

async def run_offline_loop():
    log_system("=== RUNNING IN TELEGRAM OFFLINE MODE ===")
    
    # Start Zerodha WebSocket Ticker
    ticker = ZerodhaTicker()
    ticker.start()
    log_system("Zerodha Ticker background thread started.")
    
    # Run the price checker loop every 15 seconds
    while True:
        try:
            await check_trades(None)
        except Exception as e:
            log_system(f"Error in offline price checker: {e}", level=logging.ERROR)
        await asyncio.sleep(15)

def main():
    # Initialize DB
    init_db()

    # Start Flask Server in background (before retry loop so health checks pass on Render)
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Check if Telegram Offline mode is requested
    if os.environ.get("TELEGRAM_OFFLINE", "False").lower() == "true":
        log_system("TELEGRAM_OFFLINE=True configured. Bypassing Telegram and entering Offline Mode directly.")
        asyncio.run(run_offline_loop())
        return

    # Retry loop — handles transient DNS failures on Render cold start
    max_retries = 10
    telegram_started = False
    for attempt in range(1, max_retries + 1):
        try:
            log_system(f"Starting bot (attempt {attempt}/{max_retries})...")
            build_and_run()
            telegram_started = True
            break  # clean exit
        except NetworkError as e:
            wait = min(2 ** attempt, 60)  # exponential backoff, capped at 60s
            log_system(f"NetworkError on startup (attempt {attempt}): {e}. Retrying in {wait}s...", level=logging.WARNING)
            time.sleep(wait)
        except RuntimeError as e:
            log_system(f"RuntimeError on startup: {e}. Falling back to Telegram Offline Mode.", level=logging.WARNING)
            break
        except Exception as e:
            wait = min(2 ** attempt, 60)
            log_system(f"Unexpected error on startup (attempt {attempt}): {e}. Retrying in {wait}s...", level=logging.ERROR)
            time.sleep(wait)
            
    if not telegram_started:
        log_system("Telegram Bot failed to start. Falling back to TELEGRAM OFFLINE MODE to preserve trailing/monitoring functionality...", level=logging.WARNING)
        asyncio.run(run_offline_loop())

if __name__ == '__main__':
    main()

