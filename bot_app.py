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
from google.genai.errors import APIError

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Константы и настройки ---
# Токен бота будет браться из переменной окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") 
# Render автоматически предоставит этот URL для вебхуков
WEBHOOK_URL = os.getenv("RENDER_EXTERNAL_URL") 
# Порт, на котором будет слушать сервис (рекомендуется использовать PORT из env, если доступен)
PORT = int(os.environ.get('PORT', 10000))

# Модель Gemini
GEMINI_MODEL = "gemini-2.5-flash" 

# --- Системная инструкция ---
SYSTEM_PROMPT = (
    "Ты дружелюбный и информативный Telegram-бот, основанный на модели Gemini. "
    "Твоя цель — поддерживать беседу и давать полезные советы на русском языке. "
    "Будь кратким, но старайся сохранять контекст разговора."
)

# --- Инициализация Gemini Client и Chat Manager ---
# ВАЖНО: Клиент инициализируется здесь, он автоматически ищет ключ в GEMINI_API_KEY
try:
    GENAI_CLIENT = genai.Client()
    logger.info("Gemini Client успешно инициализирован.")
except Exception as e:
    logger.error(f"Ошибка инициализации Gemini клиента: {e}")
    # Выход, если не удалось инициализировать клиент
    if not TELEGRAM_BOT_TOKEN: 
        exit(1)


# --- Вспомогательная функция для управления контекстом ---
def get_chat_session(chat_id: int):
    """Возвращает или создает новую сессию чата Gemini для данного chat_id."""
    # Используем глобальный клиент
    
    # В реальном приложении, если вы хотите хранить контекст между перезапусками,
    # нужно использовать базу данных (например, Redis). 
    # Здесь для простоты используем `user_data` контекста, 
    # но он будет сбрасываться при перезапуске Render!
    
    # Для целей этого примера, мы будем просто создавать новый чат при каждом запросе,
    # пока не реализуем более надежное хранение.
    # Для использования контекста вам нужно хранить объект chat.

    # Создаем НОВУЮ сессию чата, чтобы задать системную инструкцию
    # Если вы хотите сохранить контекст, вам нужно хранить этот объект 'chat' 
    # в 'context.chat_data' или базе данных и использовать его.
    
    try:
        # Создаем новый объект Chat с системной инструкцией
        # В режиме chat.create, system_instruction передается напрямую
        chat = GENAI_CLIENT.chats.create(
            model=GEMINI_MODEL,
            system_instruction=SYSTEM_PROMPT
        )
        return chat
    except Exception as e:
        logger.error(f"Не удалось создать сессию Gemini Chat: {e}")
        return None


# --- Обработчики команд ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет приветственное сообщение при команде /start."""
    await update.message.reply_text(
        '🤖 Привет! Я бот, работающий на Gemini. Отправь мне любое сообщение, и я постараюсь ответить!'
    )
    # Очищаем старый контекст, если он был (для примера с сохранением контекста)
    if 'gemini_chat' in context.chat_data:
        del context.chat_data['gemini_chat']


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение пользователя в Gemini и возвращает ответ."""
    user_text = update.message.text
    chat_id = update.effective_chat.id

    # 1. Получение или создание сессии чата
    # Для сохранения контекста, мы будем хранить объект Chat в контексте
    if 'gemini_chat' not in context.chat_data:
        context.chat_data['gemini_chat'] = get_chat_session(chat_id)

    chat_session = context.chat_data['gemini_chat']

    if not chat_session:
        await update.message.reply_text("❌ Извините, не удалось подключиться к Gemini API.")
        return

    # Отображаем "печатает..." в чате
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # 2. Отправка сообщения в сессию чата (сохраняет историю!)
        response = chat_session.send_message(user_text)

        # 3. Отправляем ответ от Gemini обратно пользователю
        await update.message.reply_text(response.text)

    except APIError as e:
        error_message = f"❌ ОШИБКА API: Не удалось сгенерировать ответ. Проверьте ключ и квоты. {e}"
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
            # Путь должен быть уникальным (используем токен)
            url_path=TELEGRAM_BOT_TOKEN, 
            # Полный URL для Telegram
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
        )
        logger.info(f"✅ Бот запущен в режиме Webhooks на {WEBHOOK_URL}:{PORT}")
    else:
        logger.warning("Переменная RENDER_EXTERNAL_URL не установлена. Запуск в режиме Polling (Только для локального теста!).")
        application.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
