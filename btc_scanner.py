"""
Scanner futures (Bitget) - Liquidity Map + Sweep + Order Block + Fibonacci
Mode scalping: bias 15m, entry zone 5m. Pair: BTC, ETH, XRP, SOL.
Peta likuiditas = perkiraan dari high/low yang belum tersapu (bukan data liquidation).
Kirim sinyal LONG/SHORT ke Telegram.

Env:
  TG_TOKEN, TG_CHAT_ID   (wajib untuk kirim Telegram)
  SYMBOLS (default BTCUSDT,ETHUSDT,XRPUSDT,SOLUSDT), MIN_RR (default 1.5)
"""
import os
import json
import requests

SYMBOLS = [x.strip() for x in os.getenv(
    "SYMBOLS", "BTCUSDT,ETHUSDT,XRPUSDT,SOLUSDT").split(",") if x.strip()]
ENTRY_TF = "5m"    # timeframe entry (OB, sweep, fib)
BIAS_TF = "15m"    # timeframe arah
MIN_RISK_PCT = 0.0015  # jarak SL minimal 0.15% agar tidak habis kena fee
MIN_RR = float(os.getenv("MIN_RR", "1.0"))
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
STATE_FILE = "last_signal.json"
URL = "https://api.bitget.com/api/v2/mix/market/candles"

PIVOT_N = 2        # kiri-kanan candle untuk swing high/low
LOOKBACK = 60      # setup dicari di 60 candle 5m terakhir (~5 jam)
ATR_BUFFER = 0.15  # buffer SL = 0.15 ATR
FIB_TOP, FIB_BOT = 0.382, 0.886  # zona fib lebar untuk scalping


