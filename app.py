import math
import os
import threading
import time

import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static")

# ─────────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────────

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")

FAPI = os.getenv(
    "BINANCE_FAPI",
    "https://fapi.binance.com"
)

BYBIT = os.getenv(
    "BYBIT_BASE",
    "https://api.bybit.com"
)

USE_BYBIT = os.getenv("USE_BYBIT", "1") == "1"

# CoinGlass opcional
CG_KEY = os.getenv("COINGLASS_API_KEY", "").strip()

CG_BASE = os.getenv(
    "COINGLASS_BASE",
    "https://open-api-v4.coinglass.com"
)

CG_EXCHANGE = os.getenv(
    "CG_EXCHANGE",
    "Binance"
)

# Seguridad
ACCESS_KEY = os.getenv("ACCESS_KEY", "").strip()

# Caché del heatmap
CACHE_TTL = int(os.getenv("CACHE_TTL", "60"))

# Telegram
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ALERT_TF = os.getenv("ALERT_TF", "15m")

ALERT_DIST_PCT = float(
    os.getenv("ALERT_DIST_PCT", "0.4")
)

ALERT_MIN_RATIO = float(
    os.getenv("ALERT_MIN_RATIO", "0.6")
)

ALERT_INTERVAL = int(
    os.getenv("ALERT_INTERVAL", "60")
)

ALERT_COOLDOWN = int(
    os.getenv("ALERT_COOLDOWN_MIN", "60")
) * 60

# Rango Asia UTC
ASIA_START = int(
    os.getenv("ASIA_START_UTC", "0")
)

ASIA_END = int(
    os.getenv("ASIA_END_UTC", "7")
)

# ─────────────────────────────────────────────
# TIMEFRAMES
# ─────────────────────────────────────────────

N_CANDLES = 300

TF_SECONDS = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

BYBIT_IV = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
}

CG_RANGE = {
    "5m": "24h",
    "15m": "3d",
    "1h": "7d",
    "4h": "30d",
}

# ─────────────────────────────────────────────
# MODELO DE ESTIMACIÓN
# ─────────────────────────────────────────────

# Distribución estimada de apalancamiento.
LEVERAGES = [
    (10, 0.25),
    (25, 0.35),
    (50, 0.25),
    (100, 0.15),
]

MMR = 0.005

# Tamaño de cada bloque del heatmap.
# 0.02% de BTC ≈ 17$ con BTC en 87.000$
BIN_PCT = float(
    os.getenv("BIN_PCT", "0.02")
)

# Antes era 0.12%.
# Lo reducimos para evitar zonas artificialmente anchas.
ZONE_GAP_PCT = float(
    os.getenv("ZONE_GAP_PCT", "0.04")
)

# Rango alrededor del precio actual.
RANGE_PCT = float(
    os.getenv("RANGE_PCT", "8.0")
)

# ─────────────────────────────────────────────
# CACHÉ
# ─────────────────────────────────────────────

_cache = {}

_lock = threading.Lock()


# ─────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────

def get_json(url, params=None, headers=None):
    r = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=15
    )

    r.raise_for_status()

    return r.json()


# ─────────────────────────────────────────────
# PRECIO
# ─────────────────────────────────────────────

def fetch_current_price():
    """
    Precio actual de BTCUSDT en Binance Futures.

    El frontend utiliza WebSocket para tiempo real.
    Este endpoint sirve como respaldo y para backend.
    """

    data = get_json(
        f"{FAPI}/fapi/v1/ticker/price",
        {"symbol": SYMBOL}
    )

    return float(data["price"])


# ─────────────────────────────────────────────
# VELAS
# ─────────────────────────────────────────────

