import math
from collections import defaultdict, deque
import os
import threading
import time

import requests
from flask import Flask, jsonify, request, send_from_directory

import liqdata as LQ

app = Flask(__name__, static_folder="static")
try:
    from flask_compress import Compress   # gzip: la respuesta pesa mucho menos en el móvil
    Compress(app)
except ImportError:  # pragma: no cover
    pass

# ───────────── Configuración (variables de entorno) ─────────────
SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
FAPI = os.getenv("BINANCE_FAPI", "https://fapi.binance.com")
BYBIT = os.getenv("BYBIT_BASE", "https://api.bybit.com")
USE_BYBIT = os.getenv("USE_BYBIT", "1") == "1"

CG_KEY = os.getenv("COINGLASS_API_KEY", "").strip()
CG_BASE = os.getenv("COINGLASS_BASE", "https://open-api-v4.coinglass.com")
CG_EXCHANGE = os.getenv("CG_EXCHANGE", "Binance")

ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()
CACHE_TTL = int(os.getenv("CACHE_TTL", "60"))

TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
ALERT_TF = os.getenv("ALERT_TF", "15m")
ALERT_DIST_PCT = float(os.getenv("ALERT_DIST_PCT", "0.4"))
ALERT_MIN_RATIO = float(os.getenv("ALERT_MIN_RATIO", "0.6"))
ALERT_INTERVAL = int(os.getenv("ALERT_INTERVAL", "60"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN_MIN", "60")) * 60

ASIA_START = int(os.getenv("ASIA_START_UTC", "0"))
ASIA_END = int(os.getenv("ASIA_END_UTC", "7"))

CANDLES = {"5m": 900, "15m": 800, "1h": 600, "4h": 300}
TF_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
BYBIT_IV = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}
CG_RANGE = {"5m": "24h", "15m": "3d", "1h": "7d", "4h": "30d"}

ALL_TIERS = [5, 10, 25, 50, 100]
TIER_W = {5: 0.15, 10: 0.25, 25: 0.30, 50: 0.18, 100: 0.12}
MODELS = ("auto", "oi", "vol", "cg")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "cg" if CG_KEY else "auto")
# Modelo: entrada al precio típico, envejecimiento (vida media en horas), filtro de picos (z, 0 = apagado)
# y cierre de posiciones cuando baja el Open Interest.
DEFAULT_PARAMS = {"entry": "typical", "half_life_h": 72.0, "spike_z": 0.0, "close_on_drop": True}
MMR = 0.005
BIN_PCT = float(os.getenv("BIN_PCT", "0.02"))
ZONE_GAP_PCT = float(os.getenv("ZONE_GAP_PCT", "0.04"))
RANGE_PCT = float(os.getenv("RANGE_PCT", "8.0"))

_cache = {}
_lock = threading.Lock()


# ───────────── Datos de mercado ─────────────
def get_json(url, params=None, headers=None):
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_current_price():
    """Precio actual de BTCUSDT en Binance Futures (respaldo del precio en directo)."""
    data = get_json(f"{FAPI}/fapi/v1/ticker/price", {"symbol": SYMBOL})
    return float(data["price"])


