import numpy as np
import pandas as pd
import pytest

from smc.backtest import BTConfig, run_signal_backtest
from smc.squeeze import SqueezeParams, generate_squeeze_signals, linreg, squeeze_indicator
from tests.test_smc import ohlc, random_ohlc


def test_linreg_matches_polyfit():
    y = pd.Series(np.random.default_rng(1).normal(size=60).cumsum())
    got = linreg(y, 20)
    for t in (19, 33, 59):
        coef = np.polyfit(np.arange(20), y.iloc[t - 19:t + 1], 1)
        assert got.iloc[t] == pytest.approx(np.polyval(coef, 19))
    assert got.iloc[:19].isna().all()


def test_indicator_reference_values():
    """Comparación con un cálculo directo, vela a vela, de la fórmula de LazyBear."""
    df = random_ohlc(300, seed=3)
    p = SqueezeParams()
    f = squeeze_indicator(df, p)
    t, L = 250, 20
    w = df.iloc[t - L + 1:t + 1]
    c = w["close"].to_numpy()
    basis, dev = c.mean(), 1.5 * c.std()          # bug original: multKC para las BB
    tr = np.maximum(df["high"] - df["low"],
                    np.maximum((df["high"] - df["close"].shift()).abs(),
                               (df["low"] - df["close"].shift()).abs())).iloc[t - L + 1:t + 1]
    lkc, ukc = basis - 1.5 * tr.mean(), basis + 1.5 * tr.mean()
    assert f["sqz_on"].iloc[t] == ((basis - dev > lkc) and (basis + dev < ukc))
    # val en t: regresión de las últimas 20 diferencias
    diffs = []
    for k in range(t - L + 1, t + 1):
        ww = df.iloc[k - L + 1:k + 1]
        mid = ((ww["high"].max() + ww["low"].min()) / 2 + ww["close"].mean()) / 2
        diffs.append(df["close"].iloc[k] - mid)
    coef = np.polyfit(np.arange(L), diffs, 1)
    assert f["val"].iloc[t] == pytest.approx(np.polyval(coef, L - 1))


def test_bug_flag_changes_squeeze():
    df = random_ohlc(2000, seed=5)
    a = squeeze_indicator(df, SqueezeParams(replicate_bug=True))["sqz_on"].sum()
    b = squeeze_indicator(df, SqueezeParams(replicate_bug=False))["sqz_on"].sum()
    assert a > b  # con BB más estrechas (1.5) hay más compresión


@pytest.mark.parametrize("p", [SqueezeParams(), SqueezeParams(min_squeeze_bars=1, entry_window=3,
                                                               exit_mode="zero", trend_filter="ema")])
def test_no_lookahead_squeeze(p):
    df = random_ohlc(3000, seed=11)
    e_full, x_full = generate_squeeze_signals(df, p)
    assert len(e_full) > 10
    for cut in (800, 1999, 2555):
        e, x = generate_squeeze_signals(df.iloc[:cut], p)
        pd.testing.assert_frame_equal(e_full[e_full["bar"] < cut].reset_index(drop=True),
                                      e.reset_index(drop=True))
        pd.testing.assert_frame_equal(x_full.iloc[:cut], x)


# ------------------------------------------------------------ simulador por señales
def _entries(df, bar, side, stop_dist, tp_r=0.0):
    return pd.DataFrame([{"bar": bar, "time": df.index[bar], "side": side,
                          "stop_dist": stop_dist, "tp_r": tp_r}])


def _no_exits(df):
    return pd.DataFrame({"exit_long": False, "exit_short": False}, index=df.index)


def test_market_entry_next_open_and_signal_exit():
    df = ohlc([[100, 101, 99, 100], [102, 104, 101, 103], [103, 106, 102, 105], [107, 108, 106, 107]])
    x = _no_exits(df)
    x.loc[df.index[2], "exit_long"] = True
    tr, eq = run_signal_backtest(df, _entries(df, 0, 1, 5.0), x, BTConfig(costs="zero", max_leverage=100))
    t = tr.iloc[0]
    assert t["entry"] == 102 and t["exit"] == 107 and t["reason"] == "signal"
    assert t["r"] == pytest.approx(1.0)  # +5 con riesgo 5
    assert eq.iloc[-1] == pytest.approx(10_100)


def test_stop_on_entry_bar_and_fees():
    df = ohlc([[100, 101, 99, 100], [100, 100.5, 94, 95], [95, 96, 94, 95]])
    tr, _ = run_signal_backtest(df, _entries(df, 0, 1, 5.0), _no_exits(df),
                                BTConfig(costs="futures", max_leverage=100))
    t = tr.iloc[0]
    assert t["reason"] == "sl"
    fill = 100 * 1.0002
    assert t["entry"] == pytest.approx(fill)
    assert t["exit"] == pytest.approx((fill - 5) * (1 - 0.0002))
    assert t["r"] < -1  # comisiones y slippage empeoran la pérdida


def test_short_take_profit():
    df = ohlc([[100, 101, 99, 100], [100, 100.5, 97, 98], [98, 99, 89, 90]])
    tr, _ = run_signal_backtest(df, _entries(df, 0, -1, 5.0, tp_r=2), _no_exits(df),
                                BTConfig(costs="zero", max_leverage=100))
    assert tr.iloc[0]["reason"] == "tp" and tr.iloc[0]["r"] == pytest.approx(2.0)


def test_squeeze_random_walk_no_edge():
    rs = []
    for seed in range(5):
        df = random_ohlc(5000, seed=100 + seed)
        e, x = generate_squeeze_signals(df, SqueezeParams(min_squeeze_bars=3))
        tr, _ = run_signal_backtest(df, e, x, BTConfig(costs="zero"))
        rs.append(tr["r"].to_numpy())
    r = np.concatenate(rs)
    assert len(r) > 150
    assert abs(r.mean()) < 3 * r.std() / np.sqrt(len(r)) + 0.05