def fetch_candles(tf, limit=N_CANDLES):

    rows = get_json(
        f"{FAPI}/fapi/v1/klines",
        {
            "symbol": SYMBOL,
            "interval": tf,
            "limit": limit
        }
    )

    return [
        {
            "time": int(r[0] // 1000),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
        }
        for r in rows
    ]


# ─────────────────────────────────────────────
# OPEN INTEREST BINANCE
# ─────────────────────────────────────────────

def fetch_oi_binance(tf, limit=N_CANDLES):

    rows = get_json(
        f"{FAPI}/futures/data/openInterestHist",
        {
            "symbol": SYMBOL,
            "period": tf,
            "limit": min(limit, 500),
        },
    )

    step = TF_SECONDS[tf]

    out = {}

    for r in rows:

        ts = int(r["timestamp"]) // 1000

        ts = ts - ts % step

        out[ts] = float(
            r["sumOpenInterestValue"]
        )

    return out


# ─────────────────────────────────────────────
# OPEN INTEREST BYBIT
# ─────────────────────────────────────────────

def fetch_oi_bybit(tf, limit=200):

    r = get_json(
        f"{BYBIT}/v5/market/open-interest",
        {
            "category": "linear",
            "symbol": SYMBOL,
            "intervalTime": BYBIT_IV[tf],
            "limit": limit,
        },
    )

    if r.get("retCode") != 0:

        raise RuntimeError(
            r.get(
                "retMsg",
                "error Bybit"
            )
        )

    step = TF_SECONDS[tf]

    out = {}

    for row in r["result"]["list"]:

        ts = int(
            row["timestamp"]
        ) // 1000

        ts = ts - ts % step

        out[ts] = float(
            row["openInterest"]
        )

    return out


# ─────────────────────────────────────────────
# ALINEAR OI
# ─────────────────────────────────────────────

def align_oi(candles, oi, step):

    times = {
        c["time"]
        for c in candles
    }

    best = 0
    best_n = -1

    for off in (
        0,
        -step,
        step
    ):

        n = sum(
            1
            for t in oi
            if t + off in times
        )

        if n > best_n:
            best = off
            best_n = n

    return {
        t + best: v
        for t, v in oi.items()
    }


# ─────────────────────────────────────────────
# CARGAR FUENTES
# ─────────────────────────────────────────────

def load_sources(tf, candles):

    step = TF_SECONDS[tf]

    sources = {}

    errors = []

    # Binance
    try:

        sources["binance"] = align_oi(
            candles,
            fetch_oi_binance(tf),
            step
        )

    except Exception as e:

        errors.append(
            f"OI Binance: {e}"
        )

    # Bybit
    if USE_BYBIT:

        try:

            raw = align_oi(
                candles,
                fetch_oi_bybit(tf),
                step
            )

            px = {
                c["time"]: c["close"]
                for c in candles
            }

            sources["bybit"] = {
                t: v * px[t]
                for t, v in raw.items()
                if t in px
            }

        except Exception as e:

            errors.append(
                f"OI Bybit: {e}"
            )

    return sources, errors


# ─────────────────────────────────────────────
# AGRUPACIÓN DE ZONAS
# ─────────────────────────────────────────────

def cluster_zones(active, price, top=6):

    if not active:
        return []

    gap = (
        price *
        ZONE_GAP_PCT /
        100
    )

    pts = sorted(
        active,
        key=lambda x: x[0]
    )

    groups = []

    cur = [pts[0]]

    for a in pts[1:]:

        if (
            a[0] - cur[-1][0] <= gap
            and
            a[0] - cur[0][0] <= 3 * gap
        ):

            cur.append(a)

        else:

            groups.append(cur)

            cur = [a]

    groups.append(cur)

    zones = []

    for g in groups:

        L = sum(
            a[1]
            for a in g
        )

        S = sum(
            a[2]
            for a in g
        )

        total = L + S

        if total <= 0:
            continue

        weighted_price = (
            sum(
                a[0] * (a[1] + a[2])
                for a in g
            )
            /
            total
        )

        zones.append(
            {
                "price": round(
                    weighted_price,
                    2
                ),

                "side":
                    "long"
                    if L >= S
                    else "short",

                "long_value":
                    round(L),

                "short_value":
                    round(S),

                "total":
                    round(total),
            }
        )

    zones.sort(
        key=lambda z: z["total"],
        reverse=True
    )

    zones = zones[:top]

    top_total = (
        zones[0]["total"]
        if zones
        else 0
    )

    for z in zones:

        z["ratio"] = round(
            z["total"] / top_total,
            3
        ) if top_total else 0

    return zones


# ─────────────────────────────────────────────
# DELTA DE OI
# ─────────────────────────────────────────────

def oi_delta(sources, prev, t):

    delta = 0.0

    seen = False

    for name, series in sources.items():

        cur = series.get(t)

        if cur is None:
            continue

        if name in prev:

            delta += (
                cur -
                prev[name]
            )

            seen = True

        prev[name] = cur

    if not seen:
        return None

    return delta


# ─────────────────────────────────────────────
# HEATMAP ESTIMADO
# ─────────────────────────────────────────────

def estimate_heat(
    candles,
    sources,
    price
):

    size = (
        price *
        BIN_PCT /
        100
    )

    lo = (
        price *
        (1 - RANGE_PCT / 100)
    )

    hi = (
        price *
        (1 + RANGE_PCT / 100)
    )

    recs = []

    vals = {}

    opened = {}

    segs = []

    prev = {}

    def settle(i, touched):

        for b in touched:

            v = vals[b]

            if v[0] < 1e-6:
                v[0] = 0.0

            if v[1] < 1e-6:
                v[1] = 0.0

            old = opened.get(b)

            if (
                old
                and
                abs(
                    old[1] -
                    v[0]
                ) < 1e-6
                and
                abs(
                    old[2] -
                    v[1]
                ) < 1e-6
            ):

                continue

            if old:

                segs.append(
                    [
                        round(
                            (b + 0.5) *
                            size,
                            2
                        ),

                        candles[
                            old[0]
                        ]["time"],

                        candles[i]["time"],

                        round(old[1]),

                        round(old[2]),
                    ]
                )

                del opened[b]

            if (
                v[0] +
                v[1]
                > 0
            ):

                opened[b] = (
                    i,
                    v[0],
                    v[1]
                )

    # Procesar velas
    for i, c in enumerate(candles):

        touched = set()

        kept = []

        # Liquidaciones estimadas
        for r in recs:

            p, vol, side, b = r

            hit = (
                (
                    side == 1
                    and
                    c["low"] <= p
                )
                or
                (
                    side == -1
                    and
                    c["high"] >= p
                )
            )

            if hit:

                vals[b][
                    0 if side == 1 else 1
                ] -= vol

                touched.add(b)

            else:

                kept.append(r)

        recs = kept

        # Cambio OI
        d = oi_delta(
            sources,
            prev,
            c["time"]
        )

        if (
            d is not None
            and
            d > 0
            and
            c["close"] != c["open"]
        ):

            direction = (
                1
                if c["close"] > c["open"]
                else -1
            )

            for lev, weight in LEVERAGES:

                if direction == 1:

                    # LONG
                    p = (
                        c["close"]
                        *
                        (
                            1 -
                            1 / lev +
                            MMR
                        )
                    )

                else:

                    # SHORT
                    p = (
                        c["close"]
                        *
                        (
                            1 +
                            1 / lev -
                            MMR
                        )
                    )

                if (
                    lo <= p <= hi
                ):

                    b = math.floor(
                        p / size
                    )

                    value = (
                        d *
                        weight
                    )

                    recs.append(
                        (
                            p,
                            value,
                            direction,
                            b
                        )
                    )

                    if b not in vals:

                        vals[b] = [
                            0.0,
                            0.0
                        ]

                    vals[b][
                        0
                        if direction == 1
                        else 1
                    ] += value

                    touched.add(b)

        settle(
            i,
            touched
        )

    # Posiciones activas
    active = []

    for b, (
        i0,
        L,
        S
    ) in opened.items():

        pr = round(
            (b + 0.5) *
            size,
            2
        )

        segs.append(
            [
                pr,

                candles[
                    i0
                ]["time"],

                candles[
                    -1
                ]["time"],

                round(L),

                round(S),
            ]
        )

        active.append(
            (
                pr,
                L,
                S
            )
        )

    # Máximo robusto
    totals = sorted(
        s[3] + s[4]
        for s in segs
        if s[3] + s[4] > 0
    )

    if totals:

        idx = min(
            len(totals) - 1,
            int(
                0.95 *
                (len(totals) - 1)
            )
        )

        vmax = totals[idx]

    else:

        vmax = 0

    # Zonas
    zones = cluster_zones(
        active,
        price
    )

    heat = {

        "mode": "time",

        "size": size,

        "max": vmax,

        "segments": segs,

        "active": [
            [
                p,
                round(L),
                round(S)
            ]
            for p, L, S
            in sorted(active)
        ],
    }

    return heat, zones


# ─────────────────────────────────────────────
# COINGLASS OPCIONAL
# ─────────────────────────────────────────────

def coinglass_heat(tf, price):

    r = get_json(

        f"{CG_BASE}/api/futures/"
        f"liquidation/heatmap/model2",

        {
            "exchange": CG_EXCHANGE,
            "symbol": SYMBOL,
            "range": CG_RANGE[tf],
        },

        {
            "CG-API-KEY": CG_KEY
        }
    )

    if str(
        r.get("code")
    ) != "0":

        raise RuntimeError(
            f"Coinglass: "
            f"{r.get('msg', 'error')}"
        )

    d = r["data"]

    ys = [
        float(y)
        for y in d["y_axis"]
    ]

    cells = d[
        "liquidation_leverage_data"
    ]

    if (
        not cells
        or
        len(ys) < 2
    ):

        return {
            "mode": "static",
            "size":
                price *
                BIN_PCT /
                100,
            "max": 0,
            "bins": [],
            "active": [],
        }

    size = abs(
        ys[1] -
        ys[0]
    )

    max_x = max(
        c[0]
        for c in cells
    )

    latest = {}

    for x, y, v in cells:

        if (
            x >= max_x - 2
            and
            (
                y not in latest
                or
                x >
                latest[y][0]
            )
        ):

            latest[y] = (
                x,
                float(v)
            )

    bins = []

    for y, (_, v) in sorted(
        latest.items()
    ):

        if (
            0 <= y < len(ys)
            and
            abs(
                ys[y] -
                price
            )
            /
            price
            *
            100
            <= RANGE_PCT
        ):

            bins.append(
                {
                    "price":
                        ys[y],
                    "value":
                        v
                }
            )

    vmax = max(
        (
            b["value"]
            for b in bins
        ),
        default=0
    )

    active = [
        [
            b["price"],

            b["value"]
            if b["price"] < price
            else 0,

            b["value"]
            if b["price"] >= price
            else 0
        ]

        for b in bins
    ]

    return {
        "mode": "static",
        "size": size,
        "max": vmax,
        "bins": bins,
        "active": active
    }


def zones_from_bins(
    heat,
    price
):

    return cluster_zones(
        [
            tuple(a)
            for a in heat["active"]
        ],
        price
    )


# ─────────────────────────────────────────────
# ASIA
# ─────────────────────────────────────────────

def asia_info(c15):

    if not c15:
        return None

    last_t = c15[-1]["time"]

    last_day = (
        last_t -
        last_t % 86400
    )

    hour_last = (
        last_t % 86400
    ) // 3600

    day = (
        last_day
        if hour_last >= ASIA_START
        else last_day - 86400
    )

    s = (
        day +
        ASIA_START * 3600
    )

    e = (
        day +
        ASIA_END * 3600
    )

    rng = [
        c
        for c in c15
        if s <= c["time"] < e
    ]

    if not rng:
        return None

    hi = max(
        c["high"]
        for c in rng
    )

    lo = min(
        c["low"]
        for c in rng
    )

    complete = last_t >= e

    sweep = None

    if complete:

        for c in c15[:-1]:

            if c["time"] < e:
                continue

            if (
                c["high"] > hi
                and
                c["close"] < hi
            ):

                sweep = {
                    "side": "high",
                    "time": c["time"],
                    "level": hi
                }

            elif (
                c["low"] < lo
                and
                c["close"] > lo
            ):

                sweep = {
                    "side": "low",
                    "time": c["time"],
                    "level": lo
                }

    return {
        "high": hi,
        "low": lo,
        "start": s,
        "end": e,
        "complete": complete,
        "sweep": sweep
    }


# ─────────────────────────────────────────────
# BUILD
# ─────────────────────────────────────────────

def build(tf):

    candles = fetch_candles(tf)

    # Último cierre confirmado
    price = candles[-1]["close"]

    notes = []

    used = []

    heat = None
    zones = None

    source = "estimado"

    # Intentar CoinGlass
    if CG_KEY:

        try:

            heat = coinglass_heat(
                tf,
                price
            )

            zones = zones_from_bins(
                heat,
                price
            )

            source = "coinglass"

            used = [
                "coinglass"
            ]

        except Exception as e:

            notes.append(
                f"Coinglass falló ({e})"
            )

    # Estimación propia
    if heat is None:

        sources, errs = load_sources(
            tf,
            candles
        )

        notes += errs

        if not sources:

            raise RuntimeError(
                "; ".join(notes)
                or
                "sin datos de Open Interest"
            )

        heat, zones = estimate_heat(
            candles,
            sources,
            price
        )

        used = list(
            sources
        )

    # Asia
    try:

        c15 = (
            candles
            if tf == "15m"
            else fetch_candles(
                "15m",
                300
            )
        )

        asia = asia_info(c15)

    except Exception as e:

        asia = None

        notes.append(
            f"Asia: {e}"
        )

    return {

        "symbol": SYMBOL,

        "tf": tf,

        "source": source,

        "used": used,

        "note":
            "; ".join(notes)
            if notes
            else None,

        "updated":
            int(time.time()),

        "price":
            price,

        "candles":
            candles,

        "heat":
            heat,

        "zones":
            zones,

        "asia":
            asia,
    }


# ─────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────

def get_data(tf):

    now = time.time()

    with _lock:

        hit = _cache.get(tf)

        if (
            hit
            and
            now - hit[0]
            < CACHE_TTL
        ):

            return hit[1]

    data = build(tf)

    with _lock:

        _cache[tf] = (
            now,
            data
        )

    return data


# ─────────────────────────────────────────────
# TELEGRAM
# ─────────────────────────────────────────────

_alert_state = {
    "zones": {},
    "sweeps": set()
}


def fmt(x):

    return (
        f"{x:,.0f}"
        .replace(",", ".")
    )


def tg_send(text):

    if not (
        TG_TOKEN
        and
        TG_CHAT
    ):

        return False

    r = requests.post(

        f"https://api.telegram.org/"
        f"bot{TG_TOKEN}/sendMessage",

        json={
            "chat_id": TG_CHAT,
            "text": text
        },

        timeout=15
    )

    r.raise_for_status()

    return True


def check_alerts(
    data,
    now=None
):

    now = now or time.time()

    msgs = []

    price = data["price"]

    for z in data["zones"]:

        if (
            z["ratio"]
            < ALERT_MIN_RATIO
        ):
            continue

        dist = (
            z["price"] -
            price
        )

        if (
            abs(dist) /
            price *
            100
            >
            ALERT_DIST_PCT
        ):
            continue

        key = round(
            z["price"] /
            (
                price *
                0.0025
            )
        )

        recent = [

            _alert_state[
                "zones"
            ].get(k)

            for k in (
                key - 1,
                key,
                key + 1
            )

            if
            _alert_state[
                "zones"
            ].get(k)
            is not None
        ]

        if any(
            now - t <
            ALERT_COOLDOWN
            for t in recent
        ):

            continue

        _alert_state[
            "zones"
        ][key] = now

        lado = (
            "largos"
            if z["side"] == "long"
            else "cortos"
        )

        signo = (
            "+"
            if dist > 0
            else "-"
        )

        msgs.append(

            f"⚡ {data['symbol']} "
            f"{fmt(price)}: a "
            f"{fmt(abs(dist))} puntos "
            f"({signo}"
            f"{abs(dist) / price * 100:.2f}%) "
            f"de una zona fuerte de "
            f"liquidación de {lado} "
            f"en {fmt(z['price'])}. "
            f"Intensidad "
            f"{int(z['ratio'] * 100)}%."
        )

    asia = data.get("asia")

    if (
        asia
        and
        asia.get("sweep")
    ):

        sw = asia["sweep"]

        key = (
            asia["start"],
            sw["side"]
        )

        if (
            key not in
            _alert_state["sweeps"]
            and
            now - sw["time"]
            < 7200
        ):

            _alert_state[
                "sweeps"
            ].add(key)

            lado = (
                "máximo"
                if sw["side"] == "high"
                else "mínimo"
            )

            txt = (

                f"🎯 {data['symbol']}: "
                f"barrido del {lado} "
                f"del rango asiático "
                f"({fmt(sw['level'])}) "
                f"con cierre de vela de "
                f"15m de vuelta dentro "
                f"del rango."
            )

            near = [

                z
                for z in data["zones"]

                if
                z["ratio"] >= 0.4
                and
                abs(
                    z["price"] -
                    sw["level"]
                )
                /
                sw["level"]
                *
                100
                <= 0.5
            ]

            if near:

                z = min(
                    near,
                    key=lambda z:
                    abs(
                        z["price"] -
                        sw["level"]
                    )
                )

                zl = (
                    "largos"
                    if z["side"] == "long"
                    else "cortos"
                )

                txt += (
                    f" Coincide con zona "
                    f"de {zl} en "
                    f"{fmt(z['price'])}."
                )

            msgs.append(txt)

    return msgs


def alert_loop():

    while True:

        try:

            data = get_data(
                ALERT_TF
            )

            for m in check_alerts(
                data
            ):

                tg_send(m)

        except Exception as e:

            print(
                "alert error:",
                e,
                flush=True
            )

        time.sleep(
            ALERT_INTERVAL
        )


_started = False


def start_alerts():

    global _started

    if (
        _started
        or
        not (
            TG_TOKEN
            and
            TG_CHAT
        )
    ):

        return

    _started = True

    threading.Thread(
        target=alert_loop,
        daemon=True
    ).start()


start_alerts()


# ─────────────────────────────────────────────
# RUTAS
# ─────────────────────────────────────────────

def authorized():

    return (
        not ACCESS_KEY
        or
        request.args.get(
            "k",
            ""
        ) == ACCESS_KEY
    )


@app.route("/api/data")
def api_data():

    if not authorized():

        return jsonify(
            {
                "error":
                    "clave de acceso incorrecta"
            }
        ), 401

    tf = request.args.get(
        "tf",
        "15m"
    )

    if tf not in TF_SECONDS:

        return jsonify(
            {
                "error":
                    "tf no válido"
            }
        ), 400

    try:

        return jsonify(
            get_data(tf)
        )

    except Exception as e:

        return jsonify(
            {
                "error":
                    str(e)
            }
        ), 502


@app.route("/api/price")
def api_price():

    if not authorized():

        return jsonify(
            {
                "error":
                    "clave de acceso incorrecta"
            }
        ), 401

    try:

        price = fetch_current_price()

        return jsonify(
            {
                "symbol": SYMBOL,
                "price": price,
                "updated":
                    int(time.time())
            }
        )

    except Exception as e:

        return jsonify(
            {
                "error":
                    str(e)
            }
        ), 502


@app.route("/api/test-alert")
def api_test_alert():

    if not authorized():

        return jsonify(
            {
                "error":
                    "clave de acceso incorrecta"
            }
        ), 401

    if not (
        TG_TOKEN
        and
        TG_CHAT
    ):

        return jsonify(
            {
                "error":
                    "faltan TELEGRAM_TOKEN "
                    "o TELEGRAM_CHAT_ID"
            }
        ), 400

    try:

        tg_send(
            "✅ Liquidation Heatmap conectado. "
            "Las alertas llegarán a este chat."
        )

        return jsonify(
            {
                "ok": True
            }
        )

    except Exception as e:

        return jsonify(
            {
                "error":
                    str(e)
            }
        ), 502


@app.route("/health")
def health():

    return "ok"


@app.route("/")
def index():

    return send_from_directory(
        app.static_folder,
        "index.html"
    )


# ─────────────────────────────────────────────
# START
# ─────────────────────────────────────────────

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.getenv(
                "PORT",
                "8000"
            )
        )
    )
