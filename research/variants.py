"""¿Menos cambios = más fiable? Variantes de la regla 3/3 (mismos 3 votos)."""
import numpy as np, pandas as pd
from common import COST, COST_SPOT, SPLIT, load, metrics, resample, run, random_benchmark
from ensemble import build

def hysteresis(sc):
    pos, p = np.zeros(len(sc)), 0
    for i, s in enumerate(sc):
        if np.isnan(s): p = 0
        elif p == 0 and s >= 3: p = 1
        elif p == 1 and s <= 1: p = 0
        pos[i] = p
    return pos

for mk, raw, cost, fund, start in (("spot", load("spot_1h"), COST_SPOT, False, pd.Timestamp("2018-01-01", tz="UTC")),
                                   ("futuros", load("fut_15m"), COST, True, pd.Timestamp("2020-08-01", tz="UTC"))):
    h4, d = resample(raw, "4h"), resample(raw, "1D")
    g = build(h4, d)
    sc = g["score"].values
    ok = ~np.isnan(sc)
    base = np.where(ok & (sc >= 3), 1.0, 0.0)
    conf = base.copy()
    run3 = pd.Series(base).rolling(6).min().fillna(0).values     # 3/3 seguido durante 24 h (6 velas de 4h)
    conf = np.zeros(len(base)); p = 0
    for i in range(len(base)):
        if p == 0 and run3[i] == 1: p = 1
        elif p == 1 and base[i] == 0: p = 0
        conf[i] = p
    daily_only = pd.Series(np.where(h4.index.hour == 20, base, np.nan)).ffill().fillna(0).values
    print(f"== {mk}")
    for name, pos in (("actual (3/3)", base), ("histéresis: sale con ≤1/3", hysteresis(sc)),
                      ("confirmación 24 h", conf), ("solo al cierre diario", daily_only)):
        r = run(h4, pos, "4h", cost, fund)
        i, o, a = metrics(r, start, SPLIT), metrics(r, SPLIT), metrics(r, start)
        rb = random_benchmark(r, start, None, n=300)
        print(f"  {name:26s} IS sh {i['sharpe']:.2f} dd {i['dd']*100:4.0f}% | OOS sh {o['sharpe']:.2f} dd {o['dd']*100:4.0f}% ret {o['ret']*100:+4.0f}% | "
              f"total {a['ret']*100:+6.0f}% oper {a['trades']:3d} gana {a['win']*100:3.0f}% expo {a['expo']*100:3.0f}% | azar {rb['pct']*100:3.0f}%")