def fetch_candles(tf, limit=None):
    limit = limit or CANDLES[tf]
    rows = get_json(f"{FAPI}/fapi/v1/klines", {"symbol": SYMBOL, "interval": tf, "limit": min(limit, 1500)})
    return [
        {
            "time": int(r[0] // 1000),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "v": float(r[7]),
            "tb": float(r[10]) if len(r) > 10 else None,
        }
        for r in rows
    ]


def fetch_oi_binance(tf, limit=None):
    limit = limit or CANDLES[tf]
    step = TF_SECONDS[tf]
    out, end, remaining = {}, None, limit
    for _ in range(5):
        n = min(500, remaining)
        params = {"symbol": SYMBOL, "period": tf, "limit": n}
        if end:
            params["endTime"] = end
        rows = get_json(f"{FAPI}/futures/data/openInterestHist", params)
        if not rows:
            break
        for r in rows:
            ts = int(r["timestamp"]) // 1000
            out[ts - ts % step] = float(r["sumOpenInterestValue"])
        remaining -= len(rows)
        if remaining <= 0 or len(rows) < n:
            break
        end = min(int(r["timestamp"]) for r in rows) - 1
    return out


def fetch_oi_bybit(tf, limit=None):
    limit = limit or CANDLES[tf]
    step = TF_SECONDS[tf]
    out, cursor = {}, None
    for _ in range(min(5, math.ceil(limit / 200))):
        params = {"category": "linear", "symbol": SYMBOL, "intervalTime": BYBIT_IV[tf], "limit": 200}
        if cursor:
            params["cursor"] = cursor
        r = get_json(f"{BYBIT}/v5/market/open-interest", params)
        if r.get("retCode") != 0:
            raise RuntimeError(r.get("retMsg", "error Bybit"))
        for row in r["result"]["list"]:
            ts = int(row["timestamp"]) // 1000
            out[ts - ts % step] = float(row["openInterest"])
        cursor = r["result"].get("nextPageCursor")
        if not cursor:
            break
    return out


def align_oi(candles, oi, step):
    times = {c["time"] for c in candles}
    best, best_n = 0, -1
    for off in (0, -step, step):
        n = sum(1 for t in oi if t + off in times)
        if n > best_n:
            best, best_n = off, n
    return {t + best: v for t, v in oi.items()}


def load_sources(tf, candles):
    step = TF_SECONDS[tf]
    sources, errors = {}, []
    try:
        sources["binance"] = align_oi(candles, fetch_oi_binance(tf), step)
    except Exception as e:
        errors.append(f"OI Binance: {e}")
    if USE_BYBIT:
        try:
            raw = align_oi(candles, fetch_oi_bybit(tf), step)
            px = {c["time"]: c["close"] for c in candles}
            sources["bybit"] = {t: v * px[t] for t, v in raw.items() if t in px}
        except Exception as e:
            errors.append(f"OI Bybit: {e}")
    return sources, errors


# ───────────── Zonas (agrupa niveles vecinos) ─────────────
def cluster_zones(active, price, top=6):
    if not active:
        return []
    gap = price * ZONE_GAP_PCT / 100
    pts = sorted(active)
    groups, cur = [], [pts[0]]
    for a in pts[1:]:
        if a[0] - cur[-1][0] <= gap and a[0] - cur[0][0] <= 3 * gap:
            cur.append(a)
        else:
            groups.append(cur)
            cur = [a]
    groups.append(cur)
    zones = []
    for g in groups:
        L = sum(a[1] for a in g)
        S = sum(a[2] for a in g)
        tot = L + S
        if tot <= 0:
            continue
        zones.append({
            "price": round(sum(a[0] * (a[1] + a[2]) for a in g) / tot, 2),
            "side": "long" if L >= S else "short",
            "long_value": round(L),
            "short_value": round(S),
            "total": round(tot),
        })
    zones.sort(key=lambda z: z["total"], reverse=True)
    zones = zones[:top]
    top_total = zones[0]["total"] if zones else 0
    for z in zones:
        z["ratio"] = round(z["total"] / top_total, 3) if top_total else 0
    return zones


def cluster_bands(active, price, size, top=15):
    """Agrupa niveles vecinos en bandas para la vista limpia: (precio, L, S, inicio)."""
    if not active:
        return []
    gap = price * ZONE_GAP_PCT / 100
    pts = sorted(active)
    groups, cur = [], [pts[0]]
    for a in pts[1:]:
        if a[0] - cur[-1][0] <= gap and a[0] - cur[0][0] <= 3 * gap:
            cur.append(a)
        else:
            groups.append(cur)
            cur = [a]
    groups.append(cur)
    bands = []
    for g in groups:
        L = sum(a[1] for a in g)
        S = sum(a[2] for a in g)
        tot = L + S
        if tot <= 0:
            continue
        t0 = sum(a[3] * (a[1] + a[2]) for a in g) / tot
        bands.append({
            "lo": round(g[0][0] - size / 2, 2), "hi": round(g[-1][0] + size / 2, 2),
            "price": round(sum(a[0] * (a[1] + a[2]) for a in g) / tot, 2),
            "t0": int(t0), "L": round(L), "S": round(S), "total": round(tot),
        })
    bands.sort(key=lambda b: -b["total"])
    return bands[:top]


# ───────────── Estimación del heatmap con tiempo ─────────────
def oi_delta(sources, prev, t):
    d, seen = 0.0, False
    for name, series in sources.items():
        cur = series.get(t)
        if cur is None:
            continue
        if name in prev:
            d += cur - prev[name]
            seen = True
        prev[name] = cur
    return d if seen else None


def tiers_from(levs):
    sel = [l for l in ALL_TIERS if l in (levs or ALL_TIERS)] or list(ALL_TIERS)
    tot = sum(TIER_W[l] for l in sel)
    return [(l, TIER_W[l] / tot) for l in sel]


def buy_share(c):
    """Parte del volumen que fue compra agresiva (0 a 1). Sin dato: color de la vela."""
    v, tb = c.get("v") or 0, c.get("tb")
    if v > 0 and tb is not None:
        return min(1.0, max(0.0, tb / v))
    if c["close"] > c["open"]:
        return 1.0
    if c["close"] < c["open"]:
        return 0.0
    return 0.5


def estimate_heat(candles, sources, price, tiers, model="oi", out=None, params=None):
    """Mapa de liquidaciones estimado, vela a vela y sin mirar al futuro.

    - OI sube: entran largos y cortos por la misma cantidad (cada contrato tiene las dos partes).
    - OI baja: se cierran posiciones; con vela alcista cierran cortos, con vela bajista largos.
    - Envejecimiento: todas las posiciones pierden peso con el tiempo (vida media en horas).
    - Toque: si el precio alcanza el nivel, esa liquidación se da por ejecutada.
    Las cantidades se guardan como "masa"; el valor real = masa x F[lado], donde F recoge cierres y
    envejecimiento. Así un cierre o el paso del tiempo no parte los segmentos del gráfico.
    """
    P = dict(DEFAULT_PARAMS)
    P.update(params or {})
    size = price * BIN_PCT / 100
    lo, hi = price * (1 - RANGE_PCT / 100), price * (1 + RANGE_PCT / 100)
    n = len(candles)
    step = (candles[1]["time"] - candles[0]["time"]) if n > 1 else 300
    decay = 0.5 ** (step / (P["half_life_h"] * 3600)) if P.get("half_life_h") else 1.0
    nt = len(ALL_TIERS)
    F = {1: 1.0, -1: 1.0}
    M = {1: 0.0, -1: 0.0}
    FL, FS = [], []
    recs = []
    vals = {}
    tv = {}
    opened = {}
    segs = []
    prev = {}
    touches = defaultdict(float)
    pos_hist = deque(maxlen=96)

    def k_of(side):
        return 0 if side == 1 else 1

    def settle(i, touched):
        for b in touched:
            v = vals[b]
            if v[0] < 1e-6:
                v[0] = 0.0
            if v[1] < 1e-6:
                v[1] = 0.0
            old = opened.get(b)
            if old and abs(old[1] - v[0]) < 1e-6 and abs(old[2] - v[1]) < 1e-6:
                continue
            if old:
                segs.append([round((b + 0.5) * size, 2), candles[old[0]]["time"], candles[i]["time"], round(old[1]), round(old[2])])
                del opened[b]
            if v[0] + v[1] > 0:
                opened[b] = (i, v[0], v[1])

    def add(p, vol, side, lev, touched):
        if vol <= 0 or not (lo <= p <= hi):
            return
        b = math.floor(p / size)
        ti = ALL_TIERS.index(lev)
        mass = vol / F[side]
        recs.append([p, mass, side, b, ti])
        vals.setdefault(b, [0.0, 0.0])[k_of(side)] += mass
        tv.setdefault(b, [[0.0] * nt, [0.0] * nt])[k_of(side)][ti] += mass
        M[side] += mass
        touched.add(b)

    def renorm(side, touched):
        f, k = F[side], k_of(side)
        for r in recs:
            if r[2] == side:
                r[1] *= f
        for b, v in vals.items():
            if v[k] > 0:
                v[k] *= f
                tv[b][k] = [x * f for x in tv[b][k]]
                touched.add(b)
        M[side] *= f
        F[side] = 1.0

    for i, c in enumerate(candles):
        touched = set()
        if decay < 1:
            F[1] *= decay
            F[-1] *= decay
        kept = []
        for r in recs:
            p, mass, side, b, ti = r
            if (side == 1 and c["low"] <= p) or (side == -1 and c["high"] >= p):
                k = k_of(side)
                vals[b][k] -= mass
                tv[b][k][ti] -= mass
                M[side] -= mass
                touched.add(b)
                touches[(b, i, side)] += mass * F[side]
            else:
                kept.append(r)
        recs = kept
        entry = (c["high"] + c["low"] + c["close"]) / 3 if P.get("entry") == "typical" else c["close"]
        if model == "vol":
            amount = c.get("v") or 0
            if amount > 0:
                s_buy = buy_share(c)
                for lev, w in tiers:
                    add(entry * (1 - 1 / lev + MMR), amount * w * s_buy, 1, lev, touched)
                    add(entry * (1 + 1 / lev - MMR), amount * w * (1 - s_buy), -1, lev, touched)
        else:
            d = oi_delta(sources, prev, c["time"])
            if d is not None and d > 0:
                ok = True
                if P.get("spike_z", 0) > 0:
                    if len(pos_hist) >= 20:
                        mu = sum(pos_hist) / len(pos_hist)
                        sd = (sum((x - mu) ** 2 for x in pos_hist) / len(pos_hist)) ** 0.5
                        ok = sd > 0 and (d - mu) / sd >= P["spike_z"]
                    else:
                        ok = False   # sin historial suficiente no se puede saber si es un pico
                pos_hist.append(d)
                if ok:
                    for lev, w in tiers:
                        add(entry * (1 - 1 / lev + MMR), d * w, 1, lev, touched)
                        add(entry * (1 + 1 / lev - MMR), d * w, -1, lev, touched)
            elif d is not None and d < 0 and P.get("close_on_drop", True):
                if c["close"] > c["open"]:
                    closing = [(-1, 1.0)]
                elif c["close"] < c["open"]:
                    closing = [(1, 1.0)]
                else:
                    closing = [(1, 0.5), (-1, 0.5)]
                for side, share in closing:
                    cur = M[side] * F[side]
                    if cur > 0:
                        F[side] *= max(0.0, 1 - (-d) * share / cur)
        for side in (1, -1):
            if F[side] < 1e-3:
                renorm(side, touched)
        settle(i, touched)
        FL.append(F[1])
        FS.append(F[-1])

    tidx = {c["time"]: i for i, c in enumerate(candles)}
    active = []
    starts = []
    for b, (i0, mL, mS) in opened.items():
        pr = round((b + 0.5) * size, 2)
        segs.append([pr, candles[i0]["time"], candles[-1]["time"], round(mL), round(mS)])
        L, S = mL * FL[-1], mS * FS[-1]
        tiers_v = [max(0.0, tv[b][0][t] * FL[-1] + tv[b][1][t] * FS[-1]) for t in range(nt)]
        active.append((pr, L, S, [round(x) for x in tiers_v]))
        starts.append((pr, L, S, candles[i0]["time"]))

    def seg_value(sg):
        j = tidx.get(sg[2], n - 1)
        return sg[3] * FL[j] + sg[4] * FS[j]

    totals = sorted(v for v in (seg_value(sg) for sg in segs) if v > 0)
    vmax = totals[min(len(totals) - 1, int(0.95 * (len(totals) - 1)))] if totals else 0
    zones = cluster_zones(active, price)
    heat = {
        "mode": "time",
        "size": size,
        "max": vmax,
        "segments": segs,
        "F": {"L": [round(x, 6) for x in FL], "S": [round(x, 6) for x in FS]},
        "active": [[p, round(L), round(S), t] for p, L, S, t in sorted(active) if L + S >= 1],
        "bands": cluster_bands(starts, price, size),
        "params": P,
    }
    if out is not None:
        out["touches"] = dict(touches)
    return heat, zones


# ───────────── Coinglass (opcional, planes de pago) ─────────────
def coinglass_heat(tf, price):
    r = get_json(
        f"{CG_BASE}/api/futures/liquidation/heatmap/model2",
        {"exchange": CG_EXCHANGE, "symbol": SYMBOL, "range": CG_RANGE[tf]},
        {"CG-API-KEY": CG_KEY},
    )
    if str(r.get("code")) != "0":
        raise RuntimeError(f"Coinglass: {r.get('msg', 'error')}")
    d = r["data"]
    ys = [float(y) for y in d["y_axis"]]
    cells = d["liquidation_leverage_data"]
    if not cells or len(ys) < 2:
        return {"mode": "static", "size": price * BIN_PCT / 100, "max": 0, "bins": [], "active": []}
    size = abs(ys[1] - ys[0])
    max_x = max(c[0] for c in cells)
    latest = {}
    for x, y, v in cells:
        if x >= max_x - 2 and (y not in latest or x > latest[y][0]):
            latest[y] = (x, float(v))
    bins = []
    for y, (_, v) in sorted(latest.items()):
        if 0 <= y < len(ys) and abs(ys[y] - price) / price * 100 <= RANGE_PCT:
            bins.append({"price": ys[y], "value": v})
    vmax = max((b["value"] for b in bins), default=0)
    active = [[b["price"], b["value"] if b["price"] < price else 0, b["value"] if b["price"] >= price else 0, None] for b in bins]
    return {"mode": "static", "size": size, "max": vmax, "bins": bins, "active": active}


def zones_from_bins(heat, price):
    return cluster_zones([tuple(a[:3]) for a in heat["active"]], price)


# ───────────── Rango asiático y barridos ─────────────
def asia_info(c15):
    if not c15:
        return None
    last_t = c15[-1]["time"]
    last_day = last_t - last_t % 86400
    hour_last = (last_t % 86400) // 3600
    day = last_day if hour_last >= ASIA_START else last_day - 86400
    s = day + ASIA_START * 3600
    e = day + ASIA_END * 3600
    rng = [c for c in c15 if s <= c["time"] < e]
    if not rng:
        return None
    hi = max(c["high"] for c in rng)
    lo = min(c["low"] for c in rng)
    complete = last_t >= e
    sweep = None
    if complete:
        for c in c15[:-1]:  # solo velas cerradas
            if c["time"] < e:
                continue
            if c["high"] > hi and c["close"] < hi:
                sweep = {"side": "high", "time": c["time"], "level": hi}
            elif c["low"] < lo and c["close"] > lo:
                sweep = {"side": "low", "time": c["time"], "level": lo}
    return {"high": hi, "low": lo, "start": s, "end": e, "complete": complete, "sweep": sweep}


# ───────────── Construcción de la respuesta ─────────────
_ctx_cache = {"t": 0, "v": {}}


def market_context():
    now = time.time()
    if now - _ctx_cache["t"] < 60:
        return _ctx_cache["v"]
    ctx = {}
    if LQ.MARK["price"] and now * 1000 - LQ.MARK["ts"] < 120000:
        ctx["funding"], ctx["next_funding"] = LQ.MARK["funding"], LQ.MARK["next_funding"]
    else:
        try:
            r = get_json(f"{FAPI}/fapi/v1/premiumIndex", {"symbol": SYMBOL})
            ctx["funding"], ctx["next_funding"] = float(r["lastFundingRate"]), int(r["nextFundingTime"])
        except Exception:
            pass
    try:
        r = get_json(f"{FAPI}/fapi/v1/ticker/24hr", {"symbol": SYMBOL})
        ctx["chg24"], ctx["last"] = float(r["priceChangePercent"]), float(r["lastPrice"])
        ctx["high24"], ctx["low24"] = float(r["highPrice"]), float(r["lowPrice"])
    except Exception:
        pass
    if LQ.MARK["price"]:
        ctx["mark"] = LQ.MARK["price"]
    for key, path in (("ls_accounts", "globalLongShortAccountRatio"), ("ls_top", "topLongShortPositionRatio")):
        try:
            r = get_json(f"{FAPI}/futures/data/{path}", {"symbol": SYMBOL, "period": "5m", "limit": 1})
            ctx[key] = float(r[-1]["longShortRatio"])
            if key == "ls_accounts":
                ctx["long_pct"] = float(r[-1]["longAccount"])
        except Exception:
            pass
    _ctx_cache.update(t=now, v=ctx)
    return ctx


_book_cache = {"t": 0, "v": None}


def order_book_walls(price):
    """Muros de liquidez reales del libro de Binance Futuros (no son liquidaciones)."""
    now = time.time()
    if now - _book_cache["t"] < 30 and _book_cache["v"]:
        return _book_cache["v"]
    r = get_json(f"{FAPI}/fapi/v1/depth", {"symbol": SYMBOL, "limit": 1000})
    step = price * 0.0005
    out = {}
    for name, rows in (("bids", r.get("bids") or []), ("asks", r.get("asks") or [])):
        acc = defaultdict(float)
        for p, q in rows:
            p, q = float(p), float(q)
            acc[math.floor(p / step)] += p * q
        vals = sorted(v for v in acc.values() if v > 0)
        med = vals[len(vals) // 2] if vals else 0
        walls = [[round((k + 0.5) * step, 2), round(v)] for k, v in acc.items() if med and v >= 2.5 * med]
        walls.sort(key=lambda w: -w[1])
        out[name] = walls[:8]
        out[name + "_total"] = round(sum(vals))
    _book_cache.update(t=now, v=out)
    return out


def calib_params():
    c = LQ.meta_get("calib") if LQ else None
    if c and c.get("status") == "ok":
        return c
    return None


def build(tf, model, levs):
    candles = fetch_candles(tf)
    price = candles[-1]["close"]
    notes, used = [], []
    heat = zones = None
    source = "estimado"
    if model == "cg":
        if not CG_KEY:
            notes.append("Sin clave de Coinglass; usando OI")
            model = "oi"
        else:
            try:
                heat = coinglass_heat(tf, price)
                zones = zones_from_bins(heat, price)
                source, used = "coinglass", ["coinglass"]
            except Exception as e:
                notes.append(f"Coinglass falló ({e})")
                model = "oi"
    calib = None
    if heat is None:
        tiers = tiers_from(levs)
        params = None
        if model == "auto":
            calib = calib_params()
            if calib:
                tiers = tiers_from(calib["params"]["tiers"])
                params = {"half_life_h": calib["params"]["half_life_h"], "spike_z": calib["params"]["spike_z"]}
            else:
                tiers = tiers_from(None)
        if model == "vol":
            heat, zones = estimate_heat(candles, {}, price, tiers, "vol")
            used = ["volumen"]
        else:
            sources, errs = load_sources(tf, candles)
            notes += errs
            if not sources:
                raise RuntimeError("; ".join(notes) or "sin datos de Open Interest")
            heat, zones = estimate_heat(candles, sources, price, tiers, "oi", params=params)
            used = list(sources)
    try:
        c15 = candles if tf == "15m" else fetch_candles("15m", 300)
        asia = asia_info(c15)
    except Exception as e:
        asia = None
        notes.append(f"Asia: {e}")
    return {
        "symbol": SYMBOL,
        "tf": tf,
        "model": model,
        "models": ["auto", "oi", "vol"] + (["cg"] if CG_KEY else []),
        "levs": [l for l, _ in tiers] if heat.get("mode") == "time" else [],
        "calib": ({"lift": calib.get("lift"), "events": calib.get("events"), "params": calib.get("params"), "updated": calib.get("updated")}
                  if calib else ({"status": "pendiente"} if model == "auto" else None)),
        "source": source,
        "used": used,
        "note": "; ".join(notes) or None,
        "updated": int(time.time()),
        "price": price,
        "candles": [{k: c[k] for k in ("time", "open", "high", "low", "close")} for c in candles],
        "heat": heat,
        "zones": zones,
        "asia": asia,
        "liqs": safe_chart_liqs(candles, TF_SECONDS[tf]),
        "book": safe_book(price),
        "ctx": market_context(),
        "collectors": LQ.status(),
    }


def safe_book(price):
    try:
        return order_book_walls(price)
    except Exception as e:
        return {"error": str(e)[:120]}


def safe_chart_liqs(candles, step):
    try:
        return LQ.chart_liqs(candles, step)
    except Exception as e:
        return {"items": [], "error": str(e)[:120]}


def norm_args(model, levs):
    model = model if model in MODELS else DEFAULT_MODEL
    levs = tuple(sorted({int(l) for l in (levs or ALL_TIERS) if int(l) in ALL_TIERS})) or tuple(ALL_TIERS)
    return model, levs


def get_data(tf, model=None, levs=None):
    model, levs = norm_args(model, levs)
    key = (tf, model, levs)
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    data = build(tf, model, levs)
    with _lock:
        _cache[key] = (now, data)
        if len(_cache) > 40:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:10]:
                del _cache[k]
    return data


# ───────────── Validación con liquidaciones reales ─────────────
_val_cache = {}
VAL_TTL = int(os.getenv("VAL_TTL", "300"))


def run_validation(tf, levs):
    candles = fetch_candles(tf)
    price = candles[-1]["close"]
    step = TF_SECONDS[tf]
    t0, t1 = candles[0]["time"], candles[-1]["time"] + step
    events = LQ.events_between(t0, t1)
    covered = LQ.covered_minutes(t0, t1)
    tiers = tiers_from(levs)
    res = {"tf": tf, "levs": [l for l, _ in tiers], "from": t0, "to": t1, "models": {}, "notes": []}
    sources, errs = load_sources(tf, candles)
    res["notes"] += errs
    calib = calib_params()
    plan = [("oi", tiers, None), ("vol", tiers, None)]
    if calib:
        plan.insert(0, ("auto", tiers_from(calib["params"]["tiers"]),
                        {"half_life_h": calib["params"]["half_life_h"], "spike_z": calib["params"]["spike_z"]}))
    for name, tr, params in plan:
        if name != "vol" and not sources:
            continue
        out = {}
        heat, _ = estimate_heat(candles, sources if name != "vol" else {}, price, tr,
                                "vol" if name == "vol" else "oi", out=out, params=params)
        res["models"][name] = LQ.validate(candles, step, heat, out["touches"], events, covered)
    res["calib"] = LQ.meta_get("calib")
    res["collectors"] = LQ.status()
    res["db"] = LQ.db_counts()
    res["updated"] = int(time.time())
    return res


CALIB_GRID = [
    {"tiers": t, "half_life_h": h, "spike_z": z}
    for t in ([25, 50, 100], [10, 25, 50, 100], [5, 10, 25, 50, 100])
    for h in (None, 24.0, 72.0)
    for z in (0.0, 1.0)
]
CALIB_MIN_EVENTS = int(os.getenv("CALIB_MIN_EVENTS", "100"))
CALIB_MIN_HOURS = float(os.getenv("CALIB_MIN_HOURS", "24"))


def calibrate(tf="5m"):
    """Prueba cada configuración contra las liquidaciones reales y guarda la que más acierta."""
    candles = fetch_candles(tf)
    price = candles[-1]["close"]
    step = TF_SECONDS[tf]
    t0, t1 = candles[0]["time"], candles[-1]["time"] + step
    events = LQ.events_between(t0, t1)
    covered = LQ.covered_minutes(t0, t1)
    hours = round(len(covered) / 60, 1)
    if len(events) < CALIB_MIN_EVENTS or hours < CALIB_MIN_HOURS:
        res = {"status": "insuficiente", "events": len(events), "hours": hours, "updated": int(time.time())}
        prev = LQ.meta_get("calib")
        if not (prev and prev.get("status") == "ok"):
            LQ.meta_set("calib", res)
        return res
    sources, errs = load_sources(tf, candles)
    if not sources:
        return {"status": "error", "error": "; ".join(errs)}
    results = []
    for g in CALIB_GRID:
        out = {}
        heat, _ = estimate_heat(candles, sources, price, tiers_from(g["tiers"]), "oi", out=out,
                                params={"half_life_h": g["half_life_h"], "spike_z": g["spike_z"]})
        r = LQ.validate(candles, step, heat, out["touches"], events, covered)
        results.append((g, r))
    def score(r):
        # acierto (¿estaban marcadas las liquidaciones?) y confirmación (¿las zonas marcadas eran reales?)
        if r["lift"] is None:
            return None
        if r.get("prec_lift"):
            return (r["lift"] * r["prec_lift"]) ** 0.5
        return r["lift"]

    for g, r in results:
        r["score"] = score(r)
    eligible = [(g, r) for g, r in results if r["score"] is not None and (r["hit"] or 0) >= 0.15]
    pool = eligible or [(g, r) for g, r in results if r["score"] is not None]
    if not pool:
        return {"status": "error", "error": "sin resultados"}
    best_g, best_r = max(pool, key=lambda gr: gr[1]["score"])
    default = next((r for g, r in results if g["tiers"] == [5, 10, 25, 50, 100] and g["half_life_h"] == 72.0 and g["spike_z"] == 0.0), None)
    res = {
        "status": "ok", "params": best_g, "score": best_r["score"], "lift": best_r["lift"], "hit": best_r["hit"], "base": best_r["base"],
        "prec_lift": best_r["prec_lift"],
        "prec": best_r["prec"], "events": best_r["events"], "hours": hours, "tested": len(results),
        "default_lift": default["lift"] if default else None, "default_score": default["score"] if default else None,
        "updated": int(time.time()),
        "ranking": [[g, r["score"], r["lift"], r["prec_lift"]] for g, r in sorted(results, key=lambda gr: -(gr[1]["score"] or 0))[:5]],
    }
    LQ.meta_set("calib", res)
    with _lock:
        for k in [k for k in _cache if k[1] == "auto"]:
            del _cache[k]
    return res


def calib_loop():
    time.sleep(120)
    while True:
        try:
            r = calibrate()
            print("calibración:", r.get("status"), r.get("params"), r.get("lift"), flush=True)
        except Exception as e:
            print("calibración error:", e, flush=True)
        time.sleep(3 * 3600)


def get_validation(tf, levs):
    _, levs = norm_args(None, levs)
    key = (tf, levs)
    now = time.time()
    with _lock:
        hit = _val_cache.get(key)
        if hit and now - hit[0] < VAL_TTL:
            return hit[1]
    res = run_validation(tf, levs)
    with _lock:
        _val_cache[key] = (now, res)
    return res


# ───────────── Alertas Telegram ─────────────
_alert_state = {"zones": {}, "sweeps": set()}


def fmt(x):
    return f"{x:,.0f}".replace(",", ".")


def tg_send(text):
    if not (TG_TOKEN and TG_CHAT):
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text},
        timeout=15,
    )
    r.raise_for_status()
    return True