def fetch(symbol, gran, limit=300):
    r = requests.get(URL, params={
        "symbol": symbol, "productType": "USDT-FUTURES",
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
    """Bias naik: close 15m di atas EMA50 (turun = di bawahnya)."""
    e = ema([c["c"] for c in cs1h], 50)
    return cs1h[-1]["c"] > e[-1]


def px(p):
    """Format harga sesuai ukuran koin (XRP butuh lebih banyak desimal)."""
    if p >= 1000:
        return f"{p:,.1f}"
    if p >= 10:
        return f"{p:.2f}"
    if p >= 1:
        return f"{p:.4f}"
    return f"{p:.5f}"


def liquidity_map(c_mid, c_hour):
    """Peta likuiditas (perkiraan): high/low yang belum tersapu = tempat stop-loss
    menumpuk. Makin kuat kalau ada equal high/low (beberapa sentuhan) atau
    merupakan high/low 24 jam / 48 jam."""
    a = atr(c_mid)
    ref = c_mid[-1]["c"]
    lv = []
    for i, pr in pivots(c_mid, "h", 3):
        if not any(c["h"] > pr for c in c_mid[i + 1:]):
            lv.append((pr, 1))
    for i, pr in pivots(c_mid, "l", 3):
        if not any(c["l"] < pr for c in c_mid[i + 1:]):
            lv.append((pr, 1))
    for span, w in ((24, 2), (48, 1)):
        ch = c_hour[-span:]
        lv.append((max(c["h"] for c in ch), w))
        lv.append((min(c["l"] for c in ch), w))
    lv.sort()
    tol = max(0.3 * a, ref * 0.0008)
    groups = []
    for pr, w in lv:
        if groups and pr - groups[-1]["lo"] <= tol:
            g = groups[-1]
            g["sum"] += pr * w
            g["w"] += w
        else:
            groups.append(dict(lo=pr, sum=pr * w, w=w))
    return [dict(price=g["sum"] / g["w"], strength=g["w"]) for g in groups]


def split_pools(pools, price, n=3):
    up = sorted([p for p in pools if p["price"] > price], key=lambda p: p["price"])[:n]
    dn = sorted([p for p in pools if p["price"] <= price], key=lambda p: -p["price"])[:n]
    return up, dn


def heat_text(pools, price, n=3):
    up, dn = split_pools(pools, price, n)
    bar = lambda st: "▇" * min(st, 4)
    pct = lambda x: (x / price - 1) * 100
    lines = ["Peta likuiditas (makin banyak ▇ makin kuat):"]
    for q in reversed(up):
        lines.append(f"  ↑ {px(q['price'])} {bar(q['strength'])} ({pct(q['price']):+.2f}%)")
    lines.append(f"  • harga {px(price)}")
    for q in dn:
        lines.append(f"  ↓ {px(q['price'])} {bar(q['strength'])} ({pct(q['price']):+.2f}%)")
    return "\n".join(lines)


def apply_pools(sig, pools, a):
    """Arahkan TP ke pool likuiditas terdekat searah posisi (min 1R, maks 6R)."""
    side = 1 if sig["side"] == "LONG" else -1
    entry, sl = sig["entry"], sig["sl"]
    risk = abs(entry - sl)
    cands = sorted([q["price"] for q in pools
                    if 1.0 * risk <= side * (q["price"] - entry) <= 6 * risk],
                   key=lambda x: side * x)
    if not cands:
        return sig
    off = 0.1 * a * side  # TP sedikit sebelum pool supaya lebih mudah kena
    tp2 = cands[0] - off
    tp3 = cands[1] - off if len(cands) > 1 else tp2 + side * 0.5 * abs(tp2 - entry)
    rr = abs(tp2 - entry) / risk
    if rr < MIN_RR:
        return sig
    sig.update(tp1=entry + (tp2 - entry) / 2, tp2=tp2, tp3=tp3, rr=rr, liq_target=True)
    return sig


def find_sweep(c5, pools, a):
    """Harga menyapu pool likuiditas (wick tembus, close balik) -> entry arah berlawanan."""
    price = c5[-1]["c"]
    for c in reversed(c5[-3:]):
        rng = c["h"] - c["l"]
        if rng <= 0:
            continue
        for q in pools:
            P = q["price"]
            if q["strength"] < 2:
                continue
            if c["h"] > P > c["c"] and (c["h"] - max(c["o"], c["c"])) >= 0.4 * rng:
                side, entry, sl, d = "SHORT", c["c"], c["h"] + ATR_BUFFER * a, -1
            elif c["l"] < P < c["c"] and (min(c["o"], c["c"]) - c["l"]) >= 0.4 * rng:
                side, entry, sl, d = "LONG", c["c"], c["l"] - ATR_BUFFER * a, 1
            else:
                continue
            risk = abs(entry - sl)
            if risk / abs(entry) < MIN_RISK_PCT:
                continue
            if abs(price - entry) > 1.0 * a:
                continue  # harga sudah lari
            if (side == "SHORT" and price >= sl) or (side == "LONG" and price <= sl):
                continue
            return dict(side=side, key=f"W{c['t']}", entry=entry, sl=sl,
                        tp1=entry + d * risk, tp2=entry + d * 1.8 * risk,
                        tp3=entry + d * 2.6 * risk, rr=1.8, price=price,
                        sweep=True, kind="sweep", swept=P)
    return None


def find_breakout(cs, a, why):
    """Candle terakhir kuat menembus high 10 candle sebelumnya -> entry market."""
    last = len(cs) - 1
    c = cs[last]
    look = cs[last - 10:last]
    ref = max(range(len(look)), key=lambda i: look[i]["h"])
    level = look[ref]["h"]
    body = c["c"] - c["o"]
    if not (body >= 0.8 * a and c["c"] > level):
        why.append("belum ada breakout")
        return None
    entry = c["c"]
    sl = max(min(x["l"] for x in cs[last - 4:last + 1]) - ATR_BUFFER * a,
             entry - 1.5 * a)
    risk = entry - sl
    if risk <= 0 or risk / abs(entry) < MIN_RISK_PCT:
        why.append("SL terlalu dekat")
        return None
    return dict(key="B" + str(look[ref]["t"]), entry=entry, sl=sl,
                tp1=entry + risk, tp2=entry + 1.8 * risk, tp3=entry + 2.6 * risk,
                rr=1.8, price=entry, sweep=False, kind="breakout")


def find_long(c15, c1h, why=None):
    why = why if why is not None else []
    """Setup long: bias naik + impuls menembus struktur (BOS) + pullback ke OB/fib.
    Sweep liquidity bersifat bonus (ditandai di pesan), bukan syarat wajib."""
    if not bias_up(c1h):
        why.append("bias tidak searah")
        return None
    highs, lows = pivots(c15, "h"), pivots(c15, "l")
    last = len(c15) - 1
    a = atr(c15)
    price = c15[last]["c"]

    # kandidat low awal impuls: swing low terbaru dulu
    for s_idx, s_low in reversed([p for p in lows if last - p[0] <= LOOKBACK]):
        # impuls: harus ada high yang lebih tinggi setelah low ini
        k = max(range(s_idx, last + 1), key=lambda j: c15[j]["h"])
        if k <= s_idx:
            why.append("belum ada impuls")
            continue

        leg_low = s_low
        if min(c["l"] for c in c15[s_idx:]) < leg_low:
            why.append("low baru terbentuk")
            continue  # low baru terbentuk, setup batal
        leg_high = max(c["h"] for c in c15[s_idx:])
        R = leg_high - leg_low
        if R < 0.8 * a:
            why.append("impuls terlalu kecil")
            continue

        # order block: candle bearish terakhir sebelum impuls (fallback: candle low itu)
        bears = [j for j in range(s_idx, k) if c15[j]["c"] < c15[j]["o"]]
        ob = c15[bears[-1]] if bears else c15[s_idx]

        # entry: overlap OB dengan zona fib; kalau tidak ada, pakai fib 0.618
        z_hi = leg_high - FIB_TOP * R
        z_lo = leg_high - FIB_BOT * R
        ov_lo, ov_hi = max(ob["l"], z_lo), min(ob["h"], z_hi)
        if ov_lo < ov_hi:
            entry = (ov_lo + ov_hi) / 2
        else:
            entry = leg_high - 0.618 * R

        sl = min(leg_low, ob["l"]) - ATR_BUFFER * a
        tp2 = leg_high
        tp3 = leg_low + 1.272 * R
        tp1 = entry + (tp2 - entry) / 2
        risk = entry - sl
        if risk <= 0 or risk / abs(entry) < MIN_RISK_PCT:
            why.append("SL terlalu dekat")
            continue
        rr = (tp2 - entry) / risk
        if rr < MIN_RR:
            why.append("RR kurang")
            continue
        # sinyal hanya jika harga masih dekat entry dan belum invalid
        if price <= sl or price > entry + 2.0 * a:
            why.append("harga sudah jauh dari entry")
            continue

        prev_lows = [p for p in lows if p[0] < s_idx]
        sweep = bool(prev_lows) and leg_low < prev_lows[-1][1]
        return dict(key=str(c15[s_idx]["t"]), entry=entry, sl=sl, tp1=tp1,
                    tp2=tp2, tp3=tp3, rr=rr, price=price, sweep=sweep,
                    kind="pullback")

    # tidak ada pullback: cek breakout momentum (menangkap gerakan cepat)
    bo = find_breakout(c15, a, why)
    if bo:
        return bo
    if not why:
        why.append("belum ada swing low/impuls")
    return None


def scan(symbol):
    c5, c15 = fetch(symbol, ENTRY_TF), fetch(symbol, BIAS_TF)
    c1h = fetch(symbol, "1H")
    # peta dibuat tanpa 45 menit terakhir supaya sapuan terbaru masih terdeteksi
    pools = liquidity_map(c15[:-3], c1h)
    a5 = atr(c5)
    heat = heat_text(pools, c5[-1]["c"])
    out = []
    sw = find_sweep(c5, pools, a5)
    if sw:
        sw["symbol"] = symbol
        sw["key"] = f"{symbol}-{sw['key']}"
        out.append(sw)
    lg = None if (sw and sw["side"] == "LONG") else find_long(c5, c15)
    if lg:
        lg["side"] = "LONG"
        lg["symbol"] = symbol
        lg["key"] = f"{symbol}-L" + lg["key"]
        out.append(lg)
    sh = None if (sw and sw["side"] == "SHORT") else find_long(flip(c5), flip(c15))
    if sh:
        for f in ("entry", "sl", "tp1", "tp2", "tp3", "price"):
            sh[f] = -sh[f]
        sh["side"] = "SHORT"
        sh["symbol"] = symbol
        sh["key"] = f"{symbol}-S" + sh["key"]
        out.append(sh)
    for sg in out:
        apply_pools(sg, pools, a5)
        sg["heat"] = heat
    return out


def fmt(s):
    e = "🟢" if s["side"] == "LONG" else "🔴"
    kind = s.get("kind")
    if kind == "sweep":
        head, setup = "MARKET - sweep likuiditas", f"sweep likuiditas di {px(s['swept'])} lalu balik arah"
    elif kind == "breakout":
        head, setup = "MARKET - breakout momentum", "breakout momentum (bias 15m searah)"
    else:
        head = "limit order"
        setup = "pullback order block + fib" + (" + sweep liquidity" if s.get("sweep") else "") + " (bias 15m searah)"
    tp_note = " (pool likuiditas)" if s.get("liq_target") else ""
    return (
        f"{e} {s['side']} {s['symbol']} ({head}, scalping)\n"
        f"Harga sekarang: {px(s['price'])}\n"
        f"Entry : {px(s['entry'])}\n"
        f"SL    : {px(s['sl'])}\n"
        f"TP1   : {px(s['tp1'])} (ambil 50%)\n"
        f"TP2   : {px(s['tp2'])}{tp_note}\n"
        f"TP3   : {px(s['tp3'])}\n"
        f"RR (ke TP2): 1:{s['rr']:.1f}\n"
        f"Setup: {setup}\n\n"
        f"{s.get('heat', '')}\n\n"
        f"Bukan saran finansial. Pakai risiko kecil per trade."
    )


def send(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("ERROR: secret TG_TOKEN / TG_CHAT_ID kosong atau salah nama")
        print(text)
        raise SystemExit(1)
    r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      data={"chat_id": TG_CHAT_ID, "text": text}, timeout=15)
    if not r.ok:
        print("ERROR Telegram:", r.status_code, r.text)
        raise SystemExit(1)
    print("Telegram terkirim")


def status():
    lines = ["✅ Bot aktif"]
    for sym in SYMBOLS:
        try:
            c5, c15 = fetch(sym, ENTRY_TF), fetch(sym, BIAS_TF)
            pools = liquidity_map(c15[:-3], fetch(sym, "1H"))
            up, dn = split_pools(pools, c5[-1]["c"], 1)
            why = []
            if bias_up(c15):
                b = "NAIK"
                res = find_long(c5, c15, why)
            elif bias_up(flip(c15)):
                b = "TURUN"
                res = find_long(flip(c5), flip(c15), why)
            else:
                b = "NETRAL"
                res = None
                why.append("arah 15m belum jelas")
            if res:
                note = "ada setup!"
            else:
                top = max(set(why), key=why.count) if why else "-"
                note = f"belum ada setup ({top})"
            now = c5[-1]["c"]
            liq = ""
            if up:
                liq += f" ↑{px(up[0]['price'])} ({(up[0]['price'] / now - 1) * 100:+.2f}%)"
            if dn:
                liq += f" ↓{px(dn[0]['price'])} ({(dn[0]['price'] / now - 1) * 100:+.2f}%)"
            lines.append(f"{sym}: {px(now)} | bias 15m {b} | {note}\n   likuiditas terdekat:{liq}")
        except Exception as ex:
            lines.append(f"{sym}: gagal ambil data ({ex})")
    lines.append("Scan tiap 5 menit. Sinyal dikirim hanya kalau setup lengkap muncul.")
    return "\n".join(lines)


def main():
    if os.getenv("GITHUB_EVENT_NAME") == "workflow_dispatch":
        send(status())  # pesan tes saat dijalankan manual

    try:
        with open(STATE_FILE) as f:
            sent = set(json.load(f).get("keys", []))
    except Exception:
        sent = set()

    for sym in SYMBOLS:
        try:
            sigs = scan(sym)
        except Exception as ex:
            print(f"{sym}: gagal scan: {ex}")
            continue
        for s in sigs:
            if s["key"] in sent:
                continue
            send(fmt(s))
            sent.add(s["key"])

    with open(STATE_FILE, "w") as f:
        json.dump({"keys": sorted(sent)[-60:]}, f)


if __name__ == "__main__":
    main()
