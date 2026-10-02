import hmac
import json
import math
import os
import threading
import time

import requests
from flask import Flask, abort, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static")


def env(name, default=""):
    return os.getenv(name, default).strip()


# ───────── Configuración (variables de entorno) ─────────
SYMBOL = env("SYMBOL", "BTCUSDT").upper()
BASE_COIN = SYMBOL[:-4] if SYMBOL.endswith("USDT") else SYMBOL
OKX_INST = env("OKX_INST", f"{BASE_COIN}-USDT-SWAP")
EXCHANGES = [x.strip() for x in env("EXCHANGES", "binance,bybit,okx").lower().split(",") if x.strip()]

FAPI = env("BINANCE_FAPI", "https://fapi.binance.com")
VISION = env("BINANCE_VISION", "https://data-api.binance.vision")
BYBIT = env("BYBIT_BASE", "https://api.bybit.com")
OKX = env("OKX_BASE", "https://www.okx.com")
CG_BASE = env("COINGLASS_BASE", "https://open-api-v4.coinglass.com")
CG_KEY = env("COINGLASS_API_KEY")
CG_EXCHANGE = env("CG_EXCHANGE", "Binance")

ACCESS_KEY = env("ACCESS_KEY")
CACHE_TTL = int(env("CACHE_TTL", "60"))

ASIA_START = int(env("ASIA_START", "0"))  # hora UTC
ASIA_END = int(env("ASIA_END", "7"))      # hora UTC

TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")
ALERT_TF = env("ALERT_TF", "5m")
ALERT_EVERY = int(env("ALERT_EVERY", "60"))
ALERT_DIST_PCT = float(env("ALERT_DIST_PCT", "0.25"))
ALERT_MIN_RATIO = float(env("ALERT_MIN_RATIO", "0.6"))
ALERT_COOLDOWN = int(env("ALERT_COOLDOWN", "10800"))

TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
CG_RANGE = {"5m": "24h", "15m": "3d", "1h": "7d", "4h": "30d"}
BYBIT_KLINE = {"5m": "5", "15m": "15", "1h": "60", "4h": "240"}
BYBIT_OI = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}
OKX_OI = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H"}

LEVERAGES = [(10, 0.25), (25, 0.35), (50, 0.25), (100, 0.15)]
MMR = 0.005
BIN_PCT = 0.15
RANGE_PCT = 8.0
MIN_DRAW = 0.04
N_CANDLES = 500

_cache = {}
_lock = threading.Lock()
_build_lock = threading.Lock()


def get_json(url, params=None, headers=None):
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


# ───────── Velas ─────────
def _candle(t, o, h, l, c):
    return {"time": int(t), "open": float(o), "high": float(h), "low": float(l), "close": float(c)}