def check_alerts(data, now=None):
    now = now or time.time()
    msgs = []
    price = data["price"]
    for z in data["zones"]:
        if z["ratio"] < ALERT_MIN_RATIO:
            continue
        dist = z["price"] - price
        if abs(dist) / price * 100 > ALERT_DIST_PCT:
            continue
        key = round(z["price"] / (price * 0.0025))
        recent = [
            _alert_state["zones"].get(k) for k in (key - 1, key, key + 1)
            if _alert_state["zones"].get(k) is not None
        ]
        if any(now - t < ALERT_COOLDOWN for t in recent):
            continue
        _alert_state["zones"][key] = now
        lado = "largos" if z["side"] == "long" else "cortos"
        signo = "+" if dist > 0 else "-"
        msgs.append(
            f"⚡ {data['symbol']} {fmt(price)}: a {fmt(abs(dist))} puntos ({signo}{abs(dist) / price * 100:.2f}%) "
            f"de una zona fuerte de liquidación de {lado} en {fmt(z['price'])}. Intensidad {int(z['ratio'] * 100)}%."
        )
    asia = data.get("asia")
    if asia and asia.get("sweep"):
        sw = asia["sweep"]
        key = (asia["start"], sw["side"])
        if key not in _alert_state["sweeps"] and now - sw["time"] < 7200:
            _alert_state["sweeps"].add(key)
            lado = "máximo" if sw["side"] == "high" else "mínimo"
            txt = (
                f"🎯 {data['symbol']}: barrido del {lado} del rango asiático ({fmt(sw['level'])}) "
                f"con cierre de vela de 15m de vuelta dentro del rango."
            )
            near = [
                z for z in data["zones"]
                if z["ratio"] >= 0.4 and abs(z["price"] - sw["level"]) / sw["level"] * 100 <= 0.5
            ]
            if near:
                z = min(near, key=lambda z: abs(z["price"] - sw["level"]))
                zl = "largos" if z["side"] == "long" else "cortos"
                txt += f" Coincide con zona de {zl} en {fmt(z['price'])}."
            msgs.append(txt)
    return msgs


