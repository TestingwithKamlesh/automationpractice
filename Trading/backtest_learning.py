import os
import json
import pandas as pd
import numpy as np
import ta
import ccxt

# Get the directory where the script is located
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
config_path = os.path.join(BASE_DIR, "config.json")

# Load configuration parameters safely
with open(config_path, "r") as f:
    config = json.load(f)

def calculate_indicators(df):
    df = df.copy()
    df['ema20'] = ta.trend.ema_indicator(df['close'], window=config['ema_micro_fast'])
    df['ema50'] = ta.trend.ema_indicator(df['close'], window=config['ema_micro_slow'])
    df['ema800'] = ta.trend.ema_indicator(df['close'], window=config['ema_macro'])
    df['ema800_slope'] = df['ema800'] - df['ema800'].shift(6)
    df['rsi14'] = ta.momentum.rsi(df['close'], window=14)
    df['atr14'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    df['swing_low_8'] = df['low'].rolling(8).min()
    df['swing_high_8'] = df['high'].rolling(8).max()
    return df

def run_backtest(df):
    capital = config['initial_capital']
    position = None
    trades = []
    
    rsi_long_limit = config['rsi_hook_threshold_long']
    rsi_short_limit = config['rsi_hook_threshold_short']
    vol_mult = config['volume_multiplier']
    rr = config['risk_reward_ratio']

    print("\n==========================================")
    print(" EXECUTING DUAL-DIRECTION PRECISION ENGINE")
    print("==========================================")

    for i in range(config['ema_macro'], len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]
        close = row['close']
        high = row['high']
        low = row['low']
        atr = row['atr14']

        # Manage Open Position
        if position is not None:
            if position['type'] == 'LONG':
                # Breakeven check
                if not position['is_breakeven'] and high >= position['entry'] + (1.1 * position['sl_dist']):
                    position['sl'] = position['entry']
                    position['is_breakeven'] = True

                # Exit conditions for LONG
                if row['rsi14'] >= 70 and close > position['entry']:
                    pnl = (close - position['entry']) * position['size']
                    capital += pnl
                    trades.append({'type': 'WIN', 'pnl': pnl})
                    position = None
                    continue
                elif high >= position['tp']:
                    pnl = (position['tp'] - position['entry']) * position['size']
                    capital += pnl
                    trades.append({'type': 'WIN', 'pnl': pnl})
                    position = None
                    continue
                elif low <= position['sl']:
                    pnl = (position['sl'] - position['entry']) * position['size']
                    capital += pnl
                    trade_type = 'BE' if position['is_breakeven'] and pnl >= 0 else 'LOSS'
                    trades.append({'type': trade_type, 'pnl': pnl})
                    position = None
                    continue

            elif position['type'] == 'SHORT':
                # Breakeven check for short
                if not position['is_breakeven'] and low <= position['entry'] - (1.1 * position['sl_dist']):
                    position['sl'] = position['entry']
                    position['is_breakeven'] = True

                # Exit conditions for SHORT
                if row['rsi14'] <= 30 and close < position['entry']:
                    pnl = (position['entry'] - close) * position['size']
                    capital += pnl
                    trades.append({'type': 'WIN', 'pnl': pnl})
                    position = None
                    continue
                elif low <= position['tp']:
                    pnl = (position['entry'] - position['tp']) * position['size']
                    capital += pnl
                    trades.append({'type': 'WIN', 'pnl': pnl})
                    position = None
                    continue
                elif high >= position['sl']:
                    pnl = (position['entry'] - position['sl']) * position['size']
                    capital += pnl
                    trade_type = 'BE' if position['is_breakeven'] and pnl >= 0 else 'LOSS'
                    trades.append({'type': trade_type, 'pnl': pnl})
                    position = None
                    continue

        # Signal Generation using Config Values
        # --- LONG SETUP ---
        macro_uptrend = (close > row['ema800']) and (row['ema800_slope'] > 0)
        micro_uptrend = (row['ema20'] > row['ema50'])
        price_above_ema20 = close > row['ema20']
        rsi_hook_long = (prev_row['rsi14'] <= rsi_long_limit) and (row['rsi14'] > rsi_long_limit)
        volume_spike = pd.notna(row['vol_sma20']) and (row['volume'] >= vol_mult * row['vol_sma20'])
        bullish_candle = close > row['open']

        long_signal = macro_uptrend and micro_uptrend and price_above_ema20 and rsi_hook_long and volume_spike and bullish_candle

        # --- SHORT SETUP ---
        macro_downtrend = (close < row['ema800']) and (row['ema800_slope'] < 0)
        micro_downtrend = (row['ema20'] < row['ema50'])
        price_below_ema20 = close < row['ema20']
        rsi_hook_short = (prev_row['rsi14'] >= rsi_short_limit) and (row['rsi14'] < rsi_short_limit)
        bearish_candle = close < row['open']

        short_signal = macro_downtrend and micro_downtrend and price_below_ema20 and rsi_hook_short and volume_spike and bearish_candle

        # Execute Long Entry
        if long_signal and position is None:
            entry_price = close
            sl_price = row['swing_low_8'] - (0.5 * atr)
            sl_dist = entry_price - sl_price
            
            if sl_dist <= 0 or sl_dist > (3.0 * atr):
                sl_dist = 1.8 * atr
                sl_price = entry_price - sl_dist

            tp_price = entry_price + (sl_dist * rr)
            risk_amount = capital * config['risk_per_trade']
            position_size = risk_amount / sl_dist if sl_dist > 0 else 0

            position = {
                'type': 'LONG',
                'entry': entry_price, 'sl': sl_price, 'tp': tp_price,
                'sl_dist': sl_dist, 'size': position_size, 'is_breakeven': False
            }

        # Execute Short Entry
        elif short_signal and position is None:
            entry_price = close
            sl_price = row['swing_high_8'] + (0.5 * atr)
            sl_dist = sl_price - entry_price
            
            if sl_dist <= 0 or sl_dist > (3.0 * atr):
                sl_dist = 1.8 * atr
                sl_price = entry_price + sl_dist

            tp_price = entry_price - (sl_dist * rr)
            risk_amount = capital * config['risk_per_trade']
            position_size = risk_amount / sl_dist if sl_dist > 0 else 0

            position = {
                'type': 'SHORT',
                'entry': entry_price, 'sl': sl_price, 'tp': tp_price,
                'sl_dist': sl_dist, 'size': position_size, 'is_breakeven': False
            }

    total_trades = len(trades)
    wins = [t for t in trades if t['type'] == 'WIN']
    breakevens = [t for t in trades if t['type'] == 'BE']
    losses = [t for t in trades if t['type'] == 'LOSS']
    
    decisive_trades = len(wins) + len(losses)
    win_rate = (len(wins) / decisive_trades * 100) if decisive_trades > 0 else 0.0
    total_pnl = sum([t['pnl'] for t in trades])

    print("\n------------------------------------------")
    print(" BACKTEST RESULTS ")
    print("------------------------------------------")
    print(f"Total Signals Executed  : {total_trades}")
    print(f"Winning Trades          : {len(wins)}")
    print(f"Breakeven Trades        : {len(breakevens)}")
    print(f"Losing Trades           : {len(losses)}")
    print(f"Decisive Win Rate       : {win_rate:.2f}%")
    print(f"Net Portfolio Return    : ${total_pnl:.2f} (Ending Equity: ${capital:.2f})")
    print("------------------------------------------\n")

def fetch_binance_data():
    cache_file = "data_cache_BTC_USDT_15m.csv"
    if os.path.exists(cache_file):
        df = pd.read_csv(cache_file)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df
    
    exchange = ccxt.binance({'enableRateLimit': True})
    since = exchange.parse8601('2024-01-01T00:00:00Z')
    all_ohlcv = []

    while True:
        ohlcv = exchange.fetch_ohlcv(config['symbol'], config['timeframe'], since=since, limit=1000)
        if not ohlcv: break
        all_ohlcv.extend(ohlcv)
        since = ohlcv[-1][0] + 1
        if len(ohlcv) < 1000: break

    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.to_csv(cache_file, index=False)
    return df

if __name__ == "__main__":
    df_raw = fetch_binance_data()
    df_processed = calculate_indicators(df_raw)
    run_backtest(df_processed)