#!/usr/bin/env python3
"""
Pantau Satu Koin (long dan short)
---------------------------------
Memantau SATU koin saja di timeframe 15m dengan konfirmasi tren 1 jam, dua arah:
LONG dan SHORT. Mengirim ke Telegram:
  - sinyal lengkap: entry, SL, TP1, TP2, ukuran posisi
  - kabar saat TP1 / TP2 / SL kena
  - status tiap 15 menit (bias, calon masuk, peringatan kalau koin sudah kurang bagus)

Ganti koin kapan saja langsung dari Telegram:
  /koin SOL     pantau SOL
  /cari         cari kandidat long dan short terbaik
  /status       minta status sekarang
  /bantuan      daftar perintah
(Perintah dibaca bot tiap kali jalan, jadi balasan bisa tertunda beberapa menit.)

File ini butuh long_bot.py di folder yang sama. Hanya library bawaan Python 3.8+.

Perintah terminal:
  python3 coin_bot.py scan    # jalan sekali lalu selesai (untuk GitHub Actions)
  python3 coin_bot.py once    # cetak status, tidak mengirim apa pun
"""
import os, sys, json, time, html, logging
from concurrent.futures import ThreadPoolExecutor

import long_bot as lb

log = logging.getLogger("koin")
STATE_FILE = os.getenv("COIN_STATE_FILE", "coin_state.json")
DEFAULT_COIN = os.getenv("COIN", "").upper().replace("USDT", "").strip()
COOLDOWN_H = float(os.getenv("SIGNAL_COOLDOWN_HOURS", "2"))
POOR_LIMIT = int(os.getenv("POOR_LIMIT", "8"))      # berapa kali status 15m berturut-turut "kurang bagus" sebelum disarankan ganti
fmt = lb.fmt


def pct(a, b):
    return (a / b - 1) * 100


# ---------------- State ----------------
def load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_FILE, "w") as fh:
        json.dump(st, fh)


# ---------------- Analisis arah ----------------
def mirror(cs):
    L = cs[-1][4]
    return [(x[0], 2 * L - x[1], 2 * L - x[3], 2 * L - x[2], 2 * L - x[4], x[5]) for x in cs]


def market_view(raw):
    """Bias 1 jam dan kualitas koin untuk dipantau."""
    cs = raw[:-1]
    up = bool((lb.htf(cs) or {}).get("up"))
    m = mirror(cs)
    down = bool(min(x[3] for x in m) > 0 and (lb.htf(m) or {}).get("up"))
    c = [x[4] for x in cs]
    atr_pct = sum((x[2] - x[3]) / x[4] for x in cs[-14:]) / 14 * 100
    chg24 = (c[-1] / c[-97] - 1) * 100 if len(c) > 97 else 0.0
    bias = "LONG" if up else "SHORT" if down else "NETRAL"
    poor = []
    if bias == "NETRAL":
        poor.append("sideways di 1 jam")
    if atr_pct < 0.25:
        poor.append("terlalu sepi (gerakan 15m kecil)")
    return dict(bias=bias, atr_pct=atr_pct, chg24=chg24, price=c[-1], poor=poor)


# ---------------- Pesan ----------------
def help_text(state):
    coin = state.get("coin") or DEFAULT_COIN
    return "\n".join([
        "<b>Pantau Satu Koin</b>",
        "Koin sekarang: <b>%s</b>" % (html.escape(coin) if coin else "belum dipilih"),
        "",
        "/koin SOL : pantau SOL (ganti SOL dengan koin lain)",
        "/cari : cari kandidat long dan short terbaik",
        "/status : minta status sekarang",
        "/bantuan : tampilkan pesan ini",
        "",
        "Perintah dibaca tiap beberapa menit, jadi balasan bisa tertunda sebentar.",
    ])


def levels_lines(r):
    e = r["entry"]
    return [
        "Masuk (entry): <b>%s</b>" % fmt(e),
        "Stop loss (SL): <b>%s</b>  (risiko %.1f%%)" % (fmt(r["sl"]), abs(pct(r["sl"], e))),
        "Ambil untung 1 (TP1): <b>%s</b>  (+%.1f%%)" % (fmt(r["tp1"]), abs(pct(r["tp1"], e))),
        "Ambil untung 2 (TP2): <b>%s</b>  (+%.1f%%)" % (fmt(r["tp2"]), abs(pct(r["tp2"], e))),
    ]


