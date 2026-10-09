"""Hyperliquid: posiciones REALES de BTC y su precio de liquidación exacto (datos públicos del exchange).

Hyperliquid es on-chain: cada posición abierta publica su precio de liquidación. Aquí:
- Se descubren direcciones: las que operan BTC (el canal público de operaciones trae comprador y vendedor)
  y las cuentas más grandes de la clasificación pública.
- Se consulta clearinghouseState de cada una sin pasar de ~25 % del límite de la API (peso 2 de 1200 por minuto):
  las que tienen BTC abierto cada 3 a 30 min según su tamaño y el resto cada pocas horas.
- Cada 5 min se graba el mapa por tramos (largos debajo del precio, cortos encima) para verlo en el tiempo.
Solo es Hyperliquid y solo las posiciones encontradas: no es todo el mercado, pero lo que hay es real, no estimado.
"""
import bisect
import json
import math
import os
import re
import threading
import time
from collections import defaultdict

import requests

import liqdata as LQ

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover
    websocket = None

INFO = os.getenv("HL_INFO", "https://api.hyperliquid.xyz/info")
WS_URL = os.getenv("HL_WS", "wss://api.hyperliquid.xyz/ws")
LEADERBOARD = os.getenv("HL_LEADERBOARD", "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard")
COIN = os.getenv("HL_COIN", "BTC")
ENABLED = os.getenv("HL_REAL", "1") != "0"
RATE = float(os.getenv("HL_RATE", "2.5"))        # consultas por segundo (2,5/s = 300 de peso por minuto, 25 % del límite)
TOP_LEADERS = int(os.getenv("HL_TOP_LEADERS", "1500"))
MIN_TRADER_USD = 20000.0       # operado en BTC para empezar a seguir una dirección
MIN_POS_USD = 10000.0          # posiciones más pequeñas no se siguen
HOLDER_EVERY = ((1e6, 180), (1e5, 600), (0, 1800))   # con BTC abierto: ≥ 1M cada 3 min, ≥ 100K cada 10, resto cada 30
LEADER_EVERY = 4 * 3600        # cuentas grandes sin BTC: cada 4 h
SNAP_EVERY = 300               # mapa grabado cada 5 min
KEEP_DAYS = 7
MAX_ADDRS = 6000
TIERS = (3, 5, 10, 25, 50, 100)

_http = requests.Session()
_lock = threading.Lock()
addrs = {}       # dirección -> {"due", "src": "trades"|"leader", "ntl": operado en BTC (USD)}
pos = {}         # dirección -> {"szi", "entry", "liq", "lev", "ntl", "upd"}
STAT = {"ws": False, "ws_msg": 0, "since": None, "queries": 0, "errors": 0, "last_error": None,
        "leaders": 0, "leaders_at": None, "snap_at": None, "hl_oi": None, "wait_until": 0}
_started = False
_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


# ───────────── Base de datos (la misma de liqdata) ─────────────
def _db():
    c = LQ._conn()
    c.execute("CREATE TABLE IF NOT EXISTS hl_snap(ts INTEGER PRIMARY KEY, price REAL, data TEXT)")
    return c


def tier_of(lev):
    """Apalancamiento real -> grupo del perfil (3x … 100x)."""
    lev = float(lev or 0)
    for t, top in ((3, 4), (5, 7.5), (10, 17.5), (25, 37.5), (50, 75)):
        if lev < top:
            return t
    return 100


# ───────────── Descubrimiento de direcciones ─────────────
def on_trades(msg, now=None):
    """Mensaje del canal público de operaciones de BTC: [comprador, vendedor] de cada operación."""
    if not isinstance(msg, dict) or msg.get("channel") != "trades":
        return 0
    now = now or time.time()
    added = 0
    with _lock:
        STAT["ws_msg"] = int(now)
        for t in msg.get("data") or []:
            if t.get("coin") != COIN:
                continue
            try:
                usd = float(t.get("px") or 0) * float(t.get("sz") or 0)
            except (TypeError, ValueError):
                continue
            for a in t.get("users") or []:
                if not isinstance(a, str) or not _ADDR.match(a):
                    continue
                a = a.lower()
                d = addrs.get(a)
                if d is None:
                    d = addrs[a] = {"due": None, "src": "trades", "ntl": 0.0}
                d["ntl"] += usd
                if a in pos:
                    d["due"] = min(d["due"] or now, now + 20)      # cambió su posición: se mira enseguida
                elif d["due"] is None and d["ntl"] >= MIN_TRADER_USD:
                    d["due"] = now                                  # nueva: a la cola
                    added += 1
        if len(addrs) > MAX_ADDRS:
            _evict()
    return added


