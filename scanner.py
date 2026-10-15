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
        requests.post(url, json=payload)
    except Exception as e:
        print("Gagal kirim Telegram:", e)

def crypto_scanner():
    print("Memindai pasar Binance Futures...")
    exchange = ccxt.binance({
        'options': {'defaultType': 'future'},
        'enableRateLimit': True
    })
    
    try:
        markets = exchange.load_markets()
        symbols = [s for s in markets if s.endswith('/USDT') and not ':USDT' in s]
    except Exception as e:
        print("Gagal memuat market:", e)
        return

    laporan = "🚨 *CRYPTO SCANNER ALERT (BINANCE)* 🚨\n\n"
    ditemukan = 0

    count = 0
    for symbol in symbols:
        if count >= 80:
            break
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=24)
            if len(ohlcv) < 24:
                continue
                
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            
            harga_awal = df['close'].iloc[0]
            harga_sekarang = df['close'].iloc[-1]
            perubahan_persen = ((harga_sekarang - harga_awal) / harga_awal) * 100
            
            volume_rata2 = df['volume'].mean()
            volume_terakhir = df['volume'].iloc[-1]
            
            if volume_terakhir > (volume_rata2 * 3):
                if perubahan_persen > 3.5:
                    ditemukan += 1
                    laporan += f"🟢 *POTENSI LONG* \nCoin: `{symbol}`\nKenaikan: `+{perubahan_persen:.2f}%`\nVolume Meledak! 🚀\n\n"
                elif perubahan_persen < -3.5:
                    ditemukan += 1
                    laporan += f"🔴 *POTENSI SHORT* \nCoin: `{symbol}`\nPenurunan: `{perubahan_persen:.2f}%`\nDump / Tekanan Jual Kuat! ⚠️\n\n"
            
            count += 1
        except Exception:
            continue

    if ditemukan > 0:
        kirim_telegram(laporan)
        print("Sinyal berhasil dikirim ke Telegram!")
    else:
        print("Belum ada koin yang memenuhi kriteria ekstrem.")

if __name__ == "__main__":
    crypto_scanner()
