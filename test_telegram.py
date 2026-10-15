import requests
import os

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

pesan = "🔔 *TES KONEKSI BERHASIL!*\nBot crypto scanner kamu sudah terhubung sempurna dengan Telegram."
url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
payload = {"chat_id": CHAT_ID, "text": pesan, "parse_mode": "Markdown"}

response = requests.post(url, json=payload)
print("Response dari Telegram:", response.text)