def _evict():
    """Se olvidan las direcciones pequeñas que no tienen BTC abierto."""
    loose = sorted((d["ntl"], a) for a, d in addrs.items() if a not in pos and d["src"] == "trades")
    for _, a in loose[: len(addrs) - int(MAX_ADDRS * 0.9)]:
        del addrs[a]


_LB_ROW = re.compile(r'"ethAddress"\s*:\s*"(0x[0-9a-fA-F]{40})"\s*,\s*"accountValue"\s*:\s*"?([0-9.eE+-]+)"?')


def parse_leaderboard(text):
    """[(dirección, valor de la cuenta)] de la clasificación pública. Primero con una expresión (poca memoria);
    si el formato cambia, se lee el JSON entero."""
    rows = [(a.lower(), float(v)) for a, v in _LB_ROW.findall(text)]
    if rows:
        return rows
    try:
        j = json.loads(text)
    except ValueError:
        return []
    items = j.get("leaderboardRows") if isinstance(j, dict) else j
    out = []
    for r in items if isinstance(items, list) else []:
        if not isinstance(r, dict):
            continue
        a = r.get("ethAddress") or r.get("eth_address") or r.get("user")
        try:
            v = float(r.get("accountValue") or r.get("account_value") or 0)
        except (TypeError, ValueError):
            continue
        if isinstance(a, str) and _ADDR.match(a):
            out.append((a.lower(), v))
    return out


def add_leaders(rows, now=None):
    now = now or time.time()
    top = sorted(rows, key=lambda r: -r[1])[:TOP_LEADERS]
    with _lock:
        for k, (a, _) in enumerate(top):
            d = addrs.get(a)
            if d is None:
                addrs[a] = {"due": now + k * 0.6, "src": "leader", "ntl": 0.0}   # repartidas para no ir a golpes
            else:
                d["src"] = "leader"
                if d["due"] is None:
                    d["due"] = now + k * 0.6
        STAT.update(leaders=len(top), leaders_at=int(now))
    return len(top)


def fetch_leaders():
    r = _http.get(LEADERBOARD, timeout=90)
    r.raise_for_status()
    return add_leaders(parse_leaderboard(r.text))


# ───────────── Consulta de posiciones ─────────────
def parse_state(j):
    """clearinghouseState -> posición de BTC o None. Los números llegan como texto."""
    if not isinstance(j, dict):
        raise ValueError("respuesta inesperada")
    for ap in j.get("assetPositions") or []:
        p = (ap or {}).get("position") or {}
        if p.get("coin") != COIN:
            continue
        szi = float(p.get("szi") or 0)
        if szi == 0:
            return None
        liq = p.get("liquidationPx")
        lev = p.get("leverage") or {}
        entry = float(p.get("entryPx") or 0)
        value = float(p.get("positionValue") or 0) or abs(szi) * entry
        return {"szi": szi, "entry": entry, "liq": float(liq) if liq not in (None, "", "0", 0) else None,
                "lev": float(lev.get("value") or 0) if isinstance(lev, dict) else float(lev or 0),
                "cross": (lev.get("type") == "cross") if isinstance(lev, dict) else None, "ntl": value}
    return None


def holder_every(ntl):
    return next(every for top, every in HOLDER_EVERY if ntl >= top)


