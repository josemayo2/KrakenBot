import numpy as np
import pandas as pd
import pytest

from live.brokers import PaperBroker
from live.config import LiveConfig
from live.squeeze_bot import Notifier, load_state, run_once
from smc.backtest import BTConfig, run_signal_backtest
from smc.squeeze import SqueezeParams, generate_squeeze_signals
from tests.test_smc import random_ohlc

PARAMS = SqueezeParams(exit_mode="zero", trend_filter="ema", min_squeeze_bars=6)


class Quiet(Notifier):
    def __init__(self):
        super().__init__()
        self.msgs = []

    def send(self, msg):
        self.msgs.append(msg)


def first_entry(e):
    return int(e.loc[e["bar"] >= 400, "bar"].iloc[0])


def make_cfg(tmp_path, **kw):
    c = LiveConfig(mode="paper", symbols=["TEST"], timeframe="1h", params=PARAMS,
                   state_path=str(tmp_path / "state.json"), trades_log=str(tmp_path / "trades.csv"),
                   max_notional_per_trade=1e9, max_total_notional=1e9, max_drawdown_halt=0.99)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


@pytest.fixture(scope="module")
def market():
    df = random_ohlc(1400, seed=21, vol=0.01)
    e, x = generate_squeeze_signals(df, PARAMS)
    return df, e, x


def test_live_paper_matches_backtest(tmp_path, market):
    """El bot ejecutado vela a vela (como con cron) hace las mismas operaciones que el backtest."""
    df, e, x = market
    start = 400
    cfg = make_cfg(tmp_path)
    broker = PaperBroker(str(tmp_path / "paper.json"), initial_equity=10_000)
    for cut in range(start, len(df) + 1):
        run_once(cfg, broker, lambda s, c=cut: df.iloc[:c], Quiet(), now=df.index[cut - 1] + pd.Timedelta(hours=1))
    live = pd.read_csv(cfg.trades_log)
    live_entries = live[live["action"] == "entry"]

    # backtest equivalente: mismas señales, desde la misma vela
    e2 = e[e["bar"] >= start - 1].copy()
    tr, _ = run_signal_backtest(df, e2, x, BTConfig(costs="futures", max_leverage=1e9))
    tr = tr[tr["reason"] != "eod"]

    assert len(live_entries) >= 5, "el test necesita operaciones"
    n = min(len(tr), len(live_entries))
    assert n >= 5
    np.testing.assert_array_equal(live_entries["side"].to_numpy()[:n], tr["side"].to_numpy()[:n])
    np.testing.assert_allclose(live_entries["price"].to_numpy()[:n], tr["entry"].to_numpy()[:n], rtol=1e-9)
    exits = live[live["action"].isin(["exit", "stop"])]
    reasons = exits["action"].map({"exit": "signal", "stop": "sl"}).to_numpy()[:n]
    np.testing.assert_array_equal(reasons, tr["reason"].replace({"sl_gap": "sl"}).to_numpy()[:n])
    np.testing.assert_allclose(exits["price"].to_numpy()[:n], tr["exit"].to_numpy()[:n], rtol=1e-9)


