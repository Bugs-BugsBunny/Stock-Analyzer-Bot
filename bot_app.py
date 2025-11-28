import os
import logging
import json
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
from sqlalchemy import create_engine, text

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
FUNCTION_NAME = "query_database" 

# --- Системная инструкция (ОБНОВЛЕНА ДЛЯ АНАЛИТИКИ) ---
SYSTEM_PROMPT = (
    "Ты продвинутый Telegram-бот, специализирующийся на финансовой аналитике акций "
    "технологических компаний. Ты работаешь с базой данных, содержащей исторические данные о ценах. "
    
    "Твоя цель — отвечать на запросы пользователя, используя функцию 'query_database'. "
    "Когда пользователь запрашивает аналитику, цены, сводки или графики за 2024 год: "
    "1. **Всегда** формулируй SQL-запрос, явно включая условие WHERE для дат в диапазоне '2024-01-01' AND '2024-12-31'. "
    "2. Запрос должен извлекать **дату, название компании и цену**."
    "3. После получения данных, ты должен **провести анализ**: найти минимальную, максимальную и среднюю цену за указанный период."
    "4. Сформулируй дружелюбный ответ, предоставив ключевые аналитические выводы (минимум, максимум, среднее). "
    "5. Если пользователь просит график, объясни, что ты можешь предоставить только текстовую сводку."
)

# --- Инициализация Gemini Client (оставлено прежним) ---
try:
    GENAI_CLIENT = genai.Client()
    logger.info("Gemini Client успешно инициализирован.")
except Exception as e:
    logger.error(f"Ошибка инициализации Gemini клиента: {e}")
    GENAI_CLIENT = None

# ----------------------------------------------------------------------
#                         ФУНКЦИЯ ДЛЯ БАЗЫ ДАННЫХ
# ----------------------------------------------------------------------

def query_database(query: str) -> str:
    """
    Выполняет SQL-запрос (только SELECT) к базе данных инвентаризации, чтобы получить 
    данные о товарах, ценах или складских запасах.

    Args:
        query: Полный SQL-запрос, который необходимо выполнить (например, 'SELECT * FROM products WHERE price > 100').
               
    Returns:
        Результат запроса в формате JSON-строки или сообщение об ошибке.
    """
    
    # 1. Чтение учетных данных из переменных окружения
    db_host = os.getenv("DB_HOST")
    db_name = os.getenv("DB_NAME")
    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")
    db_port = os.getenv("DB_PORT", 5432)

    if not all([db_host, db_name, db_user, db_password]):
        return json.dumps({"error": "Отсутствуют учетные данные базы данных (DB_HOST, DB_USER и т.д.)"})

    # 2. Формирование строки подключения (для PostgreSQL)
    db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    
    # 3. Подключение к БД и выполнение запроса
    try:
        engine = create_engine(db_url)
        with engine.connect() as connection:
            result = connection.execute(text(query))
            
            # 4. Преобразование результата в JSON-формат
            column_names = list(result.keys())
            data_list = [dict(zip(column_names, row)) for row in result.all()]
            
            return json.dumps(data_list, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": f"Ошибка выполнения запроса к базе данных: {str(e)}"})

# ----------------------------------------------------------------------
#                       КОНФИГУРАЦИЯ ИНСТРУМЕНТОВ
# ----------------------------------------------------------------------

AVAILABLE_TOOLS = {
    FUNCTION_NAME: query_database,
}

# --- Вспомогательная функция для управления контекстом ---
def get_chat_session(chat_id: int):
    # ... (логика создания чат-сессии) ...
    if not GENAI_CLIENT:
        return None

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=AVAILABLE_TOOLS.values() 
    )
    
    try:
        chat = GENAI_CLIENT.chats.create(
            model=GEMINI_MODEL,
            config=config 
        )
        logger.info(f"Сессия Gemini Chat для {chat_id} успешно создана.")
        return chat
    except Exception as e:
        logger.error(f"Не удалось создать сессию Gemini Chat: {e}")
        return None


# --- Обработчики команд и основная логика Tool Calling ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🤖 Привет! Я бот на Gemini 2.5 Flash, готовый предоставить аналитику цен акций за 2024 год. '
        'Спросите меня, например: "Какова была минимальная и максимальная цена Microsoft в 2024 году?". Контекст сброшен.'
    )
    if 'gemini_chat' in context.chat_data:
        del context.chat_data['gemini_chat']


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id

    if not GENAI_CLIENT:
        await update.message.reply_text("❌ Внутренняя ошибка: Gemini Client не инициализирован.")
        return

    if 'gemini_chat' not in context.chat_data:
        context.chat_data['gemini_chat'] = get_chat_session(chat_id)

    chat_session = context.chat_data['gemini_chat']

    if not chat_session:
        await update.message.reply_text("❌ Извините, не удалось начать чат.")
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # 2. Отправляем сообщение вместе с доступными инструментами
        response = chat_session.send_message(user_text)
        
        # 3. Цикл обработки вызова функций (Tool Calling)
        while response.function_calls:
            
            tool_responses = []
            
            for call in response.function_calls:
                function_name = call.name
                function_args = dict(call.args)
                
                logger.info(f"Модель запросила вызов функции: {function_name} с аргументами: {function_args}")

                if function_name in AVAILABLE_TOOLS:
                    function_to_call = AVAILABLE_TOOLS[function_name]
                    tool_result = function_to_call(**function_args) 
                    
                    tool_responses.append(
                        types.Part.from_function_response(
                            name=function_name,
                            response={'result': tool_result}
                        )
                    )
                else:
                    logger.warning(f"Неизвестная функция: {function_name}")
                    
            response = chat_session.send_message(tool_responses)
            
        # 4. Отправляем финальный текстовый ответ от Gemini
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
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Переменная TELEGRAM_BOT_TOKEN не установлена. Завершение работы.")
        return
        
    logger.info("Начало настройки Application...")
    
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    if WEBHOOK_URL:
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