def signal_msg(c, r, btc, bonus):
    risk_usdt = lb.MODAL * lb.RISK_PCT / 100
    notional = risk_usdt / (r["sl_pct"] / 100)
    long_ = r["dir"] == "LONG"
    return "\n".join([
        ("🟢 <b>SINYAL LONG: %s</b>" if long_ else "🔴 <b>SINYAL SHORT: %s</b>") % html.escape(c["name"]),
        "%s (skor %d dari 100)" % (r["label"], round(min(100, r["score"] + bonus))),
        "",
    ] + levels_lines(r) + [
        "",
        "Ukuran posisi: %.0f USDT (%.1fx), risiko maksimal %.2f USDT" % (notional, notional / lb.MODAL, risk_usdt),
        "",
        "Alasan: " + html.escape(", ".join(r["tags"])),
        html.escape(btc["text"]),
        "",
        "Cara pakai: masuk di sekitar harga entry dan pasang SL. Saat TP1 kena, %s sebagian dan geser SL ke entry. "
        "Bot akan memberi tahu kalau TP atau SL kena." % ("jual" if long_ else "tutup"),
        c["link"],
    ])


def watch_msg_lines(r):
    return [
        "Calon %s: %s" % (r["dir"], html.escape(r["note"])),
        "Masuk hanya jika %s" % html.escape(r["cond"]),
        "SL %s (risiko %.1f%%) | TP1 %s | TP2 %s" % (
            fmt(r["plan"]["sl"]), r["plan"]["sl_pct"], fmt(r["plan"]["tp1"]), fmt(r["plan"]["tp2"])),
    ]


def status_msg(c, view, btc, rl, rs, state, held):
    lines = ["🕒 <b>Status %s</b> (15 menit)" % html.escape(c["name"]),
             "Harga: <b>%s</b> (%+.1f%% dalam 24 jam)" % (fmt(view["price"]), view["chg24"])]
    desc = {"LONG": "LONG (uptrend 1 jam)", "SHORT": "SHORT (downtrend 1 jam)", "NETRAL": "NETRAL (sideways)"}
    lines.append("Bias: <b>%s</b>" % desc[view["bias"]])
    lines.append(html.escape(btc["text"]))
    a = state.get("active")
    if a:
        lines += ["", "Trade aktif: <b>%s</b> masuk %s | SL %s | TP1 %s%s | TP2 %s" % (
            a["dir"], fmt(a["entry"]), fmt(a["sl"]), fmt(a["tp1"]),
            " (sudah kena, SL di entry)" if a.get("tp1_hit") else "", fmt(a["tp2"]))]
    else:
        cand = rl if view["bias"] == "LONG" else rs if view["bias"] == "SHORT" else None
        if cand and cand["kind"] == "W":
            lines += [""] + watch_msg_lines(cand)
        elif cand and cand["kind"] in "AB":
            lines += ["", "Setup %s sudah terbentuk tapi %s." % (cand["dir"], held or "belum dikirim (jeda antar sinyal)")]
        elif view["bias"] == "NETRAL":
            lines += ["", "Belum ada setup. Tunggu tren 1 jam terbentuk."]
        else:
            lines += ["", "Belum ada setup. Tunggu koreksi ke EMA21 atau breakout dengan volume."]
    if view["poor"]:
        lines += ["", "Catatan: koin ini %s (%d dari %d kali berturut-turut)." % (
            " dan ".join(view["poor"]), state.get("poor", 0), POOR_LIMIT)]
    return "\n".join(lines)


def event_msg(a, ev, c):
    n = html.escape(c["name"])
    if ev == "SL":
        return "🛑 <b>%s</b>: SL kena di %s. Trade %s selesai (rugi sekitar %.1f%%).\nKalau mau lanjut atau ganti koin: /status atau /cari" % (
            n, fmt(a["sl"]), a["dir"], abs(pct(a["sl"], a["entry"])))
    if ev == "TP1":
        return "✅ <b>%s</b>: TP1 kena di %s. %s sebagian dan geser SL ke entry (%s)." % (
            n, fmt(a["tp1"]), "Jual" if a["dir"] == "LONG" else "Tutup", fmt(a["entry"]))
    if ev == "BE":
        return "⚪ <b>%s</b>: harga kembali ke entry (%s), sisa posisi keluar tanpa rugi. Trade selesai." % (n, fmt(a["entry"]))
    return "🎯 <b>%s</b>: TP2 kena di %s. Trade %s selesai. Mau cari yang baru? /cari" % (n, fmt(a["tp2"]), a["dir"])


