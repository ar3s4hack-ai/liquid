"""Mapa de liquidez: el libro de órdenes de Binance Futuros (BTCUSDT) grabado en el tiempo.

Es lo que CoinGlass llama «Liquidity Heatmap»: dónde hay órdenes límite esperando y cuánto duran.
No son liquidaciones (eso es el heatmap estimado); son órdenes reales que se ven en el libro.

- Libro local: instantánea REST (1000 niveles por lado) + cambios por WebSocket (btcusdt@depth@500ms, ruta /public),
  con las reglas de Binance (U, u, pu) para no perder ni repetir cambios. Si hay un hueco se vuelve a sincronizar.
  Los niveles lejanos que nunca cambian solo aparecen cuando alguien los toca, así que el libro se completa con las horas.
- Cada 30 s se suma el dinero en espera por tramos de 10 $ (±6 % del precio) y se acumula en bloques de 5 min y de 1 h;
  al cerrar cada bloque se guarda la media en SQLite (5 min: 7 días; 1 h: 60 días).
- Para el gráfico: media por vela y tramo, en 7 niveles (escala log, cada nivel el doble que el anterior),
  codificada como una cadena de dígitos por vela (pesa poco y la web la pinta como una imagen).
"""
import bisect
import json
import math
import os
import threading
import time
import zlib
from array import array
from collections import OrderedDict, defaultdict

import requests

import liqdata as LQ

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover
    websocket = None

SYMBOL = LQ.SYMBOL
FAPI = os.getenv("BINANCE_FAPI", "https://fapi.binance.com")
WS_PUBLIC = os.getenv("BINANCE_WS_PUBLIC", "wss://fstream.binance.com/public")
BIN_USD = float(os.getenv("BOOK_BIN_USD", "10"))
RANGE_PCT = float(os.getenv("BOOK_RANGE_PCT", "6"))
KEEP_PCT = 15.0          # niveles más lejanos se olvidan
SNAP_S = 30
LEVELS = 7
TABLES = {"book5": (300, 7), "book60": (3600, 60)}   # tabla: (segundos por bloque, días guardados)

STATE = {"connected": False, "since": None, "last_msg": 0, "resyncs": 0, "snapshots": 0, "rows": 0, "error": None}


