"""Perfil del servidor con datos de tamaño real (sin red): tiempos de build por temporalidad y modelo,
calibración, validación, Hyperliquid, libro y tamaño de la respuesta por campo.

Uso: python3 profile_server.py REPO [quick]"""
import cProfile
import io
import json
import math
import os
import pstats
import random
import sys
import tempfile
import time

os.environ["COLLECT"] = "0"
REPO = sys.argv[1]
QUICK = len(sys.argv) > 2 and sys.argv[2] == "quick"
sys.path.insert(0, REPO)
import liqdata as LQ  # noqa: E402

LQ.set_data_dir(tempfile.mkdtemp())
import app  # noqa: E402
import book as BK  # noqa: E402
import hl as HL  # noqa: E402

random.seed(5)
STEP = 300
DAYS = 130
N5 = DAYS * 288
now = int(time.time()) // STEP * STEP
start = now - (N5 - 1) * STEP
# camino de precio con tendencias y rangos (paseo aleatorio con deriva que cambia)
px, drift, C5, OI5 = 60000.0, 0.0, [], {}
oi = 6.0e9
for i in range(N5):
    if i % 2000 == 0:
        drift = random.gauss(0, 0.00025)
    o = px
    px = max(1000.0, px * (1 + drift + random.gauss(0, 0.0018)))
    hi = max(o, px) * (1 + abs(random.gauss(0, 0.0008)))
    lo = min(o, px) * (1 - abs(random.gauss(0, 0.0008)))
    v = abs(random.gauss(6e7, 2e7))
    C5.append({"time": start + i * STEP, "open": o, "high": hi, "low": lo, "close": px, "v": v, "tb": v * min(0.95, max(0.05, random.gauss(0.5, 0.08)))})
    oi = max(1e9, oi + random.gauss(2e5, 4e6))
    OI5[start + i * STEP] = oi


def agg(tf_s):
    out = {}
    for c in C5:
        t = c["time"] - c["time"] % tf_s
        a = out.get(t)
        if not a:
            out[t] = dict(c, time=t)
        else:
            a["high"], a["low"], a["close"] = max(a["high"], c["high"]), min(a["low"], c["low"]), c["close"]
            a["v"] += c["v"]
            a["tb"] += c["tb"]
    return [out[t] for t in sorted(out)]


AGG = {tf: agg(s) for tf, s in app.TF_SECONDS.items()}
CALLS = []


def oi_rows(step, limit, end_ms=None):
    ts = [t for t in OI5 if t % step == 0 and (end_ms is None or t * 1000 <= end_ms)]
    return ts[-limit:]


