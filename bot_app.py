import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai # Клиент Gemini
import os

# -----------------------------------------------------------
# 1. ТОКЕНЫ И НАСТРОЙКИ (Считываем из переменных среды)
# -----------------------------------------------------------
# Render автоматически предоставит эти значения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
# Ключ Gemini будет считываться из GEMINI_API_KEY
# Переменные БД удалены
# -----------------------------------------------------------

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Инициализация клиента Gemini (Глобально) ---
try:
    # Ключ берется из переменной среды GEMINI_API_KEY
    gemini_client = genai.Client()
    logging.info("Клиент Gemini успешно инициализирован.")
except Exception as e:
    logging.error(f"Ошибка инициализации клиента Gemini: {e}")

# --- Функции, использующие Gemini ---

def generate_gemini_response(user_request: str) -> str:
    """Генерирует ответ на основе текстового промпта, используя Gemini API."""
    try:
        # Проверяем, что клиент инициализирован
        if 'gemini_client' not in globals() or not gemini_client:
            return "ОШИБКА: Клиент Gemini не инициализирован. Проверьте GEMINI_API_KEY."

        # Системная инструкция, чтобы задать модели роль
        system_instruction = (
            "Вы — полезный и дружелюбный помощник. Отвечайте на вопросы пользователя, "
            "будьте информативным и четким."
        )

        # Вызов модели Gemini
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=user_request,
            system_instruction=system_instruction
        )

        return response.text.strip()

    except Exception as e:
        # Логируем конкретную ошибку API Gemini
        logging.error(f"ОШИБКА генерации ответа через Gemini: {e}")
        return f"ОШИБКА API: Не удалось сгенерировать ответ. Возможно, неверный ключ Gemini или проблема с сетью."


# --- Обработчики команд Telegram ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start."""
    await update.message.reply_text(
        "👋 Привет! Я — простой чат-бот, работающий на Gemini. Спросите меня что угодно!"
    )


async def analyze_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Основной обработчик текстовых сообщений."""
    user_request = update.message.text

    # 1. Защита от слишком длинных запросов
    if len(user_request) > 500:
        await update.message.reply_text("❌ Пожалуйста, сформулируйте запрос короче.")
        return

    await update.message.reply_text("🔎 Думаю над ответом... Пожалуйста, подождите.")

    # 2. Запрос к Gemini для генерации ответа
    gemini_response = generate_gemini_response(user_request)

    # 3. Отправка результата
    await update.message.reply_text(gemini_response)

    
# --- Обработчик ошибок для всей программы ---

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует ошибки, вызванные обработчиками, и отправляет сообщение пользователю."""
    logging.error("Обнаружена ошибка при обработке запроса:", exc_info=context.error)
    
    # Отправляем дружелюбное сообщение, если это возможно
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "🛑 Извините, при обработке вашего запроса произошла внутренняя ошибка. "
            "Попробуйте другой запрос."
        )


# --- Основная функция запуска бота ---

def main() -> None:
    """Запускает бота."""
    # Проверка наличия токена
    if not TELEGRAM_TOKEN:
        logging.error("TELEGRAM_TOKEN не установлен. Бот не может быть запущен.")
        return

    # Создание Application и передача токена
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Обработчики команд и сообщений
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_message))
    
    # Регистрация глобального обработчика ошибок
    application.add_error_handler(error_handler)
    
    # -----------------------------------------------------------
    # Запуск бота (Polling vs. Webhook)
    # -----------------------------------------------------------
    # Порт для Webhook (используем тот, который предоставляет Render)
    PORT = int(os.environ.get('PORT', 8080))
    
    if 'RENDER_EXTERNAL_URL' in os.environ:
        # Режим Webhook для Render
        url = os.environ['RENDER_EXTERNAL_URL']
        print(f"Бот запущен в режиме Webhook. URL: {url}, Порт: {PORT}")
        
        # Запуск Webhook
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_TOKEN, # Путь должен соответствовать токену (стандартная практика)
            webhook_url=f"{url}/{TELEGRAM_TOKEN}"
        )
    else:
        # Режим Polling для локального запуска
        print("Бот запущен в режиме Polling. Откройте Telegram и начните диалог.")
        application.run_polling(poll_interval=1.0)


if __name__ == '__main__':
    main()
