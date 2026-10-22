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

def scan_pasar_long_pola():
    waktu_sekarang = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"Memulai pemindaian Long Pola Akumulasi pada {waktu_sekarang}...")
    sinyal_ditemukan = []

    try:
        exchange = ccxt.binance({'options': {'defaultType': 'future'}, 'enableRateLimit': True})
        markets = exchange.load_markets()
        symbols = [s for s in markets if s.endswith('/USDT') and not ':USDT' in s and markets[s]['active']]
        
        # Ambil 70 koin dengan volume terbesar agar proses cepat dan akurat
        volume_list = []
        for symbol in symbols[:150]:
            try:
                ticker = exchange.fetch_ticker(symbol)
                volume_list.append((symbol, ticker.get('quoteVolume', 0)))
            except:
                continue
        
        volume_list.sort(key=lambda x: x[1], reverse=True)
        top_symbols = [item[0] for item in volume_list[:70]]

        for symbol in top_symbols:
            try:
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe='15m', limit=40)
                if len(ohlcv) < 40: continue
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
                df['rsi'] = hitung_rsi(df['close'], 14)
                df['ma20'] = df['close'].rolling(window=20).mean()
                
                if len(df) < 25: continue
                
                harga_sekarang = df['close'].iloc[-1]
                open_terakhir = df['open'].iloc[-1]
                low_terakhir = df['low'].iloc[-1]
                ma20_sekarang = df['ma20'].iloc[-1]
                rsi_sekarang = df['rsi'].iloc[-1]
                
                # Cek apakah beberapa candle sebelumnya bergerak menyamping (sideways sempit)
                # Dilihat dari selisih High dan Low dalam 5 candle terakhir yang relatif kecil
                range_sebelumnya = (df['high'].iloc[-6:-1].max() - df['low'].iloc[-6:-1].min()) / df['close'].iloc[-1]
                
                # Syarat Pola Breakout dari Sideways/Akumulasi:
                # 1. Rentang harga sebelumnya sempit (sideways / konsolidasi)
                # 2. Harga saat ini mulai breakout / ditutup hijau di atas MA20
                # 3. RSI berada di area sehat (40 hingga 65) dan mulai menanjak
                kondisi_sideways = range_sebelumnya < 0.04 # Range ketat kurang dari 4%
                kondisi_breakout = (harga_sekarang > open_terakhir) and (harga_sekarang > ma20_sekarang)
                kondisi_rsi = 40 <= rsi_sekarang <= 65
                
                if kondisi_sideways and kondisi_breakout and kondisi_rsi:
                    entry = harga_sekarang
                    sl = low_terakhir * 0.990
                    risiko = entry - sl
                    if risiko <= 0: continue
                    tp1 = entry + (risiko * 1.5)
                    tp2 = entry + (risiko * 2.5)
                    
                    teks = (f"🚀 *SINYAL LONG POLA AKUMULASI* 🚀\n"
                            f"• Aset: `{symbol}`\n"
                            f"• RSI: `{rsi_sekarang:.1f}` (Mulai Naik)\n"
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
        kirim_telegram(f"🔍 *Hasil Scan Long Akumulasi ({waktu_sekarang}) - Ditemukan {len(sinyal_ditemukan)} Sinyal:*")
        for item in sinyal_ditemukan:
            kirim_telegram(item)
    else:
        kirim_telegram(f"🤖 *Scan Update Long Akumulasi ({waktu_sekarang}):* Belum ada koin yang breakout dari pola konsolidasi.")

if __name__ == "__main__":
    scan_pasar_long_pola()
