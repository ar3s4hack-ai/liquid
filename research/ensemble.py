"""Señal final candidata: voto de 3 tendencias que pasaron la prueba.
  1) cierre diario > EMA100 diaria
  2) EWO 5/35 diario con bandas de ruptura (estado largo)
  3) EMA50 > EMA200 en 4h
Se evalúa en velas de 4h (las señales diarias se conocen al cierre del día). Futuros 2020+ con funding y
spot 2017+ sin funding. Probabilidades: P(sube) N días después según el estado de la señal."""
import json

import numpy as np
import pandas as pd

from common import (COST, COST_SPOT, SPLIT, ema_fast, fmt_pct, hold_metrics, load, metrics, random_benchmark,
                    resample, run, sma, wilson, years_table)
from trend_rules import state


def daily_signals(d):
    c, h, l = d.c.values, d.h.values, d.l.values
    e100 = ema_fast(c, 100)
    s1 = (c > e100).astype(float)
    hl2 = (h + l) / 2
    ewo = np.nan_to_num(sma(hl2, 5) - sma(hl2, 35))
    up = ema_fast(np.maximum(ewo, 0), 35)
    lo = ema_fast(np.minimum(ewo, 0), 35)
    s2 = (state(ewo > up, ewo < 0, ewo < lo, ewo > 0) > 0).astype(float)
    s1[:100] = np.nan
    s2[:40] = np.nan
    return pd.DataFrame({"ema100": s1, "ewo": s2, "e100v": e100, "ewov": ewo, "up": up, "lo": lo}, index=d.index)


def build(h4, d):
    ds = daily_signals(d)
    ds.index = ds.index + pd.Timedelta(hours=20)      # se conoce al cerrar la última vela de 4h del día
    g = ds.reindex(h4.index).ffill()
    c = h4.c.values
    e50, e200 = ema_fast(c, 50), ema_fast(c, 200)
    s3 = (e50 > e200).astype(float)
    s3[:200] = np.nan
    g["x4h"] = s3
    g["score"] = g[["ema100", "ewo", "x4h"]].sum(axis=1, min_count=3)
    return g


def evaluate(h4, g, cost, fund, label, start):
    sc = g["score"].values
    ok = ~np.isnan(sc)
    rules = {
        "Solo EMA100 diaria": np.nan_to_num(g["ema100"].values),
        "Solo EWO bandas diario": np.nan_to_num(g["ewo"].values),
        "Solo EMA50/200 4h": np.nan_to_num(g["x4h"].values),
        "Mayoría (2 de 3)": np.where(ok & (sc >= 2), 1.0, 0.0),
        "Unanimidad (3 de 3)": np.where(ok & (sc >= 3), 1.0, 0.0),
        "Alguna (1 de 3)": np.where(ok & (sc >= 1), 1.0, 0.0),
        "Proporcional (score/3)": np.where(ok, np.nan_to_num(sc) / 3, 0.0),
    }
    print(f"\n=== {label} (desde {start.date()}) ===")
    hold = {p: hold_metrics(h4, "4h", a, b, cost, fund) for p, (a, b) in
            {"IS": (start, SPLIT), "OOS": (SPLIT, None), "ALL": (start, None)}.items()}
    print(f"{'Mantener':26s} | IS {fmt_pct(hold['IS']['ret']):>7s} dd {fmt_pct(hold['IS']['dd']):>5s} sh {hold['IS']['sharpe']:.2f} "
          f"| OOS {fmt_pct(hold['OOS']['ret']):>6s} dd {fmt_pct(hold['OOS']['dd']):>5s} sh {hold['OOS']['sharpe']:.2f} "
          f"| ALL {fmt_pct(hold['ALL']['ret']):>7s} dd {fmt_pct(hold['ALL']['dd']):>5s} sh {hold['ALL']['sharpe']:.2f}")
    out = {}
    for name, pos in rules.items():
        res = run(h4, pos, "4h", cost, fund)
        m = {p: metrics(res, a, b) for p, (a, b) in {"IS": (start, SPLIT), "OOS": (SPLIT, None), "ALL": (start, None)}.items()}
        rb = random_benchmark(res, start, None, n=400) if name != "Proporcional (score/3)" else {"pct": float("nan")}
        print(f"{name:26s} | IS {fmt_pct(m['IS']['ret']):>7s} dd {fmt_pct(m['IS']['dd']):>5s} sh {m['IS']['sharpe']:.2f} "
              f"| OOS {fmt_pct(m['OOS']['ret']):>6s} dd {fmt_pct(m['OOS']['dd']):>5s} sh {m['OOS']['sharpe']:.2f} "
              f"| ALL {fmt_pct(m['ALL']['ret']):>7s} dd {fmt_pct(m['ALL']['dd']):>5s} sh {m['ALL']['sharpe']:.2f} "
              f"tr {m['ALL']['trades']:3d} gana {m['ALL']['win'] * 100:3.0f}% expo {m['ALL']['expo'] * 100:3.0f}% "
              f"| azar {rb['pct'] * 100:3.0f}%")
        out[name] = {"m": m, "years": years_table(res), "rand": rb["pct"]}
    return out, hold


