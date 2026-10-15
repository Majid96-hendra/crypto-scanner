import ccxt
import pandas as pd
import requests
import os

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

def kirim_telegram(pesan):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": pesan, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload)
        print("Status kirim Telegram:", response.status_code)
    except Exception as e:
        print("Gagal kirim Telegram:", e)

def crypto_scanner():
    print("Memulai scanner Binance Futures (Logika Kelelahan Pembeli / Exhaustion Short)...")

    exchange = ccxt.binance({
        'options': {'defaultType': 'future'},
        'enableRateLimit': True
    })
    
    try:
        markets = exchange.load_markets()
        symbols = [s for s in markets if s.endswith('/USDT') and not ':USDT' in s and markets[s]['active']]
    except Exception as e:
        print("Gagal memuat market:", e)
        return

    sinyal_list = []

    for symbol in symbols:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=24)
            if len(ohlcv) < 24:
                continue
                
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            harga_sekarang = df['close'].iloc[-1]
            open_terakhir = df['open'].iloc[-1]
            high_terakhir = df['high'].iloc[-1]
            low_terakhir = df['low'].iloc[-1]
            
            # Perhitungan tren kenaikan sebelumnya
            kenaikan_sebelumnya = ((df['close'].iloc[-2] - df['close'].iloc[0]) / df['close'].iloc[0]) * 100
            volume_rata2 = df['volume'].iloc[:-1].mean()
            volume_terakhir = df['volume'].iloc[-1]
            
            # --- KRITERIA SHORT EXHAUSTION (KELELAHAN PEMBELI) ---
            # 1. Harga sebelumnya mengalami lonjakan naik yang signifikan (> 3%)
            # 2. Volume transaksi melonjak tinggi (> 2x rata-rata) di area atas
            # 3. Candle terakhir mulai melambat/menutup merah (harga close < open) atau membentuk sumbu atas
            if volume_terakhir > (volume_rata2 * 2.0):
                if kenaikan_sebelumnya > 3.0 and harga_sekarang < open_terakhir:
                    entry = harga_sekarang
                    # SL dipasang di atas sumbu tertinggi candle tersebut sebagai pengaman ketat
                    sl = high_terakhir * 1.008
                    risiko = sl - entry
                    if risiko <= 0: continue
                    
                    # Target TP bertahap mengejar kembali area rata-rata bawah
                    tp1 = entry - (risiko * 1.5)
                    tp2 = entry - (risiko * 2.5)
                    tp3 = entry - (risiko * 4.0)
                    tp_max = entry - (risiko * 6.0)
                    
                    skor_potensi = kenaikan_sebelumnya * (volume_terakhir / volume_rata2)
                    sinyal_list.append({
                        'tipe': '🔴 *SETUP SHORT EXHAUSTION (MOMENTUM MATI)*',
                        'symbol': symbol, 'entry': entry, 'sl': sl, 
                        'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'tp_max': tp_max, 'skor': skor_potensi
                    })
        except Exception:
            continue

    # Ambil 5 potensi terbaik
    sinyal_list = sorted(sinyal_list, key=lambda x: x['skor'], reverse=True)[:5]

    if len(sinyal_list) > 0:
        laporan = "🚨 *BINANCE FUTURES SHORT SCANNER* 🚨\n*Deteksi Dini Kelelahan Pembeli (Exhaustion)*\n\n"
        for s in sinyal_list:
            laporan += f"{s['tipe']}\n"
            laporan += f"🪙 Pair: `{s['symbol']}`\n"
            laporan += f"📍 **Entry:** `{s['entry']:.4f}`\n"
            laporan += f"🛑 **SL:** `{s['sl']:.4f}`\n"
            laporan += f"🎯 **TP 1:** `{s['tp1']:.4f}`\n"
            laporan += f"🎯 **TP 2:** `{s['tp2']:.4f}`\n"
            laporan += f"🎯 **TP 3:** `{s['tp3']:.4f}`\n"
            laporan += f"🚀 **TP Max:** `{s['tp_max']:.4f}`\n"
            laporan += "-----------------------------------\n"
        
        kirim_telegram(laporan)
    else:
        print("Scan selesai: Belum ada sinyal short exhaustion yang terdeteksi.")

if __name__ == "__main__":
    crypto_scanner()
