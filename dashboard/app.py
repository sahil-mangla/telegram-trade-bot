import streamlit as st
import pandas as pd
import sqlite3
import os
import json
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta

# Configuration
DB_PATH = os.environ.get("DATABASE_URL", "sqlite:///trades.db").replace("sqlite:///", "")

def get_connection():
    return sqlite3.connect(DB_PATH)

def load_data():
    conn = get_connection()
    trades_df = pd.read_sql_query("SELECT * FROM trades", conn)
    summary_df = pd.read_sql_query("SELECT * FROM daily_summary", conn)
    conn.close()
    return trades_df, summary_df

st.set_page_config(page_title="TradeBot Analytics", layout="wide")
st.title("📈 TradeBot Autonomous Dashboard")

# Auto-refresh
# st.empty() # Placeholder for refresh logic if needed

trades, summaries = load_data()

# Sidebar Metrics
st.sidebar.header("System Status")
active_count = len(trades[trades['status'] == 'ACTIVE'])
pending_count = len(trades[trades['status'] == 'PENDING'])
st.sidebar.metric("Active Trades", active_count)
st.sidebar.metric("Pending Trades", pending_count)

# --- Top Row: Today's Stats ---
st.header("Today's Performance")
today = datetime.now().strftime('%Y-%m-%d')
today_trades = trades[trades['created_at'].str.contains(today, na=False)]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Trades Today", len(today_trades))
win_rate = (len(today_trades[today_trades['pnl'] > 0]) / len(today_trades[today_trades['pnl'].notnull()]) * 100) if len(today_trades[today_trades['pnl'].notnull()]) > 0 else 0
col2.metric("Win Rate", f"{win_rate:.1f}%")
total_pnl = today_trades['pnl'].sum()
col3.metric("Total PnL", f"${total_pnl:.2f}", delta=f"{total_pnl:.2f}")
max_r = today_trades['r_multiple_at_exit'].max()
col4.metric("Highest R Reached", f"{max_r:.1f}R" if not pd.isna(max_r) else "0R")

# --- Middle Row: Active Trades ---
st.header("🚀 Active Trades")
active_df = trades[trades['status'] == 'ACTIVE'].copy()
if not active_df.empty:
    # Basic Calculation for display
    # (Price - Entry) / (Entry - SL)
    st.table(active_df[['id', 'symbol', 'entry_price', 'stop_loss', 'quantity', 'signal_source']])
else:
    st.info("No active trades at the moment.")

# --- Bottom Row: Historical Performance ---
st.header("📊 Performance Analytics")
tab1, tab2, tab3 = st.tabs(["PnL Curve", "R-Multiple Distribution", "Trade History"])

with tab1:
    # Cumulative PnL
    closed_trades = trades[trades['status'].isin(['CLOSED_SL', 'CLOSED_TARGET'])].sort_values('closed_at')
    if not closed_trades.empty:
        closed_trades['cum_pnl'] = closed_trades['pnl'].cumsum()
        fig = px.line(closed_trades, x='closed_at', y='cum_pnl', title="Equity Curve (Cumulative PnL)")
        st.plotly_chart(fig, use_container_with_width=True)
    else:
        st.info("No closed trades to show curve.")

with tab2:
    # R-Multiple Histogram
    if not closed_trades.empty:
        fig = px.histogram(closed_trades, x='r_multiple_at_exit', nbins=20, title="R-Multiple Distribution")
        st.plotly_chart(fig, use_container_with_width=True)

with tab3:
    st.dataframe(closed_trades.sort_values('closed_at', ascending=False))

# --- Raw Data Export ---
if st.checkbox("Show Raw Data"):
    st.write(trades)
