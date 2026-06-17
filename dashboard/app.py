import streamlit as st
import pandas as pd
import sqlite3
import os
import json
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import sys

# Add parent directory to sys.path to allow importing from database, services, etc.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.zerodha_service import ZerodhaService
from database.operations import add_manually_placed_trade, update_trade_execution, update_trade_fields
from engine.price_checker import parse_gtt_id
from services.market_data import get_live_price

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

# Sidebar: System Status
st.sidebar.header("⚙️ System Status")
active_count = len(trades[trades['status'] == 'ACTIVE'])
placed_count = len(trades[trades['status'] == 'ORDER_PLACED'])
pending_count = len(trades[trades['status'] == 'PENDING'])
st.sidebar.metric("Active Trades", active_count)
st.sidebar.metric("Placed Orders", placed_count)
st.sidebar.metric("Pending Trades", pending_count)

# Sidebar: Zerodha Authentication Panel
st.sidebar.markdown("---")
st.sidebar.header("🔑 Zerodha Authentication")

zerodha = ZerodhaService()
is_session_active = False
try:
    is_session_active = zerodha.load_session()
except Exception as e:
    st.sidebar.error(f"Error checking session: {e}")

if is_session_active:
    st.sidebar.success("Zerodha Session Active ✅")
else:
    st.sidebar.error("Zerodha Session Inactive ❌")

login_url = zerodha.get_login_url()
st.sidebar.markdown(f"[🔗 **Click here to Login on Kite**]({login_url})")

token_input = st.sidebar.text_input("Manual Request Token", type="password", help="Paste request_token from URL here if redirect doesn't auto-authenticate")
if st.sidebar.button("Authenticate Zerodha"):
    if token_input:
        success, msg = zerodha.set_access_token(token_input)
        if success:
            st.sidebar.success("Authenticated successfully!")
            st.rerun()
        else:
            st.sidebar.error(f"Authentication failed: {msg}")
    else:
        st.sidebar.warning("Please enter a token first.")

# Sidebar: Manually Create Active Trade
st.sidebar.markdown("---")
with st.sidebar.expander("➕ Import manual position (Trail only)"):
    st.markdown("Use this to tell the bot to monitor and trail a trade you manually bought on the Kite app.")
    sym_input = st.text_input("Symbol (e.g. INFY)", "").upper().strip()
    entry_input = st.number_input("Entry Price (Avg Buy Price)", min_value=0.0, step=0.05)
    sl_input = st.number_input("Stop Loss Price", min_value=0.0, step=0.05)
    target_input = st.number_input("Target Price (optional, default 0.0)", min_value=0.0, step=0.05)
    qty_input = st.number_input("Quantity", min_value=1, step=1)
    prod_input = st.selectbox("Product Type", ["MIS", "CNC", "NRML"])
    
    if st.button("Add Active Trade"):
        if sym_input and entry_input > 0 and sl_input > 0 and qty_input > 0:
            import time
            dummy_order_id = f"manual-dashboard-{int(time.time())}"
            if entry_input == sl_input:
                st.error("Entry and SL cannot be equal.")
            else:
                try:
                    trade_id = add_manually_placed_trade(
                        user_id=12345678,
                        symbol=sym_input,
                        entry_price=entry_input,
                        stop_loss=sl_input,
                        target_price=target_input,
                        quantity=qty_input,
                        product_type=prod_input,
                        order_id=dummy_order_id
                    )
                    st.success(f"Trade #{trade_id} ({sym_input}) created successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to create trade: {e}")
        else:
            st.warning("Please fill in Symbol, Entry Price, Stop Loss, and Quantity.")

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

# --- Middle Row: Active Trades / Placed Orders ---
st.header("🚀 Active Trades & Placed Orders")
active_or_placed_df = trades[trades['status'].isin(['ACTIVE', 'ORDER_PLACED'])].copy()

if not active_or_placed_df.empty:
    cols = st.columns([1, 2, 2, 2, 2, 2, 2, 3])
    cols[0].write("**ID**")
    cols[1].write("**Symbol**")
    cols[2].write("**Status**")
    cols[3].write("**Product**")
    cols[4].write("**Entry**")
    cols[5].write("**Stop Loss**")
    cols[6].write("**Qty**")
    cols[7].write("**Actions**")
    st.markdown("<hr style='margin:0; padding:0; margin-bottom:10px;'>", unsafe_allow_html=True)
    
    for _, row in active_or_placed_df.iterrows():
        trade_id = int(row['id'])
        cols = st.columns([1, 2, 2, 2, 2, 2, 2, 3])
        cols[0].write(str(trade_id))
        cols[1].write(f"**{row['symbol']}**")
        cols[2].write(row['status'])
        cols[3].write(row['product_type'])
        cols[4].write(f"₹{row['entry_price']:.2f}")
        cols[5].write(f"₹{row['stop_loss']:.2f}")
        cols[6].write(str(int(row['quantity'])))
        
        # Exit action
        if cols[7].button("❌ Force Exit", key=f"exit_{trade_id}"):
            with st.spinner(f"Exiting position for {row['symbol']}..."):
                zerodha = ZerodhaService()
                has_z = zerodha.load_session()
                exit_ok = True
                exit_order_id = None
                
                if has_z:
                    # 1. Delete GTT if CNC/NRML
                    gtt_id_val = row.get('gtt_id')
                    gtt_id = parse_gtt_id(gtt_id_val)
                    if gtt_id and row['product_type'] in ('CNC', 'NRML'):
                        zerodha.delete_gtt(gtt_id)
                        
                    # 2. Place market exit order
                    is_long = row['entry_price'] > row['stop_loss']
                    tx_type = "SELL" if is_long else "BUY"
                    exit_order_id, exit_err = zerodha.place_order(
                        row['symbol'], tx_type, int(row['quantity']), product_type=row['product_type']
                    )
                    if not exit_order_id:
                        exit_ok = False
                        st.error(f"Failed to place exit order on Zerodha: {exit_err}")
                else:
                    st.warning("No Zerodha session. Closed locally in database only (Paper trade).")
                    
                if exit_ok:
                    current_price = get_live_price(row['symbol']) or row['entry_price']
                    is_long = row['entry_price'] > row['stop_loss']
                    multiplier = 1 if is_long else -1
                    pnl = (current_price - row['entry_price']) * int(row['quantity']) * multiplier
                    
                    new_status = 'CLOSED_TARGET' if pnl >= 0 else 'CLOSED_SL'
                    update_trade_execution(trade_id, new_status, current_price=current_price, pnl=pnl, order_id=exit_order_id)
                    update_trade_fields(trade_id, {'exit_reason': 'manual_dashboard_exit'})
                    st.success(f"Trade #{trade_id} ({row['symbol']}) closed successfully!")
                    st.rerun()
else:
    st.info("No active trades or placed orders at the moment.")

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
