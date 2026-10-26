"""
Scanner BTC futures (Bitget) - Liquidity Sweep + Order Block + Fibonacci
Bias 1H, entry zone 15m. Kirim sinyal LONG/SHORT ke Telegram.

Env:
  TG_TOKEN, TG_CHAT_ID   (wajib untuk kirim Telegram)
  SYMBOL (default BTCUSDT), MIN_RR (default 2.0)
"""
import os
import json
import requests

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
MIN_RR = float(os.getenv("MIN_RR", "1.5"))
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
STATE_FILE = "last_signal.json"
URL = "https://api.bitget.com/api/v2/mix/market/candles"

PIVOT_N = 3        # kiri-kanan candle untuk swing high/low
LOOKBACK = 48      # sweep dicari di 48 candle 15m terakhir (~12 jam)
ATR_BUFFER = 0.15  # buffer SL = 0.15 ATR
FIB_TOP, FIB_BOT = 0.5, 0.886  # zona fib dilonggarkan


def fetch(gran, limit=300):
    r = requests.get(URL, params={
        "symbol": SYMBOL, "productType": "USDT-FUTURES",
        "granularity": gran, "limit": limit}, timeout=15)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != "00000":
        raise RuntimeError(d)
    rows = sorted(d["data"], key=lambda x: int(x[0]))[:-1]  # buang candle belum close
    return [dict(t=int(x[0]), o=float(x[1]), h=float(x[2]),
                 l=float(x[3]), c=float(x[4])) for x in rows]


def flip(cs):
    """Cermin harga supaya logika LONG bisa dipakai untuk SHORT."""
    return [dict(t=c["t"], o=-c["o"], h=-c["l"], l=-c["h"], c=-c["c"]) for c in cs]


def pivots(cs, kind, n=PIVOT_N):
    out = []
    key = "h" if kind == "h" else "l"
    fn = max if kind == "h" else min
    for i in range(n, len(cs) - n):
        w = [cs[j][key] for j in range(i - n, i + n + 1)]
        if cs[i][key] == fn(w):
            out.append((i, cs[i][key]))
    return out


def atr(cs, n=14):
    trs = []
    for i in range(1, len(cs)):
        p, c = cs[i - 1], cs[i]
        trs.append(max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])))
    return sum(trs[-n:]) / n


def ema(vals, n):
    k = 2 / (n + 1)
    e = vals[0]
    out = [e]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def bias_up(cs1h):
    """Bias naik: close 1H di atas EMA50 dan EMA50 sedang naik."""
    e = ema([c["c"] for c in cs1h], 50)
    return cs1h[-1]["c"] > e[-1] and e[-1] > e[-6]


