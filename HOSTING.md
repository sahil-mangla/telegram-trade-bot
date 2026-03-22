# 🚀 DevOps Deployment Guide: Telegram Trade Bot

This guide explains how to deploy your Python Telegram Trading bot to **Render** as a 24/7 background worker.

---

## 📄 1. `requirements.txt`
Your application requires the following dependencies. These are already generated in your repo:
```text
python-telegram-bot==22.7
yfinance==1.2.0
APScheduler==3.11.2
```

---

## 💻 2. Start Command
The entry point for your application. This command will initialize the database, attach the background polling engine, and start the Telegram listeners.
**Start Command:**
```bash
python main.py
```

---

## 🔐 3. Environment Variables Setup
Your bot needs a Telegram API token securely injected into the environment. 
Do **NOT** commit your `.env` file to GitHub!
Instead, you will add this directly into the hosting platform's configuration:
- **Key:** `TELEGRAM_BOT_TOKEN`
- **Value:** `your_bot_token_from_botfather`

---

## ⚙️ 4. Deployment Steps (Render Background Worker)
We use Render's **Background Worker** instead of a Web Service because Web Services go to sleep after 15 minutes of inactivity on the free tier. A Background Worker runs 24/7.

1. Push your `trade_bot` code to a GitHub repository.
2. Go to the [Render Dashboard](https://dashboard.render.com/) and create an account.
3. Click **New +** and select **Background Worker**.
4. Connect your GitHub account and select your `trade_bot` repository.
5. Configure the service:
   - **Name:** `telegram-trade-bot`
   - **Environment:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python main.py`
6. Scroll down to **Advanced** and click **Add Environment Variable**. Add your `TELEGRAM_BOT_TOKEN`.
7. Select the **Free Tier** ($0/month).
8. Click **Create Background Worker**. Render will automatically build and start your bot!

---

## 📈 5. How to Monitor Logs
Because we integrated the Python `logging` module, you can monitor logs directly from the Render dashboard:
1. Go to your **Render Dashboard** and click on your `telegram-trade-bot` service.
2. On the main service page, you will see a live **Logs** terminal window.
3. You will see lines like: `2026-03-22 10:55:01 | INFO | TradeID:14 | Symbol:AAPL | Event:ENTRY_HIT ...`
4. *(Note: Render automatically captures `stdout`/`stderr`. Our `logger.py` uses `StreamHandler` to print directly to this console, alongside the file backups).*

---

## 🔄 6. How to Restart Automatically if Bot Crashes
By deploying as a **Render Background Worker**, high availability and auto-restarting are handled for you automatically!
- **Container Orchestration:** Render uses container orchestration (like Kubernetes) under the hood. If your Python process exits with a crash code (e.g., `Exit Code 1`), Render's control plane detects that the worker stopped and will automatically spin up a fresh container to restart `python main.py` within seconds.
- **Handling Exceptions:** Our bot's `python-telegram-bot` polling loop natively catches dropped network connections and retries. Your `price_checker.py` has `try/except` blocks around `yfinance` to prevent API timeouts from crashing the whole program.

---

### ⚠️ Important Note on Free-Tier Databases
Render Free tiers use ephemeral filesystems. If your bot restarts, `trades.db` might revert to whatever was in your GitHub repo.
**Solution:** If you intend to trade real money, you should eventually migrate `database/operations.py` from `sqlite3` to an external free PostgreSQL database (Render offers one for free) to ensure your data survives container restarts. For paper trading, SQLite on a worker is perfectly fine to start!