# ───────────── Libro local ─────────────
class LocalBook:
    """Libro de órdenes con las reglas de Binance Futuros para un libro local:
    1) guardar los eventos mientras llega la instantánea; 2) descartar los que tengan u < lastUpdateId;
    3) el primero aplicado debe cumplir U <= lastUpdateId <= u; 4) después cada pu debe ser el u anterior."""

    def __init__(self):
        self.lock = threading.Lock()
        self.bids, self.asks = {}, {}
        self.snap_id = None
        self.synced = False
        self.last_u = None
        self.buf = []

    def reset(self):
        with self.lock:
            self._reset()

    def _reset(self):
        self.bids, self.asks = {}, {}
        self.snap_id, self.synced, self.last_u, self.buf = None, False, None, []

    def _apply(self, ev):
        for side, book in (("b", self.bids), ("a", self.asks)):
            for p, q in ev.get(side) or ():
                p, q = float(p), float(q)
                if q > 0:
                    book[p] = q
                else:
                    book.pop(p, None)
        self.last_u = ev["u"]

    def _feed(self, ev):
        if self.snap_id is None:
            self.buf.append(ev)
            if len(self.buf) > 4000:
                self.buf = self.buf[-2000:]
            return "wait"
        if not self.synced:
            if ev["u"] < self.snap_id:
                return "drop"
            if ev["U"] <= self.snap_id <= ev["u"]:
                self._apply(ev)
                self.synced = True
                return "ok"
            return "gap"          # la instantánea es más vieja que lo que llega
        if ev.get("pu") != self.last_u:
            return "gap"
        self._apply(ev)
        return "ok"

    def on_event(self, ev):
        """Cambio del WebSocket. Devuelve "gap" si hay que pedir otra instantánea (el libro se vacía)."""
        with self.lock:
            r = self._feed(ev)
            if r == "gap":
                self._reset()
                self.buf = [ev]
            return r

    def load_snapshot(self, snap):
        """Instantánea REST + lo guardado. "ok" (sincronizado), "wait" (falta el primer cambio) o "gap" (pedir otra)."""
        with self.lock:
            buf = self.buf
            self.bids = {float(p): float(q) for p, q in snap.get("bids") or () if float(q) > 0}
            self.asks = {float(p): float(q) for p, q in snap.get("asks") or () if float(q) > 0}
            self.snap_id, self.synced, self.last_u, self.buf = int(snap["lastUpdateId"]), False, None, []
            for ev in buf:
                r = self._feed(ev)
                if r == "gap":
                    self._reset()
                    self.buf = buf[-200:]
                    return "gap"
            return "ok" if self.synced else "wait"

    def prune(self, mid):
        lo, hi = mid * (1 - KEEP_PCT / 100), mid * (1 + KEEP_PCT / 100)
        with self.lock:
            for book in (self.bids, self.asks):
                for p in [p for p in book if p < lo or p > hi]:
                    del book[p]

    def binned(self, range_pct=RANGE_PCT):
        """(precio medio, {tramo de BIN_USD: USD en espera}) con compras y ventas juntas, o None si no está listo."""
        with self.lock:
            if not self.synced or not self.bids or not self.asks:
                return None
            bb, ba = max(self.bids), min(self.asks)
            mid = (bb + ba) / 2
            lo, hi = mid * (1 - range_pct / 100), mid * (1 + range_pct / 100)
            acc = defaultdict(float)
            for p, q in self.bids.items():
                if p >= lo:
                    acc[int(p // BIN_USD)] += p * q
            for p, q in self.asks.items():
                if p <= hi:
                    acc[int(p // BIN_USD)] += p * q
        return mid, acc

    def walls(self, price, n=8):
        """Muros del libro (tramos de 0,05 % con ≥ 2,5 veces la mediana), como los de la capa «Libro»."""
        with self.lock:
            if not self.synced:
                return None
            rows = {"bids": list(self.bids.items()), "asks": list(self.asks.items())}
        step = price * 0.0005
        out = {}
        for name, lv in rows.items():
            acc = defaultdict(float)
            for p, q in lv:
                if abs(p - price) <= price * 0.03:
                    acc[math.floor(p / step)] += p * q
            vals = sorted(v for v in acc.values() if v > 0)
            med = vals[len(vals) // 2] if vals else 0
            w = [[round((k + 0.5) * step, 2), round(v)] for k, v in acc.items() if med and v >= 2.5 * med]
            w.sort(key=lambda x: -x[1])
            out[name] = w[:n]
            out[name + "_total"] = round(sum(vals))
        return out

    def info(self):
        with self.lock:
            if not self.bids or not self.asks:
                return {"synced": self.synced, "levels": 0}
            return {"synced": self.synced, "levels": len(self.bids) + len(self.asks),
                    "bid_low": min(self.bids), "ask_high": max(self.asks), "best_bid": max(self.bids), "best_ask": min(self.asks)}


BOOK = LocalBook()


# ───────────── Medias por bloque (5 min y 1 h) ─────────────
class Accum:
    def __init__(self, period):
        self.period = period
        self.t = None
        self.sum = defaultdict(float)
        self.n = 0

    def add(self, ts, acc):
        """Suma una instantánea. Si empieza un bloque nuevo devuelve el anterior cerrado: (ts, {tramo: media}, n)."""
        t = int(ts) - int(ts) % self.period
        closed = None
        if self.t is not None and t != self.t and self.n:
            closed = (self.t, {k: v / self.n for k, v in self.sum.items()}, self.n)
            self.sum, self.n = defaultdict(float), 0
        self.t = t
        for k, v in acc.items():
            self.sum[k] += v
        self.n += 1
        return closed

    def current(self):
        return (self.t, {k: v / self.n for k, v in self.sum.items()}, self.n) if self.n else None


ACC = {name: Accum(per) for name, (per, _) in TABLES.items()}
_acc_lock = threading.Lock()
_tables_for = {"c": None}


def _conn():
    """Conexión de liqdata (misma base de datos); crea las tablas la primera vez. Llamar con LQ._db_lock cogido."""
    c = LQ._conn()
    if _tables_for["c"] is not c:
        for name in TABLES:
            c.execute(f"CREATE TABLE IF NOT EXISTS {name}(ts INTEGER PRIMARY KEY, b0 INTEGER, n INTEGER, data BLOB)")
        c.commit()
        _tables_for["c"] = c
    return c


def save_row(table, ts, vals, n):
    if not vals:
        return
    b0, b1 = min(vals), max(vals)
    dense = [round(vals.get(k, 0.0)) for k in range(b0, b1 + 1)]
    blob = zlib.compress(json.dumps(dense, separators=(",", ":")).encode())
    with LQ._db_lock:
        c = _conn()
        c.execute(f"INSERT OR REPLACE INTO {table} VALUES (?, ?, ?, ?)", (int(ts), int(b0), int(n), blob))
        c.commit()
    STATE["rows"] += 1


def cleanup(now=None):
    now = now or time.time()
    with LQ._db_lock:
        c = _conn()
        for name, (_, days) in TABLES.items():
            c.execute(f"DELETE FROM {name} WHERE ts < ?", (int(now - days * 86400),))
        c.commit()


def snapshot_tick(now=None, book=None):
    """Una instantánea: se suma a los bloques abiertos y se guardan los que se cierran."""
    now = now or time.time()
    book = book or BOOK
    r = book.binned()
    if not r:
        return False
    mid, acc = r
    book.prune(mid)
    closed = []
    with _acc_lock:
        for name, a in ACC.items():
            c = a.add(now, acc)
            if c:
                closed.append((name, c))
    for name, c in closed:
        save_row(name, *c)
    STATE["snapshots"] += 1
    return True


# ───────────── Para el gráfico ─────────────
_rebin_cache = OrderedDict()
_map_cache = {}


def _rebin(table, ts, b0, blob, m):
    """Fila guardada -> (primer tramo del gráfico, array de USD) con tramos de m × BIN_USD. Con caché."""
    key = (table, ts, m)
    hit = _rebin_cache.get(key)
    if hit is not None:
        _rebin_cache.move_to_end(key)
        return hit
    dense = json.loads(zlib.decompress(blob))
    out = _rebin_dict({b0 + i: v for i, v in enumerate(dense) if v}, m)
    _rebin_cache[key] = out
    if len(_rebin_cache) > 5000:
        _rebin_cache.popitem(last=False)
    return out


def _rebin_dict(vals, m):
    if not vals:
        return (0, array("d"))
    k0, k1 = min(vals) // m, max(vals) // m
    arr = array("d", [0.0]) * (k1 - k0 + 1)
    for b, v in vals.items():
        arr[b // m - k0] += v
    return (k0, arr)


def _rows(table, t0, t1):
    with LQ._db_lock:
        return _conn().execute(f"SELECT ts, b0, n, data FROM {table} WHERE ts >= ? AND ts < ? ORDER BY ts",
                               (int(t0), int(t1))).fetchall()


def book_map(candles, step, size, price, range_pct=8.0, now=None):
    """Mapa de liquidez por vela para el gráfico, o None si aún no hay nada grabado.

    S: alto de cada tramo (múltiplo de 10 $ más cercano al del heatmap); k0, nk: primer tramo y cuántos;
    rows: por vela [desplazamiento, "dígitos"] (0 = nada, 1..7 = nivel) o None; levels: USD donde empieza cada nivel."""
    if not candles:
        return None
    m = max(1, int(round(size / BIN_USD)))
    S = m * BIN_USD
    table = "book5" if step < 3600 else "book60"
    key = (candles[0]["time"], candles[-1]["time"], int(price // S))
    hit = _map_cache.get((table, step, m))
    now = now or time.time()
    if hit and hit[1] == key and now - hit[0] < 20:
        return hit[2]
    t0, t1 = candles[0]["time"], candles[-1]["time"] + step
    per = defaultdict(list)                       # vela -> [(k0, array)]
    for ts, b0, n, blob in _rows(table, t0, t1):
        per[ts - ts % step].append(_rebin(table, ts, b0, blob, m))
    with _acc_lock:
        cur = ACC[table].current()                # bloque abierto: la vela de ahora también se ve
    if cur and t0 <= cur[0] < t1:
        per[cur[0] - cur[0] % step].append(_rebin_dict({k: v for k, v in cur[1].items()}, m))
    if not per:
        return None
    lo_k = int(price * (1 - range_pct / 100) // S)
    hi_k = int(price * (1 + range_pct / 100) // S)
    avg = {}
    for tc, parts in per.items():
        acc = defaultdict(float)
        for k0, arr in parts:
            for i, v in enumerate(arr):
                if v:
                    acc[k0 + i] += v
        avg[tc] = {k: v / len(parts) for k, v in acc.items() if lo_k <= k <= hi_k}
    vals = sorted(v for d in avg.values() for v in d.values() if v > 0)
    if not vals:
        return None
    vmax = vals[min(len(vals) - 1, int(0.995 * (len(vals) - 1)))]
    thr = [vmax / 2 ** (LEVELS - 1 - i) for i in range(LEVELS)]    # nivel 1 desde vmax/64, nivel 7 desde vmax
    level = lambda v: bisect.bisect_right(thr, v)   # noqa: E731  (cuántos umbrales alcanza)

    ks = [k for d in avg.values() for k in d]
    k0, k1 = min(ks), max(ks)
    rows = []
    for c in candles:
        d = avg.get(c["time"])
        if not d:
            rows.append(None)
            continue
        lv = [level(d.get(k, 0.0)) for k in range(k0, k1 + 1)]
        a = next((i for i, x in enumerate(lv) if x), None)
        if a is None:
            rows.append(None)
            continue
        b = len(lv) - next(i for i, x in enumerate(reversed(lv)) if x)
        rows.append([a, "".join(str(x) for x in lv[a:b])])
    first = min(per)
    out = {"S": S, "k0": k0, "nk": k1 - k0 + 1, "levels": [round(t) for t in thr], "rows": rows, "since": first}
    _map_cache[(table, step, m)] = (now, key, out)
    return out


def cell_value(bm, i, price):
    """Para pruebas: nivel en la vela i y el precio dado."""
    row = bm["rows"][i]
    if not row:
        return 0
    k = int(price // bm["S"]) - bm["k0"] - row[0]
    return int(row[1][k]) if 0 <= k < len(row[1]) else 0


# ───────────── Grabadora (hilos) ─────────────
_want_snap = threading.Event()
_started = False


def _snapshot_worker():
    while True:
        _want_snap.wait()
        _want_snap.clear()
        time.sleep(1.5)              # que se acumulen algunos cambios antes de la instantánea
        try:
            snap = requests.get(f"{FAPI}/fapi/v1/depth", params={"symbol": SYMBOL, "limit": 1000}, timeout=10).json()
            r = BOOK.load_snapshot(snap)
            STATE["error"] = None
            if r == "gap":
                STATE["resyncs"] += 1
                time.sleep(3)
                _want_snap.set()
        except Exception as e:
            STATE["error"] = f"instantánea: {e}"[:200]
            time.sleep(10)
            _want_snap.set()


def _on_open(ws):
    BOOK.reset()
    STATE.update(connected=True, since=int(time.time()), error=None)
    _want_snap.set()


def _on_message(ws, raw):
    STATE["last_msg"] = int(time.time())
    try:
        msg = json.loads(raw)
    except ValueError:
        return
    ev = msg.get("data", msg) if isinstance(msg, dict) else {}
    if ev.get("e") != "depthUpdate":
        return
    if BOOK.on_event(ev) == "gap":
        STATE["resyncs"] += 1
        _want_snap.set()


def _on_close(ws, *a):
    STATE["connected"] = False
    BOOK.reset()


def _ws_loop():
    backoff = 5
    url = f"{WS_PUBLIC}/stream?streams={SYMBOL.lower()}@depth@500ms"
    while True:
        try:
            app = websocket.WebSocketApp(url, on_open=_on_open, on_message=_on_message,
                                         on_error=lambda ws, e: STATE.update(error=str(e)[:200]), on_close=_on_close)
            app.run_forever(ping_interval=60, ping_timeout=20)
            backoff = 5 if STATE["last_msg"] and time.time() - STATE["last_msg"] < 120 else min(60, backoff * 2)
        except Exception as e:  # pragma: no cover
            STATE["error"] = str(e)[:200]
        STATE["connected"] = False
        time.sleep(backoff)


def _tick_loop():
    n = 0
    while True:
        time.sleep(SNAP_S - time.time() % SNAP_S + 0.05)   # a :00 y :30 de cada minuto
        try:
            snapshot_tick()
            n += 1
            if n % 120 == 0:
                cleanup()
        except Exception as e:  # pragma: no cover
            STATE["error"] = f"grabación: {e}"[:200]


def start():
    global _started
    if _started or os.getenv("COLLECT", "1") == "0" or os.getenv("BOOK", "1") == "0" or websocket is None:
        return
    _started = True
    for target, name in ((_snapshot_worker, "book-snap"), (_ws_loop, "book-ws"), (_tick_loop, "book-tick")):
        threading.Thread(target=target, daemon=True, name=name).start()


def status():
    now = int(time.time())
    with LQ._db_lock:
        c = _conn()
        counts = {name: c.execute(f"SELECT COUNT(*), MIN(ts) FROM {name}").fetchone() for name in TABLES}
    info = BOOK.info()
    return dict(info, connected=STATE["connected"], last_msg_ago=(now - STATE["last_msg"]) if STATE["last_msg"] else None,
                resyncs=STATE["resyncs"], snapshots=STATE["snapshots"], error=STATE["error"],
                rows={k: v[0] for k, v in counts.items()}, since={k: v[1] for k, v in counts.items()})
