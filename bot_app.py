import io
import time
import logging
import pandas as pd
import psycopg2
import matplotlib.pyplot as plt
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
# OPENAI_API_KEY теперь не используется

# Данные для подключения к PostgreSQL (берем ИСКЛЮЧИТЕЛЬНО из переменных среды Render)
# ВАЖНО: Эти переменные нужно будет добавить в настройки Environment на Render!
DB_NAME = os.environ.get("DB_NAME") # Должно быть установлено на Render
DB_USER = os.environ.get("DB_USER") # Должно быть установлено на Render
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_HOST = os.environ.get("DB_HOST") # Полный адрес хоста (например, dpg-xxxx.render.com)
# -----------------------------------------------------------

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- Инициализация клиента Gemini (Глобально) ---
# Ключ будет взят из переменной среды GEMINI_API_KEY
try:
    gemini_client = genai.Client()
    logging.info("Клиент Gemini успешно инициализирован.")
except Exception as e:
    logging.error(f"Ошибка инициализации клиента Gemini: {e}")
    # Если ключ не найден, это будет обрабатываться в функции generate_sql_query

# --- Вспомогательная функция для выполнения SQL-запроса ---

def execute_db_query(sql_query: str) -> pd.DataFrame | None:
    """Выполняет SQL-запрос и возвращает данные в DataFrame."""
    conn = None
    df = None
    
    # Дополнительная проверка на наличие всех переменных БД
    if not all([DB_NAME, DB_USER, DB_PASSWORD, DB_HOST]):
        logging.error("Отсутствуют необходимые переменные среды для подключения к БД.")
        return None

    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
        )
        # Установите имя курсора для отладки
        conn.cursor().execute("SET application_name = 'telegram_bot_app'")
        df = pd.read_sql(sql_query, conn)
        logging.info(f"Успешно выполнено: {sql_query}")
        return df

    except psycopg2.Error as e:
        # Критическая ошибка подключения или выполнения SQL
        logging.error(f"КРИТИЧЕСКАЯ ОШИБКА БД: {e}")
        return None
    finally:
        if conn:
            conn.close()


# --- Функции, использующие Gemini (заменили OpenAI) ---

def generate_sql_query(user_request: str) -> str:
    """Генерирует SQL-запрос на основе текстового промпта, используя Gemini API."""
    try:
        # Проверяем, что клиент инициализирован
        if 'gemini_client' not in globals():
            return "ОШИБКА: Клиент Gemini не инициализирован. Проверьте GEMINI_API_KEY."

        # Описание структуры БД для модели
        db_schema = (
            "У тебя есть таблица 'stock_data' с колонками: date (TEXT, YYYY-MM-DD), ticker (TEXT), "
            "brand_name (TEXT), close (REAL), industry_tag (TEXT), year_extracted (INTEGER). "
            "Все данные за 2024 год."
        )

        # Полный промпт
        full_prompt = (
            f"Вы эксперт по SQL для PostgreSQL. Ваша задача - преобразовать запрос пользователя "
            f"('{user_request}') в ОДИН корректный SQL-запрос. "
            f"Используй ТОЛЬКО таблицу 'stock_data'. Генерируй ТОЛЬКО чистый SQL-запрос, "
            f"не добавляй объяснений, знаков препинания или кавычек.\n"
            f"1. Запрос должен ВСЕГДА выбирать колонки **date** и **close**.\n"
            f"2. Фильтруйте по 'brand_name' (ИЛИ 'ticker', если указан) и по 'date' (используйте BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD').\n"
            f"3. **ОБЯЗАТЕЛЬНО** сортируйте результат по date (ASC).\n"
            f"СТРУКТУРА БД: {db_schema}"
        )

        # Вызов модели Gemini
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=full_prompt
        )

        sql_query = response.text.strip()

        # Удаляем форматирование, если модель его добавила
        if sql_query.lower().startswith('```sql'):
            sql_query = sql_query[7:-3].strip()
        
        logging.info(f"Сгенерированный SQL (Gemini): {sql_query}")
        return sql_query

    except Exception as e:
        logging.error(f"ОШИБКА генерации SQL через Gemini: {e}")
        return f"ОШИБКА: Не удалось сгенерировать SQL-запрос. Проверьте ваш API-ключ Gemini."


def generate_analysis_text(user_request: str, df_data: pd.DataFrame, stats: dict) -> str:
    """Использует Gemini для генерации аналитического разбора."""

    # Форматируем статистику для промпта
    stats_str = "\n".join([f"- {k}: {v:.2f}" for k, v in stats.items()])

    prompt = (
        f"Пользователь запросил анализ данных: '{user_request}'.\n"
        "Предоставлены следующие статистические данные:\n"
        f"{stats_str}\n"
        "Начальная цена: {:.2f}, Конечная цена: {:.2f}.\n"
        "Напишите краткий аналитический разбор (не более 4-5 предложений) для ответа боту.\n"
        "Сфокусируйтесь на росте/падении, общей волатильности и основных выводах за период. НЕ упоминайте SQL или БД."
        .format(df_data['close'].iloc[0], df_data['close'].iloc[-1])
    )

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=prompt,
            # Дополнительные настройки для творческого текста
            config={"temperature": 0.5} 
        )
        return response.text.strip()
    
    except Exception as e:
        logging.error(f"ОШИБКА генерации аналитики через Gemini: {e}")
        return "❌ Ошибка: Не удалось сгенерировать аналитический текст. Проверьте ваш API-ключ Gemini."

