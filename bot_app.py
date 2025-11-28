import os
import logging
import json
import io 
from telegram import Update, InputFile 
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

# Библиотеки для анализа и графики
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter

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
FUNCTION_NAME = "analyze_and_plot_stock_data" 

# --- Системная инструкция (ОБНОВЛЕНА С ТОЧНОЙ СХЕМОЙ) ---
SYSTEM_PROMPT = (
    "Ты продвинутый Telegram-бот, специализирующийся на финансовой аналитике акций. "
    "Твоя задача — генерировать SQL-запросы для получения данных из **ТАБЛИЦЫ 'stock_data'**. "
    
    "Эта таблица имеет следующие **КЛЮЧЕВЫЕ СТОЛБЦЫ** для анализа: "
    "1. **Date** (Дата): Используй для фильтрации по датам (например, '2024-01-01')."
    "2. **Close** (Цена закрытия): Используй для анализа цен и построения графиков."
    "3. **Brand_Name** (Название компании): Используй для фильтрации по полному названию компании."
    "4. **Ticker** (Тикер акции): Используй для фильтрации по тикеру, если это указано в запросе (например, 'AAPL')."

    "Когда пользователь запрашивает аналитику, цены, сводки или графики за 2024 год: "
    "1. **ВСЕГДА** вызывай функцию 'analyze_and_plot_stock_data', передавая ей название компании и период."
    "2. Внутренний SQL-запрос должен использовать **Date, Close, Brand_Name (или Ticker)** и фильтровать по датам в диапазоне '2024-01-01' AND '2024-12-31'."
    "3. После анализа, сформулируй дружелюбный ответ, предоставив ключевые выводы и график."
)

# --- Инициализация Gemini Client ---
try:
    GENAI_CLIENT = genai.Client()
    logger.info("Gemini Client успешно инициализирован.")
except Exception as e:
    logger.error(f"Ошибка инициализации Gemini клиента: {e}")
    GENAI_CLIENT = None

# ----------------------------------------------------------------------
#                       ГЛАВНАЯ ФУНКЦИЯ ДЛЯ АНАЛИЗА
# ----------------------------------------------------------------------

def analyze_and_plot_stock_data(company_name: str, date_range_query: str) -> str:
    """
    Выполняет SQL-запрос, анализирует данные и строит график цен акций. 
    
    Args:
        company_name: Название компании (например, 'Apple' или 'Microsoft').
        date_range_query: Запрос временного диапазона (например, 'Март 2024', 'первое полугодие').
               
    Returns:
        JSON-строка, содержащая аналитическую сводку и путь к файлу графика.
    """
    
    # --- Внутренняя функция для выполнения SQL-запроса ---
    def execute_sql_query(sql_query: str):
        db_host = os.getenv("DB_HOST")
        db_name = os.getenv("DB_NAME")
        db_user = os.getenv("DB_USER")
        db_password = os.getenv("DB_PASSWORD")
        db_port = os.getenv("DB_PORT", 5432)

        if not all([db_host, db_name, db_user, db_password]):
            return None, "Отсутствуют учетные данные базы данных."

        db_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
        
        try:
            engine = create_engine(db_url)
            with engine.connect() as connection:
                result = connection.execute(text(sql_query))
                
                # Создаем DataFrame из результатов запроса
                column_names = list(result.keys())
                df = pd.DataFrame(result.all(), columns=column_names)
                return df, None
        except Exception as e:
            return None, f"Ошибка выполнения запроса к базе данных: {str(e)}"
    
    # 1. Генерируем SQL-запрос с использованием ТОЧНЫХ ИМЕН (stock_data, Date, Close, Brand_Name)
    # Предполагаем, что пользовательский запрос подразумевает 2024 год.
    sql_query = f"""
    SELECT 
        Date, 
        Close 
    FROM 
        stock_data 
    WHERE 
        Brand_Name = '{company_name}' AND 
        Date BETWEEN '2024-01-01' AND '2024-12-31'
    ORDER BY 
        Date;
    """
    
    # 2. Выполнение запроса
    df, error = execute_sql_query(sql_query)

    if error:
        return json.dumps({"status": "error", "message": error, "image_path": ""})
    
    if df.empty:
        return json.dumps({"status": "error", "message": f"Данные для компании {company_name} за 2024 год не найдены.", "image_path": ""})

    # Очистка и подготовка данных
    # Переименовываем столбцы для работы Pandas/Matplotlib
    df.columns = ['Date', 'Price'] 
    df['Date'] = pd.to_datetime(df['Date'])
    df['Price'] = pd.to_numeric(df['Price'])
    df = df.sort_values(by='Date')
    
    # 3. Анализ данных (Pandas)
    stats = {}
    stats['min_price'] = round(df['Price'].min(), 2)
    stats['max_price'] = round(df['Price'].max(), 2)
    stats['avg_price'] = round(df['Price'].mean(), 2)
    
    start_price = df['Price'].iloc[0]
    end_price = df['Price'].iloc[-1]
    stats['start_price'] = round(start_price, 2)
    stats['end_price'] = round(end_price, 2)
    stats['price_change'] = round(end_price - start_price, 2)
    stats['change_percent'] = round((stats['price_change'] / start_price) * 100, 2)
    
    # 4. Построение графика (Matplotlib)
    image_filename = f"{company_name}_{df['Date'].min().strftime('%Y%m%d')}_chart.png"
    image_path = os.path.join("/tmp", image_filename) 

    try:
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(df['Date'], df['Price'], label=f"Цена {company_name}", color='green' if stats['price_change'] >= 0 else 'red')
        
        ax.set_title(f"Динамика цен {company_name} ({date_range_query})", fontsize=16)
        ax.set_xlabel("Дата", fontsize=12)
        ax.set_ylabel("Цена закрытия (USD)", fontsize=12)
        ax.grid(True, linestyle='--', alpha=0.7)
        ax.legend()
        
        date_form = DateFormatter("%b %d, %Y")
        ax.xaxis.set_major_formatter(date_form)
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        
        plt.savefig(image_path)
        plt.close(fig)
        
    except Exception as e:
        image_path = ""
        logger.error(f"Не удалось построить график: {str(e)}")
        stats['analysis_error'] = "График не может быть построен."

    # 5. Возвращаем результаты
    return json.dumps({
        "status": "success", 
        "analysis_summary": stats, 
        "image_path": image_path,
        "company": company_name,
        "period": date_range_query
    }, ensure_ascii=False)


