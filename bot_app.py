import os
import logging
from telegram import Update
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes
)
from google import genai
from google.genai import types 
from google.genai.errors import APIError

# --- Дополнительные импорты для БД ---
# Оставлен psycopg2 только для функции db_connect.
# Удалены pandas и matplotlib, так как они не нужны без анализа и графиков.
import psycopg2 

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Константы и настройки ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") 
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL") 
PORT = int(os.environ.get('PORT', 10000))

GEMINI_MODEL = "gemini-2.5-flash" 

# --- Настройки БД (Получите эти переменные из панели Render) ---
# Переменные БД оставлены, так как вы просили оставить подключение.
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST") 

# --- Системная инструкция (ОБНОВЛЕНА) ---
SYSTEM_PROMPT = (
    "Ты дружелюбный и информативный Telegram-бот, основанный на модели Gemini. "
    "Твоя цель — отвечать на вопросы пользователя общими знаниями, поскольку у тебя нет прямого доступа к базе данных."
)

# --- Инициализация Gemini Client ---
try:
    GENAI_CLIENT = genai.Client()
    logger.info("Gemini Client успешно инициализирован.")
except Exception as e:
    logger.error(f"Ошибка инициализации Gemini клиента: {e}")
    GENAI_CLIENT = None

# --- Функции Инструментов (Tools) ---
# Оставлена только функция подключения к БД, как вы просили.

def db_connect():
    """
    Устанавливает соединение с БД Render.
    Эта функция существует, но не вызывается в логике бота.
    """
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST
        )
        logger.info("Успешная попытка подключения к БД (для проверки).")
        return conn
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        return None

# --- УДАЛЕНЫ: sql_query_executor, plot_data и AVAILABLE_TOOLS. ---


# --- Вспомогательная функция для управления контекстом ---
def get_chat_session(chat_id: int):
    """
    Создает новую сессию чата Gemini только с системной инструкцией.
    """
    if not GENAI_CLIENT:
        return None

    try:
        # Создание объекта конфигурации для системной инструкции (БЕЗ ИНСТРУМЕНТОВ)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT
        )
        
        # Создание чата
        chat = GENAI_CLIENT.chats.create(
            model=GEMINI_MODEL,
            config=config 
        )
        logger.info(f"Сессия Gemini Chat для {chat_id} успешно создана.")
        return chat
    except Exception as e:
        logger.error(f"Не удалось создать сессию Gemini Chat: {e}")
        return None


# --- Обработчики команд ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет приветственное сообщение и сбрасывает контекст чата.
    """
    await update.message.reply_text(
        '🤖 Привет! Я бот, работающий на Gemini. Я отвечаю на общие вопросы. Контекст сброшен.'
    )
    # Сбрасываем контекст при старте новой сессии
    if 'gemini_chat' in context.chat_data:
        del context.chat_data['gemini_chat']


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет сообщение пользователя в Gemini и возвращает ответ (без вызова функций).
    """
    # 🚨 ПРОВЕРКА: Исключаем ошибку NoneType, как обсуждалось ранее
    if update.message is None or update.message.text is None:
        logger.warning(f"Получено нетекстовое сообщение или сообщение без содержимого от {update.effective_chat.id}. Игнорируем.")
        return
        
    user_text = update.message.text
    chat_id = update.effective_chat.id

    if not GENAI_CLIENT:
        await update.message.reply_text("❌ Внутренняя ошибка: Gemini Client не инициализирован.")
        return

    # 1. Получение или создание сессии чата
    if 'gemini_chat' not in context.chat_data:
        context.chat_data['gemini_chat'] = get_chat_session(chat_id)

    chat_session = context.chat_data['gemini_chat']

    if not chat_session:
        await update.message.reply_text("❌ Извините, не удалось подключиться к Gemini API и начать чат.")
        return

    # Отображаем "печатает..." в чате
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # 2. Прямая отправка сообщения в сессию чата (без Function Calling)
        response = chat_session.send_message(user_text)

        # 3. УДАЛЕНА: Логика обработки response.function_calls

        # 4. Отправляем финальный ответ от Gemini
        await update.message.reply_text(response.text)

        # 5. УДАЛЕНА: Логика отправки графика
        
    except APIError as e:
        error_message = f"❌ ОШИБКА API: Проверьте ключ и квоты. Код: {e.status_code}"
        logger.error(error_message)
        await update.message.reply_text("Извините, произошла ошибка Gemini API. Попробуйте снова позже.")
    except Exception as e:
        error_message = f"❌ Непредвиденная ошибка: {e}"
        logger.error(error_message)
        await update.message.reply_text("Произошла непредвиденная ошибка. Пожалуйста, проверьте логи.")


# --- Основная функция запуска (без изменений) ---

def main():
    """Запускает бота в режиме Webhooks для Render."""
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Переменная TELEGRAM_BOT_TOKEN не установлена. Завершение работы.")
        return
    
    # ПРОВЕРКА БД (оставлена по вашей просьбе)
    if not all([DB_NAME, DB_USER, DB_PASSWORD, DB_HOST]):
        logger.error("Не установлены все переменные окружения для подключения к БД. Бот будет работать, но без возможности подключения к БД.")
    else:
         db_connect() # Попробуем подключиться для лога
        
    logger.info("Начало настройки Application...")
    
    # Создаем Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Настраиваем Webhooks для Render
    if WEBHOOK_URL:
        # Устанавливаем Webhook
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN, 
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
        )
        logger.info(f"✅ Бот запущен в режиме Webhooks на {WEBHOOK_URL}:{PORT}")
    else:
        logger.warning("Переменная RENDER_EXTERNAL_URL не установлена. Запуск в режиме Polling (Только для локального теста!).")
        application.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()