def query(addr, now=None):
    r = _http.post(INFO, json={"type": "clearinghouseState", "user": addr}, timeout=10)
    if r.status_code == 429:
        raise RuntimeError("429")
    r.raise_for_status()
    p = parse_state(r.json())
    if p and p["ntl"] < MIN_POS_USD:
        p = None
    now = now or time.time()
    with _lock:
        STAT["queries"] += 1
        d = addrs.get(addr)
        if p:
            p["upd"] = int(now)
            pos[addr] = p
            if d:
                d["due"] = now + holder_every(p["ntl"])
        else:
            pos.pop(addr, None)
            if d:
                if d["src"] == "leader":
                    d["due"] = now + LEADER_EVERY
                else:
                    del addrs[addr]            # vuelve si opera otra vez
    return p


def next_due(now=None):
    now = now or time.time()
    with _lock:
        best = None
        for a, d in addrs.items():
            if d["due"] is not None and d["due"] <= now and (best is None or d["due"] < best[0]):
                best = (d["due"], a)
    return best[1] if best else None


def poll_once(now=None):
    """Una consulta (la más atrasada). Devuelve la dirección consultada o None."""
    now = now or time.time()
    if now < STAT["wait_until"]:
        return None
    a = next_due(now)
    if not a:
        return None
    try:
        query(a, now)
    except Exception as e:
        with _lock:
            STAT["errors"] += 1
            STAT["last_error"] = str(e)[:160]
            if a in addrs:
                addrs[a]["due"] = now + 60
            if "429" in str(e):
                STAT["wait_until"] = now + 30           # el exchange pide calma
    return a


# ───────────── Agregados ─────────────
def positions():
    with _lock:
        return dict(pos)


def levels(price, bin_usd, range_pct=None, ps=None, tiers=None):
    """Tramos de liquidación reales: {tramo: [USD largos, USD cortos, [USD por grupo de apalancamiento]]}.
    Un largo se liquida por debajo del precio y un corto por encima; lo que ya quedó al otro lado no cuenta.
    tiers: solo las posiciones de esos grupos de apalancamiento (3x … 100x).
    bin_usd=None: sin tramos, por precio exacto al céntimo (para grabar)."""
    ps = positions() if ps is None else ps
    out = {}
    lo, hi = (price * (1 - range_pct / 100), price * (1 + range_pct / 100)) if range_pct else (0, math.inf)
    for p in ps.values():
        liq = p.get("liq")
        if not liq or not (lo <= liq <= hi) or (tiers and tier_of(p["lev"]) not in tiers):
            continue
        side = 1 if p["szi"] > 0 else -1
        if (side == 1 and liq >= price) or (side == -1 and liq <= price):
            continue
        usd = abs(p["szi"]) * liq
        b = round(liq, 2) if bin_usd is None else math.floor(liq / bin_usd)
        cell = out.setdefault(b, [0.0, 0.0, [0.0] * len(TIERS)])
        cell[0 if side == 1 else 1] += usd
        cell[2][TIERS.index(tier_of(p["lev"]))] += usd
    return out


def summary(price=None, range_pct=8.0, top=12):
    ps = positions()
    long_usd = sum(p["ntl"] for p in ps.values() if p["szi"] > 0)
    short_usd = sum(p["ntl"] for p in ps.values() if p["szi"] < 0)
    tot = long_usd + short_usd
    near = []
    if price:
        lo, hi = price * (1 - range_pct / 100), price * (1 + range_pct / 100)
        for p in ps.values():
            liq = p.get("liq")
            side = 1 if p["szi"] > 0 else -1
            if liq and lo <= liq <= hi and ((side == 1 and liq < price) or (side == -1 and liq > price)):
                near.append([round(liq, 1), side, round(abs(p["szi"]) * liq), round(p["lev"], 1), round(p["entry"], 1)])
        near.sort(key=lambda x: -x[2])
    with _lock:
        st = dict(STAT)
        tracked = len(addrs)
    cov = (tot / (2 * st["hl_oi"])) if st["hl_oi"] else None      # el OI cuenta un lado; aquí van largos y cortos
    return {
        "positions": len(ps), "tracked": tracked, "long_usd": round(long_usd), "short_usd": round(short_usd),
        "long_pct": round(long_usd / tot, 3) if tot else None,
        "coverage": round(min(1.0, cov), 3) if cov else None, "hl_oi": st["hl_oi"],
        "top": near[:top], "since": st["since"], "snap_at": st["snap_at"],
        "ws": st["ws"] and time.time() - st["ws_msg"] < 120,
    }