def probabilities(d, g4, start):
    """Estado de la señal al cierre de cada día → P(precio más alto N días después)."""
    sc = g4["score"]
    daily = sc[sc.index.hour == 20]
    daily.index = daily.index.normalize()
    c = d.c.reindex(daily.index)
    rows = {}
    for N in (1, 7, 30):
        fwd = c.shift(-N) / c - 1
        for per, (a, b) in {"IS": (start, SPLIT), "OOS": (SPLIT, None), "ALL": (start, None)}.items():
            m = (daily.index >= a) & ((daily.index < b) if b is not None else True) & fwd.notna() & daily.notna()
            base = fwd[m]
            r = {"base_up": float((base > 0).mean()), "base_mu": float(base.mean()), "n": int(m.sum())}
            for lab, cond in (("compra", daily >= 2), ("venta", daily <= 1), ("3/3", daily == 3), ("0/3", daily == 0)):
                x = fwd[m & cond]
                k = int((x > 0).sum())
                lo, hi = wilson(k, len(x))
                r[lab] = {"n": int(len(x)), "up": float(k / len(x)) if len(x) else float("nan"), "lo": lo, "hi": hi,
                          "mu": float(x.mean()), "med": float(x.median())}
            rows[(N, per)] = r
            print(f"  {N:2d}d {per:3s}: base P↑ {r['base_up'] * 100:4.1f}% | COMPRA n={r['compra']['n']:4d} P↑ "
                  f"{r['compra']['up'] * 100:4.1f}% [{r['compra']['lo'] * 100:.0f}-{r['compra']['hi'] * 100:.0f}] media "
                  f"{r['compra']['mu'] * 100:+5.2f}% | VENTA n={r['venta']['n']:4d} P↑ {r['venta']['up'] * 100:4.1f}% "
                  f"[{r['venta']['lo'] * 100:.0f}-{r['venta']['hi'] * 100:.0f}] media {r['venta']['mu'] * 100:+5.2f}% "
                  f"| 3/3 P↑ {r['3/3']['up'] * 100:4.1f}% 0/3 P↑ {r['0/3']['up'] * 100:4.1f}%")
    return rows


def main():
    f15 = load("fut_15m")
    h4f, d_f = resample(f15, "4h"), resample(f15, "1D")
    gf = build(h4f, d_f)
    start_f = pd.Timestamp("2020-08-01", tz="UTC")     # tras calentar EMA200 de 4h y EMA100 diaria
    outf, holdf = evaluate(h4f, gf, COST, True, "Futuros BTCUSDT (con funding)", start_f)
    s1h = load("spot_1h")
    h4s, d_s = resample(s1h, "4h"), resample(s1h, "1D")
    gs = build(h4s, d_s)
    start_s = pd.Timestamp("2018-01-01", tz="UTC")
    outs, holds = evaluate(h4s, gs, COST_SPOT, False, "Spot BTCUSDT (sin funding)", start_s)
    print("\nProbabilidades (spot 2018+): estado al cierre diario → precio N días después")
    probs = probabilities(d_s, gs, start_s)
    print("\nProbabilidades (futuros 2020-08+)")
    probf = probabilities(d_f, gf, start_f)
    last = gs.iloc[-1]
    print("\nEstado actual (último cierre):", dict(last[["ema100", "ewo", "x4h", "score"]]), "EMA100 diaria",
          round(float(last["e100v"]), 1))
    json.dump({"fut": {k: v["m"] for k, v in outf.items()}, "spot": {k: v["m"] for k, v in outs.items()},
               "years_fut": {k: v["years"] for k, v in outf.items()}, "years_spot": {k: v["years"] for k, v in outs.items()},
               "hold_fut": holdf, "hold_spot": holds,
               "probs_spot": {f"{k[0]}|{k[1]}": v for k, v in probs.items()},
               "probs_fut": {f"{k[0]}|{k[1]}": v for k, v in probf.items()}},
              open("ensemble.json", "w"), default=float)


if __name__ == "__main__":
    main()
