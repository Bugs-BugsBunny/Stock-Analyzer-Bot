import pandas as pd
import psycopg2

# 1. Параметры подключения
DB_NAME = "telegram_bot_db"
DB_USER = "postgres"
DB_PASSWORD = "MKportal1587*"  # !!! Замените на реальный пароль пользователя postgres
DB_HOST = "localhost"

# Имя вашего CSV-файла
CSV_FILE_NAME = 'filtered_tech_stocks_2024.csv'

# 2. Чтение и Фильтрация данных с помощью pandas
try:
    print(f"Загрузка файла: {CSV_FILE_NAME}...")
    df = pd.read_csv(CSV_FILE_NAME)

    # Преобразуем колонку 'Date' в формат даты и извлекаем год
    df['Date'] = pd.to_datetime(df['Date'], errors='coerce', utc=True)
    df = df.dropna(subset=['Date'])
    df['Year_Extracted'] = df['Date'].dt.year

    # Фильтрация: Год = 2024 И Industry_Tag = 'technology'
    df_filtered = df[
        (df['Year_Extracted'] == 2024) &
        (df['Industry_Tag'] == 'technology')
        ]

    # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Выбираем все колонки ---
    # Удаляем колонку 'Year_Extracted', которую мы создали
    # ВНИМАНИЕ: Если в вашем CSV есть колонки 'Dividends', 'Stock Splits', 'Capital Gains',
    # их тип может быть float/decimal, как и Close, Open, High, Low.
    final_df = df_filtered.drop(columns=['Year_Extracted'], errors='ignore')

    if final_df.empty:
        print("ВНИМАНИЕ: После фильтрации не найдено ни одной строки.")
        exit()

    print(f"Фильтрация завершена. Найдено {len(final_df)} строк для загрузки.")

    # --- Список колонок для SQL (должен совпадать с заголовками CSV) ---
    COLUMNS = list(final_df.columns)

except Exception as e:
    print(f"КРИТИЧЕСКАЯ ОШИБКА при чтении/фильтрации данных: {e}")
    exit()

# 3. Загрузка данных в PostgreSQL
try:
    print("Подключение к PostgreSQL...")
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST
    )
    cursor = conn.cursor()

    # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Динамическое создание SQL-схемы ---

    # Создаем SQL-определение для колонок (Date - DATE, Ticker - VARCHAR, остальные - DECIMAL/FLOAT)
    column_definitions = []
    for col in COLUMNS:
        if col in ['Date']:
            # Дата/время
            column_definitions.append(f'"{col}" TIMESTAMP WITH TIME ZONE')
        elif col in ['Brand_Name', 'Ticker', 'Industry_Tag', 'Country']:
            # Текстовые колонки
            column_definitions.append(f'"{col}" VARCHAR(100)')
        elif col in ['Volume']:
            # Целые числа (или BIGINT)
            column_definitions.append(f'"{col}" BIGINT')
        else:
            # Цены и дивиденды (FLOAT/DECIMAL)
            column_definitions.append(f'"{col}" DECIMAL')

    sql_columns_def = ", ".join(column_definitions)
    sql_columns_names = ", ".join([f'"{c}"' for c in COLUMNS])
    sql_placeholders = ", ".join(['%s'] * len(COLUMNS))

    # Создание таблицы (динамически!)
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS stock_data ({sql_columns_def});
    """)
    conn.commit()
    # -------------------------------------------------------------

    # Запись данных
    print("Начало записи данных в таблицу stock_data...")
    for index, row in final_df.iterrows():
        # Подготавливаем значения для вставки
        values = [row[col] for col in COLUMNS]

        # Вставка
        cursor.execute(f"""
            INSERT INTO stock_data ({sql_columns_names})
            VALUES ({sql_placeholders});
        """, values)

    conn.commit()
    print("\n✅ УСПЕХ: Данные успешно загружены в таблицу stock_data.")

except Exception as e:
    print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА при работе с базой данных: {e}")
    print("Проверьте: 1. Пароль. 2. Имя БД. 3. Запущен ли сервер PostgreSQL.")
finally:
    if 'conn' in locals() and conn:
        conn.close()