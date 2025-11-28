import os
import logging
import io
import re
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

# --- Дополнительные импорты для БД и анализа ---
import psycopg2 
import pandas as pd
import matplotlib.pyplot as plt

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
# --- ОБНОВЛЕНО: Используем имя таблицы, предоставленное пользователем ---
TARGET_TABLE = "stock_data" 

# --- Настройки БД ---
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST") 

# --- Системная инструкция (КРИТИЧЕСКИЙ РАЗДЕЛ - ОБНОВЛЕНО) ---
SYSTEM_PROMPT = (
    "Ты — бот-аналитик по акциям технологических компаний за 2024 год. "
    "Твоя задача — анализировать пользовательские запросы на естественном языке и использовать "
    "предоставленные тебе инструменты для получения данных из PostgreSQL и их визуализации. "
    
    f"**КЛЮЧЕВАЯ ИНФОРМАЦИЯ О БД:**\n"
    f"Таблица называется **{TARGET_TABLE}** и содержит следующие столбцы:\n"
    f"| Колонка | Описание |\n"
    f"|---|---|\n"
    f"| **Date** | Временная метка сделки (используй для фильтрации по дате) |\n"
    f"| **Ticker** | Короткий код компании (AAPL, GOOGL, MSFT и т.д.) |\n"
    f"| **Brand_Name** | Полное название компании |\n"
    f"| **Close** | Цена закрытия (используй для большинства ценовых расчетов) |\n"
    f"| **Volume** | Объем торгов |\n"
    f"| Open, High, Low | Другие цены торгов |\n"
    f"| Industry_Tag, Country, Dividends, Stock Splits, Capital Gains | Дополнительные поля |\n"
    
    "**ПРОЦЕСС АНАЛИЗА:**\n"
    "1. **Данные:** Используй `sql_query_executor` для получения данных (статистики, цен за период).\n"
    "2. **График:** Если запрос требует динамики (например, 'покажи график'), используй `plot_data` с полученными данными, используя **Date** для оси X и **Close** для оси Y.\n"
    "3. **Ответ:** Всегда давай развернутый итоговый ответ (анализ, выводы, статистика) на русском языке."
)

# --- Инициализация Gemini Client ---
try:
    GENAI_CLIENT = genai.Client()
    logger.info("Gemini Client успешно инициализирован.")
except Exception as e:
    logger.error(f"Ошибка инициализации Gemini клиента: {e}")
    GENAI_CLIENT = None

# --- Функции Инструментов (Tools) для Gemini ---

def db_connect():
    """Устанавливает соединение с БД Render."""
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST
        )
        return conn
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        return None

def get_db_schema() -> str:
    """
    Возвращает схему столбцов для целевой таблицы 'stock_data'.
    Поскольку схема жестко задана в SYSTEM_PROMPT, эта функция упрощена
    для подтверждения существования таблицы.
    """
    conn = db_connect()
    if conn is None:
        return "ERROR: Не удалось подключиться к базе данных для чтения схемы."

    try:
        with conn.cursor() as cur:
            # Простая проверка существования таблицы
            query = f"""
                SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{TARGET_TABLE}';
            """
            cur.execute(query)
            if cur.fetchone()[0] == 0:
                return f"ERROR: Таблица '{TARGET_TABLE}' не найдена."
            
            # Возвращаем схему, которую Gemini должен использовать
            return SYSTEM_PROMPT.split('**КЛЮЧЕВАЯ ИНФОРМАЦИЯ О БД:**\n')[1]
            
    except Exception as e:
        logger.error(f"Ошибка при получении схемы БД: {e}")
        return f"ERROR_SCHEMA: Ошибка при получении схемы. Детали: {e}"
    finally:
        if conn:
            conn.close()

def sql_query_executor(sql_query: str) -> str:
    """
    Выполняет SQL-запрос к базе данных и возвращает результат в виде CSV-строки.
    """
    conn = db_connect()
    if conn is None:
        return "ERROR: Не удалось подключиться к базе данных. Проверьте настройки."

    try:
        df = pd.read_sql(sql_query, conn)
        conn.close()
        
        if df.empty:
             return "No data found for the query."

        # Ограничиваем вывод 50 строками для краткости
        result_csv = df.head(50).to_csv(index=False)
        return result_csv
    
    except Exception as e:
        conn.close()
        return f"ERROR_SQL: Ошибка выполнения запроса. Проверьте синтаксис SQL. Детали: {e}"


