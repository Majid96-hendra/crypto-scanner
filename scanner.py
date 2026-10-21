import os
import requests
import ccxt
import pandas as pd
from datetime import datetime

# Token Bot Telegram dan Chat ID kamu
TELEGRAM_TOKEN = "8812904478:AAGg1o5vg8l0GHaDLhMidknBTGU5PL22_hg"
CHAT_ID = "8626570783"

def kirim_telegram(pesan):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": pesan,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Gagal kirim ke telegram: {e}")

def hitung_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def scan_pasar_long_diperbaiki():
    waktu_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"Memulai pemindaian Crypto LONG Rebound pada {waktu_sekarang}...")
    sinyal_ditemukan = []

    try:
        exchange = ccxt.binance({'options': {'defaultType': 'future'}, 'enableRateLimit': True})
        markets = exchange.load_markets()
        symbols = [s for s in markets if s.endswith('/USDT') and not ':USDT' in s and markets[s]['active']]
        
        for symbol in symbols:
            try:
                # Mengambil data kline/OHLCV timeframe 15m
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=50)
                if len(ohlcv) < 50: continue
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
                # Hitung RSI 14
                df['rsi'] = hitung_rsi(df['close'], 14)
                if len(df) < 3: continue
                
                rsi_sebelumnya = df['rsi'].iloc[-2]
                rsi_sekarang = df['rsi'].iloc[-1]
                
                harga_sekarang = df['close'].iloc[-1]
                open_terakhir = df['open'].iloc[-1]
                low_terakhir = df['low'].iloc[-1]
                
                vol_rata2 = df['volume'].iloc[:-1].mean()
                vol_terakhir = df['volume'].iloc[-1]
                
                # --- KRITERIA LONG REBOUND DIPERBAIKI ---
                # A. RSI mulai bergerak naik (RSI naik dari sebelumnya, tidak harus di bawah 32 agar koin yang baru mau naik ketangkap)
                kondisi_rsi_naik = (rsi_sekarang > rsi_sebelumnya) and (rsi_sekarang < 60)
                
                # B. Harga menguat (Close > Open) atau ada lonjakan volume pembelian
                kondisi_momentum = (harga_sekarang > open_terakhir) or (vol_terakhir > vol_rata2 * 1.2)
                
                if kondisi_rsi_naik and kondisi_momentum:
                    entry = harga_sekarang
                    sl = low_terchakrair = low_terakhir * 0.992 # Stop loss tipis di bawah low
                    risiko = entry - sl
                    if risiko <= 0: continue
                    tp1 = entry + (risiko * 1.5)
                    tp2 = entry + (risiko * 2.5)
                    
                    teks = (f"🚀 *SINYAL LONG REBOUND CRYPTO* 🚀\n"
                            f"• Aset: `{symbol}`\n"
                            f"• RSI: `{rsi_sebelumnya:.1f}` ➔ `{rsi_sekarang:.1f}` (Naik)\n"
                            f"• Entry: `{entry:.4f}`\n"
                            f"• Stop Loss: `{sl:.4f}`\n"
                            f"• TP1: `{tp1:.4f}` | TP2: `{tp2:.4f}`")
                    sinyal_ditemukan.append(teks)
            except:
                continue
    except Exception as e:
        print(f"Error crypto: {e}")

    # Laporan ke Telegram
    if len(sinyal_ditemukan) > 0:
        kirim_telegram(f"🔍 *Hasil Scan Crypto LONG ({waktu_sekarang}) - Ditemukan {len(sinyal_ditemukan)} Sinyal:*")
        for item in sinyal_ditemukan:
            kirim_telegram(item)
    else:
        kirim_telegram(f"🤖 *Scan Update LONG ({waktu_sekarang}):* Belum ada koin yang memenuhi syarat pantulan.")

if __name__ == "__main__":
    scan_pasar_long_diperbaiki()
