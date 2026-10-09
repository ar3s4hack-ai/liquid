"""Pruebas del servidor (sin red): modelo, fuentes, feed en directo, rutas, validación, calibración y recolectores."""
import json
import os
import random
import sys
import tempfile
import time

REAL_SLEEP = time.sleep
os.environ["COLLECT"] = "0"
REPO = sys.argv[1]
OUT = sys.argv[2]
sys.path.insert(0, REPO)
import liqdata as LQ  # noqa: E402
import app  # noqa: E402

LQ.set_data_dir(tempfile.mkdtemp())
STEP = 300
BASE = 1759363200  # 02-10-2026 00:00 UTC
ok = lambda m: print("✓", m)  # noqa: E731


def flat(n, p=100000.0, start=BASE):
    return [{"time": start + i * STEP, "open": p, "high": p * 1.0001, "low": p * 0.9999, "close": p, "v": 1e6, "tb": 5e5}
            for i in range(n)]


def tot(heat, k):
    return sum(a[1 if k == "L" else 2] for a in heat["active"])


# ───── 1) código eliminado
for name in ("coinglass_heat", "zones_from_bins", "cluster_bands", "CG_KEY", "USE_BYBIT"):
    assert not hasattr(app, name), name
assert app.MODELS == ("auto", "oi", "vol", "hl") and app.DEFAULT_MODEL == "auto"
cl = app.app.test_client()
assert cl.get("/api/price").status_code == 404
assert not hasattr(app, "CG") and not os.path.exists(os.path.join(REPO, "cg.py"))     # la comprobación de CoinGlass (API de pago) fuera
ok("código sobrante eliminado (Coinglass, vista limpia, /api/price, USE_BYBIT)")

# ───── 2) modelo
cs = flat(3)
cs[1].update(open=100000, high=100100, low=99900, close=100050)
oi = {cs[0]["time"]: 1000.0, cs[1]["time"]: 1100.0, cs[2]["time"]: 1100.0}
out = {}
h, z = app.estimate_heat(cs, {"binance": oi}, 100000.0, app.tiers_from([25]), "oi", out=out)
assert abs(tot(h, "L") - 100) <= 1 and abs(tot(h, "S") - 100) <= 1
assert out["doi"] == [None, 100.0, 0.0], out["doi"]
assert set(h) == {"size", "max", "max_abs", "segments", "F", "active"}, set(h)
typ = (100100 + 99900 + 100050) / 3
lp = [a[0] for a in h["active"] if a[1] > 0][0]
assert abs(lp - typ * (1 - 1 / 25 + app.MMR)) < h["size"]
# cierres por bajada de OI (solo si se activan)
cs = flat(4)
cs[1].update(high=100100, low=99900)
cs[2].update(open=100000, close=100030, high=100040, low=99990)
oi = {cs[0]["time"]: 1000.0, cs[1]["time"]: 1100.0, cs[2]["time"]: 1060.0, cs[3]["time"]: 1060.0}
h, _ = app.estimate_heat(cs, {"binance": oi}, 100000.0, app.tiers_from([25]), "oi", params={"close_on_drop": True})
assert abs(tot(h, "L") - 100) <= 1 and abs(tot(h, "S") - 60) <= 1
h, _ = app.estimate_heat(cs, {"binance": oi}, 100000.0, app.tiers_from([25]), "oi")
assert abs(tot(h, "L") - 100) <= 1 and abs(tot(h, "S") - 100) <= 1      # por defecto no se cierra nada
# envejecimiento
cs = flat(14)
cs[1].update(high=100100, low=99900)
oi = {c["time"]: (1000.0 if i == 0 else 1100.0) for i, c in enumerate(cs)}
h1, _ = app.estimate_heat(cs, {"binance": oi}, 100000.0, app.tiers_from([25]), "oi", params={"half_life_h": 1.0})
h0, _ = app.estimate_heat(cs, {"binance": oi}, 100000.0, app.tiers_from([25]), "oi")
assert abs(tot(h1, "L") / tot(h0, "L") - 0.5) < 0.01
ok("modelo: ambos lados, precio típico, ΔOI por vela, cierres opcionales y envejecimiento")

# escenario de la captura: subida, caída a 83.900 y rebote -> 83.500 debe ser la zona fuerte
random.seed(21)


def leg(p0, p1, n, noise):
    return [p0 + (p1 - p0) * (i + 1) / n + random.gauss(0, noise) for i in range(n)]


path = (leg(84500, 84600, 120, 60) + leg(84600, 87000, 72, 80) + leg(87000, 86600, 60, 90)
        + leg(86600, 83920, 48, 70) + leg(83920, 84700, 140, 60) + leg(84700, 85300, 140, 70))
scen, prev = [], path[0]
for i, c in enumerate(path):
    o = prev
    scen.append({"time": BASE - 86400 + i * STEP, "open": o, "high": max(o, c) + abs(random.gauss(0, 25)),
                 "low": min(o, c) - abs(random.gauss(0, 25)), "close": c, "v": 6e7, "tb": 3e7})
    prev = c
soi, v = {}, 6.0e9
for i, c in enumerate(scen):
    if 120 <= i < 252:
        v += 6e6 + random.gauss(0, 4e6)
    elif 252 <= i < 300:
        v -= 9e6 + random.gauss(0, 4e6)
    else:
        v += random.gauss(1e6, 3e6)
    soi[c["time"]] = v
sp = scen[-1]["close"]
h, z = app.estimate_heat(scen, {"binance": soi}, sp, app.tiers_from([25, 50, 100]), "oi", params={"bin_pct": 0.05})
zmax = max(a[1] for a in h["active"] if 83200 <= a[0] <= 83850)
assert zmax / h["max_abs"] >= 0.8, zmax / h["max_abs"]
ok(f"escenario 83.500: {round(100 * zmax / h['max_abs'])} % del maxHeat")

