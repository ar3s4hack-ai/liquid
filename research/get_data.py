"""Descarga los datos del estudio desde data.binance.vision (archivos públicos de Binance) a research/data/.

  futuros BTCUSDT: velas de 5m, 15m y 1D desde 2020 y el funding cobrado cada 8 h
  spot BTCUSDT: velas de 1h y 1D desde agosto de 2017
  futuros ETHUSDT: velas de 5m desde 2020 (solo para la divergencia SMT, smt.py)

Uso: python3 research/get_data.py   (unos 60 MB; hace falta poder entrar en data.binance.vision)"""
import datetime as dt
import gzip
import io
import os
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

BASE = "https://data.binance.vision/data/"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TODAY = dt.date.today()


def months(y, m):
    last = TODAY.replace(day=1) - dt.timedelta(days=1)
    while (y, m) <= (last.year, last.month):
        yield f"{y:04d}-{m:02d}"
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def days():
    d = TODAY.replace(day=1)
    while d < TODAY:
        yield d.isoformat()
        d += dt.timedelta(days=1)


def get(url):
    for i in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
        except Exception:
            pass
        time.sleep(2 + 3 * i)
    raise SystemExit("fallo " + url)


def rows(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    for name in z.namelist():
        for line in io.TextIOWrapper(z.open(name), encoding="utf-8"):
            line = line.strip()
            if line and line[0].isdigit():          # algunos meses traen cabecera
                yield line.split(",")


def fetch_all(urls):
    with ThreadPoolExecutor(8) as ex:
        return list(ex.map(get, urls))


def klines(market, itv, y0, m0, name, sym="BTCUSDT"):
    p = f"{market}/monthly/klines/{sym}/{itv}/{sym}-{itv}-"
    d = f"{market}/daily/klines/{sym}/{itv}/{sym}-{itv}-"
    urls = [BASE + p + ym + ".zip" for ym in months(y0, m0)] + [BASE + d + x + ".zip" for x in days()]
    seen = {}
    for u, b in zip(urls, fetch_all(urls)):
        if b is None:
            print("sin datos:", u)
            continue
        for r in rows(b):
            t = int(r[0])
            while t > 10**13:                       # spot desde 2025 viene en microsegundos
                t //= 1000
            seen[t // 1000] = [str(t // 1000), r[1], r[2], r[3], r[4], r[5], r[7], r[8], r[9]]
    with gzip.open(os.path.join(OUT, f"{name}.csv.gz"), "wt") as f:
        f.write("t,o,h,l,c,v,q,n,tbv\n")
        for t in sorted(seen):
            f.write(",".join(seen[t]) + "\n")
    print(name, len(seen), "velas")


def funding():
    p = "futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-"
    urls = [BASE + p + ym + ".zip" for ym in months(2019, 9)]
    seen = {}
    for u, b in zip(urls, fetch_all(urls)):
        if b is None:
            print("sin datos:", u)
            continue
        for r in rows(b):
            seen[int(r[0]) // 1000] = [str(int(r[0]) // 1000), r[1], r[2]]
    with gzip.open(os.path.join(OUT, "btcusdt_funding.csv.gz"), "wt") as f:
        f.write("ts,interval,rate\n")
        for t in sorted(seen):
            f.write(",".join(seen[t]) + "\n")
    print("funding", len(seen))


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    klines("futures/um", "15m", 2020, 1, "fut_15m")
    klines("futures/um", "5m", 2020, 1, "fut_5m")
    klines("futures/um", "1d", 2019, 9, "fut_1d")
    klines("spot", "1h", 2017, 8, "spot_1h")
    klines("spot", "1d", 2017, 8, "spot_1d")
    klines("futures/um", "5m", 2020, 1, "eth_fut_5m", sym="ETHUSDT")
    funding()