def status():
    s = summary()
    with _lock:
        st = dict(STAT)
    s.update(queries=st["queries"], errors=st["errors"], last_error=st["last_error"],
             leaders=st["leaders"], leaders_at=st["leaders_at"], enabled=ENABLED)
    s.pop("top", None)
    return s


# ───────────── Grabación cada 5 min y mapa en el tiempo ─────────────
def snapshot(price, now=None):
    """Graba las posiciones de ahora para pintarlas en el tiempo con el tramo que se use después: por columnas
    (precio exacto de liquidación al céntimo, USD con signo: + largos, − cortos, y grupo de apalancamiento).
    Así pesa la mitad que por filas y se lee 3-4 veces más rápido."""
    if not price:
        return 0
    now = int(now or time.time())
    P, U, T = [], [], []
    for p in positions().values():
        liq = p.get("liq")
        if not liq:
            continue
        side = 1 if p["szi"] > 0 else -1
        if (side == 1 and liq >= price) or (side == -1 and liq <= price):
            continue                            # ya quedó al otro lado del precio
        usd = abs(p["szi"]) * liq
        if usd >= 1000:
            P.append(round(liq, 2))
            U.append(round(usd) * side)
            T.append(TIERS.index(tier_of(p["lev"])))
    data = json.dumps({"v": 3, "p": P, "u": U, "t": T}, separators=(",", ":"))
    with LQ._db_lock:
        c = _db()
        c.execute("INSERT OR REPLACE INTO hl_snap VALUES (?, ?, ?)", (now, float(price), data))
        c.execute("DELETE FROM hl_snap WHERE ts < ?", (now - KEEP_DAYS * 86400,))
        c.commit()
    with _lock:
        STAT["snap_at"] = now
    return len(P)


def snapshots(t0, t1, step=None):
    """Grabaciones entre t0 y t1. Con step, solo la última de cada vela (las demás no se usan y leerlas cuesta)."""
    with LQ._db_lock:
        rows = _db().execute("SELECT ts, price, data FROM hl_snap WHERE ts >= ? AND ts <= ? ORDER BY ts", (int(t0), int(t1))).fetchall()
    if step:
        last = {}
        for r in rows:
            last[r[0] - r[0] % step] = r
        rows = [last[k] for k in sorted(last)]
    out = []
    for ts, price, data in rows:
        try:
            out.append((ts, price, json.loads(data)))
        except ValueError:
            continue
    return out


def _q(v):
    """Redondeo a 2 cifras: pequeños cambios (funding, PnL) no parten los segmentos del gráfico."""
    if v <= 0:
        return 0
    e = 10 ** (math.floor(math.log10(v)) - 1)
    return round(v / e) * e