# --- Обработчики команд Telegram ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start."""
    await update.message.reply_text(
        "👋 Привет! Я бот для анализа цен акций технологических компаний за 2024 год.\n"
        "Спросите меня что-нибудь на естественном языке, например:\n"
        "\"Покажи график цен Apple за март\"\n"
        "\"Сделай анализ за первое полугодие Microsoft\""
    )


async def analyze_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Основной обработчик текстовых сообщений."""
    user_request = update.message.text
    chat_id = update.message.chat_id

    # 1. Защита от слишком длинных запросов
    if len(user_request) > 150:
        await update.message.reply_text("❌ Пожалуйста, сформулируйте запрос короче.")
        return

    await update.message.reply_text("🔎 Анализирую ваш запрос... Пожалуйста, подождите.")

    # 2. Запрос к Gemini для генерации SQL-запроса
    try:
        sql_query = generate_sql_query(user_request)
    except Exception as e:
        logging.error(f"Ошибка генерации SQL: {e}")
        await update.message.reply_text(
            "❌ Извините, не удалось интерпретировать ваш запрос в SQL-запрос. Попробуйте еще раз.")
        return

    # 3. Выполнение SQL-запроса
    df_data = execute_db_query(sql_query)

    if df_data is None or df_data.empty:
        # Проверяем на ошибку API Gemini (если функция вернула строку с ошибкой)
        if sql_query.startswith("ОШИБКА:"):
            await update.message.reply_text(sql_query)
        else:
            await update.message.reply_text(
                "⚠️ По вашему запросу не найдено данных или произошла ошибка в БД.\n"
                "Убедитесь, что вы запрашиваете акции технологических компаний за 2024 год, используя тикер (MSFT) или название (Microsoft)."
            )
        return

    # 4. Генерация статистики, графика и аналитики
    await update.message.reply_text("📈 Данные получены. Готовлю аналитику и график...")

    # Преобразование даты в datetime, если это не сделано
    if 'date' in df_data.columns:
        df_data['date'] = pd.to_datetime(df_data['date'])
        df_data = df_data.sort_values(by='date') # Сортировка данных по дате

    # Расчет базовой статистики
    if 'close' not in df_data.columns or df_data.empty:
        await update.message.reply_text("⚠️ Ошибка: В полученных данных нет колонки 'close' для анализа.")
        return

    stats = {
        "Средняя цена": df_data['close'].mean(),
        "Минимальная цена": df_data['close'].min(),
        "Максимальная цена": df_data["close"].max(),
        "Изменение (начало-конец)": df_data['close'].iloc[-1] - df_data['close'].iloc[0],
    }

    # Генерация графика
    photo_file = generate_chart(df_data, user_request)

    # Генерация аналитического текста через Gemini
    analysis_text = generate_analysis_text(user_request, df_data, stats)

    # 5. Отправка результатов
    await context.bot.send_photo(chat_id=chat_id, photo=photo_file)
    await update.message.reply_text(analysis_text)


# --- Функция для генерации графика ---

def generate_chart(df_data: pd.DataFrame, title: str) -> io.BytesIO:
    """Генерирует график и возвращает его в виде файла в памяти."""

    plt.style.use('seaborn-v0_8-whitegrid')
    fig, ax = plt.subplots(figsize=(10, 6))

    # Имя тикера для легенды и заголовка
    ticker = df_data['ticker'].iloc[0] if 'ticker' in df_data.columns and not df_data['ticker'].empty else 'Акции'

    ax.plot(df_data['date'], df_data['close'], marker='o', linestyle='-', color='#0077c9', markersize=3,
            label=f'{ticker} Цена закрытия')

    # Настройка осей и заголовка
    ax.set_title(
        f"Динамика цен: {ticker} ({df_data['date'].min().strftime('%Y-%m-%d')} - {df_data['date'].max().strftime('%Y-%m-%d')})",
        fontsize=14, fontweight='bold')
    ax.set_xlabel("Дата", fontsize=12)
    ax.set_ylabel("Цена закрытия (USD)", fontsize=12)

    # Форматирование оси X (даты)
    fig.autofmt_xdate(rotation=45)

    ax.legend()

    # Сохранение графика в памяти (в виде байтового потока)
    buffer = io.BytesIO()
    plt.savefig(buffer, format='png', bbox_inches='tight')
    buffer.seek(0)
    plt.close(fig)

    return buffer


# --- Основная функция запуска бота ---

def main() -> None:
    """Запускает бота."""
    # Создание Application и передача токена
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Обработчики команд и сообщений
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, analyze_message))

    # Запуск бота
    print("Бот запущен. Откройте Telegram и начните диалог.")
    application.run_polling(poll_interval=1.0)


if __name__ == '__main__':
    # На Render переменные среды уже установлены.
    # На локальной машине этот код запускается напрямую
    # Для целей Render этот блок можно упростить:
    main()
