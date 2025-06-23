import ccxt
import pandas as pd
import numpy as np
import ta

def run_backtest():
    # 1) Parámetros de la estrategia
    M               = 5
    STOP_LOSS_PTS   = 150
    TAKE_PROFIT_PTS = 600
    TRAILING_PTS    = 70
    MIN_BARS_OPEN   = 1
    ENTRY_START     = 6   # 06:00 UTC
    ENTRY_END       = 22  # 22:00 UTC
    ATR_LEN         = 14
    ATR_THRESH_FAC  = 0.9
    ADX_LEN         = 4
    ADX_THRESHOLD   = 20
    XMF_LEN         = 12
    OB_UP_LEN       = 35
    OB_DN_LEN       = 140
    PVI_LEN         = 90
    NVI_LEN         = 90
    RSI_LEN         = 20
    STOCH_K, STOCH_D = 14, 1

    # 2) Carga de datos desde Kraken Futures (perpetual XBT/USD 4h)
    exchange = ccxt.krakenfutures()
    symbol   = 'PI_XBTUSD'
    timeframe= '4h'
    since    = exchange.parse8601('2020-01-01T00:00:00Z')
    limit    = 5000

    # Fetch OHLCV
    ohlc = exchange.fetch_ohlcv(symbol, timeframe, since, limit)
    df = pd.DataFrame(ohlc, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    # 3) Cálculo de indicadores
    df['pvi']  = ta.volume.positive_volume_index(df['close'], df['volume'])
    df['pvim'] = df['pvi'].ewm(span=M).mean()
    df['oscp'] = (df['pvi'] - df['pvim']) * 100 / (df['pvim'].rolling(PVI_LEN).max() - df['pvim'].rolling(PVI_LEN).min())

    df['nvi']  = ta.volume.negative_volume_index(df['close'], df['volume'])
    df['nvim'] = df['nvi'].ewm(span=M).mean()
    df['azul'] = (df['nvi'] - df['nvim']) * 100 / (df['nvim'].rolling(NVI_LEN).max() - df['nvim'].rolling(NVI_LEN).min())

    df['xmf']     = ta.volume.money_flow_index(df['high'], df['low'], df['close'], df['volume'], window=XMF_LEN)
    bb_up         = ta.volatility.BollingerBands(df['close'], window=OB_UP_LEN)
    bb_dn         = ta.volatility.BollingerBands(df['close'], window=OB_DN_LEN)
    df['OB1']     = (bb_up.bollinger_hband() + bb_dn.bollinger_lband()) / 2
    df['OB2']     = bb_up.bollinger_hband() - bb_dn.bollinger_lband()
    df['BollOsc'] = (df['close'] - df['OB1']) * 100 / df['OB2']

    df['xrsi'] = ta.momentum.rsi(df['close'], window=RSI_LEN)
    stoch     = ta.momentum.StochasticOscillator(df['high'], df['low'], df['close'], window=STOCH_K, smooth_window=STOCH_D)
    df['STOC'] = stoch.stoch()

    df['marron'] = (df['xrsi'] + df['xmf'] + df['BollOsc'] + df['STOC']/3) / 2
    df['verde']  = df['marron'] + df['oscp']
    df['media']  = df['marron'].ewm(span=M).mean()

    df['atr']    = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=ATR_LEN)
    df['atr_th'] = ATR_THRESH_FAC * df['atr'].rolling(50).mean()
    df['adx']    = ta.trend.adx(df['high'], df['low'], df['close'], window=ADX_LEN)

    # 4) Señales de entrada/salida
    df['hour']      = df.index.hour
    df['trade_ok']  = ((df['hour'] >= ENTRY_START) & (df['hour'] <= ENTRY_END))
    df['long_cond'] = (
        (df['verde'] > df['marron']) &
        (df['marron'] > df['media']) &
        (df['azul'] > 0.01) &
        (df['atr'] > df['atr_th']) &
        (df['adx'] > ADX_THRESHOLD) &
        df['trade_ok']
    )
    df['short_cond']= (
        (df['verde'] < df['marron']) &
        (df['marron'] < df['media']) &
        (df['azul'] < 0.96) &
        (df['atr'] > df['atr_th']) &
        (df['adx'] > ADX_THRESHOLD) &
        df['trade_ok']
    )

    # 5) Backtest
    position = 0
    entry_price = 0.0
    bars = 0

    tot_trades = win_trades = loss_trades = 0
    tot_profit = tot_loss = 0.0

    for i in range(1, len(df)):
        price = df['close'].iat[i]
        pnl = None

        if position == 1:
            bars += 1
            if price <= entry_price - STOP_LOSS_PTS or price >= entry_price + TAKE_PROFIT_PTS:
                pnl, position, bars = price - entry_price, 0, 0
            elif price >= entry_price + TRAILING_PTS:
                entry_price = price - TRAILING_PTS
            elif df['short_cond'].iat[i] and bars >= MIN_BARS_OPEN:
                pnl, position, bars = price - entry_price, 0, 0

        elif position == -1:
            bars += 1
            if price >= entry_price + STOP_LOSS_PTS or price <= entry_price - TAKE_PROFIT_PTS:
                pnl, position, bars = entry_price - price, 0, 0
            elif price <= entry_price - TRAILING_PTS:
                entry_price = price + TRAILING_PTS
            elif df['long_cond'].iat[i] and bars >= MIN_BARS_OPEN:
                pnl, position, bars = entry_price - price, 0, 0

        else:
            if df['long_cond'].iat[i]:
                position, entry_price, bars = 1, price, 0
            elif df['short_cond'].iat[i]:
                position, entry_price, bars = -1, price, 0

        if pnl is not None:
            tot_trades += 1
            if pnl > 0:
                win_trades += 1
                tot_profit += pnl
            else:
                loss_trades += 1
                tot_loss += abs(pnl)

    win_rate = win_trades / tot_trades * 100 if tot_trades else 0
    profit_factor = tot_profit / tot_loss if tot_loss else np.inf
    rr_ratio = (tot_profit/win_trades)/(tot_loss/loss_trades) if win_trades and loss_trades else 0

    print(f"Trades: {tot_trades}, WinRate: {win_rate:.2f}%")
    print(f"ProfitFactor: {profit_factor:.2f}, R:R: {rr_ratio:.2f}")

if __name__ == "__main__":
    run_backtest()
