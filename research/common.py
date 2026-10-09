"""Utilidades comunes de la investigación: datos, indicadores, backtest por posición y por operación."""
import math
import os

import numpy as np
import pandas as pd

D = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")   # python3 get_data.py las descarga aquí
COST = 0.0006          # futuros, por lado (0,05 % comisión taker + 0,01 % deslizamiento)
COST_SPOT = 0.0010     # spot, por lado
MAKER = 0.0002         # orden límite en futuros
SPLIT = pd.Timestamp("2024-01-01", tz="UTC")
BPY = {"5m": 105120, "15m": 35040, "30m": 17520, "1h": 8760, "4h": 2190, "1d": 365}
AGG = {"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum", "q": "sum", "n": "sum", "tbv": "sum"}


def load(name):
    df = pd.read_csv(f"{D}/{name}.csv.gz")
    df.index = pd.to_datetime(df.t, unit="s", utc=True)
    return df[["o", "h", "l", "c", "v", "q", "n", "tbv"]].astype(float)


def secs(idx):
    """Segundos Unix de un DatetimeIndex (sea cual sea su resolución interna)."""
    return np.asarray(idx.as_unit("s").asi8, dtype=np.int64)


def resample(df, rule):
    return df.resample(rule, label="left", closed="left").agg(AGG).dropna(subset=["o"])


_FUND = None


def funding_rates():
    global _FUND
    if _FUND is None:
        f = pd.read_csv(f"{D}/btcusdt_funding.csv.gz")
        _FUND = (f.ts.values.astype(np.int64), f.rate.values.astype(float))
    return _FUND


def funding_per_bar(idx):
    """Suma de funding cobrado en cada vela (el que paga quien está largo durante esa vela)."""
    ts, rate = funding_rates()
    t = secs(idx)
    out = np.zeros(len(t))
    k = np.searchsorted(t, ts, side="right") - 1
    ok = (k >= 0) & (ts < t[-1] + (t[-1] - t[-2]))
    np.add.at(out, k[ok], rate[ok])
    return out