def build_heat(candles, step, size, price, range_pct, now=None, tiers=None, out=None):
    """Mapa REAL de Hyperliquid con el mismo formato que el estimado: segmentos por tramo en el tiempo
    (de las grabaciones), niveles activos (las posiciones de ahora) y toques (cuando el precio llegó a un nivel).
    Con out: out["fuel"] = por vela (USD de largos, USD de cortos) por liquidar al cierre, o None antes de grabar."""
    now = now or time.time()
    t0, t_last = candles[0]["time"], candles[-1]["time"]
    snaps = snapshots(t0 - step, now, step)
    lo, hi = price * (1 - range_pct / 100), price * (1 + range_pct / 100)
    tidx = {c["time"]: i for i, c in enumerate(candles)}
    sel = {TIERS.index(t) for t in tiers if t in TIERS} if tiers and set(tiers) != set(TIERS) else None
    per_candle = {}            # índice de vela -> {tramo: (L, S)} de la última grabación dentro de la vela
    for ts, _, d in snaps:
        tc = max(t0, min(t_last, ts - ts % step))
        i = tidx.get(tc)
        if i is None:
            continue
        cell = defaultdict(lambda: [0.0, 0.0])
        if d.get("v") == 3:
            for p, u, ti in zip(d["p"], d["u"], d["t"]):
                if lo <= p <= hi and (sel is None or ti in sel):
                    cl = cell[math.floor(p / size)]
                    if u > 0:
                        cl[0] += u
                    else:
                        cl[1] -= u
        else:                                   # grabaciones por filas (antes del 9-10-2026): se borran solas a los 7 días
            for p, L, S, tv in d.get("rows") or ():
                if not (lo <= p <= hi):
                    continue
                if sel is not None:             # solo los apalancamientos elegidos (un tramo es de largos o de cortos)
                    part = sum(tv[j] for j in sel if j < len(tv))
                    L, S = (part, 0) if L >= S else (0, part)
                k = math.floor(p / size)
                cell[k][0] += L
                cell[k][1] += S
        per_candle[i] = cell
    # lo de ahora (posiciones al día) en la última vela
    cur = levels(price, size, range_pct, tiers=tiers)
    per_candle[len(candles) - 1] = {k: [v[0], v[1]] for k, v in cur.items()}
    segs, touches, opened = [], defaultdict(float), {}
    idxs = sorted(per_candle)
    first = idxs[0] if idxs else len(candles) - 1
    state = {}
    lk, sk = [], []             # tramos con largos / con cortos vivos, ordenados: solo se miran los que la vela puede tocar
    tot_l = tot_s = 0
    fuel = [None] * len(candles)
    qmemo = {}                  # las grabaciones seguidas repiten casi siempre los mismos importes

    def q(v):
        r = qmemo.get(v)
        if r is None:
            r = qmemo[v] = _q(v)
        return r

    for i in range(first, len(candles)):
        c = candles[i]
        check = []
        # el precio de la vela toca niveles que estaban vivos: liquidación (real) ejecutada.
        # largos: se tocan si el mínimo llega a su precio (todos los de encima); cortos: si el máximo llega (todos los de debajo)
        if lk:
            low = c["low"]
            j = bisect.bisect_left(lk, math.floor(low / size) - 1)
            while j < len(lk) and (lk[j] + 0.5) * size < low:
                j += 1
            for k in lk[j:]:
                L, S = state[k]
                touches[(k, i, 1)] += L
                state[k] = (0, S)
                tot_l -= L
                check.append(k)
            del lk[j:]
        if sk:
            high = c["high"]
            j = bisect.bisect_right(sk, math.floor(high / size) + 1)
            while j > 0 and (sk[j - 1] + 0.5) * size > high:
                j -= 1
            for k in sk[:j]:
                L, S = state[k]
                touches[(k, i, -1)] += S
                state[k] = (L, 0)
                tot_s -= S
                check.append(k)
            del sk[:j]
        if i in per_candle:
            new = {k: (q(v[0]), q(v[1])) for k, v in per_candle[i].items()}
            if new != state:
                check = set(check) | set(new) | set(state)
                state = new
                lk = sorted(k for k, v in state.items() if v[0])
                sk = sorted(k for k, v in state.items() if v[1])
                tot_l = sum(v[0] for v in state.values())
                tot_s = sum(v[1] for v in state.values())
        for k in check:
            v = state.get(k, (0, 0))
            o = opened.get(k)
            if o and (o[1], o[2]) == v:
                continue
            if o:
                segs.append([round((k + 0.5) * size, 2), candles[o[0]]["time"], c["time"], round(o[1]), round(o[2])])
                del opened[k]
            if v[0] + v[1] > 0:
                opened[k] = (i, v[0], v[1])
        fuel[i] = (tot_l, tot_s)
    for k, (i0, L, S) in opened.items():
        segs.append([round((k + 0.5) * size, 2), candles[i0]["time"], t_last, round(L), round(S)])
    if out is not None:
        out["fuel"] = fuel
    active = [[round((k + 0.5) * size, 2), round(v[0]), round(v[1]), [round(x) for x in v[2]]]
              for k, v in sorted(cur.items()) if v[0] + v[1] >= 1]
    vals = sorted(v for v in (sg[3] + sg[4] for sg in segs) if v > 0)
    heat = {
        "size": size,
        "max": vals[min(len(vals) - 1, int(0.95 * (len(vals) - 1)))] if vals else 0,
        "max_abs": vals[-1] if vals else 0,
        "segments": segs,
        "F": {"L": [1.0] * len(candles), "S": [1.0] * len(candles)},
        "active": active,
    }
    return heat, dict(touches), (snaps[0][0] if snaps else None)


