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
from google.genai import types # <-- Импорт для GenerateContentConfig
from google.genai.errors import APIError

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Константы и настройки ---
# Чтение из переменных окружения Render
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") 
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL") 
PORT = int(os.environ.get('PORT', 10000))

GEMINI_MODEL = "gemini-2.5-flash" 

# --- Системная инструкция ---
SYSTEM_PROMPT = (
    "Ты дружелюбный и информативный Telegram-бот, основанный на модели Gemini. "
    "Твоя цель — поддерживать беседу и давать полезные советы на русском языке. "
    "Будь кратким, но старайся сохранять контекст разговора."
)

# --- Инициализация Gemini Client ---
try:
    # Клиент автоматически ищет ключ в GEMINI_API_KEY
    GENAI_CLIENT = genai.Client()
    logger.info("Gemini Client успешно инициализирован.")
except Exception as e:
    logger.error(f"Ошибка инициализации Gemini клиента: {e}")
    GENAI_CLIENT = None # Устанавливаем в None на случай ошибки

# --- Вспомогательная функция для управления контекстом ---
def get_chat_session(chat_id: int):
    """
    Создает новую сессию чата Gemini с системной инструкцией.
    Сессия чата нужна для сохранения истории сообщений.
    """
    if not GENAI_CLIENT:
        return None

    # 1. Создание объекта конфигурации для системной инструкции
    # ЭТО ИСПРАВЛЕНИЕ ОШИБКИ: 'system_instruction' должен быть внутри config
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT
    )
    
    try:
        # 2. Создание чата с передачей конфигурации
        chat = GENAI_CLIENT.chats.create(
            model=GEMINI_MODEL,
            config=config # <-- Правильный аргумент
        )
        logger.info(f"Сессия Gemini Chat для {chat_id} успешно создана.")
        return chat
    except Exception as e:
        logger.error(f"Не удалось создать сессию Gemini Chat: {e}")
        return None


# --- Обработчики команд ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Отправляет приветственное сообщение и очищает/сбрасывает контекст чата.
    """
    await update.message.reply_text(
        '🤖 Привет! Я бот, работающий на Gemini. Отправь мне любое сообщение, и я постараюсь ответить!'
    )
    # Сбрасываем контекст при старте новой сессии
    if 'gemini_chat' in context.chat_data:
        del context.chat_data['gemini_chat']


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение пользователя в Gemini и возвращает ответ."""
    user_text = update.message.text
    chat_id = update.effective_chat.id

    if not GENAI_CLIENT:
        await update.message.reply_text("❌ Внутренняя ошибка: Gemini Client не инициализирован.")
        return

    # 1. Получение или создание сессии чата
    if 'gemini_chat' not in context.chat_data:
        # Сессия будет храниться в 'context.chat_data' до перезапуска бота
        context.chat_data['gemini_chat'] = get_chat_session(chat_id)

    chat_session = context.chat_data['gemini_chat']

    if not chat_session:
        await update.message.reply_text("❌ Извините, не удалось подключиться к Gemini API и начать чат.")
        return

    # Отображаем "печатает..." в чате
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # 2. Отправка сообщения в сессию чата (сохраняет историю!)
        response = chat_session.send_message(user_text)

        # 3. Отправляем ответ от Gemini обратно пользователю
        await update.message.reply_text(response.text)

    except APIError as e:
        error_message = f"❌ ОШИБКА API: Проверьте ключ и квоты. Код: {e.status_code}"
        logger.error(error_message)
        await update.message.reply_text("Извините, произошла ошибка Gemini API. Попробуйте снова позже.")
    except Exception as e:
        error_message = f"❌ Непредвиденная ошибка: {e}"
        logger.error(error_message)
        await update.message.reply_text("Произошла непредвиденная ошибка. Пожалуйста, проверьте логи.")


# --- Основная функция запуска ---

def main():
    """Запускает бота в режиме Webhooks для Render."""
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Переменная TELEGRAM_BOT_TOKEN не установлена. Завершение работы.")
        return
        
    logger.info("Начало настройки Application...")
    
    # Создаем Application
    # persistence=Application.DEFAULT_PERSISTENCE, # Можно добавить для сохранения контекста между перезапусками
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
            # Путь для вебхука
            url_path=TELEGRAM_BOT_TOKEN, 
            # Полный URL, который Telegram будет использовать
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
        )
        logger.info(f"✅ Бот запущен в режиме Webhooks на {WEBHOOK_URL}:{PORT}")
    else:
        logger.warning("Переменная RENDER_EXTERNAL_URL не установлена. Запуск в режиме Polling (Только для локального теста!).")
        application.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
