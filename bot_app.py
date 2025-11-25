import os
import io
import time
import logging
import pandas as pd
import psycopg2
import matplotlib.pyplot as plt
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI

# -----------------------------------------------------------
# 1. ТОКЕНЫ И НАСТРОЙКИ (Считываем из переменных среды)
# -----------------------------------------------------------
# Render автоматически предоставит эти значения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

DB_NAME = "telegram_bot_db"
DB_USER = "postgres"
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_HOST = "localhost"
# -----------------------------------------------------------

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Инициализация клиента OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)


# --- Вспомогательные функции для работы с БД ---

def execute_db_query(query: str, fetch_results=True):
    """Выполняет SQL-запрос и возвращает данные, если необходимо."""
    conn = None
    try:
        conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST)
        cursor = conn.cursor()
        cursor.execute(query)

        if fetch_results:
            # Извлекаем данные и имена колонок
            data = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return pd.DataFrame(data, columns=columns)
        else:
            # Для INSERT/UPDATE/CREATE
            conn.commit()
            return None

    except Exception as e:
        logging.error(f"Ошибка выполнения SQL: {e}")
        # Возвращаем пустой DataFrame, чтобы избежать сбоя
        return pd.DataFrame() if fetch_results else None
    finally:
        if conn:
            conn.close()


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

    # 2. Запрос к OpenAI для генерации SQL-запроса
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
        await update.message.reply_text(
            "⚠️ По вашему запросу не найдено данных или произошла ошибка в БД.\n"
            "Убедитесь, что вы запрашиваете акции технологических компаний за 2024 год, используя тикер (MSFT) или название (Microsoft)."
        )
        return

    # 4. Генерация статистики, графика и аналитики
    await update.message.reply_text("📈 Данные получены. Готовлю аналитику и график...")

    # Расчет базовой статистики (если данные есть)
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

    # Генерация аналитического текста через OpenAI
    analysis_text = generate_analysis_text(user_request, df_data, stats)

    # 5. Отправка результатов
    await context.bot.send_photo(chat_id=chat_id, photo=photo_file)
    await update.message.reply_text(analysis_text)


# --- Функции, использующие OpenAI ---

def generate_sql_query(user_request: str) -> str:
    """Использует OpenAI для преобразования запроса в SQL-запрос."""
    # ОЧЕНЬ ВАЖНО: Подробный промпт для LLM!
    prompt = (
        "Вы эксперт по SQL для PostgreSQL. Ваша задача - преобразовать запрос пользователя "
        f"('{user_request}') в ОДИН корректный SQL-запрос.\n"
        "База данных: 'telegram_bot_db'. Таблица: 'stock_data'.\n"
        "Колонки таблицы (ВАЖНО): date (TIMESTAMP), close (DECIMAL), brand_name (VARCHAR), ticker (VARCHAR), open (DECIMAL), high (DECIMAL), low (DECIMAL), volume (BIGINT), industry_tag (VARCHAR), country (VARCHAR), dividends (DECIMAL), stock splits (DECIMAL), capital gains (DECIMAL).\n"
        "1. Запрос должен ВСЕГДА выбирать колонки **date** и **close**.\n"
        "2. Фильтруйте по 'brand_name' (ИЛИ 'ticker', если указан) и по 'date' (используйте BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD').\n"
        "3. **ОБЯЗАТЕЛЬНО** сортируйте результат по date (ASC).\n"
        "4. **ОБЯЗАТЕЛЬНО** верните ТОЛЬКО чистый SQL-запрос без объяснений, знаков препинания или кавычек.\n"
        "ПРИМЕР: 'SELECT date, close FROM stock_data WHERE brand_name = 'Apple Inc.' AND date BETWEEN '2024-03-01' AND '2024-03-31' ORDER BY date ASC;'"
    )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0  # Низкая температура для предсказуемости
    )

    sql_text = response.choices[0].message.content.strip()
    logging.info(f"Сгенерированный SQL: {sql_text}")
    return sql_text


def generate_analysis_text(user_request: str, df_data: pd.DataFrame, stats: dict) -> str:
    """Использует OpenAI для генерации аналитического разбора."""

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

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5  # Средняя температура для творческого анализа
    )

    return response.choices[0].message.content.strip()


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

    # Запуск бота (будет работать до тех пор, пока вы его не остановите)
    print("Бот запущен. Откройте Telegram и начните диалог.")
    application.run_polling(poll_interval=1.0)


if __name__ == '__main__':
    # Установка API-ключа OpenAI в переменную среды (для безопасности)
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
    main()