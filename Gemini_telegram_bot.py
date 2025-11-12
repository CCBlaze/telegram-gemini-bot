import os
import requests
import sqlite3
import json
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from datetime import datetime

# --- 1. ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ДЛЯ БЕЗОПАСНОСТИ ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

# Инициализация Flask и CORS
app = Flask(__name__)
CORS(app)
DATABASE = 'bot_chats.db'  # Файл базы данных на Render


# --- 2. ФУНКЦИИ РАБОТЫ С БАЗОЙ ДАННЫХ ---

def get_db_connection():
    """Создает и возвращает подключение к базе данных."""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Инициализирует таблицу в базе данных."""
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER NOT NULL,
                conversation_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                history_json TEXT NOT NULL,
                is_active BOOLEAN NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


# --- 3. ФУНКЦИИ УПРАВЛЕНИЯ ЧАТОМ ---

def send_telegram_message(chat_id, text):
    """Отправляет ответ пользователю в Telegram."""
    telegram_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    # Parse mode установлен в Markdown для корректного форматирования
    requests.post(telegram_url, json={'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'})


def get_active_conversation(chat_id):
    """Получает активный разговор пользователя или создает новый."""
    with get_db_connection() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE chat_id = ? AND is_active = 1", (chat_id,)).fetchone()

        if chat is None:
            conn.execute(
                "INSERT INTO chats (chat_id, title, history_json, is_active) VALUES (?, ?, ?, ?)",
                (chat_id, "Новый чат: " + datetime.now().strftime("%Y-%m-%d"), '[]', 1)
            )
            conn.commit()
            chat = conn.execute("SELECT * FROM chats WHERE chat_id = ? AND is_active = 1", (chat_id,)).fetchone()

        history = json.loads(chat['history_json'])
        return chat['conversation_id'], history


def save_message_to_history(conversation_id, role, text):
    """Добавляет сообщение в историю и сохраняет в БД."""
    with get_db_connection() as conn:
        chat = conn.execute("SELECT history_json FROM chats WHERE conversation_id = ?", (conversation_id,)).fetchone()
        if chat:
            history = json.loads(chat['history_json'])
            history.append({"role": role, "parts": [{"text": text}]})

            conn.execute(
                "UPDATE chats SET history_json = ? WHERE conversation_id = ?",
                (json.dumps(history), conversation_id)
            )
            conn.commit()


# --- 4. TELEGRAM WEBHOOK (ОБНОВЛЕННАЯ ФУНКЦИЯ) ---

@app.route('/webhook', methods=['POST'])
def webhook():
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        print("CRITICAL ERROR: Tokens are not set in environment variables.")
        return 'OK'

    init_db()

    try:
        data = request.get_json()
        message = data.get('message')

        if not message or 'text' not in message:
            return 'OK'

        chat_id = message['chat']['id']
        user_text = message['text'].strip()

        # --- ОБРАБОТКА КОМАНД ---

        if user_text == '/start':
            send_telegram_message(chat_id,
                                  "*Привет! Я ваш Gemini-бот с памятью.* \n\nЯ запоминаю наш разговор. \n\n*Команды:*\n/new - Начать новый разговор\n/history - Показать сохраненные чаты")
            return 'OK'

        if user_text == '/new':
            with get_db_connection() as conn:
                conn.execute("UPDATE chats SET is_active = 0 WHERE chat_id = ? AND is_active = 1", (chat_id,))
                conn.execute(
                    "INSERT INTO chats (chat_id, title, history_json, is_active) VALUES (?, ?, ?, ?)",
                    (chat_id, "Новый чат: " + datetime.now().strftime("%Y-%m-%d %H:%M"), '[]', 1)
                )
                conn.commit()
            send_telegram_message(chat_id, "🆕 *Начат новый разговор.* Предыдущий сохранен.")
            return 'OK'

        if user_text == '/history':
            with get_db_connection() as conn:
                chats = conn.execute(
                    "SELECT conversation_id, title, is_active, created_at FROM chats WHERE chat_id = ? ORDER BY created_at DESC",
                    (chat_id,)).fetchall()

            if not chats:
                send_telegram_message(chat_id, "У вас пока нет сохраненных разговоров.")
                return 'OK'

            response_text = "*Ваши сохраненные разговоры:*\n\n"
            for chat in chats:
                active_status = " (✅ Активный)" if chat['is_active'] else ""
                # ИСПОЛЬЗУЕМ ЗВЕЗДОЧКИ ВМЕСТО ОБРАТНЫХ КАВЫЧЕК
                response_text += f"ID: *{chat['conversation_id']}*\n"
                response_text += f"*{chat['title']}*{active_status}\n"
                response_text += f"Создан: {chat['created_at'].split()[0]}\n\n"

            response_text += "Чтобы продолжить разговор, введите: */switch ID*, где ID — номер из списка."
            send_telegram_message(chat_id, response_text)
            return 'OK'

        if user_text.startswith('/switch'):
            try:
                new_conv_id = int(user_text.split()[1])
                with get_db_connection() as conn:
                    chat = conn.execute("SELECT title FROM chats WHERE conversation_id = ? AND chat_id = ?",
                                        (new_conv_id, chat_id)).fetchone()
                    if chat:
                        conn.execute("UPDATE chats SET is_active = 0 WHERE chat_id = ? AND is_active = 1", (chat_id,))
                        conn.execute("UPDATE chats SET is_active = 1 WHERE conversation_id = ?", (new_conv_id,))
                        conn.commit()
                        send_telegram_message(chat_id, f"🔄 *Разговор переключен* на: *{chat['title']}*")
                    else:
                        send_telegram_message(chat_id, "❌ Разговор с таким ID не найден или не принадлежит вам.")
                return 'OK'
            except (IndexError, ValueError):
                send_telegram_message(chat_id, "❌ Неверный формат команды. Используйте: */switch ID*")
                return 'OK'

        # --- ОБРАБОТКА ТЕКСТА ---
        conversation_id, history = get_active_conversation(chat_id)

        save_message_to_history(conversation_id, "user", user_text)

        history.append({"role": "user", "parts": [{"text": user_text}]})

        # --- ОТПРАВЛЯЕМ ЗАПРОС В GOOGLE GEMINI С ИСТОРИЕЙ ---
        headers = {"Content-Type": "application/json"}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

        payload = {
            "contents": history
        }

        response = requests.post(url, headers=headers, json=payload).json()

        # 3. Обрабатываем ответ Gemini
        cleaned_text = "API вернул ошибку."
        if response.get('candidates'):
            raw_response_text = response['candidates'][0]['content']['parts'][0]['text']
            cleaned_text = raw_response_text.strip()
            save_message_to_history(conversation_id, "model", cleaned_text)

        elif response.get('error'):
            cleaned_text = f"Gemini API Error: {response['error'].get('message', 'Unknown error')}"

        send_telegram_message(chat_id, cleaned_text)

        return 'OK'

    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        send_telegram_message(chat_id, "Произошла внутренняя ошибка сервера. Попробуйте еще раз.")
        return 'OK'


# --- ВЕБ-ИНТЕРФЕЙС (HTML) - ОСТАВЛЯЕМ ДЛЯ ПОЛНОТЫ ---

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/chat', methods=['POST'])
def api_chat():
    try:
        data = request.get_json()
        user_text = data.get('message')
        if not user_text or not GEMINI_API_KEY:
            return jsonify({'response': 'Ошибка.'}), 400

        headers = {"Content-Type": "application/json"}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

        payload = {"contents": [{"role": "user", "parts": [{"text": user_text}]}]}
        response = requests.post(url, headers=headers, json=payload).json()

        if response.get('candidates'):
            cleaned_text = response['candidates'][0]['content']['parts'][0]['text'].strip()
        else:
            cleaned_text = "API error."
        return jsonify({'response': cleaned_text})

    except Exception as e:
        print(f"WEB CHAT ERROR: {e}")
        return jsonify({'response': f'Внутренняя ошибка сервера: {e}'}), 500


```eof
