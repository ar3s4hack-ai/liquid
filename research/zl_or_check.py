"""«3/3 O liquidez 4h»: robustez a los parámetros de la liquidez y prueba contra el azar (liquidez desplazada en el tiempo)."""
import numpy as np, pandas as pd
from common import COST, COST_SPOT, SPLIT, load, metrics, resample, run
from ensemble import build
from zl_check import zl

rng = np.random.default_rng(11)
for mk, raw, ltf, cost, fund, start in (("futuros", load("fut_15m"), load("fut_5m"), COST, True, pd.Timestamp("2020-08-01", tz="UTC")),
                                        ("spot", load("spot_1h"), None, COST_SPOT, False, pd.Timestamp("2018-01-01", tz="UTC"))):
    h4, d = resample(raw, "4h"), resample(raw, "1D")
    g = build(h4, d)
    sc = np.nan_to_num(g["score"].values)
    ok = ~np.isnan(g["score"].values)
    base = ok & (sc >= 3)
    rb = run(h4, np.where(base, 1.0, 0.0), "4h", cost, fund)
    bi, bo = metrics(rb, start, SPLIT)["sharpe"], metrics(rb, SPLIT)["sharpe"]
    print(f"== {mk}: actual IS {bi:.2f} OOS {bo:.2f}")
    lt = ltf if ltf is not None else raw
    for name, kw in (("como el script", {}), ("mitad de mecha", {"poc": "mid"}), ("RSI vol 55", {"rsi_thr": 55}), ("RSI vol 65", {"rsi_thr": 65}),
                     ("mecha ×1,5", {"mult": 1.5}), ("mecha ×2,5", {"mult": 2.5}), ("media 14", {"sma_n": 14}), ("media 30", {"sma_n": 30})):
        z = zl(h4, lt, **kw) > 0 if kw.get("poc") != "mid" else zl(h4, poc="mid") > 0
        r = run(h4, np.where(base | z, 1.0, 0.0), "4h", cost, fund)
        i, o, a = metrics(r, start, SPLIT), metrics(r, SPLIT), metrics(r, start)
        print(f"  {name:16s} IS {i['sharpe']:.2f} ({i['sharpe'] - bi:+.2f}) OOS {o['sharpe']:.2f} ({o['sharpe'] - bo:+.2f}) dd {a['dd']*100:.0f}%")
    # azar: misma liquidez desplazada en el tiempo
    z = zl(h4, lt) > 0
    act = metrics(run(h4, np.where(base | z, 1.0, 0.0), "4h", cost, fund), start)["sharpe"]
    sims = []
    for _ in range(500):
        k = int(rng.integers(500, len(z) - 500))
        zr = np.roll(z, k)
        sims.append(metrics(run(h4, np.where(base | zr, 1.0, 0.0), "4h", cost, fund), start)["sharpe"])
    sims = np.array(sims)
    print(f"  azar (liquidez desplazada): Sharpe real {act:.2f}, azar mediana {np.median(sims):.2f}, igual o mejor {np.mean(sims >= act)*100:.1f}%")
