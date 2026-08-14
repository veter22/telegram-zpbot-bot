import os
import sqlite3
import csv
import asyncio
import io
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify

# Графики
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from aiogram import Bot, Dispatcher, F, types
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
SECRET_PATH = os.getenv("WEBHOOK_SECRET")
WEBHOOK_URL = f"https://veterr.pythonanywhere.com{SECRET_PATH}"

app = Flask(__name__)

@app.route('/')
def test_page():
    return "СЕРВЕР УСПЕШНО ПОДКЛЮЧЕН К КОДУ БОТА! 🚀"

# --- БАЗА ДАННЫХ ---
DB_PATH = os.path.join(os.path.dirname(__file__), "income_v4.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS income (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL,
                    category TEXT, payment_type TEXT, rate REAL, hours REAL, date TEXT)''')
conn.commit()

# --- ИНИЦИАЛИЗАЦИЯ БОТА ---
dp = Dispatcher(storage=MemoryStorage())

class IncomeStates(StatesGroup):
    waiting_for_payment_type = State()
    waiting_for_amount = State()
    waiting_for_rate = State()
    waiting_for_hours = State()
    waiting_for_date = State()
    waiting_for_category = State()
    waiting_for_stats_date = State()
    waiting_for_delete_id = State()

# --- КЛАВИАТУРЫ ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Добавить доход")],
        [KeyboardButton(text="📊 Статистика"), KeyboardButton(text="📈 График доходов")],
        [KeyboardButton(text="🗑 Удалить запись"), KeyboardButton(text="📥 Экспорт CSV")]
    ], resize_keyboard=True
)

type_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="💰 Фиксированная сумма"), KeyboardButton(text="⏱ Почасовая оплата")],
        [KeyboardButton(text="🚫 Отмена")]
    ], resize_keyboard=True
)

date_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="☀️ Сегодня"), KeyboardButton(text="🌙 Вчера")],
        [KeyboardButton(text="🚫 Отмена")]
    ], resize_keyboard=True
)

stats_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📅 За этот месяц"), KeyboardButton(text="🌍 За все время")],
        [KeyboardButton(text="🔍 На определенную дату"), KeyboardButton(text="📂 По категориям")],
        [KeyboardButton(text="🚫 Отмена")]
    ], resize_keyboard=True
)

cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🚫 Отмена")]], resize_keyboard=True)

# --- БОТ-УБОРЩИК (ЭФФЕКТ ОДНОГО ОКНА) ---
user_last_msg = {} # Память для хранения ID последних сообщений бота

async def clean_previous(message: Message):
    user_id = message.from_user.id
    bot = message.bot
    
    # 1. Удаляем сообщение юзера (то, что он написал или нажал)
    try:
        await message.delete()
    except:
        pass
        
    # 2. Удаляем прошлое сообщение бота (очищаем экран)
    if user_id in user_last_msg:
        try:
            await bot.delete_message(chat_id=user_id, message_id=user_last_msg[user_id])
        except:
            pass

async def send_clean(message: Message, text: str, reply_markup=None):
    await clean_previous(message)
    # Отправляем новое сообщение и запоминаем его ID
    msg = await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
    user_last_msg[message.from_user.id] = msg.message_id
    return msg

# --- БАЗОВЫЕ КОМАНДЫ ---
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await send_clean(message, "Привет! Я готов к работе. Выбирай действие или просто напиши мне текст (например, '5000 Фриланс'):", reply_markup=main_kb)

@dp.message(F.text == "🚫 Отмена")
async def process_cancel(message: Message, state: FSMContext):
    await state.clear()
    await send_clean(message, "Действие отменено. Главное меню:", reply_markup=main_kb)

# --- ТЕКСТОВОЕ НАДЕЖНОЕ УДАЛЕНИЕ ЗАПИСЕЙ ---
@dp.message(F.text == "🗑 Удалить запись")
async def start_delete(message: Message, state: FSMContext):
    cursor.execute("SELECT id, amount, category, date FROM income WHERE user_id = ? ORDER BY id DESC LIMIT 5", (message.from_user.id,))
    rows = cursor.fetchall()
    if not rows:
        await send_clean(message, "Список доходов пуст.", reply_markup=main_kb)
        return

    text = "📋 <b>Последние 5 записей:</b>\n\n"
    for r in rows:
        r_id, amt, cat, d = r
        text += f"🆔 <b>{r_id}</b> | {d} | {amt:g} руб. ({cat})\n"

    text += "\nПришлите в ответ <b>🆔 ID (цифру)</b> записи, которую нужно удалить:"
    await send_clean(message, text, reply_markup=cancel_kb)
    await state.set_state(IncomeStates.waiting_for_delete_id)

@dp.message(IncomeStates.waiting_for_delete_id)
async def process_delete_id(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit():
        await send_clean(message, "Пожалуйста, введите корректный числовой ID записи:", reply_markup=cancel_kb)
        return

    record_id = int(text)

    cursor.execute("SELECT id FROM income WHERE id = ? AND user_id = ?", (record_id, message.from_user.id))
    if not cursor.fetchone():
        await send_clean(message, "Запись с таким ID не найдена в вашей базе. Попробуйте еще раз:", reply_markup=cancel_kb)
        return

    cursor.execute("DELETE FROM income WHERE id = ? AND user_id = ?", (record_id, message.from_user.id))
    conn.commit()

    await send_clean(message, f"✅ Запись №{record_id} успешно удалена!", reply_markup=main_kb)
    await state.clear()

# --- ЭКСПОРТ В CSV ---
@dp.message(F.text == "📥 Экспорт CSV")
async def export_csv(message: Message):
    cursor.execute("SELECT id, amount, category, payment_type, rate, hours, date FROM income WHERE user_id = ? ORDER BY date DESC", (message.from_user.id,))
    rows = cursor.fetchall()
    if not rows:
        await send_clean(message, "У вас пока нет данных для выгрузки.", reply_markup=main_kb)
        return

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(['ID', 'Сумма', 'Категория', 'Тип оплаты', 'Ставка в час', 'Количество часов', 'Дата'])
    writer.writerows(rows)

    bio = io.BytesIO(buf.getvalue().encode('utf-8-sig'))
    document = BufferedInputFile(bio.getvalue(), filename=f"finances_{datetime.now().strftime('%Y%m%d')}.csv")

    await clean_previous(message)
    msg = await message.answer_document(document, caption="📊 Ваша база данных готова! Откройте её в Excel.", reply_markup=main_kb)
    user_last_msg[message.from_user.id] = msg.message_id

# --- ДОБАВЛЕНИЕ ДОХОДА ---
@dp.message(F.text == "➕ Добавить доход")
async def start_add(message: Message, state: FSMContext):
    await send_clean(message, "Выберите тип оплаты:", reply_markup=type_kb)
    await state.set_state(IncomeStates.waiting_for_payment_type)

@dp.message(IncomeStates.waiting_for_payment_type, F.text.in_(["💰 Фиксированная сумма", "⏱ Почасовая оплата"]))
async def process_type(message: Message, state: FSMContext):
    await state.update_data(payment_type=message.text)
    if message.text == "💰 Фиксированная сумма":
        await send_clean(message, "Введите сумму (только число):", reply_markup=cancel_kb)
        await state.set_state(IncomeStates.waiting_for_amount)
    else:
        await send_clean(message, "Введите часовую ставку:", reply_markup=cancel_kb)
        await state.set_state(IncomeStates.waiting_for_rate)

@dp.message(IncomeStates.waiting_for_amount)
async def process_amt(message: Message, state: FSMContext):
    try:
        await state.update_data(amount=float(message.text.replace(',', '.')))
        await send_clean(message, "Когда был получен доход? (Дата вводится в формате ГГГГ-ММ-ДД).", reply_markup=date_kb)
        await state.set_state(IncomeStates.waiting_for_date)
    except:
        await send_clean(message, "Введи число!", reply_markup=cancel_kb)

@dp.message(IncomeStates.waiting_for_rate)
async def process_rate(message: Message, state: FSMContext):
    try:
        await state.update_data(rate=float(message.text.replace(',', '.')))
        await send_clean(message, "Сколько часов отработали?", reply_markup=cancel_kb)
        await state.set_state(IncomeStates.waiting_for_hours)
    except:
        await send_clean(message, "Введи число!", reply_markup=cancel_kb)

@dp.message(IncomeStates.waiting_for_hours)
async def process_hrs(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        amount = data['rate'] * float(message.text.replace(',', '.'))
        await state.update_data(amount=amount, hours=float(message.text.replace(',', '.')))
        await send_clean(message, f"Итого: {amount:g} руб.\nКогда был получен доход?", reply_markup=date_kb)
        await state.set_state(IncomeStates.waiting_for_date)
    except:
        await send_clean(message, "Введи число!", reply_markup=cancel_kb)

@dp.message(IncomeStates.waiting_for_date)
async def process_date(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == "☀️ Сегодня": target_date = datetime.now().strftime("%Y-%m-%d")
    elif text == "🌙 Вчера": target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        try: target_date = datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
        except:
            await send_clean(message, "Неверный формат. Выбери кнопку или введи ГГГГ-ММ-ДД:", reply_markup=date_kb)
            return

    await state.update_data(chosen_date=target_date)

    cursor.execute("SELECT category FROM income WHERE user_id = ? GROUP BY category ORDER BY COUNT(id) DESC LIMIT 4", (message.from_user.id,))
    top_cats = cursor.fetchall()

    builder = ReplyKeyboardBuilder()
    for cat in top_cats:
        builder.add(KeyboardButton(text=cat[0]))
    builder.add(KeyboardButton(text="🚫 Отмена"))
    builder.adjust(2)

    await send_clean(message, f"📅 Дата: {target_date}\n\nВведите категорию или выберите из частых:", reply_markup=builder.as_markup(resize_keyboard=True))
    await state.set_state(IncomeStates.waiting_for_category)

@dp.message(IncomeStates.waiting_for_category)
async def save_income(message: Message, state: FSMContext):
    data = await state.get_data()
    cursor.execute("INSERT INTO income (user_id, amount, category, payment_type, rate, hours, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (message.from_user.id, data['amount'], message.text, data.get('payment_type', '💰 Фиксированная сумма'), data.get('rate', 0), data.get('hours', 0), data['chosen_date']))
    conn.commit()
    await send_clean(message, f"✅ Успешно сохранено: {data['amount']:g} руб. ({message.text})", reply_markup=main_kb)
    await state.clear()

# --- СТАТИСТИКА И ГРАФИКИ ---
@dp.message(F.text == "📈 График доходов")
async def send_graph(message: Message):
    cursor.execute("SELECT category, SUM(amount) FROM income WHERE user_id = ? GROUP BY category", (message.from_user.id,))
    data = cursor.fetchall()
    if not data:
        await send_clean(message, "У вас пока нет записей для графика.", reply_markup=main_kb)
        return
    categories = [row[0] for row in data]
    amounts = [row[1] for row in data]
    plt.figure(figsize=(8, 6))
    plt.pie(amounts, labels=categories, autopct='%1.1f%%', startangle=140, shadow=True)
    plt.title("Распределение доходов", fontsize=14, fontweight='bold')
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight')
    buf.seek(0)
    plt.close()
    
    await clean_previous(message)
    msg = await message.answer_photo(photo=BufferedInputFile(buf.getvalue(), filename="chart.png"), caption="📊 Ваша финансовая картина:", reply_markup=main_kb)
    user_last_msg[message.from_user.id] = msg.message_id

@dp.message(F.text == "📊 Статистика")
async def stats_menu(message: Message):
    await send_clean(message, "Какую статистику показать?", reply_markup=stats_kb)

@dp.message(F.text == "📂 По категориям")
async def stats_categories(message: Message):
    cursor.execute("SELECT category, SUM(amount) FROM income WHERE user_id = ? GROUP BY category ORDER BY SUM(amount) DESC", (message.from_user.id,))
    rows = cursor.fetchall()
    if not rows:
        await send_clean(message, "У вас пока нет записей.", reply_markup=main_kb)
        return
    total = sum(row[1] for row in rows)
    text = "📊 <b>Статистика по категориям:</b>\n\n"
    for cat, amt in rows:
        percent = (amt / total) * 100 if total > 0 else 0
        text += f"▪️ <b>{cat}</b>: {amt:g} руб. <i>({percent:.1f}%)</i>\n"
    text += f"\n💰 <b>Всего заработано: {total:g} руб.</b>"
    await send_clean(message, text, reply_markup=main_kb)

@dp.message(F.text == "🌍 За все время")
async def stats_all(message: Message):
    cursor.execute("SELECT SUM(amount) FROM income WHERE user_id = ?", (message.from_user.id,))
    res = cursor.fetchone()[0] or 0
    await send_clean(message, f"Всего: {res:g} руб.", reply_markup=main_kb)

@dp.message(F.text.in_(["📅 За этот month", "📅 За этот месяц"]))
async def stats_month(message: Message):
    cursor.execute("SELECT SUM(amount) FROM income WHERE user_id = ? AND date LIKE ?", (message.from_user.id, datetime.now().strftime("%Y-%m") + "%"))
    res = cursor.fetchone()[0] or 0
    await send_clean(message, f"За этот месяц: {res:g} руб.", reply_markup=main_kb)

@dp.message(F.text == "🔍 На определенную дату")
async def stats_date_init(message: Message, state: FSMContext):
    await send_clean(message, "Введите дату в формате ГГГГ-ММ-ДД (например, 2026-07-07):", reply_markup=cancel_kb)
    await state.set_state(IncomeStates.waiting_for_stats_date)

@dp.message(IncomeStates.waiting_for_stats_date)
async def process_stats_date(message: Message, state: FSMContext):
    text = message.text.strip()
    try:
        target_date = datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except:
        await send_clean(message, "Неверный формат. Введи год-месяц-день (ГГГГ-ММ-ДД):", reply_markup=cancel_kb)
        return

    cursor.execute("SELECT SUM(amount) FROM income WHERE user_id = ? AND date = ?", (message.from_user.id, target_date))
    res = cursor.fetchone()[0] or 0
    await send_clean(message, f"📈 За {target_date} заработано: {res:g} руб.", reply_markup=main_kb)
    await state.clear()

# --- УМНЫЙ БЫСТРЫЙ ВВОД ---
@dp.message()
async def smart_fast_input(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return

    text = message.text.strip().lower()
    match = re.match(r'^([\d\.\,]+)\s+(.+)$', text)
    if match:
        amount_str = match.group(1).replace(',', '.')
        category = match.group(2).strip().capitalize()
        try:
            amount = float(amount_str)
        except:
            return

        date_val = datetime.now().strftime("%Y-%m-%d")
        if "вчера" in category.lower():
            date_val = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            category = re.compile(r'\bвчера\b', flags=re.IGNORECASE).sub('', category).strip().capitalize()

        cursor.execute("INSERT INTO income (user_id, amount, category, payment_type, rate, hours, date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (message.from_user.id, amount, category, '💰 Быстрый ввод', 0, 0, date_val))
        conn.commit()
        await send_clean(message, f"⚡ Быстрое добавление!\n✅ Сохранено: {amount:g} руб. ({category})\n📅 Дата: {date_val}", reply_markup=main_kb)
    else:
        await send_clean(message, "Я не понял команду. Выбери действие на клавиатуре или используй быстрый ввод (например: '5000 Проект').", reply_markup=main_kb)

# --- WEBHOOK И СЕРВЕР ---
@app.route(SECRET_PATH, methods=['POST'])
def webhook():
    async def process_update():
        session = AiohttpSession(proxy="http://proxy.server:3128")
        async with Bot(token=TOKEN, session=session) as temp_bot:
            update = types.Update.model_validate(request.json, context={"bot": temp_bot})
            await dp.feed_update(temp_bot, update)

    if request.json:
        asyncio.run(process_update())
    return "OK", 200