# ---------------- Trade aktif ----------------
def check_active(state, raw):
    a = state.get("active")
    if not a:
        return []
    sg = 1 if a["dir"] == "LONG" else -1
    entry, sl, tp1, tp2 = (sg * a[k] for k in ("entry", "sl", "tp1", "tp2"))
    events = []
    for t, o, h, l, c, v in raw:
        if t < a["t"]:
            continue
        hi, lo = (h, l) if sg == 1 else (-l, -h)
        if not a.get("tp1_hit"):
            if lo <= sl:                              # jika SL dan TP1 di candle yang sama, anggap SL duluan
                events.append("SL"); a["done"] = True; break
            if hi >= tp1:
                a["tp1_hit"], a["tp1_t"] = True, t
                events.append("TP1")
                if hi >= tp2:
                    events.append("TP2"); a["done"] = True; break
        else:
            if t > a.get("tp1_t", 0) and lo <= entry:
                events.append("BE"); a["done"] = True; break
            if hi >= tp2:
                events.append("TP2"); a["done"] = True; break
    return events


# ---------------- Cari kandidat ----------------
def find_candidates(coins, candler, exclude_id=None, top=3):
    pool = [x for x in coins if x["price"] > 0 and x["vol"] >= lb.MIN_VOL and x["id"] not in ("BTCUSDT", exclude_id)]
    pool.sort(key=lambda x: -x["vol"])
    pool = pool[:lb.TOP_N]

    def work(x):
        try:
            time.sleep(0.1)
            raw = lb.retry(lambda: candler(x["id"]))
            out = []
            for sh in (False, True):
                r = lb.analyze(raw, x["fund8"], sh)
                if r:
                    out.append(dict(x, **r))
            return out
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = [r for rs in ex.map(work, pool) for r in rs]
    rank = lambda r: (0 if r["kind"] in "AB" else 1, -r["score"])
    return {d: sorted([r for r in res if r["dir"] == d], key=rank)[:top] for d in ("LONG", "SHORT")}


def candidates_msg(cands, title="🔄 <b>Kandidat</b> (dicari dari koin berlikuiditas baik)"):
    lines = [title]
    for d in ("LONG", "SHORT"):
        lines += ["", "<b>%s</b>" % d]
        if not cands[d]:
            lines.append("Belum ada yang layak sekarang.")
        for i, r in enumerate(cands[d], 1):
            if r["kind"] in "AB":
                lines.append("%d. <b>%s</b>: SINYAL SIAP, %s" % (i, html.escape(r["name"]), html.escape(r["label"])))
                lines.append("   Entry %s | SL %s | TP1 %s | TP2 %s" % (fmt(r["entry"]), fmt(r["sl"]), fmt(r["tp1"]), fmt(r["tp2"])))
            else:
                p = r["plan"]
                lines.append("%d. <b>%s</b>: calon, masuk jika %s" % (i, html.escape(r["name"]), html.escape(r["cond"])))
                lines.append("   SL %s | TP1 %s | TP2 %s" % (fmt(p["sl"]), fmt(p["tp1"]), fmt(p["tp2"])))
            lines.append("   Pantau: /koin %s" % html.escape(r["name"]))
    return "\n".join(lines)


# ---------------- Perintah Telegram ----------------
def handle_commands(state, coins, candler):
    off = state.get("offset", 0)
    try:
        ups = lb.retry(lambda: lb.http("https://api.telegram.org/bot%s/getUpdates?offset=%d&timeout=0" % (lb.TOKEN, off))).get("result", [])
    except Exception as e:
        log.warning("Membaca perintah Telegram gagal (%s)", type(e).__name__)
        return
    byname = {x["name"].upper(): x for x in coins}
    for u in ups:
        off = max(off, u["update_id"] + 1)
        m = u.get("message") or {}
        if str(m.get("chat", {}).get("id")) != str(lb.CHAT_ID):
            continue
        parts = (m.get("text") or "").strip().split()
        if not parts:
            continue
        cmd = parts[0].lower().split("@")[0]
        arg = parts[1].upper().replace("USDT", "") if len(parts) > 1 else ""
        if cmd in ("/start", "/bantuan", "/help"):
            lb.send(help_text(state))
        elif cmd in ("/koin", "/ganti"):
            if not arg:
                lb.send("Tulis nama koinnya, contoh: /koin SOL")
            elif arg not in byname:
                lb.send("Koin %s tidak ditemukan di %s." % (html.escape(arg), lb.SOURCE))
            else:
                state.update(coin=arg, active=None, poor=0, warned=False, force_status=True)
                warn = " Volumenya kecil, hati-hati slippage." if byname[arg]["vol"] < lb.MIN_VOL else ""
                lb.send("Oke, sekarang memantau <b>%s</b>.%s Status lengkap menyusul." % (html.escape(arg), warn))
        elif cmd == "/status":
            state["force_status"] = True
        elif cmd == "/cari":
            state["force_find"] = True
    state["offset"] = off


