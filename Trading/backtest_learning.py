import os
import pandas as pd
import numpy as np
import ta
import ccxt

# ==========================================
# 1. INDICATOR CALCULATIONS
# ==========================================
def calculate_indicators(df):
    df = df.copy()

    # Moving Averages & Trend Slopes
    df['ema20'] = ta.trend.ema_indicator(df['close'], window=20)
    df['ema50'] = ta.trend.ema_indicator(df['close'], window=50)
    df['ema800'] = ta.trend.ema_indicator(df['close'], window=800)  # ~1H EMA 200
    
    # Macro Trend Slope (6-bar lookback)
    df['ema800_slope'] = df['ema800'] - df['ema800'].shift(6)

    # Momentum & Volatility
    df['rsi14'] = ta.momentum.rsi(df['close'], window=14)
    df['atr14'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)

    # Volume & Swing Structure
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    df['swing_low_8'] = df['low'].rolling(8).min()

    return df

# ==========================================
# 2. HIGH-CONVICTION PRECISION ENGINE
# ==========================================
def run_backtest(df, risk_reward=1.2):
    capital = 10000.0
    position = None
    trades = []

    print("\n==========================================")
    print(" EXECUTING 60%+ PRECISION PULLBACK ENGINE ")
    print("==========================================")

    for i in range(800, len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1]
        close = row['close']
        high = row['high']
        low = row['low']
        atr = row['atr14']

        # ----------------------------------
        # A. MANAGE OPEN POSITION
        # ----------------------------------
        if position is not None:
            # 1. Move Stop to Breakeven once price reaches +1.1x Risk
            if not position['is_breakeven'] and high >= position['entry'] + (1.1 * position['sl_dist']):
                position['sl'] = position['entry']
                position['is_breakeven'] = True

            # 2. RSI Overbought Exit Lock-in (>= 70)
            if row['rsi14'] >= 70 and close > position['entry']:
                pnl = (close - position['entry']) * position['size']
                capital += pnl
                trades.append({'type': 'WIN', 'reason': 'RSI OB Exit', 'pnl': pnl})
                position = None
                continue

            # 3. Target Hit
            elif high >= position['tp']:
                pnl = (position['tp'] - position['entry']) * position['size']
                capital += pnl
                trades.append({'type': 'WIN', 'reason': 'TP Hit', 'pnl': pnl})
                position = None
                continue

            # 4. Stop Loss / Breakeven Hit
            elif low <= position['sl']:
                pnl = (position['sl'] - position['entry']) * position['size']
                capital += pnl
                trade_type = 'BE' if position['is_breakeven'] and pnl >= 0 else 'LOSS'
                trades.append({'type': trade_type, 'reason': 'SL/BE Hit', 'pnl': pnl})
                position = None
                continue

        # ----------------------------------
        # B. PRECISION FILTERED SIGNAL
        # ----------------------------------
        # 1. Macro & Micro Structure Alignment
        macro_uptrend = (close > row['ema800']) and (row['ema800_slope'] > 0)
        micro_uptrend = (row['ema20'] > row['ema50'])
        
        # 2. Short-Term Momentum: Price must be actively reclaiming EMA20
        price_above_ema20 = close > row['ema20']

        # 3. Clean Pullback Hook (RSI dips <= 40 and crosses back above 40)
        rsi_hook = (prev_row['rsi14'] <= 40) and (row['rsi14'] > 40)
        
        # 4. Volume Expansion (Volume >= 1.2x SMA20)
        volume_spike = pd.notna(row['vol_sma20']) and (row['volume'] >= 1.2 * row['vol_sma20'])
        
        # 5. Bullish Candle Body Confirmation
        bullish_candle = close > row['open']

        signal = macro_uptrend and micro_uptrend and price_above_ema20 and rsi_hook and volume_spike and bullish_candle

        if signal and position is None:
            entry_price = close
            
            # SL = 8-bar Swing Low minus 0.5x ATR
            sl_price = row['swing_low_8'] - (0.5 * atr)
            sl_dist = entry_price - sl_price
            
            # Sanity safeguards on Stop Loss distance
            if sl_dist <= 0 or sl_dist > (3.0 * atr):
                sl_dist = 1.8 * atr
                sl_price = entry_price - sl_dist

            tp_dist = sl_dist * risk_reward
            tp_price = entry_price + tp_dist
            
            # Risk 1% capital per trade
            risk_amount = capital * 0.01
            position_size = risk_amount / sl_dist if sl_dist > 0 else 0

            position = {
                'entry': entry_price,
                'sl': sl_price,
                'tp': tp_price,
                'sl_dist': sl_dist,
                'size': position_size,
                'is_breakeven': False
            }

    # Statistics Calculation
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
    print(f"Winning Trades (TP/RSI) : {len(wins)}")
    print(f"Breakeven Trades (BE)   : {len(breakevens)}")
    print(f"Losing Trades           : {len(losses)}")
    print(f"Decisive Win Rate       : {win_rate:.2f}%")
    print(f"Net Portfolio Return    : ${total_pnl:.2f} (Ending Equity: ${capital:.2f})")
    print("------------------------------------------\n")

    return win_rate

# ==========================================
# 3. CCXT EXCHANGE DATA LOADER (PAST 2 YEARS)
# ==========================================
def fetch_binance_data(symbol='BTC/USDT', timeframe='15m', limit_years=2):
    cache_file = "data_cache_BTC_USDT_15m.csv"
    
    # If cache exists, load it directly to save API requests
    if os.path.exists(cache_file):
        print(f"[DATA] Loading historical exchange data from cached file: {cache_file}...")
        df = pd.read_csv(cache_file)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        return df

    print(f"[DATA] Fetching past {limit_years} years of {symbol} {timeframe} data from Binance via CCXT...")
    exchange = ccxt.binance({'enableRateLimit': True})
    
    # Calculate start timestamp (2 years ago from today)
    since = exchange.parse8601('2024-01-01T00:00:00Z')
    all_ohlcv = []

    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not ohlcv:
                break
            all_ohlcv.extend(ohlcv)
            since = ohlcv[-1][0] + 1  # Move forward to the next batch
            print(f"[DATA] Fetched up to {exchange.iso8601(ohlcv[-1][0])}...")
            if len(ohlcv) < 1000:
                break
        except Exception as e:
            print(f"[ERROR] CCXT Fetch Error: {e}")
            break

    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    # Save to local cache so subsequent runs are instant
    df.to_csv(cache_file, index=False)
    print(f"[DATA] Saved {len(df)} candles to {cache_file}")
    return df

if __name__ == "__main__":
    df_raw = fetch_binance_data()
    df_processed = calculate_indicators(df_raw)
    run_backtest(df_processed)