# Precision Pullback Strategy (BTC/USDT 15m)

## Objective
Capture high-probability trend continuations on Binance 15-minute BTC/USDT data with a target win rate $\ge 60\%$.

## Entry Rules (Long Only)
1. **Macro Trend Filter**: Price must be above EMA 800, and the EMA 800 slope must be positive over a 6-bar lookback.
2. **Micro Trend Filter**: EMA 20 must be above EMA 50.
3. **Momentum Reclaim**: Current close must be above EMA 20.
4. **Pullback Hook**: RSI(14) must dip to $\le 40$ on the previous candle and cross back above 40 on the current candle.
5. **Volume Expansion**: Volume must be $\ge 1.2\times$ the 20-period Volume SMA.
6. **Candle Confirmation**: Current candle must be bullish (Close > Open).

## Exit & Risk Management Rules
- **Initial Risk**: Fixed 1% of total portfolio capital per trade.
- **Stop Loss (SL)**: Set at the 8-bar swing low minus $0.5\times$ ATR(14).
- **Take Profit (TP)**: Set at $1.2\times$ the risk distance (Reward-to-Risk ratio = 1.2).
- **Breakeven Management**: Automatically move the stop loss to entry once price reaches $+1.1\times$ risk.
- **Overbought Lock-in**: Force close if RSI(14) $\ge 70$ while in profit.