def alert_loop():
    while True:
        try:
            data = get_data(ALERT_TF)
            for m in check_alerts(data):
                tg_send(m)
        except Exception as e:
            print("alert error:", e, flush=True)
        time.sleep(ALERT_INTERVAL)


_started = False


def start_alerts():
    global _started
    if _started or not (TG_TOKEN and TG_CHAT):
        return
    _started = True
    threading.Thread(target=alert_loop, daemon=True).start()


start_alerts()
LQ.start()
if os.getenv("COLLECT", "1") != "0":
    threading.Thread(target=calib_loop, daemon=True, name="calib").start()


# ───────────── Rutas ─────────────
def authorized():
    return not ACCESS_KEY or request.args.get("k", "") == ACCESS_KEY


@app.route("/api/data")
def api_data():
    if not authorized():
        return jsonify({"error": "clave de acceso incorrecta"}), 401
    tf = request.args.get("tf", "15m")
    if tf not in TF_SECONDS:
        return jsonify({"error": "tf no válido"}), 400
    model = request.args.get("model", "")
    try:
        levs = [int(x) for x in request.args.get("lev", "").split(",") if x.strip()]
    except ValueError:
        return jsonify({"error": "lev no válido"}), 400
    try:
        return jsonify(get_data(tf, model, levs))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