def fake_get(url, params=None, headers=None):
    p = params or {}
    CALLS.append(url)
    if "/api/v3/klines" in url or "/fapi/v1/klines" in url:
        cs = AGG[p["interval"]][-p["limit"]:]
        return [[c["time"] * 1000, str(c["open"]), str(c["high"]), str(c["low"]), str(c["close"]), "0", 0, str(c["v"]), 0, "0", str(c["tb"])] for c in cs]
    if "openInterestHist" in url:
        step = app.TF_SECONDS[p["period"]]
        ts = oi_rows(step, p["limit"], p.get("endTime"))
        if "dapi" in url:
            return [{"timestamp": t * 1000, "sumOpenInterest": str(OI5[t] * 0.1 / 100)} for t in ts]
        k = 1.0 if p["symbol"] == "BTCUSDT" else 0.05
        return [{"timestamp": t * 1000, "sumOpenInterestValue": str(OI5[t] * k)} for t in ts]
    if "bybit" in url:
        step = app.TF_SECONDS[{"5min": "5m", "15min": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}[p["intervalTime"]]]
        ts = oi_rows(step, 200)
        return {"retCode": 0, "result": {"list": [{"openInterest": str(OI5[t] * 0.4 / 60000), "timestamp": str(t * 1000)} for t in ts][::-1],
                                         "nextPageCursor": ""}}
    if "rubik" in url:
        step = app.TF_SECONDS[{"5m": "5m", "15m": "15m", "1H": "1h", "4H": "4h", "1D": "1d"}[p["period"]]]
        ts = oi_rows(step, 100, p.get("end"))
        return {"code": "0", "data": [[str(t * 1000), "1", "1", str(OI5[t] * 0.25)] for t in ts][::-1]}
    if "ticker/24hr" in url:
        return {"priceChangePercent": "1.1", "lastPrice": str(C5[-1]["close"]), "highPrice": "1", "lowPrice": "1"}
    if "premiumIndex" in url:
        return {"lastFundingRate": "0.0001", "nextFundingTime": (now + 3600) * 1000}
    if "fundingRate" in url:
        t = p["startTime"] // 1000 // 28800 * 28800
        return [{"fundingTime": (t + k * 28800) * 1000, "fundingRate": "0.0001"} for k in range(min(1000, (now - t) // 28800))]
    if "LongShort" in url:
        return [{"longShortRatio": "1.5", "longAccount": "0.6"}]
    if "depth" in url:
        p0 = C5[-1]["close"]
        return {"bids": [[str(p0 - 1 - i), "0.5"] for i in range(1000)], "asks": [[str(p0 + 1 + i), "0.5"] for i in range(1000)]}
    raise RuntimeError("no simulado " + url)


app.get_json = fake_get
# OI grabado de las 4 fuentes sin histórico (cada minuto como en producción, últimos 40 días)
for t in range(now - 40 * 86400, now, 60):
    base = OI5.get(t - t % STEP, 6e9)
    LQ.save_oi_snapshots({"hyperliquid": base * 0.4, "bitget": base * 0.3, "deribit": base * 0.15, "bitmex": base * 0.05}, now=t + 1)
# liquidaciones reales: ~9.000 en los últimos 4 días, y 4 días escuchando
evs = []
for c in C5[-4 * 288:]:
    for _ in range(random.randint(4, 12)):
        side = random.choice([1, -1])
        p = c["low"] if side == 1 else c["high"]
        evs.append(LQ._ev((c["time"] + random.randint(0, 299)) * 1000, random.choice(LQ.EXCHANGES), side, p, p, random.uniform(0.01, 3)))
LQ.save_events(evs)
for m in range(now - 4 * 86400, now + 60, 60):
    LQ.heartbeat(m)
# Hyperliquid: 1.100 posiciones y 7 días de grabaciones cada 5 min
pos = {}
for k in range(1100):
    side = random.choice([1, -1])
    lev = random.choice([3, 5, 10, 20, 25, 40])
    p0 = C5[-1]["close"] * random.uniform(0.93, 1.07)
    liq = p0 * (1 - 1 / lev + 0.005) if side == 1 else p0 * (1 + 1 / lev - 0.005)
    usd = math.exp(random.uniform(math.log(1.1e4), math.log(5e6)))
    pos["0x" + f"{k:040x}"] = {"szi": side * usd / p0, "entry": p0, "liq": liq, "lev": lev, "cross": True, "ntl": usd, "upd": 0}
HL.pos.update(pos)
t_snap = time.time()
for t in range(now - 7 * 86400, now, 300):
    HL.snapshot(C5[-1]["close"], now=t)
print(f"preparado: {N5} velas de 5m, {len(evs)} liquidaciones, {len(pos)} posiciones HL ({time.time() - t_snap:.1f} s grabando)")
# libro: 7 días de bloques de 5 min y 60 días de 1 h, ±6 % en tramos de 10 $
t_b = time.time()
for c in (C5[-7 * 288:] if not QUICK else C5[-288:]):
    mid = c["close"]
    vals = {b: (2e5 * math.exp(-abs((b + .5) * 10 - mid) / mid * 45) + 2e4) * random.uniform(.5, 1.5)
            for b in range(int(mid * .94 // 10), int(mid * 1.06 // 10) + 1)}
    BK.save_row("book5", c["time"], vals, 10)
    if c["time"] % 3600 == 0:
        BK.save_row("book60", c["time"], vals, 120)
print(f"libro grabado en {time.time() - t_b:.1f} s")


def clear():
    app._cache.clear()
    app._candle_cache.clear()
    app._val_cache.clear()
    app._okx_cache.clear()
    app._funding_cache.update(t=0, start=None, v=None)
    app._ctx_cache.update(t=0, v={})
    BK._map_cache.clear()
    BK._rebin_cache.clear()


def timed(fn, *a, **k):
    t = time.perf_counter()
    r = fn(*a, **k)
    return r, time.perf_counter() - t


results = {}
for tf in ("5m", "15m", "1h", "4h", "1d"):
    for model in ("auto", "oi", "vol", "hl"):
        clear()
        d, dt = timed(app.build, tf, model, (25, 50, 100) if model != "hl" else tuple(app.ALL_TIERS), 0.05 if model != "vol" else None)
        results[(tf, model)] = dt
        size = len(json.dumps(d, separators=(",", ":")))
        print(f"build {tf:>3} {model:<4} {dt * 1000:7.0f} ms · {len(d['heat']['segments']):6d} segmentos · {size / 1024:6.0f} KB")
# tamaño por campo (5m auto, vista Pro)
clear()
d = app.build("5m", "auto", (25, 50, 100), 0.05)
parts = sorted(((k, len(json.dumps(v, separators=(",", ":")))) for k, v in d.items()), key=lambda x: -x[1])
print("tamaño por campo (5m):", ", ".join(f"{k} {v / 1024:.0f} KB" for k, v in parts[:8]))
hp = sorted(((k, len(json.dumps(v, separators=(",", ":")))) for k, v in d["heat"].items()), key=lambda x: -x[1])
print("  dentro de heat:", ", ".join(f"{k} {v / 1024:.0f} KB" for k, v in hp))
# validación y calibración
clear()
_, dt = timed(app.run_validation, "5m", (25, 50, 100))
print(f"validación 5m: {dt:.2f} s")
if not QUICK:
    clear()
    r, dt = timed(app.calibrate, "5m")
    print(f"calibración 5m ({r.get('tested')} combinaciones): {dt:.1f} s")
# libro
for tf in ("5m", "15m", "1h"):
    clear()
    d = app.get_data(tf, "auto", (25, 50, 100), 0.05)
    _, dt = timed(BK.book_map, d["candles"], app.TF_SECONDS[tf], d["heat"]["size"], d["price"], range_pct=min(app.RANGE_BY_TF[tf], 12.0))
    _, dt2 = timed(BK.book_map, d["candles"], app.TF_SECONDS[tf], d["heat"]["size"], d["price"] + 1000, range_pct=min(app.RANGE_BY_TF[tf], 12.0))
    print(f"mapa del libro {tf}: frío {dt * 1000:.0f} ms · con caché de filas {dt2 * 1000:.0f} ms")
# perfil detallado de lo más caro
for tf, model in (("5m", "auto"), ("15m", "hl")):
    clear()
    pr = cProfile.Profile()
    pr.enable()
    app.build(tf, model, (25, 50, 100) if model != "hl" else tuple(app.ALL_TIERS), 0.05)
    pr.disable()
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(14)
    print(f"── perfil build {tf} {model}")
    print("\n".join(l for l in s.getvalue().splitlines() if l.strip() and ("{" in l or "/" in l or "ncalls" in l))[:3500])
clear()
pr = cProfile.Profile()
pr.enable()
app.run_validation("5m", (25, 50, 100))
pr.disable()
s = io.StringIO()
pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(10)
print("── perfil validación 5m (tiempo propio)")
print("\n".join(l for l in s.getvalue().splitlines() if l.strip() and ("{" in l or "/" in l or "ncalls" in l))[:3000])