def _c_binance(tf, limit):
    rows = get_json(f"{FAPI}/fapi/v1/klines", {"symbol": SYMBOL, "interval": tf, "limit": limit})
    return [_candle(r[0] // 1000, r[1], r[2], r[3], r[4]) for r in rows]


def _c_vision(tf, limit):
    rows = get_json(f"{VISION}/api/v3/klines", {"symbol": SYMBOL, "interval": tf, "limit": limit})
    return [_candle(r[0] // 1000, r[1], r[2], r[3], r[4]) for r in rows]


def _c_bybit(tf, limit):
    r = get_json(f"{BYBIT}/v5/market/kline",
                 {"category": "linear", "symbol": SYMBOL, "interval": BYBIT_KLINE[tf], "limit": limit})
    if r.get("retCode") != 0:
        raise RuntimeError(r.get("retMsg", "error"))
    rows = r["result"]["list"]
    return [_candle(int(x[0]) // 1000, x[1], x[2], x[3], x[4]) for x in reversed(rows)]


def fetch_candles(tf, limit=N_CANDLES):
    errs = []
    for name, fn in (("binance", _c_binance), ("binance-spot", _c_vision), ("bybit", _c_bybit)):
        try:
            rows = fn(tf, limit)
            if rows:
                return rows, name
        except Exception as e:
            errs.append(f"{name}: {str(e)[:60]}")
    raise RuntimeError("Sin velas (" + "; ".join(errs) + ")")


# ───────── Open Interest (en monedas, no en USD, para no contaminar con el precio) ─────────
def oi_binance(tf, n=N_CANDLES):
    rows = get_json(f"{FAPI}/futures/data/openInterestHist", {"symbol": SYMBOL, "period": tf, "limit": 500})
    return {int(r["timestamp"]) // 1000: float(r["sumOpenInterest"]) for r in rows}


def oi_bybit(tf, n=N_CANDLES):
    out = {}
    step_ms = TF_SECONDS[tf] * 1000
    end = int(time.time() * 1000)
    for _ in range(4):
        start = end - 200 * step_ms
        r = get_json(f"{BYBIT}/v5/market/open-interest",
                     {"category": "linear", "symbol": SYMBOL, "intervalTime": BYBIT_OI[tf],
                      "startTime": start, "endTime": end, "limit": 200})
        if r.get("retCode") != 0:
            raise RuntimeError(r.get("retMsg", "error"))
        rows = r["result"]["list"]
        if not rows:
            break
        for x in rows:
            out[int(x["timestamp"]) // 1000] = float(x["openInterest"])
        if len(out) >= n:
            break
        end = start - 1
    return out


def oi_okx(tf, n=N_CANDLES):
    out = {}
    end = None
    for _ in range(7):
        p = {"instId": OKX_INST, "period": OKX_OI[tf], "limit": 100}
        if end:
            p["end"] = end
        r = get_json(f"{OKX}/api/v5/rubik/stat/contracts/open-interest-history", p)
        if str(r.get("code")) != "0":
            raise RuntimeError(r.get("msg", "error"))
        rows = r.get("data") or []
        if not rows:
            break
        for x in rows:
            out[int(x[0]) // 1000] = float(x[2])  # [ts, oi contratos, oi en moneda, oi en USD]
        if len(out) >= n or len(rows) < 100:
            break
        end = min(int(x[0]) for x in rows)
    return out


OI_FETCHERS = {"binance": oi_binance, "bybit": oi_bybit, "okx": oi_okx}


def align_oi(candles, oi, step):
    times = {c["time"] for c in candles}
    best, best_n = 0, -1
    for off in (0, -step, step):
        n = sum(1 for t in oi if t + off in times)
        if n > best_n:
            best, best_n = off, n
    return {t + best: v for t, v in oi.items()}


def combined_oi(candles, tf):
    step = TF_SECONDS[tf]
    times = [c["time"] for c in candles]
    series, info = {}, {}
    for name in EXCHANGES:
        fn = OI_FETCHERS.get(name)
        if not fn:
            continue
        try:
            al = align_oi(candles, fn(tf), step)
            cov = sum(1 for t in times if t in al) / len(times)
            if cov < 0.9:
                info[name] = f"cobertura {cov:.0%}"
                continue
            series[name] = al
            info[name] = "ok"
        except Exception as e:
            info[name] = str(e)[:80]
    return series, info


def oi_deltas(candles, series):
    d = [0.0] * len(candles)
    for al in series.values():
        prev = None
        for i, c in enumerate(candles):
            cur = al.get(c["time"])
            if cur is not None and prev is not None:
                d[i] += cur - prev
            if cur is not None:
                prev = cur
    return d


# ───────── Simulación del mapa de liquidaciones ─────────
def bin_size(price):
    fixed = env("BIN_SIZE")
    if fixed:
        return float(fixed)
    raw = price * BIN_PCT / 100
    steps = [0.01, 0.05, 0.1, 0.5, 1, 2, 5, 10, 25, 50, 100, 250, 500]
    return min(steps, key=lambda s: abs(s - raw))


def simulate(candles, deltas, size):
    recs = []
    L, S = [], []
    for i, c in enumerate(candles):
        recs = [r for r in recs
                if not ((r[2] == 1 and c["low"] <= r[0]) or (r[2] == -1 and c["high"] >= r[0]))]
        d = deltas[i]
        if d > 0 and c["close"] != c["open"]:
            side = 1 if c["close"] > c["open"] else -1
            usd = d * c["close"]
            for lev, w in LEVERAGES:
                if side == 1:
                    p = c["close"] * (1 - 1 / lev + MMR)
                else:
                    p = c["close"] * (1 + 1 / lev - MMR)
                recs.append((p, usd * w, side))
        lg, sg = {}, {}
        for p, v, side in recs:
            k = math.floor(p / size)
            tgt = lg if side == 1 else sg
            tgt[k] = tgt.get(k, 0) + v
        L.append(lg)
        S.append(sg)
    return L, S


def segments(grids):
    out, opened = [], {}
    n = len(grids)
    for i, g in enumerate(grids):
        for k in list(opened):
            start, val = opened[k]
            nv = g.get(k, 0)
            if round(nv) != round(val):
                out.append([k, start, i - 1, round(val)])
                if nv > 0:
                    opened[k] = (i, nv)
                else:
                    del opened[k]
        for k, v in g.items():
            if k not in opened and v > 0:
                opened[k] = (i, v)
    for k, (start, val) in opened.items():
        out.append([k, start, n - 1, round(val)])
    return out


def near_strong(grid, lo, hi, size, thr):
    best = 0
    for k, v in grid.items():
        if lo <= (k + 0.5) * size <= hi and v >= thr and v > best:
            best = v
    return best


# ───────── Rango asiático y barridos (ICT) ─────────
def asia_ranges(candles, step):
    if step > 3600:
        return []
    last_idx, days = {}, {}
    for i, c in enumerate(candles):
        t = c["time"]
        day = t - t % 86400
        last_idx[day] = i
        h = (t - day) // 3600
        if ASIA_START <= h < ASIA_END:
            d = days.get(day)
            if d is None:
                days[day] = {"day": day, "i0": i, "i1": i, "t0": t, "t1": t + step,
                             "high": c["high"], "low": c["low"],
                             "full": t == day + ASIA_START * 3600}
            else:
                d["i1"], d["t1"] = i, t + step
                d["high"], d["low"] = max(d["high"], c["high"]), min(d["low"], c["low"])
    out = []
    for day, d in sorted(days.items()):
        if not d["full"]:
            continue
        d["i2"] = last_idx[day]
        out.append(d)
    return out


def find_sweeps(candles, ranges, L, S, size, vmax):
    out = []
    for r in ranges:
        end_ts = r["day"] + ASIA_END * 3600
        hi_done = lo_done = False
        for i in range(r["i1"] + 1, r["i2"] + 1):
            c = candles[i]
            if c["time"] < end_ts:
                continue
            if not hi_done and c["high"] > r["high"] and c["close"] < r["high"]:
                hi_done = True
                z = near_strong(S[i - 1], r["high"] * 0.998, r["high"] * 1.004, size, 0.35 * vmax)
                out.append({"time": c["time"], "type": "high", "level": r["high"], "price": c["high"],
                            "target": r["low"], "dir": "short", "confluence": z > 0, "zone": round(z)})
            if not lo_done and c["low"] < r["low"] and c["close"] > r["low"]:
                lo_done = True
                z = near_strong(L[i - 1], r["low"] * 0.996, r["low"] * 1.002, size, 0.35 * vmax)
                out.append({"time": c["time"], "type": "low", "level": r["low"], "price": c["low"],
                            "target": r["high"], "dir": "long", "confluence": z > 0, "zone": round(z)})
    return sorted(out, key=lambda s: s["time"])


# ───────── Coinglass (opcional, plan Professional) ─────────
def coinglass_levels(tf, price):
    r = get_json(f"{CG_BASE}/api/futures/liquidation/heatmap/model2",
                 {"exchange": CG_EXCHANGE, "symbol": SYMBOL, "range": CG_RANGE[tf]},
                 {"CG-API-KEY": CG_KEY})
    if str(r.get("code")) != "0":
        raise RuntimeError(f"Coinglass: {r.get('msg', 'error')}")
    d = r["data"]
    ys = [float(y) for y in d["y_axis"]]
    cells = d["liquidation_leverage_data"]
    if not cells or len(ys) < 2:
        return []
    max_x = max(c[0] for c in cells)
    latest = {}
    for x, y, v in cells:
        if x >= max_x - 2 and (y not in latest or x > latest[y][0]):
            latest[y] = (x, float(v))
    lv = [{"price": ys[y], "value": v} for y, (_, v) in latest.items()
          if 0 <= y < len(ys) and abs(ys[y] - price) / price * 100 <= RANGE_PCT]
    lv.sort(key=lambda z: -z["value"])
    return lv[:8]


# ───────── Construcción de la respuesta ─────────
def build(tf):
    candles, csrc = fetch_candles(tf)
    price = candles[-1]["close"]
    step = TF_SECONDS[tf]
    series, oi_info = combined_oi(candles, tf)
    if not series:
        raise RuntimeError("Sin Open Interest: " + json.dumps(oi_info, ensure_ascii=False)[:200])
    size = bin_size(price)
    L, S = simulate(candles, oi_deltas(candles, series), size)
    segL, segS = segments(L), segments(S)

    vals = sorted(s[3] for s in segL + segS)
    vmax = vals[min(len(vals) - 1, int(len(vals) * 0.99))] if vals else 0

    def keep(segs):
        return [s for s in segs if vmax and s[3] >= MIN_DRAW * vmax]

    zones = []
    for side, grid in (("long", L[-1]), ("short", S[-1])):
        for k, v in grid.items():
            zp = (k + 0.5) * size
            if abs(zp - price) / price * 100 <= RANGE_PCT:
                zones.append({"side": side, "price": zp, "value": round(v),
                              "dist": round((zp - price) / price * 100, 3)})
    zones.sort(key=lambda z: -z["value"])
    zones = zones[:10]

    ranges = asia_ranges(candles, step)[-3:]
    signals = find_sweeps(candles, ranges, L, S, size, vmax)[-10:] if vmax else []

    note, cg = None, []
    if CG_KEY:
        try:
            cg = coinglass_levels(tf, price)
        except Exception as e:
            note = f"Coinglass: {str(e)[:60]}"

    return {
        "symbol": SYMBOL, "tf": tf, "price": price, "updated": int(time.time()),
        "candles": candles, "candles_src": csrc,
        "oi_used": list(series), "oi_info": oi_info,
        "heat": {"bin": size, "vmax": vmax, "long": keep(segL), "short": keep(segS)},
        "zones": zones, "cg": cg,
        "asia": [{k: r[k] for k in ("day", "i0", "i1", "i2", "high", "low")} for r in ranges],
        "signals": signals,
        "note": note, "alerts_on": bool(TG_TOKEN and TG_CHAT),
    }


def cached_build(tf):
    now = time.time()
    with _lock:
        hit = _cache.get(tf)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    with _build_lock:
        with _lock:
            hit = _cache.get(tf)
            if hit and time.time() - hit[0] < CACHE_TTL:
                return hit[1]
        try:
            data = build(tf)
        except Exception as e:
            if hit and time.time() - hit[0] < 900:
                return {**hit[1], "note": f"datos antiguos ({str(e)[:50]})"}
            raise
        with _lock:
            _cache[tf] = (time.time(), data)
        return data


# ───────── Telegram + motor de alertas ─────────
alert_status = {"enabled": bool(TG_TOKEN and TG_CHAT), "cycles": 0, "sent": 0, "error": None, "last": None}
_astate = {"primed": False, "cool": {}, "last_sig": 0, "strong": {}, "size": None}


def send_telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                          json={"chat_id": TG_CHAT, "text": text}, timeout=15)
        r.raise_for_status()
        alert_status["sent"] += 1
        alert_status["error"] = None
        return True
    except Exception as e:
        alert_status["error"] = str(e)[:150].replace(TG_TOKEN, "***")
        return False


def fp(p):
    return f"{p:,.0f}"


def fv(v):
    return f"{v / 1e6:,.0f}M$" if v >= 1e6 else f"{v / 1e3:,.0f}K$"


def alert_cycle(send=send_telegram):
    data = cached_build(ALERT_TF)
    price, vmax, now = data["price"], data["heat"]["vmax"], time.time()
    candles = data["candles"]
    msgs = []
    primed = _astate["primed"]

    strong = {}
    for z in data["zones"]:
        if vmax and z["value"] >= ALERT_MIN_RATIO * vmax:
            strong[f"{z['side']}:{round(z['price'])}"] = z

    for key, z in strong.items():
        if abs(z["dist"]) <= ALERT_DIST_PCT and now - _astate["cool"].get(key, 0) > ALERT_COOLDOWN:
            if primed:
                lado = "CORTOS" if z["side"] == "short" else "LARGOS"
                msgs.append(f"⚠️ {data['symbol']} {fp(price)} · cerca de zona fuerte de liquidación de {lado} "
                            f"en {fp(z['price'])} ({abs(z['dist']):.2f}%) · ≈ {fv(z['value'])}")
            _astate["cool"][key] = now

    last_sig = _astate["last_sig"]
    for s in data["signals"]:
        if s["time"] > last_sig:
            if primed:
                cual = "máximo" if s["type"] == "high" else "mínimo"
                conf = " ★ coincide con zona de liquidación" if s["confluence"] else ""
                msgs.append(f"🎯 {data['symbol']} · barrido del {cual} asiático ({fp(s['level'])}) con rechazo{conf}. "
                            f"Sesgo {s['dir'].upper()} hacia {fp(s['target'])}")
            last_sig = max(last_sig, s["time"])
    _astate["last_sig"] = last_sig

    if _astate["size"] == data["heat"]["bin"] and len(candles) >= 2:
        lo = min(c["low"] for c in candles[-2:])
        hi = max(c["high"] for c in candles[-2:])
        for key, z in _astate["strong"].items():
            if key in strong:
                continue
            crossed = (z["side"] == "long" and lo <= z["price"]) or (z["side"] == "short" and hi >= z["price"])
            if crossed and primed:
                lado = "LARGOS" if z["side"] == "long" else "CORTOS"
                msgs.append(f"💥 {data['symbol']} · zona de liquidación de {lado} en {fp(z['price'])} barrida "
                            f"(≈ {fv(z['value'])})")
    _astate["strong"] = strong
    _astate["size"] = data["heat"]["bin"]
    _astate["primed"] = True

    for m in msgs:
        send(m)
    alert_status["cycles"] += 1
    alert_status["last"] = int(now)
    return msgs


def alert_loop():
    while True:
        try:
            alert_cycle()
        except Exception as e:
            alert_status["error"] = str(e)[:150]
        time.sleep(ALERT_EVERY)


_alert_thread = None


def start_alerts():
    global _alert_thread
    if _alert_thread or not (TG_TOKEN and TG_CHAT) or ALERT_TF not in TF_SECONDS:
        return False
    _alert_thread = threading.Thread(target=alert_loop, daemon=True)
    _alert_thread.start()
    return True


# ───────── Rutas ─────────
@app.before_request
def guard():
    if ACCESS_KEY and request.path.startswith("/api/"):
        k = request.args.get("k", "") or request.headers.get("X-Key", "")
        if not hmac.compare_digest(k.encode(), ACCESS_KEY.encode()):
            abort(401)


@app.route("/api/data")
def api_data():
    tf = request.args.get("tf", "15m")
    if tf not in TF_SECONDS:
        return jsonify({"error": "tf no válido"}), 400
    try:
        return jsonify(cached_build(tf))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/status")
def api_status():
    return jsonify({"alerts": alert_status, "alert_tf": ALERT_TF, "exchanges": EXCHANGES,
                    "coinglass_key": bool(CG_KEY), "asia_utc": [ASIA_START, ASIA_END]})


@app.route("/api/test-alert")
def api_test_alert():
    ok = send_telegram(f"✅ Prueba de alertas · {SYMBOL} liquidation map")
    return jsonify({"ok": ok, "error": alert_status["error"], "configured": bool(TG_TOKEN and TG_CHAT)})


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


start_alerts()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
