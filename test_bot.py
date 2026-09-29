import requests
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
response = requests.post(url, data={
    "chat_id": CHAT_ID,
    "text": "✅ Тестовое сообщение! Бот работает!"
})

print(response.json())