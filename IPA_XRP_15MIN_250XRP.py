import pandas as pd
import matplotlib.pyplot as plt

# Cargar datos preparados
df = pd.read_csv("xrp_15min_heiken_macd.csv", parse_dates=["datetime"])

# Parámetros
capital = 10000
xrp_size = 250  # Número fijo de XRP por operación
tp_pct = 0.01     # Take Profit 1%
sl_pct = 0.005    # Stop Loss 0.5%
trailing_pct = 0.004  # Trailing Stop 0.40%

# Variables de trading
position = 0
entry_price = 0
capital_log = [capital]
trade_log = []

for i in range(1, len(df)):
    row = df.iloc[i]
    previous = df.iloc[i - 1]

    if position == 0:
        if previous['long_signal']:
            entry_price = row['close']
            sl = entry_price * (1 - sl_pct)
            tp = entry_price * (1 + tp_pct)
            trail = entry_price * (1 - trailing_pct)
            position = 1
            direction = 'LONG'
        elif previous['short_signal']:
            entry_price = row['close']
            sl = entry_price * (1 + sl_pct)
            tp = entry_price * (1 - tp_pct)
            trail = entry_price * (1 + trailing_pct)
            position = -1
            direction = 'SHORT'
    else:
        price = row['close']
        exit = False

        if position == 1:
            trail = max(trail, price * (1 - trailing_pct))
            if price <= sl or price >= tp or price <= trail:
                pnl = (price - entry_price) * xrp_size
                capital += pnl
                exit = True
        elif position == -1:
            trail = min(trail, price * (1 + trailing_pct))
            if price >= sl or price <= tp or price >= trail:
                pnl = (entry_price - price) * xrp_size
                capital += pnl
                exit = True

        if exit:
            trade_log.append({
                "datetime": row["datetime"],
                "direction": direction,
                "entry": entry_price,
                "exit": price,
                "capital": capital
            })
            position = 0
            entry_price = 0

    capital_log.append(capital)

# Resultados
df_result = pd.DataFrame(trade_log)
df_result.to_csv("IPA_250XRP_trades_log.csv", index=False)

# Gráfico de evolución
plt.figure(figsize=(10, 5))
plt.plot(capital_log, label="Capital (€)")
plt.title("IPA | 250 XRP fijos por operación (TP, SL, Trailing)")
plt.xlabel("Velas")
plt.ylabel("Capital")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()

# Resumen
ganadoras = (df_result["exit"] > df_result["entry"]).sum()
perdedoras = (df_result["exit"] < df_result["entry"]).sum()
print(f"Total operaciones: {len(df_result)}")
print(f"Ganadoras: {ganadoras}")
print(f"Perdedoras: {perdedoras}")
print(f"Capital final: {round(capital, 2)} €")
print(f"Beneficio total: {round(capital - 10000, 2)} €")
print(f"Ratio de acierto: {round(ganadoras / len(df_result), 2) if len(df_result) > 0 else 0}")