# ───── 3) fuentes de Open Interest (paralelo, conversión y fallos aislados)
n = 300
now0 = (int(time.time()) // STEP) * STEP
cs2 = [{"time": now0 - (n - 1 - i) * STEP, "open": 86000, "high": 86050, "low": 85950, "close": 86000.0, "v": 5e7, "tb": 2.6e7}
       for i in range(n)]
calls = []


def fake_get(url, params=None, headers=None):
    calls.append(url)
    p = params or {}
    if "klines" in url:
        return [[c["time"] * 1000, str(c["open"]), str(c["high"]), str(c["low"]), str(c["close"]), "0", "0", str(c["v"]), "0", "0",
                 str(c["tb"])] for c in cs2]
    if "dapi" in url:
        return [{"timestamp": c["time"] * 1000, "sumOpenInterest": str(9e6 + i)} for i, c in enumerate(cs2)]
    if "openInterestHist" in url:
        k = 5e9 if p["symbol"] == "BTCUSDT" else 4e8
        return [{"timestamp": c["time"] * 1000, "sumOpenInterestValue": str(k + i * 1e6)} for i, c in enumerate(cs2)]
    if "bybit" in url:
        return {"retCode": 0, "result": {"list": [{"openInterest": str(4e4 + i), "timestamp": str(c["time"] * 1000)}
                                                  for i, c in enumerate(cs2[-200:])][::-1]}}
    if "rubik" in url:
        rows = [[str(c["time"] * 1000), "1", "1", str((2.5e9 if "USDT" in p["instId"] else 5e8) + i * 1e5)]
                for i, c in enumerate(cs2)][::-1]
        if "end" in p:
            rows = [r for r in rows if int(r[0]) <= p["end"]]
        return {"code": "0", "data": rows[:p["limit"]]}
    if "ticker/24hr" in url:
        return {"priceChangePercent": "1.25", "lastPrice": "86000", "highPrice": "87000", "lowPrice": "85000"}
    if "premiumIndex" in url:
        return {"lastFundingRate": "0.0001", "nextFundingTime": 1}
    if "fundingRate" in url:
        t0 = p["startTime"] // 1000
        return [{"fundingTime": (t0 + k * 28800) * 1000, "fundingRate": str(0.0001 * (1 if k % 3 else -1))} for k in range(6)]
    if "LongShort" in url:
        return [{"longShortRatio": "1.5", "longAccount": "0.6"}]
    if "depth" in url:
        return {"bids": [[str(85990 - i), "0.5"] for i in range(300)] + [["85700", "40"]],
                "asks": [[str(86010 + i), "0.5"] for i in range(300)] + [["86450", "55"]]}
    raise RuntimeError("no simulado " + url)


app.get_json = fake_get
for i, c in enumerate(cs2):          # 24 h de grabación de las 4 fuentes sin histórico
    LQ.save_oi_snapshots({"hyperliquid": 2.5e9 + i * 1e5, "bitget": 1.8e9, "deribit": 1.0e9, "bitmex": 4e8}, now=c["time"] + 61)
src, errs = app.load_sources("5m", cs2)
assert set(src) == set(app.ALL_SOURCES) and not errs, (sorted(src), errs)
t = cs2[100]["time"]
assert abs(src["bybit"][cs2[-1]["time"]] - (4e4 + 199) * 86000) < 1e-3     # Bybit en BTC -> USD
assert abs(src["binance_coinm"][t] - (9e6 + 100) * 100) < 1
only_okx, _ = app.load_sources("5m", cs2, ["okx"])
assert set(only_okx) == {"okx_usdt", "okx_usd"}


def flaky(url, params=None, headers=None):
    if "rubik" in url or "dapi" in url:
        raise RuntimeError("caída simulada")
    return fake_get(url, params, headers)


app.get_json = flaky
app._okx_cache.clear()
src2, errs2 = app.load_sources("5m", cs2)
assert "binance" in src2 and "okx_usdt" not in src2 and len(errs2) == 3, errs2
app.get_json = fake_get
app._okx_cache.clear()
ok(f"fuentes: {len(src)} de 10 · filtro por exchange · fallos aislados ({len(errs2)} avisos)")

summ = app.oi_summary(src, cs2, STEP)
assert summ["n"] == 10 and summ["usd"] > 1e10 and summ["chg24"] is not None
stale = dict(src, bitmex={cs2[0]["time"]: 1e9})
assert app.oi_summary(stale, cs2, STEP)["n"] == 10 - 1 + 0       # una fuente atrasada no cuenta
ok(f"OI total {summ['usd'] / 1e9:.1f}B · {summ['chg24']:+.2f} % 24 h")

# ───── 4) liquidaciones: barras por vela, cursor y feed en directo sin duplicados (misma base que la grabación de OI)
assert LQ.events_after(0) == ([], 0)
last = cs2[-1]["time"]
LQ.save_events([LQ._ev((last + 5) * 1000, "bybit", 1, 85900, 85900, 2.0),
                LQ._ev((last + 9) * 1000, "okx", -1, 86100, 86100, 0.5),
                LQ._ev((last - STEP + 3) * 1000, "deribit", 1, 85800, 85800, 0.01)])
chl = LQ.chart_liqs(cs2, STEP)
hist = {r[0]: r[1:] for r in chl["hist"]}
assert chl["rowid"] == 3 and hist[last] == [round(2 * 85900), round(0.5 * 86100)] and hist[last - STEP] == [858, 0], chl["hist"]
assert len(chl["items"]) == 2          # la de 858 $ no llega al mínimo de burbuja (5.000 $)
evs, cur = LQ.events_after(0)
assert evs == [] and cur == 3
LQ.save_events([LQ._ev((last + 20) * 1000, "binance", -1, 86200, 86200, 3.0)])
evs, cur = LQ.events_after(3)
assert [e["rowid"] for e in evs] == [4] and cur == 4 and LQ.events_after(4) == ([], 4)
assert LQ.events_after(999) == ([], 4)            # cursor de otra base de datos: se reinicia
r = cl.get("/api/live?after=3").get_json()
assert r["cursor"] == 4 and r["events"][0][0] == 4 and r["events"][0][2] == "binance" and r["events"][0][5] == round(3 * 86200)
assert cl.get("/api/live?after=x").status_code == 400
ok("liquidaciones: barras por vela, cursor y /api/live sin contar dos veces")

# ───── 5) /api/data
app._cache.clear()
app._candle_cache.clear()
d = cl.get("/api/data?tf=5m").get_json()
assert "error" not in d, d
for k in ("source", "collectors"):
    assert k not in d, k
assert d["model"] == "auto" and d["models"] == ["auto", "oi", "vol", "hl"] and d["levs"] == [25, 50, 100]
assert d["calib"] == {"status": "pendiente"}
assert len(d["used"]) == 10 and d["oi"]["n"] == 10 and d["oi"]["chg24"] is not None
assert d["doi"] and len(d["doi"]) == n - 1 and d["doi"][0][0] == cs2[1]["time"]
assert d["liqs"]["rowid"] == 4 and d["liqs"]["hist"] and d["ctx"]["chg24"] == 1.25
assert d["book"]["bids"][0][1] > 1e6
assert set(d["heat"]) == {"size", "max", "max_abs", "segments", "F", "active"}
dv = cl.get("/api/data?tf=5m&model=vol").get_json()
assert dv["used"] == ["volumen"] and dv["doi"] == [] and dv["oi"] is None
dp = cl.get("/api/data?tf=5m&bin=0.05&lev=10,25").get_json()
assert abs(dp["heat"]["size"] - dp["price"] * 0.0005) < 1e-6 and dp["levs"] == [25, 50, 100]   # en Auto los pools los decide Auto
do = cl.get("/api/data?tf=5m&model=oi&lev=10,25&ex=binance").get_json()
assert do["levs"] == [10, 25] and set(do["used"]) <= set(app.EX_GROUPS["binance"]) and do["ex"] == ["binance"]
assert cl.get("/api/data?tf=9x").status_code == 400 and cl.get("/api/data?bin=3").status_code == 400
assert cl.get("/api/data?lev=a").status_code == 400
# v2: CVD (futuros y spot), gasolina, funding, Hyperliquid y Coinalyze en la respuesta
assert len(d["cvd"]) == n and d["cvd"][5] == [cs2[5]["time"], round(2 * 2.6e7 - 5e7), round(2 * 2.6e7 - 5e7)]
assert len(d["fuel"]) == n and all(L >= 0 and S >= 0 for _, L, S in d["fuel"]) and d["fuel"][-1][1] > 0
assert len(d["funding"]) == 6 and d["funding"][0][1] == -0.0001 and d["funding"] == sorted(d["funding"])
assert "book_map" not in d
db_ = cl.get("/api/data?tf=5m&book=1").get_json()
assert "book_map" in db_ and db_["book_map"] is None            # aún no hay libro grabado
assert d["hl"]["positions"] == 0 and d["hl"]["top"] == [] and d["labels"] == {} and d["credits"] == []
assert d["liqs"]["exchanges"] == 8 and "cz_markets" not in d["liqs"]
# v=2: velas en listas y sin las partes que no se piden (burbujas, gasolina, CVD, muros)
d2 = cl.get("/api/data?tf=5m&v=2&want=").get_json()
assert d2["candles"][0] == [d["candles"][0][k] for k in ("time", "open", "high", "low", "close")] and len(d2["candles"]) == n
assert d2["liqs"]["items"] == [] and d2["liqs"]["hist"] == d["liqs"]["hist"] and d2["fuel"] == [] and d2["cvd"] == [] and d2["book"] is None
assert d2["heat"] == d["heat"] and d2["doi"] == d["doi"] and d2["zones"] == d["zones"] and d2["funding"] == d["funding"]
d3 = cl.get("/api/data?tf=5m&v=2&want=liq,fuel,cvd,walls").get_json()
assert d3["liqs"] == d["liqs"] and d3["fuel"] == d["fuel"] and d3["cvd"] == d["cvd"] and d3["book"] == d["book"]
raw_full, raw_slim = len(cl.get("/api/data?tf=5m").data), len(cl.get("/api/data?tf=5m&v=2&want=cvd").data)
assert raw_slim < 0.85 * raw_full, (raw_full, raw_slim)
json.dump(d, open(os.path.join(OUT, "payload_auto.json"), "w"))
json.dump(dv, open(os.path.join(OUT, "payload_vol.json"), "w"))
json.dump(do, open(os.path.join(OUT, "payload_oi.json"), "w"))
ok(f"/api/data: {len(d['heat']['segments'])} segmentos · {len(d['doi'])} barras ΔOI · OI {d['oi']['usd'] / 1e9:.1f}B · "
   f"compacta (v=2) {raw_slim / 1024:.0f} KB frente a {raw_full / 1024:.0f} KB")

# ───── 6) HTTP: reintento y caché de velas
class Resp:
    def __init__(self, code, body):
        self.status_code, self.body = code, body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise app.requests.HTTPError(str(self.status_code))

    def json(self):
        return self.body


seq = []


def fake_http(script):
    def _get(url, params=None, headers=None, timeout=None):
        x = script.pop(0)
        seq.append(x if isinstance(x, int) else "exc")
        if isinstance(x, Exception):
            raise x
        return Resp(x, {"ok": True})
    return _get


import importlib  # noqa: E402
app2 = importlib.reload(app)                        # get_json original, sin el falso de las pruebas
app2._http.get = fake_http([app2.requests.ConnectionError("corte"), 200])
assert app2.get_json("https://x") == {"ok": True} and seq == ["exc", 200]
seq.clear(); app2._http.get = fake_http([502, 200])
assert app2.get_json("https://x") == {"ok": True} and seq == [502, 200]
seq.clear(); app2._http.get = fake_http([451, 200])
try:
    app2.get_json("https://x")
    raise AssertionError("no debía reintentar un 4xx")
except app2.requests.HTTPError:
    assert seq == [451]
ok("HTTP: reintenta cortes y 5xx, no reintenta 4xx (p. ej. 451)")
app = app2
LQ.set_data_dir(tempfile.mkdtemp())
n_calls = []


def counting(url, params=None, headers=None):
    n_calls.append(url)
    return fake_get(url, params, headers)


app.get_json = counting
a1 = app.fetch_candles("5m")
a1[0]["close"] = -1
a2 = app.fetch_candles("5m")
assert len([u for u in n_calls if "klines" in u]) == 1 and a2[0]["close"] == 86000.0
ok("caché de velas (15 s) sin compartir objetos")

# ───── 7) validación y calibración con liquidaciones sembradas
random.seed(3)
wk = []
p = 86000.0
for i in range(700):
    o = p
    c = o + random.gauss(0, 60)
    wk.append({"time": BASE + i * STEP, "open": o, "high": max(o, c) + abs(random.gauss(0, 25)),
               "low": min(o, c) - abs(random.gauss(0, 25)), "close": c, "v": 5e7, "tb": 2.5e7})
    p = c
woi, v = {}, 5e9
for c in wk:
    v = max(1e9, v + random.gauss(2e6, 2e7))
    woi[c["time"]] = v
covered = {m for m in range(wk[0]["time"], wk[-1]["time"] + STEP, 60)}
out = {}
ht, _ = app.estimate_heat(wk, {"binance": woi}, wk[-1]["close"], app.tiers_from([25, 50, 100]), "oi", out=out)
thr = 0.1 * ht["max"]
planted = []
for (bn, i, side), vol in out["touches"].items():
    if vol >= thr:
        px = (bn + 0.5) * ht["size"]
        planted.append(LQ._ev((wk[i]["time"] + 30) * 1000, "bybit", side, px, px, vol / px))
rv = LQ.validate(wk, STEP, ht, out["touches"], planted, covered)
assert rv["hit"] > 0.9 and rv["lift"] > 1.3, rv
LQ.save_events(planted)
for m in sorted(covered):
    LQ.heartbeat(m)


def wk_get(url, params=None, headers=None):
    if "klines" in url:
        return [[c["time"] * 1000, str(c["open"]), str(c["high"]), str(c["low"]), str(c["close"]), "0", "0", str(c["v"]), "0", "0",
                 str(c["tb"])] for c in wk]
    if "openInterestHist" in url and params.get("symbol") == "BTCUSDT":
        return [{"timestamp": t * 1000, "sumOpenInterestValue": str(x)} for t, x in woi.items()]
    raise RuntimeError("fuente apagada en la prueba")


app.get_json = wk_get
app._candle_cache.clear()
app.CANDLES["5m"] = len(wk)
cal = app.calibrate("5m")
assert cal["status"] == "ok" and cal["params"]["tiers"] == [25, 50, 100] and cal["score"] > 1.2, cal
val = app.run_validation("5m", [25, 50, 100])
assert set(val["models"]) == {"auto", "oi", "vol"} and val["calib"]["status"] == "ok"
ok(f"validación (acierto {rv['hit']:.0%}, mejora {rv['lift']:.2f}×) y calibración ({cal['tested']} combinaciones)")

# ───── 8) alertas, Asia, lectores y grabadora
app._alert_state["zones"].clear(); app._alert_state["sweeps"].clear()
dz = {"symbol": "BTCUSDT", "price": 100000.0, "zones": [{"price": 100300.0, "side": "short", "total": 5, "ratio": 0.9}],
      "asia": {"start": BASE, "sweep": {"side": "high", "time": BASE + 9 * 3600, "level": 100250.0}}}
msgs = app.check_alerts(dz, BASE + 9 * 3600 + 60)
assert len(msgs) == 2 and "300 puntos" in msgs[0] and "Coincide con zona de cortos" in msgs[1]
c15 = [{"time": BASE - 8 * 3600 + i * 900, "open": 100, "high": 101, "low": 99, "close": 100} for i in range(80)]
for c in c15:
    if c["time"] == BASE + 2 * 3600:
        c["high"] = 105
    if c["time"] == BASE + 9 * 3600:
        c["high"], c["close"] = 106, 104
ai = app.asia_info(c15)
assert ai["high"] == 105 and ai["sweep"]["side"] == "high"
assert LQ.parse_binance({"data": {"e": "forceOrder", "o": {"s": "BTCUSDT", "S": "SELL", "z": "0.014", "ap": "9910", "T": 1}}})[0]["side"] == 1
assert [e["side"] for e in LQ.parse_bybit({"topic": "allLiquidation.BTCUSDT", "data": [
    {"T": 1, "s": "BTCUSDT", "S": "Buy", "v": "0.5", "p": "85500"}, {"T": 1, "s": "BTCUSDT", "S": "Sell", "v": "0.2", "p": "86400"}]})] == [1, -1]
assert [e["side"] for e in LQ.parse_okx({"arg": {"channel": "liquidation-orders"}, "data": [{"instId": "BTC-USDT-SWAP", "details": [
    {"side": "buy", "posSide": "short", "bkPx": "86000", "sz": "10", "ts": "1"}]}]})] == [-1]
dt = {"timestamp": 1, "price": 8960, "mark_price": 8948.9, "instrument_name": "BTC-PERPETUAL", "direction": "buy", "amount": 20, "liquidation": "T"}
assert LQ.parse_deribit({"method": "subscription", "params": {"channel": "trades.BTC-PERPETUAL.100ms", "data": [dt]}})[0]["side"] == -1
assert LQ.parse_bitmex({"table": "liquidation", "action": "insert", "data": [{"symbol": "XBTUSD", "side": "Sell", "price": 8829.5,
                                                                              "leavesQty": 68000}]})[0]["usd"] == 68000


class R2:
    def __init__(s, d): s.d = d
    def json(s): return s.d


app.requests.post = lambda url, json=None, timeout=None: R2([{"universe": [{"name": "BTC"}]}, [{"openInterest": "30000", "markPx": "86000"}]])
app.get_json = lambda url, params=None, headers=None: (
    {"result": [{"open_interest": 1.05e9}]} if "deribit" in url else [{"openInterest": 4.2e8}] if "bitmex" in url
    else {"data": {"openInterestList": [{"size": "25000"}]}} if "bitget" in url else {"price": "86000"})
LQ.MARK["price"] = 86000.0
snap = app.snapshot_oi_now()
assert set(snap) == {"hyperliquid", "bitget", "deribit", "bitmex"}
ok("alertas, rango asiático, lectores de 5 exchanges y grabadora de OI")
# ───── 10) Bitget y Gate: lectores con datos reales (07-10-2026) y consultas sin duplicados
GATE_REAL = [
    {"contract": "BTC_USDT", "left": "0", "size": "-123", "order_size": "123", "fill_price": "84288.8", "order_price": "84540.5", "time": 1791352739},
    {"contract": "BTC_USDT", "left": "0", "size": "-2", "order_size": "2", "fill_price": "84276.6", "order_price": "84535", "time": 1791352733},
    {"contract": "BTC_USDT", "left": "0", "size": "-3", "order_size": "3", "fill_price": "84266", "order_price": "84512.1", "time": 1791352245},
    {"contract": "BTC_USDT", "left": "0", "size": "-13", "order_size": "13", "fill_price": "84227.8", "order_price": "84479.6", "time": 1791352093},
    {"contract": "BTC_USDT", "left": "0", "size": "-120", "order_size": "120", "fill_price": "84227.8", "order_price": "84479.4", "time": 1791352087},
    {"contract": "BTC_USDT", "left": "0", "size": "-3231", "order_size": "3231", "fill_price": "84226", "order_price": "84476.8", "time": 1791352086},
    {"contract": "BTC_USDT", "left": "0", "size": "-26", "order_size": "26", "fill_price": "84226", "order_price": "84477.5", "time": 1791352086},
    {"contract": "BTC_USDT", "left": "0", "size": "-38", "order_size": "38", "fill_price": "84223.3", "order_price": "84474", "time": 1791352085},
    {"contract": "BTC_USDT", "left": "0", "size": "-9", "order_size": "9", "fill_price": "84220", "order_price": "84466.5", "time": 1791352076},
    {"contract": "ETH_USDT", "left": "0", "size": "50", "order_size": "-50", "fill_price": "3000", "order_price": "2990", "time": 1791352076},
]
BITGET_REAL = {"code": "00000", "msg": "success", "data": {"list": [
    {"symbol": "BTCUSDT", "side": "sell", "price": "84405.712741570031765", "amount": "0.0009", "ts": "1791342474115"},
    {"symbol": "BTCUSDT", "side": "buy", "price": "83379.3419988320678962", "amount": "0.3463", "ts": "1791340008968"},
    {"symbol": "BTCUSDT", "side": "buy", "price": "83326.8986130226594327", "amount": "1.2128", "ts": "1791338478742"},
    {"symbol": "ETHUSDT", "side": "buy", "price": "3000", "amount": "1", "ts": "1791338478742"}], "cursor": "1491547174744117255"}}
ge = LQ.parse_gate(GATE_REAL)
assert len(ge) == 9 and all(e["side"] == -1 and e["ex"] == "gate" for e in ge)          # todo cortos: el precio subía
assert ge[0]["ts"] == 1791352739000 and ge[0]["price"] == 84540.5 and ge[0]["mkt"] == 84288.8
assert abs(ge[0]["qty"] - 0.0123) < 1e-12 and abs(ge[0]["usd"] - 0.0123 * 84288.8) < 1e-6
assert LQ.parse_gate([dict(GATE_REAL[0], size="40", order_size="-40", left="-10")])[0]["side"] == 1      # largo, 30 ejecutados
assert abs(LQ.parse_gate([dict(GATE_REAL[0], size="40", order_size="-40", left="-10")])[0]["qty"] - 0.003) < 1e-12
assert LQ.parse_gate({"label": "ERROR"}) == [] and LQ.parse_gate([{"contract": "BTC_USDT", "size": "x"}]) == []
LQ._mark_hist.clear()
be = LQ.parse_bitget(BITGET_REAL)
assert [e["side"] for e in be] == [-1, 1, 1] and be[2]["qty"] == 1.2128 and be[2]["mkt"] is None   # «buy» = largo liquidado
for k in range(30):                                       # precio de marca de Binance cada segundo alrededor de la cascada
    LQ._mark_hist.append((1791338460000 + k * 1000, 83900.0 - k * 10))
assert LQ.mark_at(1791338478742) == 83900.0 - 19 * 10 and LQ.mark_at(1791338478742 + 60000) is None
be = LQ.parse_bitget(BITGET_REAL)
assert be[2]["mkt"] == 83710.0 and abs(be[2]["usd"] - 1.2128 * 83710.0) < 1e-6 and be[1]["mkt"] is None
assert LQ.parse_bitget({"code": "40001"}) == [] and LQ.parse_bitget(None) == []
assert set(LQ.EXCHANGES) >= {"bitget", "gate"} and set(LQ.status()) >= {"bitget", "gate"}

# consultas repetidas y reinicio: cada liquidación se guarda una sola vez
now0 = 1791352760.0
LQ.STATUS["gate"].update(events=0, since=None)
calls = []


def gate_fetch(since_ms, now):
    calls.append(since_ms)
    return [r for r in GATE_REAL if r["time"] <= now]


before = sum(1 for e in LQ.events_between(now0 - 4000, now0 + 100) if e["ex"] == "gate")
gc = LQ.RESTCollector("gate", gate_fetch, LQ.parse_gate)
n1 = gc.poll_once(now0)
assert [e["ts"] // 1000 for e in n1] == [1791352733, 1791352739], n1      # al arrancar solo los últimos 3 min
assert gc.poll_once(now0 + 10) == [] and calls[-1] == 1791352739000
GATE_REAL.insert(0, {"contract": "BTC_USDT", "left": "0", "size": "500", "order_size": "-500", "fill_price": "84100",
                     "order_price": "83900", "time": 1791352765})
n3 = gc.poll_once(now0 + 20)
assert len(n3) == 1 and n3[0]["side"] == 1
gc2 = LQ.RESTCollector("gate", gate_fetch, LQ.parse_gate)                  # reinicio del servidor
assert gc2.poll_once(now0 + 30) == []
after = sum(1 for e in LQ.events_between(now0 - 4000, now0 + 100) if e["ex"] == "gate")
assert after - before == 3 and LQ.STATUS["gate"]["events"] == 3
st = LQ.status()["gate"]
assert st["events"] == 3 and st["error"] is None


def boom_fetch(since_ms, now):
    raise LQ.requests.ConnectionError("caído")


bad = LQ.RESTCollector("bitget", boom_fetch, LQ.parse_bitget, every=0)
LQ.time.sleep = lambda s: (_ for _ in ()).throw(SystemExit)
try:
    bad.run()
except SystemExit:
    pass
LQ.time.sleep = REAL_SLEEP
assert LQ.STATUS["bitget"]["error"].startswith("caído") and LQ.STATUS["bitget"]["connected"] is False

# Bitget: si llega una página llena de nuevas se pide la siguiente
pages = {None: ([{"symbol": "BTCUSDT", "side": "buy", "price": "84000", "amount": "0.1", "ts": str(2000 + i)} for i in range(100)], "c1"),
         "c1": ([{"symbol": "BTCUSDT", "side": "sell", "price": "84100", "amount": "0.2", "ts": str(1000 + i)} for i in range(30)], "c2")}
seen_params = []


class FakeResp:
    def __init__(s, j): s.j = j
    def raise_for_status(s): pass
    def json(s): return s.j


def bitget_get(url, params=None, timeout=None):
    seen_params.append(dict(params))
    rows, cur = pages[params.get("cursor")]
    return FakeResp({"code": "00000", "data": {"list": rows, "cursor": cur}})


real_get = LQ.requests.get
LQ.requests.get = bitget_get
got = LQ._fetch_bitget(500, 0)
assert len(got["data"]["list"]) == 130 and [p.get("cursor") for p in seen_params] == [None, "c1"]
seen_params.clear()
assert len(LQ._fetch_bitget(2050, 0)["data"]["list"]) == 100 and len(seen_params) == 1   # ya visto: no hace falta más
LQ.requests.get = real_get
ok("Bitget y Gate: lados comprobados con datos reales, precio de marca para situarlas y sin duplicados al repetir o reiniciar")

# ───── 11) HTX: lector (formato v3 y v1), lados y errores
LQ._mark_hist.clear()
for k in range(30):
    LQ._mark_hist.append((1791350000000 + k * 1000, 84000.0 + k))
HTX_V3 = {"code": 200, "msg": "", "ts": 1791350030000, "data": [
    {"query_id": 1, "contract_code": "BTC-USDT", "symbol": "BTC", "direction": "sell", "offset": "close", "volume": 250, "amount": 0.25,
     "trade_turnover": 21000.0, "price": 84010.5, "created_at": 1791350010000, "business_type": "swap", "pair": "BTC-USDT"},
    {"query_id": 2, "contract_code": "BTC-USDT", "symbol": "BTC", "direction": "buy", "offset": "close", "volume": 1000,
     "price": 84300.0, "created_at": 1791350020000},
    {"query_id": 3, "contract_code": "ETH-USDT", "direction": "buy", "volume": 5, "amount": 0.005, "price": 3000, "created_at": 1791350020000}]}
he = LQ.parse_htx(HTX_V3)
assert [e["side"] for e in he] == [1, -1] and he[0]["qty"] == 0.25 and abs(he[1]["qty"] - 1000 * LQ.HTX_CT["v"]) < 1e-12
assert he[0]["mkt"] == 84010.0 and he[1]["mkt"] == 84020.0 and he[0]["ex"] == "htx" and abs(he[0]["usd"] - 0.25 * 84010.0) < 1e-6
assert LQ.parse_htx({"status": "ok", "data": {"orders": HTX_V3["data"][:1]}})[0]["side"] == 1          # formato v1
assert LQ.parse_htx({"code": 200, "data": None}) == [] and LQ.parse_htx("x") == []
assert LQ.parse_htx({"data": [{"contract_code": "BTC-USDT", "direction": "x", "price": 1, "amount": 1, "created_at": 1}]}) == []
assert "htx" in LQ.EXCHANGES and "htx" in LQ.status() and len(LQ.EXCHANGES) == 8


class FR2:
    def __init__(s, j, code=200): s.j, s.status_code = j, code
    def raise_for_status(s):
        if s.status_code >= 400:
            raise LQ.requests.HTTPError(str(s.status_code))
    def json(s): return s.j


real_get = LQ.requests.get
seen_htx = []
LQ.requests.get = lambda url, params=None, timeout=None: (seen_htx.append((url, dict(params))), FR2(HTX_V3))[1]
assert LQ._fetch_htx(0, 0) is HTX_V3 and seen_htx[0][1] == {"contract": "BTC-USDT", "trade_type": 0}
LQ.requests.get = lambda url, params=None, timeout=None: FR2({"code": 1032, "msg": "Too many requests"})
try:
    LQ._fetch_htx(0, 0)
    raise AssertionError("debió fallar")
except RuntimeError as e:
    assert "Too many requests" in str(e)
LQ.requests.get = real_get
ok("HTX: largos con «sell», cortos con «buy», BTC del contrato y precio de marca; errores visibles")

# ───── 12) Hyperliquid: posiciones reales, descubrimiento, límite de consultas, mapa en el tiempo y validación
import hl as HL  # noqa: E402

HL.addrs.clear()
HL.pos.clear()
A = ["0x" + f"{i:040x}" for i in range(1, 9)]
MIXED = "0x" + "aB" * 20
T = 1_800_000_000.0
msg = {"channel": "trades", "data": [
    {"coin": "BTC", "side": "B", "px": "84000", "sz": "0.5", "time": 1, "hash": "0x0", "tid": 1, "users": [A[0], A[1]]},     # 42.000 $
    {"coin": "BTC", "side": "A", "px": "84000", "sz": "0.1", "time": 2, "hash": "0x0", "tid": 2, "users": [A[2], A[3]]},     # 8.400 $
    {"coin": "ETH", "side": "A", "px": "3000", "sz": "100", "time": 3, "users": [A[4], A[5]]},
    {"coin": "BTC", "px": "84000", "sz": "1", "users": ["no-es-direccion", MIXED]}]}
assert HL.on_trades(msg, now=T) == 3
assert HL.addrs[A[0]]["due"] == T and HL.addrs[A[2]]["due"] is None and A[4] not in HL.addrs and MIXED.lower() in HL.addrs
assert HL.on_trades({"channel": "pong"}) == 0 and HL.on_trades("x") == 0
assert [HL.tier_of(x) for x in (1, 3, 5, 10, 20, 25, 40, 50, 100)] == [3, 3, 5, 10, 25, 25, 50, 50, 100]

ST = {"assetPositions": [
    {"position": {"coin": "ETH", "szi": "10", "entryPx": "3000", "positionValue": "30000", "liquidationPx": "2500",
                  "leverage": {"type": "cross", "value": 10}}, "type": "oneWay"},
    {"position": {"coin": "BTC", "szi": "-12.5", "entryPx": "85000", "positionValue": "1050000", "liquidationPx": "88800.5",
                  "leverage": {"type": "isolated", "value": 20, "rawUsd": "1100000"}}, "type": "oneWay"}],
    "marginSummary": {"accountValue": "70000"}, "time": 1}
p = HL.parse_state(ST)
assert p["szi"] == -12.5 and p["liq"] == 88800.5 and p["lev"] == 20 and p["ntl"] == 1050000 and p["cross"] is False
assert HL.parse_state({"assetPositions": []}) is None
assert HL.parse_state({"assetPositions": [{"position": {"coin": "BTC", "szi": "0"}}]}) is None
p2 = HL.parse_state({"assetPositions": [{"position": {"coin": "BTC", "szi": "2", "entryPx": "80000", "liquidationPx": None,
                                                      "leverage": {"type": "cross", "value": 3}}}]})
assert p2["liq"] is None and p2["ntl"] == 160000 and p2["cross"] is True
try:
    HL.parse_state(["no"])
    raise AssertionError("debió fallar")
except ValueError:
    pass


def big(szi, entry, liq, lev):
    return {"assetPositions": [{"position": {"coin": "BTC", "szi": str(szi), "entryPx": str(entry), "positionValue": str(abs(szi) * entry),
                                             "liquidationPx": str(liq), "leverage": {"type": "cross", "value": lev}}}]}


STATES = {A[0]: ST, A[1]: {"assetPositions": []}, MIXED.lower(): big(30, 84500, 80000, 40),
          A[6]: big(2, 84000, 85000, 50), A[7]: big(0.1, 84000, 70000, 10)}


class HR:
    def __init__(s, code, body): s.status_code, s._b = code, body
    def json(s): return s._b
    def raise_for_status(s):
        if s.status_code >= 400:
            raise HL.requests.HTTPError(str(s.status_code))


hl_calls = []


def hl_post(url, json=None, timeout=None):
    hl_calls.append(json)
    if json["user"] == A[5]:
        return HR(429, {})
    return HR(200, STATES.get(json["user"], {"assetPositions": []}))


HL._http.post = hl_post
assert HL.poll_once(T) in (A[0], A[1], MIXED.lower())
for _ in range(5):
    HL.poll_once(T)
assert hl_calls[0] == {"type": "clearinghouseState", "user": hl_calls[0]["user"]}
assert set(HL.pos) == {A[0], MIXED.lower()} and A[1] not in HL.addrs         # sin BTC y venida de operaciones: se olvida
assert HL.addrs[A[0]]["due"] == T + 180 and HL.addrs[MIXED.lower()]["due"] == T + 180     # ≥ 1M: cada 3 min
assert HL.poll_once(T + 1) is None                                              # nada pendiente
# posiciones pequeñas (< 10.000 $) no se siguen; 100K-1M cada 10 min
HL.addrs[A[7]] = {"due": T, "src": "trades", "ntl": 50000.0}
HL.addrs[A[6]] = {"due": T, "src": "leader", "ntl": 0.0}
HL.poll_once(T)
HL.poll_once(T)
assert A[7] not in HL.pos and A[7] not in HL.addrs and HL.addrs[A[6]]["due"] == T + 600
# 429: el exchange pide calma y se espera 30 s
HL.addrs[A[5]] = {"due": T + 2, "src": "leader", "ntl": 0.0}
assert HL.poll_once(T + 2) == A[5] and HL.STAT["errors"] == 1 and HL.STAT["wait_until"] == T + 32
assert HL.addrs[A[5]]["due"] == T + 62 and HL.poll_once(T + 3) is None
HL.STAT["wait_until"] = 0

# clasificación pública: expresión rápida y, si cambia el orden de los campos, el JSON entero
LB = json.dumps({"leaderboardRows": [
    {"ethAddress": A[3], "accountValue": "5000000.5", "windowPerformances": [["day", {"pnl": "1", "roi": "0", "vlm": "2"}]], "prize": 0,
     "displayName": None},
    {"ethAddress": A[4], "accountValue": "100.0", "windowPerformances": [], "prize": 0, "displayName": "x"}]})
assert HL.parse_leaderboard(LB) == [(A[3], 5000000.5), (A[4], 100.0)]
assert HL.parse_leaderboard(json.dumps({"leaderboardRows": [{"accountValue": "7", "ethAddress": A[2].upper().replace("0X", "0x")}]})) == [(A[2], 7.0)]
assert HL.parse_leaderboard("basura") == [] and HL.parse_leaderboard("[1, 2]") == []
HL.TOP_LEADERS = 1
assert HL.add_leaders(HL.parse_leaderboard(LB), now=T) == 1 and HL.addrs[A[3]]["src"] == "leader" and A[4] not in HL.addrs
HL.TOP_LEADERS = 1500

# tramos reales: largos debajo del precio, cortos encima; lo que ya quedó al otro lado no cuenta
lv = HL.levels(84000, 100, 8)
assert set(lv) == {888, 800}, lv                     # el largo de A6 (liq 85.000 > precio) no cuenta
assert lv[888][1] == 12.5 * 88800.5 and lv[888][0] == 0 and lv[888][2][HL.TIERS.index(25)] == 12.5 * 88800.5
assert lv[800][0] == 30 * 80000 and lv[800][2][HL.TIERS.index(50)] == 30 * 80000
assert set(HL.levels(84000, 100, 8, tiers=[25])) == {888}
HL.STAT["hl_oi"] = 3.0e9
sm = HL.summary(84000, 8)
assert sm["positions"] == 3 and sm["long_usd"] == round(30 * 84500 + 2 * 84000) and sm["short_usd"] == 1050000
assert sm["top"][0] == [80000.0, 1, 2400000, 40.0, 84500.0] and sm["top"][1][1] == -1 and len(sm["top"]) == 2
assert abs(sm["coverage"] - (30 * 84500 + 2 * 84000 + 1050000) / 6.0e9) < 1e-3

# grabación cada 5 min y mapa en el tiempo: el corto de 88.800 vive hasta que el precio lo toca
T0 = 1_800_003_000 - 1_800_003_000 % 300
c12 = [{"time": T0 + i * 300, "open": 84000, "high": 84100, "low": 83900, "close": 84000} for i in range(12)]
c12[8]["high"] = 89000
assert HL.snapshot(84000, now=T0 + 2 * 300 + 30) == 2
assert HL.snapshot(84000, now=T0 + 6 * 300 + 30) == 2
del HL.pos[A[0]]                                      # el corto se liquidó en la vela 8
heat, touches, since = HL.build_heat(c12, 300, 100, 84000, 8, now=T0 + 11 * 300 + 10)
assert since == T0 + 630 and set(heat) == {"size", "max", "max_abs", "segments", "F", "active"}
segs = sorted(heat["segments"], key=lambda s: (s[0], s[1]))
assert [80050.0, T0 + 600, T0 + 3300, 2400000, 0] in segs, segs
assert [88850.0, T0 + 600, T0 + 2400, 0, 1100000] in segs, segs     # cortado en la vela del toque
assert touches == {(888, 8, -1): 1100000} and heat["active"] == [[80050.0, 2400000, 0, [0, 0, 0, 0, 2400000, 0]]]
h25, t25, _ = HL.build_heat(c12, 300, 100, 84000, 8, now=T0 + 11 * 300 + 10, tiers=[25])
assert all(s[3] == 0 for s in h25["segments"]) and h25["active"] == [] and (888, 8, -1) in t25
# grabaciones del formato anterior (por filas, v2): se siguen leyendo igual hasta que caducan
with LQ._db_lock:
    db_ = HL._db()
    saved_snaps = db_.execute("SELECT ts, price, data FROM hl_snap").fetchall()
    for ts_, px_, data_ in saved_snaps:
        d3 = json.loads(data_)
        assert d3["v"] == 3 and set(d3) == {"v", "p", "u", "t"}
        rows2 = {}
        for p_, u_, t_ in zip(d3["p"], d3["u"], d3["t"]):
            r_ = rows2.setdefault(p_, [p_, 0, 0, [0] * len(HL.TIERS)])
            r_[1 if u_ > 0 else 2] += abs(u_)
            r_[3][t_] += abs(u_)
        db_.execute("UPDATE hl_snap SET data = ? WHERE ts = ?", (json.dumps({"v": 2, "rows": sorted(rows2.values())}), ts_))
    db_.commit()
h_v2, t_v2, s_v2 = HL.build_heat(c12, 300, 100, 84000, 8, now=T0 + 11 * 300 + 10)
assert sorted(h_v2["segments"]) == sorted(heat["segments"]) and t_v2 == touches and s_v2 == since
h25_v2, t25_v2, _ = HL.build_heat(c12, 300, 100, 84000, 8, now=T0 + 11 * 300 + 10, tiers=[25])
assert sorted(h25_v2["segments"]) == sorted(h25["segments"]) and t25_v2 == t25
with LQ._db_lock:
    for ts_, px_, data_ in saved_snaps:
        db_.execute("UPDATE hl_snap SET data = ? WHERE ts = ?", (data_, ts_))
    db_.commit()
# gasolina real: lo que sigue abierto al cierre de cada vela (nada antes de grabar; el corto sale al tocarse)
oh = {}
HL.build_heat(c12, 300, 100, 84000, 8, now=T0 + 11 * 300 + 10, out=oh)
assert oh["fuel"][:2] == [None, None] and oh["fuel"][2] == (2400000, 1100000) and oh["fuel"][7] == (2400000, 1100000), oh["fuel"]
assert oh["fuel"][8] == (2400000, 0) and oh["fuel"][11] == (2400000, 0)
assert HL._q(1110006) == 1100000 and HL._q(0) == 0 and HL._q(512) == 510

# comparación del mapa estimado con lo real (coincide / azar)
est = {"size": 100, "active": [[88850.0, 0, 5e6, []], [80050.0, 3e6, 0, []], [86050.0, 0, 1e5, []]]}
real = HL.levels(84000, 100, 8, ps={"a": {"szi": -12.5, "liq": 88800.5, "lev": 20, "ntl": 1}, "b": {"szi": 30, "liq": 80000, "lev": 40, "ntl": 1}})
cr = app.compare_real(est, real, 84000)
assert cr["match"] == 1.0 and abs(cr["base"] - 3 / 35) < 1e-9 and cr["usd"] == round(12.5 * 88800.5 + 2400000)
assert app.compare_real({"size": 100, "active": []}, real, 84000) is None and app.compare_real(est, {}, 84000) is None
far = HL.levels(84000, 100, 8, ps={"c": {"szi": 30, "liq": 79000, "lev": 40, "ntl": 1}})
assert app.compare_real(est, far, 84000)["match"] == 0.0

# /api/data con el modelo real y la validación con su apartado
app.get_json = fake_get
app._cache.clear()
app._candle_cache.clear()
app._val_cache.clear()
app.CANDLES["5m"] = 900
HL.snapshot(86000, now=cs2[-5]["time"] + 10)
dh = cl.get("/api/data?tf=5m&model=hl").get_json()
assert "error" not in dh, dh
assert dh["model"] == "hl" and dh["used"] == ["hyperliquid_real"] and dh["levs"] == [3, 5, 10, 25, 50, 100]
assert any(abs(a[0] - 80000) <= dh["heat"]["size"] for a in dh["heat"]["active"]) and dh["doi"] and dh["oi"]["n"] == 6   # base nueva desde la sección 6
assert dh["hl"]["positions"] == 2 and dh["hl"]["recorded_since"] == cs2[-5]["time"] + 10
assert len(dh["fuel"]) == 5 and dh["fuel"][0][0] == cs2[-5]["time"] and dh["fuel"][-1][1] > 0, dh["fuel"]   # gasolina real desde que se graba
dh25 = cl.get("/api/data?tf=5m&model=hl&lev=25").get_json()
assert dh25["levs"] == [25] and dh25["heat"]["active"] == []          # el largo de 40x es del grupo 50x
vr = app.run_validation("5m", [25, 50, 100])
assert "hl" in vr["models"] and vr["hl_compare"] is not None and vr["hl"]["positions"] == 2
assert vr["models"]["hl"]["since"] == cs2[-5]["time"] + 10 and vr["models"]["hl"]["events"] <= vr["models"]["oi"]["events"]
st = cl.get("/api/status").get_json()
assert st["hyperliquid"]["positions"] == 2 and st["hyperliquid"]["queries"] == 5 and st["hyperliquid"]["last_error"] == "429" and st["coinalyze"] is None
ok(f"Hyperliquid: posiciones reales (≥ 10K $), cadencia por tamaño, 429, clasificación, mapa en el tiempo con toques y coincidencia {cr['match']:.0%} vs azar {cr['base']:.0%}")

# ───── 13) Coinalyze: elección de mercados, límite de consultas, históricos, fuentes y panel
import cz as CZ  # noqa: E402

EXS = [{"name": "Binance", "code": "A"}, {"name": "Bybit", "code": "6"}, {"name": "OKX", "code": "3"}, {"name": "Hyperliquid", "code": "H"},
       {"name": "Gate.io", "code": "Y"}, {"name": "Deribit", "code": "2"}, {"name": "Kraken", "code": "K"}]
MK = [
    {"symbol": "BTCUSDT_PERP.A", "exchange": "A", "symbol_on_exchange": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "is_perpetual": True},
    {"symbol": "BTCUSD_PERP.A", "exchange": "A", "symbol_on_exchange": "BTCUSD_PERP", "base_asset": "BTC", "quote_asset": "USD", "is_perpetual": True},
    {"symbol": "BTCUSDT.6", "exchange": "6", "symbol_on_exchange": "BTCUSDT", "base_asset": "BTC", "is_perpetual": True},
    {"symbol": "BTC-USDT-SWAP.3", "exchange": "3", "symbol_on_exchange": "BTC-USDT-SWAP", "base_asset": "BTC", "is_perpetual": True},
    {"symbol": "BTC.H", "exchange": "H", "symbol_on_exchange": "BTC", "base_asset": "BTC", "is_perpetual": True},
    {"symbol": "BTC_USDT.Y", "exchange": "Y", "symbol_on_exchange": "BTC_USDT", "base_asset": "BTC", "is_perpetual": True},
    {"symbol": "BTC-PERPETUAL.2", "exchange": "2", "symbol_on_exchange": "BTC-PERPETUAL", "base_asset": "BTC", "is_perpetual": True},
    {"symbol": "PF_XBTUSD.K", "exchange": "K", "symbol_on_exchange": "PF_XBTUSD", "base_asset": "XBT", "is_perpetual": True},
    {"symbol": "BTCUSDT_250926.A", "exchange": "A", "symbol_on_exchange": "BTCUSDT_250926", "base_asset": "BTC", "is_perpetual": False},
    {"symbol": "ETHUSDT_PERP.A", "exchange": "A", "symbol_on_exchange": "ETHUSDT", "base_asset": "ETH", "is_perpetual": True}]
OI_NOW = {"BTCUSDT_PERP.A": 9e9, "BTCUSD_PERP.A": 1e9, "BTCUSDT.6": 5e9, "BTC-USDT-SWAP.3": 3e9, "BTC.H": 4e9, "BTC_USDT.Y": 2e9,
          "BTC-PERPETUAL.2": 1.5e9, "PF_XBTUSD.K": 3e8}
sel = CZ.pick_markets(EXS, MK, OI_NOW)
assert [(m["name"], m["label"]) for m in sel["oi"]] == [("hyperliquid", "Hyperliquid BTC"), ("deribit", "Deribit BTC-PERPETUAL"),
                                                        ("cz1", "Gate.io BTC_USDT"), ("cz2", "Kraken PF_XBTUSD")], sel["oi"]
assert [m["symbol"] for m in sel["liq"]] == ["BTCUSDT_PERP.A", "BTCUSDT.6", "BTC-USDT-SWAP.3", "BTC_USDT.Y", "BTC-PERPETUAL.2",
                                             "BTC.H", "BTCUSD_PERP.A", "PF_XBTUSD.K"]
assert len(sel["all"]) == 8 and CZ.norm_ex("Gate.io") == "gate" and CZ.norm_ex("Huobi") == "htx" and CZ.norm_sym("btc-usdt_swap") == "BTCUSDTSWAP"

# límite: nunca más de BUDGET consultas por minuto
CZ.STATE["calls"] = []
CZ.BUDGET = 5
CZ._spend(3)
CZ._spend(2)
waited = []
CZ.time.sleep = lambda s: (waited.append(s), (_ for _ in ()).throw(SystemExit))[1]
try:
    CZ._spend(1)
except SystemExit:
    pass
CZ.time.sleep = REAL_SLEEP
assert waited and 59.5 < waited[0] <= 60.2, waited
CZ.BUDGET = 34
CZ.STATE["calls"] = []


class CR:
    def __init__(s, code, body, headers=None): s.status_code, s._b, s.headers = code, body, headers or {}
    def json(s): return s._b
    def raise_for_status(s):
        if s.status_code >= 400:
            raise CZ.requests.HTTPError(str(s.status_code), response=s)


cz_calls = []
NOW_CZ = cs2[-1]["time"] + 120


def cz_series(sym, t_from, t_to, step, kind):
    out = []
    t = int(t_from) - int(t_from) % step
    k = list(OI_NOW).index(sym) + 1 if sym in OI_NOW else 1
    while t <= t_to:
        if kind == "oi":
            out.append({"t": t, "o": 1e9 * k, "h": 1e9 * k, "l": 1e9 * k, "c": 1e9 * k + (t - cs2[0]["time"]) * 10})
        else:
            out.append({"t": t, "l": 1000.0 * k, "s": 10.0 * k})
        t += step
    return out


def cz_get(url, params=None, headers=None, timeout=None):
    path = url.replace(CZ.BASE, "")
    cz_calls.append((path, dict(params or {}), dict(headers or {})))
    if path == "/exchanges":
        return CR(200, EXS)
    if path == "/future-markets":
        return CR(200, MK)
    if path == "/open-interest":
        return CR(200, [{"symbol": s, "value": OI_NOW.get(s, 0), "update": 1} for s in params["symbols"].split(",")])
    if path in ("/open-interest-history", "/liquidation-history"):
        step = {"5min": 300, "1hour": 3600}[params["interval"]]
        kind = "oi" if "open" in path else "liq"
        return CR(200, [{"symbol": s, "history": cz_series(s, params["from"], params["to"], step, kind)} for s in params["symbols"].split(",")])
    return CR(404, {})


CZ._http.get = cz_get
CZ.KEY = "clave-cz-prueba"
CZ.discover()
assert [c[0] for c in cz_calls] == ["/exchanges", "/future-markets", "/open-interest"]
assert all(c[2] == {"api_key": "clave-cz-prueba"} and "api_key" not in c[1] for c in cz_calls)
assert len(cz_calls[2][1]["symbols"].split(",")) == 8 and cz_calls[2][1]["convert_to_usd"] == "true"
CZ.refresh("5m", now=NOW_CZ)
oih = [c for c in cz_calls if c[0] == "/open-interest-history"][-1][1]
assert oih["interval"] == "5min" and oih["from"] == int(NOW_CZ - 902 * 300) and len(oih["symbols"].split(",")) == 4
cz_calls.clear()
CZ.refresh("5m", now=NOW_CZ + 300)                       # solo lo nuevo
oih2 = [c for c in cz_calls if c[0] == "/open-interest-history"][-1][1]
assert oih2["from"] >= NOW_CZ - 4 * 300, (oih2["from"], NOW_CZ)
src_cz, labels = CZ.oi_sources("5m")
assert set(src_cz) == {"hyperliquid", "deribit", "cz1", "cz2"} and labels["cz1"] == "Gate.io BTC_USDT"
t_mid = cs2[150]["time"]
kH = list(OI_NOW).index("BTC.H") + 1
assert src_cz["hyperliquid"][t_mid] == 1e9 * kH + (t_mid - cs2[0]["time"]) * 10        # OI al cierre de la vela («c»)

# en el modelo: sustituye la grabación propia de Hyperliquid y Deribit y añade los mercados nuevos
for c in cs2:
    LQ.save_oi_snapshots({"bitget": 1.8e9, "hyperliquid": 1.0e9}, now=c["time"] + 61)
app._okx_cache.clear()
src3, errs3 = app.load_sources("5m", cs2)
assert not errs3 and src3["hyperliquid"][t_mid] == src_cz["hyperliquid"][t_mid] and "cz1" in src3 and "cz2" in src3
assert src3["bitget"] == LQ.oi_snapshots("bitget", cs2, STEP)                    # Bitget no viene de Coinalyze aquí
only_bn, _ = app.load_sources("5m", cs2, ["binance"])
assert "cz1" not in only_bn
only_otros, _ = app.load_sources("5m", cs2, ["otros"])
assert set(only_otros) == {"cz1", "cz2"}

# panel: + mercados que no escuchamos; y los nuestros en las velas sin escucha
for c in cs2[200:250]:
    for m in range(c["time"], c["time"] + STEP, 60):
        LQ.heartbeat(m)
res = LQ.chart_liqs(cs2, STEP)
base_hist = {t: [L, S] for t, L, S in res["hist"]}
mz = app.merge_cz_liqs(cs2, STEP, "5m", res["hist"])
mh = {t: [L, S] for t, L, S in mz["hist"]}
own_k = sum(list(OI_NOW).index(m["symbol"]) + 1 for m in CZ.STATE["liq"] if m["key"] in CZ.OWN_LIQ)
ext_k = sum(list(OI_NOW).index(m["symbol"]) + 1 for m in CZ.STATE["liq"] if m["key"] not in CZ.OWN_LIQ)
t_gap, t_cov = cs2[100]["time"], cs2[220]["time"]
assert mh[t_gap] == [round(1000.0 * own_k) + round(1000.0 * ext_k), round(10.0 * own_k) + round(10.0 * ext_k)], (mh[t_gap], own_k, ext_k)
assert mh[t_cov] == [base_hist.get(t_cov, [0, 0])[0] + round(1000.0 * ext_k), base_hist.get(t_cov, [0, 0])[1] + round(10.0 * ext_k)]
assert mz["cz_markets"] == 3 and mz["cz_filled"] == 299 - 50
app._cache.clear()
dz = cl.get("/api/data?tf=5m").get_json()
assert dz["liqs"]["cz_markets"] == 3 and dz["credits"] == ["coinalyze"] and dz["labels"] == {
    "cz1": "Gate.io BTC_USDT", "cz2": "Kraken PF_XBTUSD", "deribit": "Deribit BTC-PERPETUAL", "hyperliquid": "Hyperliquid BTC"}
assert "cz1" in dz["used"] and "otros" in dz["exchanges"]
CZ.refresh("1h", now=NOW_CZ)
stz = cl.get("/api/status").get_json()["coinalyze"]
assert stz["btc_markets"] == 8 and stz["oi_markets"][2] == "Gate.io BTC_USDT" and stz["liq_markets"] == 8
assert stz["check_binance_24h"]["coinalyze"][0] > 0 and "clave-cz-prueba" not in json.dumps(stz)
# clave mala: se espera 15 min antes de reintentar
CZ.STATE["discovered"] = None
CZ._http.get = lambda url, params=None, headers=None, timeout=None: CR(401, {"message": "Invalid API key"})
slept_cz = []
CZ.time.sleep = lambda s: (slept_cz.append(s), (_ for _ in ()).throw(SystemExit) if s >= 600 else None)[1]
try:
    CZ._loop()
except SystemExit:
    pass
CZ.time.sleep = REAL_SLEEP
assert slept_cz == [900] and CZ.STATE["errors"] == 1 and "401" in CZ.STATE["last_error"]
CZ.KEY = ""
ok("Coinalyze: mercados elegidos por OI, ≤ 34 consultas/min, solo lo nuevo, sustituye lo grabado, panel con huecos rellenos y +3 mercados")
# ───── 14) mapa de liquidez: libro local (reglas U/u/pu), medias por bloque y mapa por vela
import book as BK  # noqa: E402


def cell_value(bm, i, price):
    """Nivel del mapa de liquidez en la vela i y el precio dado."""
    row = bm["rows"][i]
    if not row:
        return 0
    k = int(price // bm["S"]) - bm["k0"] - row[0]
    return int(row[1][k]) if 0 <= k < len(row[1]) else 0


bk = BK.LocalBook()
ev = lambda U, u, pu, b=(), a=(): {"e": "depthUpdate", "U": U, "u": u, "pu": pu, "b": list(b), "a": list(a)}   # noqa: E731
assert bk.on_event(ev(1, 5, 0, [["84000", "1"]])) == "wait"
bk.on_event(ev(6, 10, 5, [["84000", "2"]], [["84010", "1"]]))
bk.on_event(ev(11, 15, 10, [["83990", "3"]], [["84010", "0"]]))
snap = {"lastUpdateId": 8, "bids": [["84000", "1.5"], ["83900", "4"]], "asks": [["84020", "1"], ["84100", "2"]]}
assert bk.load_snapshot(snap) == "ok"                                  # descarta u<8, aplica 6-10 y 11-15
assert bk.bids == {84000.0: 2.0, 83900.0: 4.0, 83990.0: 3.0} and bk.asks == {84020.0: 1.0, 84100.0: 2.0} and bk.last_u == 15
assert bk.on_event(ev(16, 20, 15, [], [["84020", "5"]])) == "ok" and bk.asks[84020.0] == 5.0
assert bk.on_event(ev(30, 35, 25)) == "gap" and not bk.synced and bk.bids == {} and len(bk.buf) == 1   # hueco: se vacía
bk2 = BK.LocalBook()
bk2.on_event(ev(50, 60, 49))
assert bk2.load_snapshot({"lastUpdateId": 40, "bids": [], "asks": []}) == "gap"      # instantánea demasiado vieja
assert bk2.load_snapshot({"lastUpdateId": 70, "bids": [["1", "1"]], "asks": [["2", "1"]]}) == "wait"   # más nueva: esperar
assert bk2.on_event(ev(61, 69, 60)) == "drop" and bk2.on_event(ev(70, 75, 69, [["1", "3"]])) == "ok" and bk2.synced
assert bk2.bids[1.0] == 3.0
assert bk.binned() is None                                              # sin sincronizar no hay instantánea
assert bk.load_snapshot({"lastUpdateId": 32, "bids": [["84000", "2"], ["83900", "4"], ["83990", "3"]],
                         "asks": [["84020", "5"], ["84100", "2"]]}) == "ok"    # el evento 30-35 que esperaba cubre el 32
mid, acc = bk.binned()
assert mid == (84000 + 84020) / 2 and acc[8400] == 84000 * 2 and acc[8399] == 83990 * 3 and acc[8402] == 84020 * 5
inf = bk.info()                                                         # 5 tramos de 10 $ con órdenes de los 1010 de ±6 %
assert inf["coverage"] == round(5 / (int(84010 * 1.06 // 10) - int(84010 * 0.94 // 10) + 1), 3) and inf["range_pct"] == 6.0, inf
assert acc[8390] == 83900 * 4 and acc[8410] == 84100 * 2
bk.bids[70000.0] = 100.0                                               # muy lejos: fuera del ±6 % y luego olvidado
assert 7000 not in bk.binned()[1]
bk.prune(84010)
assert 70000.0 not in bk.bids
bw = BK.LocalBook()
bw.load_snapshot({"lastUpdateId": 1, "bids": [[str(84000 - j * 10), "0.5"] for j in range(80)] + [["83700", "40"]],
                  "asks": [[str(84010 + j * 10), "0.5"] for j in range(80)] + [["84450", "55"]]})
bw.on_event(ev(1, 2, 0))
w = bw.walls(84005)
assert abs(w["bids"][0][0] - 83700) < 42 and abs(w["asks"][0][0] - 84450) < 42 and w["asks"][0][1] > 4e6
assert BK.LocalBook().walls(84000) is None

# medias de 5 min y 1 h, filas en SQLite
a5 = BK.Accum(300)
for k in range(10):
    assert a5.add(1000 * 300 + k * 30, {1: 10.0 * (k + 1)}) is None
closed = a5.add(1001 * 300, {1: 1.0})
assert closed == (1000 * 300, {1: 55.0}, 10) and a5.current() == (1001 * 300, {1: 1.0}, 1)
for name in BK.ACC:
    BK.ACC[name] = BK.Accum(BK.TABLES[name][0])
T0 = (1791349200 // 3600) * 3600          # en punto
book_t = BK.LocalBook()
book_t.load_snapshot({"lastUpdateId": 1, "bids": [], "asks": []})
book_t.on_event(ev(1, 2, 0))


def set_book(bids, asks):
    book_t.bids, book_t.asks = dict(bids), dict(asks)


for k in range(0, 3600 + 300, 30):          # una hora y 5 min de instantáneas: muro fijo en 84.500 y libro normal
    wall = 30.0 if k < 1800 else 5.0         # el muro de ventas se reduce a media hora
    set_book({84000.0 - j * 10: 0.5 for j in range(1, 60)}, {**{84000.0 + j * 10: 0.5 for j in range(1, 60)}, 84500.0: wall})
    assert BK.snapshot_tick(T0 + k, book_t)
with LQ._db_lock:
    c_ = BK._conn()
    n5 = c_.execute("SELECT COUNT(*) FROM book5").fetchone()[0]
    n60 = c_.execute("SELECT COUNT(*) FROM book60").fetchone()[0]
assert n5 == 12 and n60 == 1, (n5, n60)
st = BK.status()
assert st["rows"] == {"book5": 12, "book60": 1} and st["since"]["book5"] == T0
c5 = [{"time": T0 + i * 300, "open": 84000, "high": 84050, "low": 83950, "close": 84000.0} for i in range(14)]
bm = BK.book_map(c5, 300, 42.07, 84000.0, now=T0 + 4000)
assert bm["S"] == 40 and bm["levels"][-1] > bm["levels"][0] and len(bm["rows"]) == 14 and bm["since"] == T0
assert cell_value(bm, 0, 84505) == 7 and cell_value(bm, 7, 84505) < 7            # el muro baja de nivel
assert 1 <= cell_value(bm, 0, 83500) < 7 and cell_value(bm, 0, 87000) == 0
assert bm["rows"][12] is not None and bm["rows"][13] is None                           # vela abierta: bloque en curso
assert BK.book_map(c5, 300, 42.07, 84000.0, now=T0 + 4005) is bm                        # caché de 20 s
c15 = [{"time": T0 + i * 900, "open": 84000, "high": 84050, "low": 83950, "close": 84000.0} for i in range(5)]
bm15 = BK.book_map(c15, 900, 42.0, 84000.0, now=T0 + 4100)
assert cell_value(bm15, 0, 84505) == 7 and cell_value(bm15, 2, 84505) < 7         # media de 3 filas de 5 min
c1h = [{"time": T0 + i * 3600, "open": 84000, "high": 84050, "low": 83950, "close": 84000.0} for i in range(2)]
bmh = BK.book_map(c1h, 3600, 84.0, 84000.0, now=T0 + 4200)
assert bmh["S"] == 80 and bmh["rows"][0] and bmh["rows"][1]                               # 1 h: fila guardada + bloque abierto
assert BK.book_map([{"time": T0 - 86400 * 9, "open": 1, "high": 1, "low": 1, "close": 1.0}], 300, 40, 1.0) is None
# la ruta lo devuelve con book=1
app._cache.clear()
app._candle_cache.clear()
cs2_backup = list(cs2)
cs2[:] = [dict(c, time=T0 + (i - len(cs2) + 14) * 300) for i, c in enumerate(cs2_backup)]
get_backup = app.get_json
app.get_json = fake_get
dbm = cl.get("/api/data?tf=5m&book=1").get_json()
assert "error" not in dbm, dbm.get("error")
app.get_json = get_backup
assert dbm["book_map"] and dbm["book_map"]["S"] >= 10 and len(dbm["book_map"]["rows"]) == len(dbm["candles"]), dbm.get("book_map")
cs2[:] = cs2_backup
app._cache.clear()
app._candle_cache.clear()
BK.cleanup(now=T0 + 8 * 86400)
with LQ._db_lock:
    assert BK._conn().execute("SELECT COUNT(*) FROM book5").fetchone()[0] == 0
    assert BK._conn().execute("SELECT COUNT(*) FROM book60").fetchone()[0] == 1
ok(f"mapa de liquidez: libro local con huecos y resincronización, medias de 5 min/1 h y mapa por vela ({bm['S']} $ por tramo)")

# ───── 15) MMR en la calibración y gasolina
assert len(app.CALIB_GRID) == 72 and app.CALIB_DEFAULT in app.CALIB_GRID and {g["mmr"] for g in app.CALIB_GRID} == {0.005, 0.004}
cs = flat(3)
cs[1].update(open=100000, high=100100, low=99900, close=100050)
oi = {cs[0]["time"]: 1000.0, cs[1]["time"]: 1100.0, cs[2]["time"]: 1100.0}
typ = (100100 + 99900 + 100050) / 3
for mmr in (0.005, 0.004):
    out = {}
    h, _ = app.estimate_heat(cs, {"binance": oi}, 100000.0, app.tiers_from([25]), "oi", out=out, params={"mmr": mmr})
    lp = [a[0] for a in h["active"] if a[1] > 0][0]
    assert abs(lp - typ * (1 - 1 / 25 + mmr)) < h["size"], (mmr, lp)
    assert [round(x) for x in out["fuel"][1]] == [100, 100] and out["fuel"][0] == (0.0, 0.0)
cs = flat(4)
cs[1].update(high=100100, low=99900)
cs[3].update(low=95000)                                     # baja y toca los largos de 25x
oi = {c["time"]: 1000.0 + (100 if i >= 1 else 0) for i, c in enumerate(cs)}
out = {}
app.estimate_heat(cs, {"binance": oi}, 100000.0, app.tiers_from([25]), "oi", out=out)
assert round(out["fuel"][2][0]) == 100 and round(out["fuel"][3][0]) == 0 and round(out["fuel"][3][1]) == 100
ok("MMR 0,4 % y 0,5 % en la calibración (72 combinaciones) y gasolina por vela")

# ───── 16) señal de tendencia (diario + 4h): votos, etiqueta, desde cuándo, solo velas cerradas, caché y rutas
import trend as TR  # noqa: E402


def mk(n, step, start, f):
    """Velas [(apertura, o, h, l, c)] con cierre f(i); la apertura es el cierre anterior."""
    out, prev = [], f(0)
    for i in range(n):
        c_ = f(i)
        out.append((start + i * step, prev, max(prev, c_) * 1.002, min(prev, c_) * 0.998, c_))
        prev = c_
    return out


D0 = 1700000000 // 86400 * 86400
H0 = D0 + 500 * 86400 - 1000 * 14400               # 1000 velas de 4h que cierran a la vez que el último día
up_d, dn_d = mk(500, 86400, D0, lambda i: 30000 * 1.004 ** i), mk(500, 86400, D0, lambda i: 90000 * 0.996 ** i)
up_4, dn_4 = mk(1000, 14400, H0, lambda i: 50000 * 1.0007 ** i), mk(1000, 14400, H0, lambda i: 90000 * 0.9993 ** i)
r = TR.compute(up_d, up_4)
assert r["label"] == "compra" and r["score"] == 3 and [v["on"] for v in r["votes"]] == [True, True, True], r
assert r["votes"][1]["state"] == 1 and r["flip"] == r["votes"][0]["ema"] < up_d[-1][4]
assert r["asof"] == up_4[-1][0] + 14400 == up_d[-1][0] + 86400 and r["day"] == up_d[-1][0]
assert r["since"] is None and r["study"]["spot"]["ret"] > 0                 # mismo estado en todas las velas pedidas
r = TR.compute(dn_d, dn_4)
assert r["label"] == "venta" and r["score"] == 0 and not any(v["on"] for v in r["votes"]) and r["votes"][1]["state"] == -1, r
r = TR.compute(up_d, dn_4)
assert r["label"] == "espera" and r["score"] == 2 and [v["on"] for v in r["votes"]] == [True, True, False], r
# la EMA100 diaria: el precio de cambio es la propia EMA (un cierre por debajo quita el voto)
e = TR.ema([x[4] for x in up_d], 100)
assert abs(r["flip"] - round(e[-1], 1)) < 1e-9
nxt = e[-1] - 1
assert not (nxt > e[-1] + 2 / 101 * (nxt - e[-1]))
# desde cuándo: en 4h la EMA50 cruza por debajo de la EMA200 → de COMPRA a ESPERA justo en esa vela
mix_4 = mk(1000, 14400, H0, lambda i: 50000 * 1.0007 ** i if i < 900 else 50000 * 1.0007 ** 900 * 0.996 ** (i - 900))
f_, s_ = TR.ema([x[4] for x in mix_4], 50), TR.ema([x[4] for x in mix_4], 200)
jx = next(j for j in range(900, 1000) if f_[j] <= s_[j])
r = TR.compute(up_d, mix_4)
assert r["label"] == "espera" and r["since"] == mix_4[jx][0] + 14400 and r["since_price"] == mix_4[jx][4], (r["since"], jx)
# el día que cierra cuenta desde la vela de 4h que cierra con él (no antes: sin mirar al futuro)
crash_d = up_d[:-1] + [(up_d[-1][0], up_d[-2][4], up_d[-2][4], up_d[-2][4] * 0.5, up_d[-2][4] * 0.5)]
sc, _, _ = TR.series(crash_d, up_4)
assert sc[-2][1] == 3 and sc[-1][1] in (1, 2), sc[-3:]
assert sc[-1][0] == crash_d[-1][0] + 20 * 3600 and sc[-2][0] == crash_d[-1][0] + 16 * 3600
r = TR.compute(crash_d, up_4)
assert r["label"] == "espera" and not r["votes"][0]["on"] and r["since"] == r["asof"] and r["since_price"] == up_4[-1][4]
try:
    TR.compute(up_d[:50], up_4)
    raise AssertionError("pocas velas")
except ValueError:
    pass
# solo velas cerradas: la que está en curso (cierre en el futuro) se descarta
nowt = int(time.time())
gb = app.get_json
app.get_json = lambda url, params=None, headers=None: [
    [(nowt - 2 * 86400) * 1000, "1", "2", "0.5", "1.5", "0", (nowt - 86400) * 1000 - 1],
    [(nowt - 86400) * 1000, "1.5", "3", "1", "2", "0", (nowt + 600) * 1000]]
assert app.fetch_closed("1d", 2) == [((nowt - 2 * 86400), 1.0, 2.0, 0.5, 1.5)]
app.get_json = gb
# caché de 5 min y sin esperar si otro hilo ya está pidiendo
TR._state.update(t=0.0, v=None, err=None)
asked = []


def fk(iv, lim):
    asked.append((iv, lim))
    return up_d if iv == "1d" else up_4


v1_ = TR.get(fk)
assert TR.get(fk) is v1_ and asked == [("1d", TR.N_DAILY), ("4h", TR.N_H4)]
TR._state["t"] -= TR.TTL + 1
assert TR._fetch_lock.acquire(blocking=False)
assert TR.get(fk) is v1_ and len(asked) == 2                 # otro hilo pidiendo: se sirve lo que hay
TR._fetch_lock.release()


def broken(iv, lim):
    raise RuntimeError("binance caído")


assert TR.get(broken) is v1_ and TR.status()["error"] == "binance caído"   # falla: se mantiene el último cálculo
TR._state.update(t=0.0, v=None, err=None)
try:
    TR.get(broken)
    raise AssertionError("sin caché debe fallar")
except RuntimeError:
    pass


# rutas: /api/data (también la compacta) y /api/status
def kl(rows, step):
    return [[t_ * 1000, str(o_), str(h_), str(l_), str(c_), "0", (t_ + step) * 1000 - 1, "0", "0", "0", "0"]
            for t_, o_, h_, l_, c_ in rows]


def trend_get(url, params=None, headers=None):
    iv = (params or {}).get("interval")
    if "klines" in url and iv == "1d":
        return kl(up_d, 86400)
    if "klines" in url and iv == "4h":
        return kl(mix_4, 14400)
    return fake_get(url, params, headers)


TR._state.update(t=0.0, v=None, err=None)
app._cache.clear()
app._candle_cache.clear()
app.get_json = trend_get
dt_ = cl.get("/api/data?tf=5m&v=2&want=").get_json()
stt = cl.get("/api/status").get_json()["trend"]
app.get_json = fake_get
assert "error" not in dt_, dt_.get("error")
tr_ = dt_["trend"]
assert tr_["label"] == "espera" and tr_["score"] == 2 and tr_["since"] == mix_4[jx][0] + 14400 and len(tr_["votes"]) == 3, tr_
assert stt["label"] == "espera" and stt["score"] == 2 and stt["error"] is None and stt["age_s"] is not None
TR._state.update(t=0.0, v=None, err=None)
app._cache.clear()
app.get_json = lambda url, params=None, headers=None: (_ for _ in ()).throw(RuntimeError("sin red")) \
    if (params or {}).get("interval") in ("1d", "4h") else fake_get(url, params, headers)
de_ = cl.get("/api/data?tf=5m&v=2&want=").get_json()
app.get_json = fake_get
assert "error" not in de_ and de_["trend"] == {"error": "sin red"}, de_.get("trend")   # sin tendencia, el resto sigue
app._cache.clear()
json.dump(dict(tr_), open(os.path.join(OUT, "trend.json"), "w"))
ok(f"tendencia: COMPRA/ESPERA/VENTA con 3 votos, cambio de EMA100 en {r['flip']:,.0f}, desde cuándo, solo velas cerradas, "
   "sin mirar al futuro, caché de 5 min y rutas")
print("TODO EL SERVIDOR OK")
