"""Liquidez Zero Lag en 4h: ¿aguanta? Parámetros, aproximación sin velas pequeñas, spot desde 2018, años y azar."""
import numpy as np, pandas as pd
from common import COST, COST_SPOT, SPLIT, fmt_pct, hold_metrics, load, metrics, random_benchmark, resample, run, years_table
from pine import _liq_levels, rsi_pine, wick_poc

def zl(df, f5=None, rsi_thr=60, mult=2.0, sma_n=21, poc="ltf"):
    o, h, l, c, v = df.o.values, df.h.values, df.l.values, df.c.values, df.v.values
    vol = rsi_pine(v, 14)
    lw = np.minimum(c, o) - l
    uw = h - np.maximum(c, o)
    aw = (lw + uw) / 2
    aW = pd.Series(aw).rolling(sma_n).mean().values
    hi = (vol > rsi_thr) & (aw > aW * mult) & (uw != lw)
    red, green = hi & (lw > uw), hi & (uw > lw)
    top = np.where(red, np.minimum(c, o), np.where(green, h, np.nan))
    bot = np.where(red, l, np.where(green, np.maximum(c, o), np.nan))
    p = wick_poc(df, f5, top, bot) if poc == "ltf" else (top + bot) / 2
    typ = np.where(green, 1, np.where(red, -1, 0)).astype(np.int64)
    return _liq_levels(c, h, l, (h + l) / 2, p, typ, 500)[0]

def rep(label, df, tf, tr, cost, fund, start, n=400):
    pos = np.where(tr > 0, 1.0, 0.0)
    r = run(df, pos, tf, cost, fund)
    i, o, a = metrics(r, start, SPLIT), metrics(r, SPLIT), metrics(r, start)
    rb = random_benchmark(r, start, None, n=n)["pct"]
    print(f"  {label:40s} IS sh {i['sharpe']:.2f} dd {i['dd']*100:4.0f}% | OOS sh {o['sharpe']:.2f} dd {o['dd']*100:4.0f}% {fmt_pct(o['ret']):>6s} | "
          f"total {fmt_pct(a['ret']):>7s} oper {a['trades']:3d} gana {a['win']*100:3.0f}% expo {a['expo']*100:3.0f}% | azar {rb*100:3.0f}%")
    return r

def main():
    f15, f5 = load("fut_15m"), load("fut_5m")
    h4 = resample(f15, "4h")
    st = h4.index[300]
    hm = [hold_metrics(h4, "4h", st, SPLIT), hold_metrics(h4, "4h", SPLIT, None)]
    print(f"== futuros 4h (aguantar: IS sh {hm[0]['sharpe']:.2f} dd {hm[0]['dd']*100:.0f}% | OOS sh {hm[1]['sharpe']:.2f} dd {hm[1]['dd']*100:.0f}%)")
    base = rep("como el script (RSI vol 60, mecha ×2, 5m)", h4, "4h", zl(h4, f5), COST, True, st, n=1000)
    print("   años:", {y: round(v * 100) for y, v in years_table(base).items()})
    rep("sin velas pequeñas (mitad de la mecha)", h4, "4h", zl(h4, poc="mid"), COST, True, st)
    for rt in (55, 65):
        rep(f"RSI vol > {rt}", h4, "4h", zl(h4, f5, rsi_thr=rt), COST, True, st)
    for m in (1.5, 2.5):
        rep(f"mecha × {m}", h4, "4h", zl(h4, f5, mult=m), COST, True, st)
    for sn in (14, 30):
        rep(f"media de mecha {sn}", h4, "4h", zl(h4, f5, sma_n=sn), COST, True, st)
    # otros marcos cercanos
    for tf, rule in (("2h", "2h"), ("6h", "6h"), ("8h", "8h"), ("12h", "12h")):
        df = resample(f15, rule)
        from common import BPY
        BPY[tf] = int(365 * 24 / int(rule[:-1]))
        rep(f"marco {tf}", df, tf, zl(df, f5), COST, True, df.index[300])
    # spot desde 2018 con velas de 1h como «velas pequeñas»
    s1h = load("spot_1h")
    s4 = resample(s1h, "4h")
    s4st = pd.Timestamp("2018-01-01", tz="UTC")
    hs = [hold_metrics(s4, "4h", s4st, SPLIT, COST_SPOT, False), hold_metrics(s4, "4h", SPLIT, None, COST_SPOT, False)]
    print(f"== spot 4h desde 2018 (aguantar: IS sh {hs[0]['sharpe']:.2f} dd {hs[0]['dd']*100:.0f}% | OOS sh {hs[1]['sharpe']:.2f} dd {hs[1]['dd']*100:.0f}%)")
    rs = rep("spot, velas de 1h dentro", s4, "4h", zl(s4, s1h), COST_SPOT, False, s4st)
    print("   años:", {y: round(v * 100) for y, v in years_table(rs).items()})
    rep("spot, mitad de la mecha", s4, "4h", zl(s4, poc="mid"), COST_SPOT, False, s4st)


if __name__ == "__main__":
    main()