# ----------------------------------------------------------------------
#                       КОНФИГУРАЦИЯ ИНСТРУМЕНТОВ (не менялась)
# ----------------------------------------------------------------------

AVAILABLE_TOOLS = {
    FUNCTION_NAME: analyze_and_plot_stock_data,
}

def get_chat_session(chat_id: int):
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
        return chat
    except Exception as e:
        logger.error(f"Не удалось создать сессию Gemini Chat: {e}")
        return None


# --- Основная логика Tool Calling с отправкой графика (не менялась) ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        '🤖 Я бот-аналитик акций. Спросите меня: "Покажи анализ и график цен Apple за первое полугодие 2024" '
        'или "Какая была средняя цена Microsoft в марте?".'
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
        response = chat_session.send_message(user_text)
        image_path_to_send = None
        
        while response.function_calls:
            
            tool_responses = []
            
            for call in response.function_calls:
                function_name = call.name
                function_args = dict(call.args)
                
                logger.info(f"Модель запросила: {function_name} с аргументами: {function_args}")

                if function_name in AVAILABLE_TOOLS:
                    function_to_call = AVAILABLE_TOOLS[function_name]
                    tool_result_json_str = function_to_call(**function_args) 
                    
                    try:
                        tool_data = json.loads(tool_result_json_str)
                        if tool_data.get('image_path'):
                            image_path_to_send = tool_data['image_path']
                        
                        tool_responses.append(
                            types.Part.from_function_response(
                                name=function_name,
                                response={'result': tool_result_json_str}
                            )
                        )
                    except json.JSONDecodeError:
                        tool_responses.append(
                            types.Part.from_function_response(
                                name=function_name,
                                response={'result': "Ошибка: функция вернула невалидный JSON."}
                            )
                        )
                else:
                    logger.warning(f"Неизвестная функция: {function_name}")
                    
            response = chat_session.send_message(tool_responses)
            
        final_text = response.text
        
        if image_path_to_send and os.path.exists(image_path_to_send):
            try:
                with open(image_path_to_send, 'rb') as image_file:
                    await update.message.reply_photo(
                        photo=InputFile(image_file),
                        caption=final_text
                    )
                os.remove(image_path_to_send)
                return
            except Exception as e:
                logger.error(f"Ошибка при отправке или удалении файла: {e}")
                final_text += f"\n\n[Ошибка: График не был отправлен из-за технической проблемы.]"

        await update.message.reply_text(final_text)

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
        os.makedirs("/tmp", exist_ok=True) 
        
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TELEGRAM_BOT_TOKEN, 
            webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}"
        )
        logger.info(f"✅ Бот запущен в режиме Webhooks на {WEBHOOK_URL}:{PORT}")
    else:
        logger.warning("Переменная RENDER_EXTERNAL_URL не установлена. Запуск в режиме Polling (Только для локального теста!).")
        os.makedirs("/tmp", exist_ok=True)
        application.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
