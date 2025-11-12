import requests
import os  # <-- ИМПОРТИРУЕМ МОДУЛЬ ДЛЯ БЕЗОПАСНОГО ЧТЕНИЯ КЛЮЧЕЙ
from flask import Flask, request

# --- 1. ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ДЛЯ БЕЗОПАСНОСТИ (24/7) ---

# Вместо жестко заданных значений мы считываем ключи из окружения.
# На Render вы создадите переменные с именами TELEGRAM_TOKEN и GEMINI_API_KEY.
# 🛑 ТЕЛЕГРАМ
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
# 🚀 GEMINI
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

# --------------------------------

# Инициализация Flask (для обработки вебхуков)
app = Flask(__name__)


@app.route('/webhook', methods=['POST'])
def webhook():
    # Проверяем наличие токенов. Если они не установлены (например, при локальном запуске без ENV),
    # токен будет None, что может вызвать ошибку.
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        print("CRITICAL ERROR: Tokens are not set in environment variables.")
        return 'OK'

    try:
        # 1. Получаем входящее сообщение от Telegram
        data = request.get_json()

        # Безопасное извлечение chat_id и user_text
        if not data or 'message' not in data or 'text' not in data['message']:
            return 'OK'  # Пропускаем нетекстовые сообщения

        chat_id = data['message']['chat']['id']
        user_text = data['message']['text']

        # 2. ОТПРАВЛЯЕМ ЗАПРОС В GOOGLE GEMINI

        headers = {"Content-Type": "application/json"}
        # Ключ передается через URL для Gemini
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_text}]
                }
            ]
        }

        # Отправляем запрос
        response = requests.post(url, headers=headers, json=payload).json()

        # 3. Обрабатываем ответ Gemini и очищаем текст

        if response.get('candidates'):
            # Успешный ответ
            raw_response_text = response['candidates'][0]['content']['parts'][0]['text']
            cleaned_text = raw_response_text.strip()
        elif response.get('error'):
            # Ошибка (например, неверный ключ Gemini)
            # Отображаем только безопасную часть ошибки
            cleaned_text = f"Gemini API Error: {response['error'].get('message', 'Unknown error')}"
        else:
            # Неожиданный ответ (например, блок безопасности)
            cleaned_text = "API returned an unexpected format or was blocked by safety settings."

        # 4. Отправляем ответ обратно в Telegram
        telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        requests.post(telegram_url, json={'chat_id': chat_id, 'text': cleaned_text})

        return 'OK'

    except Exception as e:
        # Логирование критических ошибок
        print(f"CRITICAL ERROR: {e}")
        return 'OK'