def parse_levs():
    return [int(x) for x in request.args.get("lev", "").split(",") if x.strip()]


@app.route("/api/validate")
def api_validate():
    if not authorized():
        return jsonify({"error": "clave de acceso incorrecta"}), 401
    tf = request.args.get("tf", "5m")
    if tf not in TF_SECONDS:
        return jsonify({"error": "tf no válido"}), 400
    try:
        levs = parse_levs()
    except ValueError:
        return jsonify({"error": "lev no válido"}), 400
    try:
        return jsonify(get_validation(tf, levs))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


_last_manual_calib = {"t": 0}


@app.route("/api/calibrate")
def api_calibrate():
    if not authorized():
        return jsonify({"error": "clave de acceso incorrecta"}), 401
    if time.time() - _last_manual_calib["t"] < 600:
        return jsonify({"error": "espera 10 minutos entre calibraciones", "calib": LQ.meta_get("calib")}), 429
    _last_manual_calib["t"] = time.time()
    try:
        return jsonify(calibrate())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/status")
def api_status():
    try:
        db = LQ.db_counts()
    except Exception as e:
        db = {"error": str(e)[:120]}
    return jsonify({"collectors": LQ.status(), "db": db, "mark": LQ.MARK, "data_dir": LQ.DATA_DIR})


@app.route("/api/price")
def api_price():
    if not authorized():
        return jsonify({"error": "clave de acceso incorrecta"}), 401
    try:
        return jsonify({"symbol": SYMBOL, "price": fetch_current_price(), "updated": int(time.time())})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/test-alert")
def api_test_alert():
    if not authorized():
        return jsonify({"error": "clave de acceso incorrecta"}), 401
    if not (TG_TOKEN and TG_CHAT):
        return jsonify({"error": "faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID"}), 400
    try:
        tg_send("✅ Liquidation Heatmap conectado. Las alertas llegarán a este chat.")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/health")
def health():
    return "ok"


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