# ───────── indicadores ─────────
def ema(x, n):
    x = np.asarray(x, float)
    a = 2.0 / (n + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def ema_fast(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def sma(x, n):
    return pd.Series(x).rolling(n).mean().values


def atr(df, n=14):
    h, l, c = df.h.values, df.l.values, df.c.values
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


# ───────── backtest por posición (señal al cierre, se opera al cierre = apertura siguiente) ─────────
def run(df, pos, tf, cost=COST, fund=True):
    """pos[i] = posición deseada tras cerrar la vela i (-1, 0, 1). Devuelve dict con series."""
    c = df.c.values
    pos = np.nan_to_num(np.asarray(pos, float))
    r = np.zeros(len(c))
    r[1:] = c[1:] / c[:-1] - 1
    held = np.zeros(len(c))
    held[1:] = pos[:-1]
    turn = np.abs(np.diff(np.r_[0.0, held]))
    f = funding_per_bar(df.index) if fund else np.zeros(len(c))
    pnl = held * r - turn * cost - held * f
    return {"idx": df.index, "pnl": pnl, "held": held, "c": c, "tf": tf, "cost": cost, "fund": f}


def trades_of(res, mask=None):
    """Operaciones (tramos con la misma posición) con su rentabilidad neta. mask limita el periodo."""
    held, c, f, cost = res["held"], res["c"], res["fund"], res["cost"]
    n = len(held)
    out = []
    i = 1
    while i < n:
        if held[i] != 0 and held[i] != held[i - 1]:
            s = held[i]
            j = i
            while j + 1 < n and held[j + 1] == s:
                j += 1
            entry, exit_ = c[i - 1], c[j]
            ret = s * (exit_ / entry - 1) - 2 * cost - s * f[i:j + 1].sum()
            if mask is None or mask[i]:
                out.append((i, j, s, ret))
            i = j + 1
        else:
            i += 1
    return out


def metrics(res, start=None, end=None):
    idx = res["idx"]
    m = np.ones(len(idx), bool)
    if start is not None:
        m &= idx >= start
    if end is not None:
        m &= idx < end
    pnl, held = res["pnl"][m], res["held"][m]
    bpy = BPY[res["tf"]]
    eq = np.cumprod(1 + pnl)
    years = len(pnl) / bpy
    tr = trades_of(res, m)
    rets = np.array([t[3] for t in tr]) if tr else np.array([])
    wins = rets[rets > 0]
    loss = rets[rets <= 0]
    sd = pnl.std()
    return {
        "ret": eq[-1] - 1,
        "cagr": eq[-1] ** (1 / years) - 1 if years > 0 and eq[-1] > 0 else -1,
        "dd": (eq / np.maximum.accumulate(eq) - 1).min(),
        "sharpe": pnl.mean() / sd * math.sqrt(bpy) if sd > 0 else 0,
        "trades": len(rets),
        "win": len(wins) / len(rets) if len(rets) else float("nan"),
        "pf": wins.sum() / -loss.sum() if len(loss) and loss.sum() < 0 else float("inf"),
        "avg": rets.mean() if len(rets) else float("nan"),
        "expo": (held != 0).mean(),
        "long": (held > 0).mean(),
        "short": (held < 0).mean(),
    }


def hold_metrics(df, tf, start=None, end=None, cost=COST, fund=True):
    return metrics(run(df, np.ones(len(df)), tf, cost, fund), start, end)


def years_table(res):
    idx = res["idx"]
    out = {}
    for y in sorted(set(idx.year)):
        m = idx.year == y
        out[y] = float(np.prod(1 + res["pnl"][m]) - 1)
    return out


def random_benchmark(res, start=None, end=None, n=500, seed=1):
    """Posiciones al azar con las mismas duraciones de operación, huecos y lados (barajados).
    Devuelve el percentil de la estrategia real y la mediana del azar (rentabilidad total)."""
    idx = res["idx"]
    m = np.ones(len(idx), bool)
    if start is not None:
        m &= idx >= start
    if end is not None:
        m &= idx < end
    held = res["held"][m]
    c = res["c"][m]
    f = res["fund"][m]
    cost = res["cost"]
    r = np.zeros(len(c))
    r[1:] = c[1:] / c[:-1] - 1
    # tramos (valor, duración) de la posición real; se barajan todos juntos
    st = np.r_[0, np.flatnonzero(np.diff(held) != 0) + 1]
    lens = np.diff(np.r_[st, len(held)])
    vals = held[st]
    rng = np.random.default_rng(seed)
    actual = np.prod(1 + res["pnl"][m]) - 1
    outs = np.empty(n)
    for s in range(n):
        p = rng.permutation(len(st))
        h = np.repeat(vals[p], lens[p])
        turn = np.abs(np.diff(np.r_[0.0, h]))
        pnl = h * r - turn * cost - h * f
        outs[s] = np.prod(1 + pnl) - 1
    return {"pct": float((outs < actual).mean()), "median": float(np.median(outs)), "actual": float(actual)}


# ───────── simulador de operaciones con stop y objetivo ─────────
def simulate(path_h, path_l, path_c, path_t, entries, max_bars, entry_cost=COST, stop_cost=COST, tp_cost=COST,
             time_cost=COST):
    """entries: lista de dicts {k: índice de la primera vela (en la serie fina) tras entrar, side, entry, stop, tp}.
    Recorre velas finas; si stop y objetivo caen en la misma vela, cuenta el stop (conservador).
    Devuelve lista de resultados con R neto y rentabilidad %."""
    out = []
    n = len(path_c)
    for e in entries:
        k, s, en, st, tp = e["k"], e["side"], e["entry"], e["stop"], e["tp"]
        risk = abs(en - st) / en
        if risk <= 0:
            continue
        end = min(n - 1, k + max_bars - 1) if max_bars else n - 1
        if "until" in e and e["until"] is not None:
            end = min(end, e["until"])
        exit_px, why, j = None, None, end
        for j in range(k, end + 1):
            hi, lo = path_h[j], path_l[j]
            if s > 0:
                if lo <= st:
                    exit_px, why = st, "stop"
                    break
                if tp is not None and hi >= tp:
                    exit_px, why = tp, "tp"
                    break
            else:
                if hi >= st:
                    exit_px, why = st, "stop"
                    break
                if tp is not None and lo <= tp:
                    exit_px, why = tp, "tp"
                    break
        if exit_px is None:
            exit_px, why, j = path_c[end], "time", end
        gross = s * (exit_px / en - 1)
        fee = entry_cost + {"stop": stop_cost, "tp": tp_cost, "time": time_cost}[why]
        ret = gross - fee
        out.append({**e, "exit": exit_px, "why": why, "j": j, "ret": ret, "R": ret / risk, "risk": risk,
                    "t": path_t[e["k"]]})
    return out


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def summarize_R(rs, label=""):
    """Resumen de una lista de resultados de simulate."""
    if not rs:
        return {"label": label, "n": 0}
    R = np.array([x["R"] for x in rs])
    ret = np.array([x["ret"] for x in rs])
    w = (ret > 0).sum()
    lo, hi = wilson(w, len(ret))
    se = R.std(ddof=1) / math.sqrt(len(R)) if len(R) > 1 else float("nan")
    eq = np.cumprod(1 + 0.01 * R)   # arriesgando 1 % por operación
    return {
        "label": label, "n": len(R), "win": w / len(R), "win_lo": lo, "win_hi": hi,
        "avgR": R.mean(), "t": R.mean() / se if se and se > 0 else float("nan"),
        "avg_ret": ret.mean(), "sumR": R.sum(),
        "eq1pct": eq[-1] - 1, "dd1pct": (eq / np.maximum.accumulate(eq) - 1).min(),
        "risk_med": float(np.median([x["risk"] for x in rs])),
        "tp": float(np.mean([x["why"] == "tp" for x in rs])),
        "stop": float(np.mean([x["why"] == "stop" for x in rs])),
    }


def fmt_pct(x, d=0):
    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:+.{d}f}%"
