import numpy as np
import pandas as pd
import pytest

from smc import BTConfig, SMCParams, generate_setups, run_backtest
from smc import indicators as ind
from smc.strategy import compute_features


def random_ohlc(n=3000, seed=7, vol=0.01):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, vol, n)) * close
    high = np.maximum(open_, close) + spread * rng.random(n)
    low = np.minimum(open_, close) - spread * rng.random(n)
    idx = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def ohlc(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)


LOOSE = SMCParams(trend_filter="none", require_mss=False, fvg_min_atr=0.0, min_stop_pct=0.0)


# ------------------------------------------------------------ anti-repintado
@pytest.mark.parametrize("params", [SMCParams(trend_filter="none"), LOOSE])
def test_no_lookahead_setups(params):
    df = random_ohlc()
    full = generate_setups(df, params)
    assert len(full) > 10, "el test necesita setups para ser significativo"
    for cut in (700, 1234, 2001, 2777):
        part = generate_setups(df.iloc[:cut], params)
        a = full[full["bar"] < cut].reset_index(drop=True)
        pd.testing.assert_frame_equal(a, part.reset_index(drop=True))


def test_no_lookahead_features():
    df = random_ohlc(1500)
    p = SMCParams()
    full = compute_features(df, p)
    part = compute_features(df.iloc[:900], p)
    pd.testing.assert_frame_equal(full.iloc[:900], part)


# ------------------------------------------------------------ indicadores
def test_pivot_confirmed_after_right_bars():
    h = np.array([1, 2, 5, 3, 2, 1], float)
    ph, _ = ind.pivot_points(h, h - 0.5, left=2, right=2)
    assert np.isnan(ph[:4]).all() and ph[4] == 5


def test_bullish_fvg():
    df = ohlc([[10, 11, 9, 10], [10, 14, 10, 13.5], [13.5, 15, 12, 14.5]])
    f = ind.fair_value_gaps(df, pd.Series(1.0, index=df.index))
    assert f["bull_fvg"].iloc[2]
    assert f["bull_fvg_bot"].iloc[2] == 11 and f["bull_fvg_top"].iloc[2] == 12


def test_sweep_vs_break():
    # mínimo pivote 8 en la barra 2 (left=1,right=1 -> confirmado en 3)
    rows = [[10, 11, 9, 10], [10, 10.5, 9, 9.5], [9.5, 10, 8, 9.5], [9.5, 10.5, 9, 10],
            [10, 10.5, 7.5, 9.8]]           # mecha bajo 8, cierra encima -> barrido
    s = ind.liquidity_sweeps(ohlc(rows), 1, 1)
    assert s["bull_sweep"].iloc[4] and s["bull_sweep_level"].iloc[4] == 8
    rows[-1] = [10, 10.5, 7.5, 7.8]          # cierra debajo -> ruptura, no barrido
    s = ind.liquidity_sweeps(ohlc(rows), 1, 1)
    assert not s["bull_sweep"].any()


# ------------------------------------------------------------ simulador
def _setup(bar, side, entry, sl, tp, expiry=None, df=None):
    return pd.DataFrame([{"bar": bar, "time": df.index[bar], "side": side, "entry": entry,
                          "sl": sl, "tp": tp, "expiry_bar": expiry or bar + 10}])


def test_fill_then_tp_fees():
    df = ohlc([[100, 101, 99, 100], [100, 100.5, 98, 99], [99, 106, 99, 105]])
    st = _setup(0, 1, entry=99, sl=97, tp=103, df=df)
    tr, eq = run_backtest(df, st, BTConfig(costs="futures", max_leverage=100))
    t = tr.iloc[0]
    assert t["reason"] == "tp" and t["entry"] == 99 and t["exit"] == 103
    qty = 0.01 * 10_000 / 2
    expected = qty * 4 - qty * 99 * 0.0002 - qty * 103 * 0.0002
    assert t["pnl"] == pytest.approx(expected)
    assert eq.iloc[-1] == pytest.approx(10_000 + expected)


def test_same_bar_sl_and_tp_counts_as_sl():
    df = ohlc([[100, 101, 99, 100], [100, 100, 98.9, 99.5], [99.5, 104, 96, 100]])
    st = _setup(0, 1, entry=99, sl=97, tp=103, df=df)
    tr, _ = run_backtest(df, st, BTConfig(costs="zero", max_leverage=100))
    assert tr.iloc[0]["reason"] == "sl" and tr.iloc[0]["r"] == pytest.approx(-1)


def test_no_fill_on_signal_bar_and_cancel_if_tp_first():
    # la barra 0 toca la entrada pero el setup nace en su cierre -> no cuenta
    df = ohlc([[100, 101, 98, 100], [100, 104, 99.5, 103.5], [103.5, 104, 98, 99]])
    st = _setup(0, 1, entry=99, sl=97, tp=103, df=df)
    tr, _ = run_backtest(df, st, BTConfig(costs="zero"))
    assert tr.empty  # en la barra 1 llega al TP sin llenar -> cancelada


def test_random_walk_has_no_edge():
    """Sobre ruido puro la estrategia no debe ganar: valida que el motor no tiene sesgo."""
    rs = []
    for seed in range(6):
        df = random_ohlc(4000, seed=seed)
        tr, _ = run_backtest(df, generate_setups(df, LOOSE), BTConfig(costs="zero"))
        rs.append(tr["r"].values)
    r = np.concatenate(rs)
    assert len(r) > 200
    assert abs(r.mean()) < 3 * r.std() / np.sqrt(len(r)) + 0.05
