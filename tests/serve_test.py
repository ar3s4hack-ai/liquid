"""Servidor local con datos de mercado simulados para ver la web en un navegador real (sin red).

Uso: python3 tests/serve_test.py . RUTA/lightweight-charts.standalone.production.js 8765
(CZ_SIM=1 simula Coinalyze; CALIB_SIM=0 no calibra al arrancar)."""
import math
import os
import random
import sys
import tempfile
import time

os.environ["COLLECT"] = "0"
REPO, LW, PORT = sys.argv[1], sys.argv[2], int(sys.argv[3])
sys.path.insert(0, REPO)
import liqdata as LQ  # noqa: E402
import app  # noqa: E402
from flask import Response  # noqa: E402

LQ.set_data_dir(tempfile.mkdtemp())
STEP = 300
random.seed(21)


def leg(p0, p1, n, noise):
    return [p0 + (p1 - p0) * (i + 1) / n + random.gauss(0, noise) for i in range(n)]


path = (leg(84300, 84700, 320, 70) + leg(84700, 84600, 120, 60) + leg(84600, 87000, 72, 80) + leg(87000, 86600, 60, 90)
        + leg(86600, 83920, 48, 70) + leg(83920, 84700, 140, 60) + leg(84700, 85300, 140, 70))
now = int(time.time()) // STEP * STEP
start = now - (len(path) - 1) * STEP
C, prev = [], path[0]
for i, c in enumerate(path):
    o = prev
    C.append({"time": start + i * STEP, "open": o, "high": max(o, c) + abs(random.gauss(0, 25)),
              "low": min(o, c) - abs(random.gauss(0, 25)), "close": c, "v": 6e7, "tb": 3e7 + random.gauss(0, 5e6)})
    prev = c
OI, v = {}, 6.0e9
for i, c in enumerate(C):
    j = i - 320
    if 120 <= j < 252:
        v += 6e6 + random.gauss(0, 4e6)
    elif 252 <= j < 300:
        v -= 9e6 + random.gauss(0, 4e6)
    else:
        v += random.gauss(1e6, 3e6)
    OI[c["time"]] = v


def agg(tf_s):
    out = {}
    for c in C:
        t = c["time"] - c["time"] % tf_s
        a = out.get(t)
        if not a:
            out[t] = dict(c, time=t)
        else:
            a["high"], a["low"], a["close"], a["v"], a["tb"] = max(a["high"], c["high"]), min(a["low"], c["low"]), c["close"], a["v"] + c["v"], a["tb"] + c["tb"]
    return [out[t] for t in sorted(out)]


TREND = os.environ.get("TREND", "compra")     # señal de tendencia simulada: compra, espera o venta


def trend_klines(iv, limit):
    """Velas diarias / 4h para la señal de tendencia, terminando en el precio de ahora (la última, en curso)."""
    step = app.TF_SECONDS[iv]
    g = {"compra": 0.002, "espera": 0.002, "venta": -0.002}[TREND] * step / 86400
    if TREND == "espera" and iv == "4h":
        g = -0.0006                       # diario alcista, 4h bajista: 2 de 3
    cur = int(time.time()) // step * step
    rows, prev = [], None
    for i in range(limit):
        t = cur - (limit - 1 - i) * step
        c = C[-1]["close"] * math.exp(g * (i - (limit - 1)) + 0.01 * math.sin(i / 7))
        o = prev if prev is not None else c
        hi, lo, vol = max(o, c) * 1.003, min(o, c) * 0.997, 1000.0
        if iv == "4h" and i == limit - 200:      # una vela con mecha enorme y mucho volumen: nivel de liquidez Zero Lag
            hi, lo, vol = (max(o, c) * 1.03, lo, 9000.0) if g > 0 else (hi, min(o, c) * 0.97, 9000.0)
        rows.append([t * 1000, str(o), str(hi), str(lo), str(c), str(vol), (t + step) * 1000 - 1])
        prev = c
    return rows