# ───────────── Hilos ─────────────
def _ws_loop():
    backoff = 5
    while True:
        try:
            def on_open(ws):
                ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": COIN}}))
                with _lock:
                    STAT["ws"] = True

            def on_msg(ws, raw):
                try:
                    on_trades(json.loads(raw))
                except Exception:
                    pass

            def on_close(ws, *a):
                with _lock:
                    STAT["ws"] = False

            app = websocket.WebSocketApp(WS_URL, on_open=on_open, on_message=on_msg, on_close=on_close)
            stop = threading.Event()

            def pinger():
                while not stop.wait(45):
                    try:
                        app.send(json.dumps({"method": "ping"}))
                    except Exception:
                        return
            threading.Thread(target=pinger, daemon=True).start()
            app.run_forever()
            stop.set()
            backoff = 5
        except Exception as e:  # pragma: no cover
            with _lock:
                STAT["last_error"] = f"ws: {e}"[:160]
        with _lock:
            STAT["ws"] = False
        time.sleep(backoff)
        backoff = min(60, backoff * 2)


def _poll_loop():
    gap = 1.0 / max(0.1, RATE)
    while True:
        t = time.time()
        if poll_once(t) is None:
            time.sleep(1.0)
        else:
            time.sleep(max(0.0, gap - (time.time() - t)))


def _save_addrs():
    with _lock:
        keep = sorted(addrs.items(), key=lambda kv: (kv[0] not in pos, -kv[1]["ntl"]))[:3000]
        rows = [[a, d["src"], round(d["ntl"])] for a, d in keep]
    LQ.meta_set("hl_addrs", rows)


def _load_addrs(now=None):
    now = now or time.time()
    rows = LQ.meta_get("hl_addrs", []) or []
    with _lock:
        for k, r in enumerate(rows):
            try:
                a, src, ntl = r
            except (TypeError, ValueError):
                continue
            if isinstance(a, str) and _ADDR.match(a) and a not in addrs:
                addrs[a] = {"due": now + 5 + k * 0.5, "src": src if src in ("leader", "trades") else "trades", "ntl": float(ntl or 0)}
    return len(rows)


def _house_loop(price_fn, oi_fn):
    n = 0
    while True:
        try:
            if n % 144 == 0:          # clasificación cada 12 h
                try:
                    fetch_leaders()
                except Exception as e:
                    with _lock:
                        STAT["last_error"] = f"clasificación: {e}"[:160]
            px = price_fn()
            oi = oi_fn()
            if oi:
                with _lock:
                    STAT["hl_oi"] = oi
            if px:
                snapshot(px)
            if n % 2 == 0:
                _save_addrs()
        except Exception as e:  # pragma: no cover
            print("hl:", e, flush=True)
        n += 1
        time.sleep(SNAP_EVERY)


def start(price_fn, oi_fn):
    """price_fn: precio actual (o None). oi_fn: OI total de BTC en Hyperliquid en USD (o None)."""
    global _started
    if _started or not ENABLED or os.getenv("COLLECT", "1") == "0" or websocket is None:
        return
    _started = True
    STAT["since"] = int(time.time())
    _load_addrs()
    threading.Thread(target=_ws_loop, daemon=True, name="hl-ws").start()
    threading.Thread(target=_poll_loop, daemon=True, name="hl-poll").start()
    threading.Thread(target=_house_loop, args=(price_fn, oi_fn), daemon=True, name="hl-house").start()
