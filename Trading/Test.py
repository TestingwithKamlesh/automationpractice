import os
import json
import time
import datetime
import pandas as pd
import numpy as np
import ta
import MetaTrader5 as mt5

# ==============================================================================
# 1. CONFIGURATION LOADING
# ==============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(BASE_DIR, "config.json")

if not os.path.exists(config_path):
    raise FileNotFoundError(f"Configuration file not found at: {config_path}")

with open(config_path, "r") as f:
    config = json.load(f)

SYMBOL = config.get("symbol", "BTCUSD#")
MAGIC = config.get("magic_number", 992026)

TIMEFRAME_MAP = {
    "1m": mt5.TIMEFRAME_M1,
    "5m": mt5.TIMEFRAME_M5,
    "15m": mt5.TIMEFRAME_M15,
    "1h": mt5.TIMEFRAME_H1,
}
MT5_TIMEFRAME = TIMEFRAME_MAP.get(config.get("timeframe", "15m"), mt5.TIMEFRAME_M15)


# ==============================================================================
# 2. MT5 INITIALIZATION
# ==============================================================================
def initialize_mt5():
    """Directly hooks into the already running and logged-in XM MT5 terminal."""
    if not mt5.initialize():
        print(f"[ERROR] MT5 Initialization failed: {mt5.last_error()}")
        print("Please ensure XM MetaTrader 5 is running on your desktop.")
        return False

    account_info = mt5.account_info()
    if account_info is None:
        print("[ERROR] Could not fetch account info. Make sure MT5 is logged into an account.")
        return False

    if not mt5.symbol_select(SYMBOL, True):
        print(f"[ERROR] Symbol '{SYMBOL}' is not available or hidden in Market Watch.")
        return False

    print(f"[SUCCESS] Attached to live XM MT5 Account: {account_info.login} ({account_info.name})")
    print(f"[INFO] Trading Symbol: {SYMBOL} | Timeframe: {config.get('timeframe', '15m')}")
    return True


# ==============================================================================
# 3. DATA FETCHING & INDICATORS
# ==============================================================================
def fetch_live_data(count=300):
    """Fetches price history directly from the open MT5 terminal."""
    rates = mt5.copy_rates_from_pos(SYMBOL, MT5_TIMEFRAME, 0, count)
    if rates is None or len(rates) == 0:
        print("[ERROR] Failed to retrieve price data from MT5.")
        return None

    df = pd.DataFrame(rates)
    df["timestamp"] = pd.to_datetime(df["time"], unit="s")
    df.rename(
        columns={
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "tick_volume": "volume",
        },
        inplace=True,
    )
    return df


def calculate_indicators(df):
    """Calculates strategy indicators."""
    df = df.copy()

    # 1. 15m EMAs
    df["ema9"] = ta.trend.ema_indicator(
        df["close"], window=config.get("ema_length", 9)
    )
    df["ema200"] = ta.trend.ema_indicator(
        df["close"], window=config.get("ema_filter", 200)
    )

    # 2. MACD (12, 26, 9)
    macd = ta.trend.MACD(
        close=df["close"],
        window_fast=config.get("macd_fast", 12),
        window_slow=config.get("macd_slow", 26),
        window_sign=config.get("macd_signal", 9),
    )
    df["macd_line"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    # 3. ADX Filter (14-period)
    adx_indicator = ta.trend.ADXIndicator(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=config.get("adx_period", 14),
    )
    df["adx14"] = adx_indicator.adx()

    # 4. ATR & Volume Filter
    df["atr14"] = ta.volatility.average_true_range(
        df["high"], df["low"], df["close"], window=14
    )
    df["vol_sma20"] = df["volume"].rolling(20).mean()

    # 5. Candle Body Ratio
    candle_range = df["high"] - df["low"]
    df["body_ratio"] = np.where(
        candle_range > 0, (df["close"] - df["open"]).abs() / candle_range, 0
    )

    return df


# ==============================================================================
# 4. POSITION & RISK MANAGEMENT
# ==============================================================================
def check_open_positions():
    """Checks for active positions associated with this bot's Magic Number."""
    positions = mt5.positions_get(symbol=SYMBOL)
    if positions is None:
        return 0
    bot_positions = [p for p in positions if p.magic == MAGIC]
    return len(bot_positions)


def calculate_position_size(entry_price, sl_price):
    """Calculates volume based on account balance and risk percentage."""
    account_info = mt5.account_info()
    symbol_info = mt5.symbol_info(SYMBOL)

    if account_info is None or symbol_info is None:
        return 0.01

    balance = account_info.balance
    risk_per_trade = config.get("risk_per_trade", 0.01)
    risk_amount = balance * risk_per_trade

    sl_distance = abs(entry_price - sl_price)
    if sl_distance == 0:
        return symbol_info.volume_min

    contract_size = symbol_info.trade_contract_size
    lot_step = symbol_info.volume_step
    min_lot = symbol_info.volume_min
    max_lot = symbol_info.volume_max

    raw_lots = risk_amount / (sl_distance * contract_size)
    lots = max(min_lot, min(max_lot, round(raw_lots / lot_step) * lot_step))

    return round(lots, 2)


# ==============================================================================
# 5. ORDER EXECUTION
# ==============================================================================
def send_order(order_type, price, sl, tp, volume):
    """Submits trade order to XM MT5."""
    symbol_info = mt5.symbol_info(SYMBOL)
    digits = symbol_info.digits

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": float(volume),
        "type": order_type,
        "price": round(float(price), digits),
        "sl": round(float(sl), digits),
        "tp": round(float(tp), digits),
        "deviation": 20,
        "magic": MAGIC,
        "comment": "XM_Live_15m_Strategy",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        request["type_filling"] = mt5.ORDER_FILLING_RETURN
        result = mt5.order_send(request)

    if result.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"\n[!!! ORDER EXECUTED !!!] {SYMBOL} | Deal ID: {result.order} | Lots: {volume}\n")
    else:
        print(f"\n[ORDER REJECTED] Retcode: {result.retcode} | Comment: {result.comment}\n")