def test_same_bar_twice_does_not_duplicate(tmp_path, market):
    df, e, _ = market
    b = int(e["bar"].iloc[len(e) // 2])
    cfg = make_cfg(tmp_path)
    broker = PaperBroker(str(tmp_path / "paper.json"))
    for _ in range(2):
        run_once(cfg, broker, lambda s: df.iloc[:b + 1], Quiet())
    assert len(pd.read_csv(cfg.trades_log)) == 1
    st = load_state(cfg.state_path)["positions"]["TEST"]
    assert st["stop"] == pytest.approx(st["entry"] - st["side"] * st["stop_dist"])


def test_notional_cap(tmp_path, market):
    df, e, _ = market
    b = first_entry(e)
    cfg = make_cfg(tmp_path, max_notional_per_trade=50.0)
    broker = PaperBroker(str(tmp_path / "paper.json"), initial_equity=1000)
    run_once(cfg, broker, lambda s: df.iloc[:b + 1], Quiet())
    st = load_state(cfg.state_path)["positions"]["TEST"]
    assert st["size"] * st["entry"] <= 50.0 * 1.001


def test_capital_cap_limits_size(tmp_path, market):
    df, e, _ = market
    b = first_entry(e)
    sizes = {}
    for cap in (0.0, 1000.0):
        d = tmp_path / f"cap{int(cap)}"
        cfg = make_cfg(d, capital_cap=cap)
        broker = PaperBroker(str(d / "paper.json"), initial_equity=5000)
        run_once(cfg, broker, lambda s: df.iloc[:b + 1], Quiet())
        sizes[cap] = load_state(cfg.state_path)["positions"]["TEST"]["size"]
    assert sizes[1000.0] == pytest.approx(sizes[0.0] / 5, rel=1e-3)


# ------------------------------------------------------------ casos de fallo con un broker falso
class FakeBroker(PaperBroker):
    def __init__(self, path, fail_stop=False):
        super().__init__(path, 1000)
        self.fail_stop = fail_stop
        self.calls = []

    def market(self, *a, **k):
        self.calls.append(("market", a, k))
        return super().market(*a, **k)

    def place_stop(self, *a, **k):
        self.calls.append(("stop", a, k))
        if self.fail_stop:
            raise RuntimeError("rechazada")
        return super().place_stop(*a, **k)


def test_stop_failure_closes_position(tmp_path, market):
    df, e, _ = market
    b = first_entry(e)
    cfg = make_cfg(tmp_path)
    br = FakeBroker(str(tmp_path / "p.json"), fail_stop=True)
    q = Quiet()
    run_once(cfg, br, lambda s: df.iloc[:b + 1], q)
    assert br.position("TEST") is None
    assert [c[0] for c in br.calls] == ["market", "stop", "market"]
    assert br.calls[-1][2]["reduce_only"] is True
    assert "TEST" not in load_state(cfg.state_path)["positions"]
    assert any("seguridad" in m for m in q.msgs)


def test_missing_stop_is_replaced(tmp_path, market):
    df, e, _ = market
    b = first_entry(e)
    cfg = make_cfg(tmp_path)
    br = FakeBroker(str(tmp_path / "p.json"))
    run_once(cfg, br, lambda s: df.iloc[:b + 1], Quiet())
    for oid in list(br.s["stops"]):  # alguien borra el stop a mano
        br.cancel(oid)
    q = Quiet()
    run_once(cfg, br, lambda s: df.iloc[:b + 2], q)
    if br.position("TEST"):  # si sigue abierta, debe volver a tener stop
        assert br.open_order_ids("TEST")
        assert any("recolocado" in m for m in q.msgs)


def test_foreign_position_untouched(tmp_path, market):
    df, e, _ = market
    b = first_entry(e)
    cfg = make_cfg(tmp_path)
    br = FakeBroker(str(tmp_path / "p.json"))
    br.s["positions"]["TEST"] = {"side": 1, "size": 1.0, "entry": 100.0}
    q = Quiet()
    run_once(cfg, br, lambda s: df.iloc[:b + 1], q)
    assert br.calls == []
    assert any("no abrió" in m for m in q.msgs)


def test_drawdown_halt_blocks_entries(tmp_path, market):
    df, e, _ = market
    b = first_entry(e)
    cfg = make_cfg(tmp_path, max_drawdown_halt=0.25)
    from live.squeeze_bot import save_state
    save_state(cfg.state_path, {"positions": {}, "last_bar": {}, "peak_equity": 2000.0, "halted": False})
    br = FakeBroker(str(tmp_path / "p.json"))  # capital 1000 -> drawdown 50%
    q = Quiet()
    run_once(cfg, br, lambda s: df.iloc[:b + 1], q)
    assert br.calls == []
    assert load_state(cfg.state_path)["halted"] is True