def plot_data(data_csv: str, title: str, x_col: str, y_col: str) -> str:
    """
    Парсит CSV-данные, строит линейный график и сохраняет его в буфер.
    """
    try:
        df = pd.read_csv(io.StringIO(data_csv))
        if df.empty:
            return "ERROR_PLOT: Нет данных для построения графика."
        
        # Преобразование даты и сортировка
        if x_col in df.columns:
            # Преобразуем Date (или другой x_col) к типу datetime, если нужно
            df[x_col] = pd.to_datetime(df[x_col], utc=True).dt.date
            df = df.sort_values(by=x_col)
        
        plt.figure(figsize=(10, 6))
        plt.plot(df[x_col], df[y_col], marker='o', linestyle='-', markersize=2)
        plt.title(title)
        plt.xlabel(x_col.capitalize())
        plt.ylabel(y_col.capitalize())
        plt.grid(True)
        plt.xticks(rotation=45)
        plt.tight_layout()

        buffer = io.BytesIO()
        plt.savefig(buffer, format='png')
        plt.close()
        buffer.seek(0)

        return buffer
    
    except Exception as e:
        logger.error(f"Ошибка при построении графика: {e}")
        return f"ERROR_PLOT: Ошибка при обработке или построении графика. Детали: {e}"

# Список доступных инструментов для Gemini
AVAILABLE_TOOLS = [
    get_db_schema, 
    sql_query_executor, 
    plot_data
]

# --- Вспомогательная функция для управления контекстом ---
def get_chat_session(chat_id: int):
    """
    Создает новую сессию чата Gemini с системной инструкцией и инструментами.
    """
    if not GENAI_CLIENT:
        return None

    try:
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=AVAILABLE_TOOLS
        )
        
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
        '📈 Привет! Я бот-аналитик акций. Использую таблицу **stock_data**.\n\n'
        '**Примеры запросов:**\n'
        '1. Покажи среднюю цену Apple за март 2024.\n'
        '2. Сделай анализ динамики цен Google за первое полугодие 2024.\n'
        'Контекст сброшен. Пожалуйста, начните с запроса.'
    )
    if 'gemini_chat' in context.chat_data:
        del context.chat_data['gemini_chat']


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Главный обработчик, который управляет общением с Gemini и выполнением инструментов.
    """
    # 🚨 Проверка на NoneType
    if update.message is None or update.message.text is None:
        logger.warning(f"Получено нетекстовое сообщение от {update.effective_chat.id}. Игнорируем.")
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

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        # 2. Отправка сообщения в сессию чата 
        response = chat_session.send_message(user_text)

        # 3. Обработка вызовов функций (Tools)
        while response.function_calls:
            function_calls = response.function_calls
            tool_responses = []

            for call in function_calls:
                func_name = call.name
                func_args = dict(call.args)
                
                logger.info(f"Вызов функции: {func_name} с аргументами: {func_args}")
                
                # Поиск и вызов функции
                func_to_call = next((f for f in AVAILABLE_TOOLS if f.__name__ == func_name), None)
                
                if func_to_call:
                    result = func_to_call(**func_args)
                    
                    if func_name == 'plot_data' and isinstance(result, io.BytesIO):
                        context.chat_data['plot_buffer'] = result
                        tool_output = "SUCCESS: График успешно создан и готов к отправке пользователю."
                    else:
                        tool_output = str(result)
                        
                    tool_responses.append(
                        types.Part.from_function_response(
                            name=func_name,
                            response={'result': tool_output}
                        )
                    )
                else:
                    logger.error(f"Неизвестная функция: {func_name}")
                    tool_responses.append(
                        types.Part.from_function_response(
                            name=func_name,
                            response={'result': "ERROR: Unknown function called."}
                        )
                    )

            # Отправляем результаты выполнения функций обратно в Gemini
            response = chat_session.send_message(tool_responses)
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")


        # 4. Отправляем финальный ответ от Gemini
        await update.message.reply_text(response.text)

        # 5. Отправляем график, если он был создан
        if 'plot_buffer' in context.chat_data:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=context.chat_data['plot_buffer'],
                caption="📈 График динамики цен"
            )
            del context.chat_data['plot_buffer']

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
    
    if not all([DB_NAME, DB_USER, DB_PASSWORD, DB_HOST]):
        logger.error("Не установлены все переменные окружения для подключения к БД. Завершение работы.")
        return
        
    logger.info("Начало настройки Application...")
    
    # Создаем Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Настраиваем Webhooks для Render
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
