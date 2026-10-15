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
    print("Memindai pasar Binance Futures untuk Sinyal Reversal...")
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
            
            perubahan_sebelumnya = ((df['close'].iloc[-2] - df['close'].iloc[0]) / df['close'].iloc[0]) * 100
            volume_rata2 = df['volume'].iloc[:-1].mean()
            volume_terakhir = df['volume'].iloc[-1]
            
            if volume_terakhir > (volume_rata2 * 2.5):
                # LONG REVERSAL
                if perubahan_sebelumnya < -2.5 and harga_sekarang > open_terakhir:
                    entry = harga_sekarang
                    sl = low_terakhir * 0.992
                    risiko = entry - sl
                    if risiko <= 0: continue
                    
                    tp1 = entry + (risiko * 1.5)
                    tp2 = entry + (risiko * 2.5)
                    tp3 = entry + (risiko * 4.0)
                    tp_max = entry + (risiko * 6.0)
                    
                    skor_potensi = abs(perubahan_sebelumnya) * (volume_terakhir / volume_rata2)
                    sinyal_list.append({
                        'tipe': '🟢 *POTENSI LONG (REVERSAL)*',
                        'symbol': symbol, 'entry': entry, 'sl': sl, 
                        'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'tp_max': tp_max, 'skor': skor_potensi
                    })

                # SHORT REVERSAL
                elif perubahan_sebelumnya > 2.5 and harga_sekarang < open_terakhir:
                    entry = harga_sekarang
                    sl = high_terakhir * 1.008
                    risiko = sl - entry
                    if risiko <= 0: continue
                    
                    tp1 = entry - (risiko * 1.5)
                    tp2 = entry - (risiko * 2.5)
                    tp3 = entry - (risiko * 4.0)
                    tp_max = entry - (risiko * 6.0)
                    
                    skor_potensi = abs(perubahan_sebelumnya) * (volume_terakhir / volume_rata2)
                    sinyal_list.append({
                        'tipe': '🔴 *POTENSI SHORT (REJEKSI)*',
                        'symbol': symbol, 'entry': entry, 'sl': sl, 
                        'tp1': tp1, 'tp2': tp2, 'tp3': tp3, 'tp_max': tp_max, 'skor': skor_potensi
                    })
        except Exception:
            continue

    sinyal_list = sorted(sinyal_list, key=lambda x: x['skor'], reverse=True)[:8]

    if len(sinyal_list) > 0:
        laporan = "🚨 *BINANCE FUTURES REVERSAL SCANNER* 🚨\n*Fokus Pembalikan Arah & Titik TP/SL*\n\n"
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
        print("Sinyal Reversal berhasil dikirim!")
    else:
        # Kirim notifikasi jika ingin memastikan bot aktif atau biarkan kosong jika ingin bersih
        print("Belum ada koin yang masuk kriteria reversal ekstrem.")

if __name__ == "__main__":
    crypto_scanner()