def find_long(c15, c1h):
    if not bias_up(c1h):
        return None
    highs, lows = pivots(c15, "h"), pivots(c15, "l")
    last = len(c15) - 1
    a = atr(c15)
    price = c15[last]["c"]

    for s in range(last, max(last - LOOKBACK, 1), -1):
        # 1) liquidity sweep: wick tembus swing low, close kembali di atasnya
        pl = [p for p in lows if p[0] < s and s - p[0] <= 60]
        if not pl:
            continue
        ref = pl[-1][1]
        if not (c15[s]["l"] < ref and c15[s]["c"] > ref):
            continue

        # 2) BOS: close di atas swing high sebelum sweep
        ph = [p for p in highs if p[0] < s]
        if not ph:
            continue
        bos = ph[-1][1]
        k = next((j for j in range(s + 1, last + 1) if c15[j]["c"] > bos), None)
        if k is None:
            continue

        # leg: low sweep -> high tertinggi setelahnya
        leg_low = c15[s]["l"]
        if min(c["l"] for c in c15[s:]) < leg_low:
            continue  # low baru terbentuk, setup batal
        leg_high = max(c["h"] for c in c15[s:])
        R = leg_high - leg_low
        if R < 0.5 * a:
            continue

        # 3) order block: candle bearish terakhir sebelum impuls
        bears = [j for j in range(s, k) if c15[j]["c"] < c15[j]["o"]]
        if not bears:
            continue
        ob = c15[bears[-1]]

        # 4) zona fib 0.618-0.786 harus overlap dengan OB
        z_hi = leg_high - FIB_TOP * R
        z_lo = leg_high - FIB_BOT * R
        ov_lo, ov_hi = max(ob["l"], z_lo), min(ob["h"], z_hi)
        if ov_lo < ov_hi:
            entry = (ov_lo + ov_hi) / 2      # OB overlap fib
        else:
            entry = (z_hi + z_lo) / 2        # tanpa overlap: tengah zona fib
        sl = min(leg_low, ob["l"]) - ATR_BUFFER * a
        tp1 = leg_high - 0.5 * R
        tp2 = leg_high
        tp3 = leg_low + 1.272 * R
        risk = entry - sl
        if risk <= 0:
            continue
        rr = (tp2 - entry) / risk
        if rr < MIN_RR:
            continue
        if price <= sl or price > tp1:
            continue  # sudah invalid / harga sudah lari

        return dict(key=str(c15[s]["t"]), entry=entry, sl=sl, tp1=tp1,
                    tp2=tp2, tp3=tp3, rr=rr, price=price)
    return None


def scan():
    c15, c1h = fetch("15m"), fetch("1H")
    out = []
    lg = find_long(c15, c1h)
    if lg:
        lg["side"] = "LONG"
        lg["key"] = "L" + lg["key"]
        out.append(lg)
    sh = find_long(flip(c15), flip(c1h))
    if sh:
        for f in ("entry", "sl", "tp1", "tp2", "tp3", "price"):
            sh[f] = -sh[f]
        sh["side"] = "SHORT"
        sh["key"] = "S" + sh["key"]
        out.append(sh)
    return out


def fmt(s):
    e = "🟢" if s["side"] == "LONG" else "🔴"
    return (
        f"{e} {s['side']} {SYMBOL} (limit order)\n"
        f"Harga sekarang: {s['price']:.1f}\n"
        f"Entry : {s['entry']:.1f}\n"
        f"SL    : {s['sl']:.1f}\n"
        f"TP1   : {s['tp1']:.1f} (ambil 50%)\n"
        f"TP2   : {s['tp2']:.1f}\n"
        f"TP3   : {s['tp3']:.1f}\n"
        f"RR (ke TP2): 1:{s['rr']:.1f}\n"
        f"Setup: sweep liquidity + order block + fib 0.5-0.886 (bias 1H searah)\n"
        f"Bukan saran finansial. Pakai risiko kecil per trade."
    )


def send(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print(text)
        return
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                  data={"chat_id": TG_CHAT_ID, "text": text}, timeout=15)


def status():
    c15, c1h = fetch("15m"), fetch("1H")
    if bias_up(c1h):
        bias = "NAIK (cari LONG)"
    elif bias_up(flip(c1h)):
        bias = "TURUN (cari SHORT)"
    else:
        bias = "NETRAL (menunggu)"
    return (f"✅ Bot aktif\n{SYMBOL}: {c15[-1]['c']:.1f}\nBias 1H: {bias}\n"
            f"Scan jalan tiap 15 menit. Sinyal dikirim hanya kalau setup lengkap muncul.")


def main():
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        send(status())  # pesan tes saat dijalankan manual

    try:
        with open(STATE_FILE) as f:
            sent = set(json.load(f).get("keys", []))
    except Exception:
        sent = set()

    for s in scan():
        if s["key"] in sent:
            continue
        send(fmt(s))
        sent.add(s["key"])

    with open(STATE_FILE, "w") as f:
        json.dump({"keys": sorted(sent)[-20:]}, f)


if __name__ == "__main__":
    main()