# ---------------- Scan ----------------
def scan(dry=False):
    lister, candler = lb.ADAPTERS[lb.SOURCE]
    coins = lb.retry(lister)
    state = load_state()
    if not dry:
        handle_commands(state, coins, candler)
    name = state.get("coin") or DEFAULT_COIN
    if not name:
        if not dry and not state.get("greeted"):
            lb.send("Belum ada koin yang dipantau.\n\n" + help_text(state))
            state["greeted"] = True
        save_state(state)
        return
    c = next((x for x in coins if x["name"].upper() == name), None)
    if not c:
        log.warning("Koin %s tidak ada di %s", name, lb.SOURCE)
        if not dry:
            lb.send("Koin %s tidak ditemukan di %s. Pilih lain: /koin NAMA" % (html.escape(name), lb.SOURCE))
        return

    send = (lambda m: print(m + "\n----")) if dry else lb.send
    raw = lb.retry(lambda: candler(c["id"]))
    btc = lb.btc_context(lambda s: lb.retry(lambda: candler(s)))
    now = time.time()

    # 1. kabar trade aktif
    a = state.get("active")
    for ev in check_active(state, raw):
        send(event_msg(a, ev, c))
        if a.get("done"):
            state["active"] = None
            state.setdefault("last_sig", {})[a["dir"]] = now

    # 2. sinyal baru (satu trade pada satu waktu)
    rl = lb.analyze(raw, c["fund8"], False)
    rs = lb.analyze(raw, c["fund8"], True)
    held = ""
    if not state.get("active"):
        for r in (rl, rs):
            if not r or r["kind"] not in "AB":
                continue
            against = btc["risk_off"] if r["dir"] == "LONG" else btc.get("risk_off_short", False)
            bonus = 0 if against else 10
            if r["score"] + bonus < lb.MIN_SCORE:
                continue
            if now - state.get("last_sig", {}).get(r["dir"], 0) < COOLDOWN_H * 3600:
                continue
            if against and lb.BTC_FILTER:
                held = "ditahan karena BTC sedang melawan arah"
                continue
            send(signal_msg(c, r, btc, bonus))
            state.setdefault("last_sig", {})[r["dir"]] = now
            state["active"] = dict(dir=r["dir"], entry=r["entry"], sl=r["sl"], tp1=r["tp1"], tp2=r["tp2"],
                                   t=r["candle_t"] + 900000, tp1_hit=False)
            break

    # 3. status 15 menit, permintaan /status, dan peringatan koin kurang bagus
    view = market_view(raw)
    slot = int(now // 900)
    new_slot = state.get("summary_slot") != slot
    if new_slot:
        state["summary_slot"] = slot
        state["poor"] = state.get("poor", 0) + 1 if view["poor"] else 0
        if not view["poor"]:
            state["warned"] = False
    if (new_slot and lb.SUMMARY) or state.pop("force_status", False):
        send(status_msg(c, view, btc, rl, rs, state, held))
    if (state.get("poor", 0) >= POOR_LIMIT and not state.get("warned") and not state.get("active")) \
            or state.pop("force_find", False):
        forced = state.get("poor", 0) < POOR_LIMIT
        cands = find_candidates(coins, candler, exclude_id=c["id"])
        title = "🔄 <b>Kandidat pengganti</b>" if not forced else "🔎 <b>Kandidat terbaik sekarang</b>"
        if not forced:
            send("⚠️ <b>%s</b> sudah kurang bagus beberapa saat (%s). Pertimbangkan ganti koin." % (
                html.escape(c["name"]), " dan ".join(view["poor"])))
            state["warned"] = True
        send(candidates_msg(cands, title))
    if not dry:
        save_state(state)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if lb.SOURCE not in lb.ADAPTERS:
        sys.exit("SOURCE harus bitget atau binance")
    if cmd == "once":
        scan(dry=True)
        return
    if not (lb.TOKEN and lb.CHAT_ID):
        sys.exit("Isi TELEGRAM_TOKEN dan TELEGRAM_CHAT_ID dulu.")
    scan()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    main()
