"""«3/3 O liquidez»: probabilidades a 7 días, operaciones malas y si 1500 velas de 4h bastan para el estado (servidor)."""
import numpy as np, pandas as pd
from common import COST, COST_SPOT, load, resample, run, trades_of, wilson
from ensemble import build
from zl_check import zl

for mk, raw, ltf, cost, fund, start in (("futuros", load("fut_15m"), load("fut_5m"), COST, True, pd.Timestamp("2020-08-01", tz="UTC")),
                                        ("spot", load("spot_1h"), None, COST_SPOT, False, pd.Timestamp("2018-01-01", tz="UTC"))):
    h4, d = resample(raw, "4h"), resample(raw, "1D")
    g = build(h4, d)
    sc = np.nan_to_num(g["score"].values)
    ok = ~np.isnan(g["score"].values)
    z = zl(h4, ltf if ltf is not None else raw) > 0
    base = ok & (sc >= 3)
    orr = base | z
    c = h4.c
    fwd7 = (c.shift(-42) / c - 1).values
    m = (h4.index >= start) & ~np.isnan(fwd7) & (h4.index.hour == 20)
    print(f"== {mk}")
    for name, cond in (("actual 3/3", base), ("3/3 O liquidez", orr), ("solo liquidez", z)):
        x = fwd7[m & cond]
        k = (x > 0).sum()
        lo, hi = wilson(k, len(x))
        r = run(h4, np.where(cond, 1.0, 0.0), "4h", cost, fund)
        tr = np.array([t[3] for t in trades_of(r, h4.index >= start)])
        print(f"  {name:16s} P(sube 7 días | compra) {k/len(x)*100:4.1f}% [{lo*100:.0f}-{hi*100:.0f}] n={len(x)} media {np.mean(x)*100:+.2f}% | "
              f"fuera: {np.mean(fwd7[m & ~cond] > 0)*100:4.1f}% | base {np.mean(fwd7[m] > 0)*100:4.1f}% | operaciones {len(tr)}: "
              f"gana {np.mean(tr > 0)*100:.0f}%, pierden >10 %: {np.mean(tr < -0.10)*100:.0f}%, peor {tr.min()*100:.0f}%")
    # ¿cuadra el estado con solo las últimas 1500 velas de 4h?
    zz = zl(h4, ltf if ltf is not None else raw)
    idx = np.arange(2000, len(h4), 97)
    agree = []
    for e in idx:
        sub = h4.iloc[e - 1500:e + 1]
        zs = zl(sub, ltf if ltf is not None else raw)
        agree.append(np.sign(zs[-1]) == np.sign(zz[e]))
    print(f"  estado con 1500 velas = estado con todo el histórico en {np.mean(agree)*100:.1f}% de {len(agree)} comprobaciones")