def fake_get(url, params=None, headers=None):
    p = params or {}
    if "klines" in url and p.get("interval") in ("1d", "4h") and p.get("limit", 0) >= 500:
        return trend_klines(p["interval"], p["limit"])
    if "klines" in url:
        cs = agg(app.TF_SECONDS[p["interval"]])[-p["limit"]:]
        return [[c["time"] * 1000, str(c["open"]), str(c["high"]), str(c["low"]), str(c["close"]), "0", "0", str(c["v"]), "0", "0",
                 str(c["tb"])] for c in cs]
    if "openInterestHist" in url and "dapi" not in url:
        k = 1.0 if p["symbol"] == "BTCUSDT" else 0.06
        step = app.TF_SECONDS[p["period"]]
        return [{"timestamp": t * 1000, "sumOpenInterestValue": str(x * k)} for t, x in OI.items() if t % step == 0]
    if "bybit" in url:
        return {"retCode": 0, "result": {"list": [{"openInterest": str(OI[c["time"]] * 0.45 / c["close"]), "timestamp": str(c["time"] * 1000)}
                                                  for c in C[-200:]][::-1]}}
    if "rubik" in url:
        return {"code": "0", "data": [[str(c["time"] * 1000), "1", "1", str(OI[c["time"]] * 0.3)] for c in C[-100:]][::-1]}
    if "dapi" in url:
        return [{"timestamp": c["time"] * 1000, "sumOpenInterest": str(OI[c["time"]] * 0.12 / 100)} for c in C[-500:]]
    if "ticker/24hr" in url:
        return {"priceChangePercent": "0.84", "lastPrice": str(C[-1]["close"]), "highPrice": "87100", "lowPrice": "83800"}
    if "premiumIndex" in url:
        return {"lastFundingRate": "0.00004", "nextFundingTime": (now + 3600) * 1000}
    if "fundingRate" in url:          # cobrado cada 8 h: positivo casi siempre, negativo tras el desplome
        t = p["startTime"] // 1000 // 28800 * 28800 + 28800
        rows = []
        while t <= now:
            k = (t // 28800) % 7
            rows.append({"fundingTime": t * 1000, "fundingRate": str([0.0001, 0.00008, 0.00012, -0.00003, 0.00005, 0.0001, -0.00006][k])})
            t += 28800
        return rows
    if "LongShort" in url:
        return [{"longShortRatio": "1.62", "longAccount": "0.618"}]
    if "depth" in url:
        px = C[-1]["close"]
        return {"bids": [[str(px - 5 - i * 3), "0.6"] for i in range(400)] + [[str(px - 420), "35"]],
                "asks": [[str(px + 5 + i * 3), "0.6"] for i in range(400)] + [[str(px + 610), "48"]]}
    raise RuntimeError("no simulado " + url)


app.get_json = fake_get
# liquidaciones reales simuladas donde el modelo tenía zonas (y algo de ruido), 30 h escuchando
out = {}
ht, _ = app.estimate_heat(C, {"binance": OI}, C[-1]["close"], app.tiers_from([25, 50, 100]), "oi", out=out)
thr = 0.1 * ht["max"]
evs = []
for (bn, i, side), vol in out["touches"].items():
    if vol >= thr and random.random() < 0.7:
        px = (bn + 0.5) * ht["size"]
        evs.append(LQ._ev((C[i]["time"] + 30) * 1000, random.choice(["binance", "bybit", "okx", "deribit"]), side, px, px, vol / px * 0.002))
for _ in range(200):
    c = random.choice(C[300:])
    evs.append(LQ._ev((c["time"] + 40) * 1000, "bybit", random.choice([1, -1]), c["close"], c["close"], random.uniform(0.05, 2)))
# una cascada enorme como la real (38,4M en largos en la vela del desplome) para ver que no aplasta al resto
crash = C[619]
evs.append(LQ._ev((crash["time"] + 20) * 1000, "binance", 1, crash["low"], crash["low"], 38.4e6 / crash["low"]))
LQ.save_events(evs)
for m in range(C[0]["time"], now + 60, 60):
    LQ.heartbeat(m)
for c in C:
    LQ.save_oi_snapshots({"hyperliquid": 2.6e9, "bitget": 1.9e9, "deribit": 1.1e9, "bitmex": 4.3e8}, now=c["time"] + 61)

# Hyperliquid simulado: posiciones que se abren a lo largo del gráfico y desaparecen cuando el precio llega a su liquidación
import hl as HL  # noqa: E402

random.seed(7)
active_at = {}
for k in range(70):
    i0 = random.randint(len(C) - 400, len(C) - 2)
    lev = random.choice([3, 5, 10, 20, 25, 40])
    side = random.choice([1, -1])
    p0 = C[i0]["close"]
    liq = p0 * (1 - 1 / lev + 0.005) if side == 1 else p0 * (1 + 1 / lev - 0.005)
    usd = math.exp(random.uniform(math.log(6e4), math.log(6e6)))
    end = len(C)
    for j in range(i0 + 1, len(C)):
        if (side == 1 and C[j]["low"] <= liq) or (side == -1 and C[j]["high"] >= liq):
            end = j
            break
    a = "0x" + f"{k + 1:040x}"
    p = {"szi": side * usd / p0, "entry": p0, "liq": liq, "lev": lev, "cross": True, "ntl": usd, "upd": 0}
    active_at[a] = (i0, end, p)
for j in range(len(C) - 300, len(C), 1):
    HL.pos.clear()
    for a, (i0, end, p) in active_at.items():
        if i0 <= j < end:
            HL.pos[a] = p
    if j % 1 == 0:
        HL.snapshot(C[j]["close"], now=C[j]["time"] + 200)
HL.pos.clear()
for a, (i0, end, p) in active_at.items():
    if end == len(C):
        HL.pos[a] = p
        HL.addrs[a] = {"due": None, "src": "trades", "ntl": p["ntl"]}
HL.STAT.update(hl_oi=2.6e9, since=int(time.time()) - 3600, ws=True, ws_msg=int(time.time()) + 10 ** 6)

# Mapa de liquidez simulado: libro de Binance grabado en bloques de 5 min y 1 h (muros en números redondos que se
# consumen cuando el precio los cruza, muros pasajeros y libro normal más denso cerca del precio)
import book as BK  # noqa: E402
from collections import defaultdict  # noqa: E402

random.seed(11)
walls = [[lvl, random.uniform(2e6, 6e6) * (2.2 if lvl % 1000 == 0 else 1), 0, len(C)] for lvl in range(83000, 88001, 500)]
for _ in range(45):
    i0 = random.randint(0, len(C) - 10)
    walls.append([round(C[i0]["close"] + random.choice([-1, 1]) * random.uniform(120, 1600), -1), random.uniform(1e6, 4e6), i0,
                  i0 + random.randint(6, 140)])
acc60, n60 = defaultdict(lambda: defaultdict(float)), defaultdict(int)
for i, c in enumerate(C):
    mid = c["close"]
    vals = {}
    for b in range(int(mid * 0.94 // 10), int(mid * 1.06 // 10) + 1):
        d = abs((b + 0.5) * 10 - mid) / mid
        vals[b] = (2.2e5 * math.exp(-d * 45) + 2.5e4) * random.uniform(0.5, 1.5)
    for w in walls:
        if w[2] <= i < w[3]:
            vals[int(w[0] // 10)] = vals.get(int(w[0] // 10), 0) + w[1]
            if c["low"] <= w[0] <= c["high"]:
                w[3] = i + 1                         # el precio lo cruza: se consume (o lo quitan)
    BK.save_row("book5", c["time"], vals, 10)
    h = c["time"] - c["time"] % 3600
    for k, v in vals.items():
        acc60[h][k] += v
    n60[h] += 1
for h, a in acc60.items():
    BK.save_row("book60", h, {k: v / n60[h] for k, v in a.items()}, n60[h] * 10)
px_ = C[-1]["close"]
BK.BOOK.bids = {round(px_ - 0.1 - j * 0.5, 1): 0.4 for j in range(6000)}
BK.BOOK.asks = {round(px_ + 0.1 + j * 0.5, 1): 0.4 for j in range(6000)}
BK.BOOK.bids[84500.0] = 60.0
BK.BOOK.asks[86000.0] = 45.0
BK.BOOK.synced, BK.BOOK.last_u = True, 1
BK.STATE.update(connected=True, since=int(time.time()) - 7200, last_msg=int(time.time()))
if os.environ.get("CALIB_SIM", "1") == "1":
    print("calibración simulada:", {k: v for k, v in app.calibrate("5m").items() if k in ("status", "params", "tested", "events")})

# Coinalyze simulado (CZ_SIM=1): Hyperliquid y Gate con histórico, y liquidaciones de mercados que no escuchamos
if os.environ.get("CZ_SIM"):
    import cz as CZ  # noqa: E402
    CZ.KEY = "sim"
    oi_m = [{"symbol": "BTC.H", "name": "hyperliquid", "label": "Hyperliquid BTC", "key": ("hyperliquid", "BTC")},
            {"symbol": "BTC_USDT.Y", "name": "cz1", "label": "Gate BTC_USDT", "key": ("gate", "BTCUSDT")},
            {"symbol": "PF_XBTUSD.K", "name": "cz2", "label": "Kraken PF_XBTUSD", "key": ("kraken", "PFXBTUSD")}]
    liq_m = [{"symbol": "BTCUSDT_PERP.A", "key": ("binance", "BTCUSDT")}, {"symbol": "BTC.H", "key": ("hyperliquid", "BTC")},
             {"symbol": "BTCUSD_PERP.A", "key": ("binance", "BTCUSDPERP")}, {"symbol": "PF_XBTUSD.K", "key": ("kraken", "PFXBTUSD")}]
    CZ.STATE.update(oi=oi_m, liq=liq_m, markets=oi_m, discovered=int(time.time()))
    for tf_, step_ in app.TF_SECONDS.items():
        cs_ = agg(step_)
        oi_d = {m["symbol"]: {c["time"]: OI[max((t for t in OI if t <= c["time"]), default=min(OI))] * f for c in cs_}
                for m, f in zip(oi_m, (0.4, 0.12, 0.03))}
        lq = {m["symbol"]: {c["time"]: [abs(random.gauss(0, 4e4)) * (step_ / 300) ** 0.5, abs(random.gauss(0, 3e4)) * (step_ / 300) ** 0.5]
                            for c in cs_} for m in liq_m}
        CZ.STATE["data"][tf_] = {"oi": oi_d, "liq": lq, "at": time.time()}

page = open(os.path.join(REPO, "static/index.html"), encoding="utf-8").read().replace(
    "https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js", "/lw.js")
lw = open(LW, encoding="utf-8").read()
app.app.view_functions["index"] = lambda: Response(page, mimetype="text/html")
app.app.add_url_rule("/lw.js", "lw", lambda: Response(lw, mimetype="application/javascript"))


def boom():
    """Añade una cascada de liquidaciones de largos «ahora» (para ver pulsos y avisos)."""
    t = int(time.time() * 1000)
    px = C[-1]["close"]
    LQ.save_events([LQ._ev(t, "binance", 1, px - 40, px - 40, 4.2), LQ._ev(t + 400, "bybit", 1, px - 55, px - 55, 2.1),
                    LQ._ev(t + 900, "okx", -1, px + 30, px + 30, 0.9)])
    return "ok"


app.app.add_url_rule("/boom", "boom", boom)
app.app.run(host="127.0.0.1", port=PORT, threaded=True)