# ==============================================================================
# 6. CORE EVALUATION WITH DETAILED LOGGING
# ==============================================================================
def evaluate_and_trade():
    """Scans completed candle (-2) and outputs real-time strategy diagnostic state."""
    df_raw = fetch_live_data(300)
    if df_raw is None or len(df_raw) < 200:
        print("[WARN] Insufficient candle history fetched. Retrying...")
        return

    df = calculate_indicators(df_raw)

    # Index -2 represents the last closed candle
    row = df.iloc[-2]
    prev_row = df.iloc[-3]
    candle_time = row["timestamp"].strftime("%Y-%m-%d %H:%M:%S")

    # Real-time Tick Price
    tick = mt5.symbol_info_tick(SYMBOL)
    current_bid = tick.bid if tick else row["close"]
    now_str = datetime.datetime.now().strftime("%H:%M:%S")

    # Position check
    if check_open_positions() > 0:
        print(f"[{now_str}] Position active for {SYMBOL}. Waiting for SL/TP exit...")
        return

    close = row["close"]
    open_price = row["open"]
    high = row["high"]
    low = row["low"]
    atr = row["atr14"]

    adx_min = config.get("adx_min", 15.0)
    min_body = config.get("min_body_ratio", 0.50)
    rr = config.get("risk_reward_ratio", 2.0)

    # Condition Calculations
    macro_uptrend = close > row["ema200"]
    macro_downtrend = close < row["ema200"]
    valid_volume = pd.notna(row["vol_sma20"]) and (row["volume"] > row["vol_sma20"])

    adx_valid = (
        pd.notna(row["adx14"])
        and pd.notna(prev_row["adx14"])
        and (row["adx14"] >= adx_min)
        and (row["adx14"] > prev_row["adx14"])
    )

    strong_candle_quality = row["body_ratio"] >= min_body

    # LONG Indicators
    macd_above_zero = row["macd_line"] > 0
    macd_long_crossover = (prev_row["macd_line"] <= prev_row["macd_signal"]) and (
        row["macd_line"] > row["macd_signal"]
    )
    bullish_candle = close > open_price
    long_ema_bounce = (low <= row["ema9"]) and (close > row["ema9"])

    long_signal = (
        macro_uptrend
        and valid_volume
        and adx_valid
        and strong_candle_quality
        and macd_above_zero
        and macd_long_crossover
        and bullish_candle
        and long_ema_bounce
    )

    # SHORT Indicators
    macd_below_zero = row["macd_line"] < 0
    macd_short_crossover = (prev_row["macd_line"] >= prev_row["macd_signal"]) and (
        row["macd_line"] < row["macd_signal"]
    )
    bearish_candle = close < open_price
    short_ema_rejection = (high >= row["ema9"]) and (close < row["ema9"])

    short_signal = (
        macro_downtrend
        and valid_volume
        and adx_valid
        and strong_candle_quality
        and macd_below_zero
        and macd_short_crossover
        and bearish_candle
        and short_ema_rejection
    )

    # --- LIVE HEARTBEAT LOG ---
    trend_str = "BULLISH (Above EMA200)" if macro_uptrend else "BEARISH (Below EMA200)"
    adx_val = f"{row['adx14']:.2f}" if pd.notna(row['adx14']) else "N/A"
    
    print(
        f"[{now_str}] Bid: {current_bid} | Closed Candle: {candle_time} | Trend: {trend_str} | ADX: {adx_val} | Signal: Searching..."
    )

    # Trigger Executions
    if long_signal:
        entry_price = tick.ask
        sl_price = low - (0.5 * atr)
        sl_dist = entry_price - sl_price

        if sl_dist > 0:
            tp_price = entry_price + (sl_dist * rr)
            volume = calculate_position_size(entry_price, sl_price)
            print(f"[SIGNAL MATCH] BUY Signal Triggered! Entry: {entry_price} | SL: {sl_price} | TP: {tp_price}")
            send_order(mt5.ORDER_TYPE_BUY, entry_price, sl_price, tp_price, volume)

    elif short_signal:
        entry_price = tick.bid
        sl_price = high + (0.5 * atr)
        sl_dist = sl_price - entry_price

        if sl_dist > 0:
            tp_price = entry_price - (sl_dist * rr)
            volume = calculate_position_size(entry_price, sl_price)
            print(f"[SIGNAL MATCH] SELL Signal Triggered! Entry: {entry_price} | SL: {sl_price} | TP: {tp_price}")
            send_order(mt5.ORDER_TYPE_SELL, entry_price, sl_price, tp_price, volume)


# ==============================================================================
# 7. EXECUTION LOOP
# ==============================================================================
def run_live_bot():
    if not initialize_mt5():
        return

    print("\n==========================================")
    print("      XM LIVE AUTOMATED TRADING ENGINE     ")
    print("==========================================")
    print("Press CTRL+C to stop the bot.\n")

    try:
        while True:
            evaluate_and_trade()
            time.sleep(15)  # Polls every 15 seconds
    except KeyboardInterrupt:
        print("\n[TERMINATED] Live bot stopped by user.")
        mt5.shutdown()


if __name__ == "__main__":
    run_live_bot()