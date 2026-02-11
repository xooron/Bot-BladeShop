import asyncio
import logging
import time
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton, Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# --- НАСТРОЙКИ ---
TOKEN = '8240181455:AAFnYvvHSjUgTBeUin1aOLRFDLHBZzJ95rg'
ADMIN_ID = 7329843850
CRYPTO_BOT_TOKEN = "ВАШ_ТОКЕН_КРИПТОБОТА"

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# Временные БД
PRODUCTS = []  # [{'game':.., 'key_type':.., 'price':.., 'secret_code':..}]
USER_BALANCES = {}
COUPONS = {}
ORDER_HISTORY = {}
DEPOSIT_HISTORY = {}


# --- СОСТОЯНИЯ (FSM) ---
class AddKey(StatesGroup):
    game = State()
    key_type = State()  # Название ключа (например, "1 День")
    price = State()
    secret_code = State()  # Сам текст ключа, который получит юзер


class Deposit(StatesGroup):
    amount = State()


class CreateCoupon(StatesGroup):
    name, activations, reward = State(), State(), State()


class ProcessCoupon(StatesGroup):
    code = State()


# --- ФУНКЦИИ ОПЛАТЫ (CRYPTOBOT) ---
async def create_crypto_invoice(amount):
    url = "https://pay.cryptobot.pay/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    data = {"amount": amount, "currency_code": "RUB", "fiat": "RUB"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data, headers=headers) as resp:
            res = await resp.json()
            return (res["result"]["pay_url"], res["result"]["invoice_id"]) if res.get("ok") else (None, None)


async def check_crypto_invoice(invoice_id):
    url = f"https://pay.cryptobot.pay/api/getInvoices?invoice_ids={invoice_id}"
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            res = await resp.json()
            return res.get("ok") and res["result"]["items"] and res["result"]["items"][0]["status"] == "paid"


# --- КЛАВИАТУРЫ ---
def get_main_menu(uid):
    b = ReplyKeyboardBuilder()
    b.row(types.KeyboardButton(text="👤 Профиль"), types.KeyboardButton(text="🔑 Купить ключ"))
    b.row(types.KeyboardButton(text="❓ Помощь"), types.KeyboardButton(text="💬 Отзывы"))
    if uid == ADMIN_ID:
        b.row(types.KeyboardButton(text="🔑 Добавить ключ"), types.KeyboardButton(text="🎟 Создать Купон"))
    return b.as_markup(resize_keyboard=True)


def get_profile_kb():
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="История заказов", callback_data="history_orders"),
          InlineKeyboardButton(text="Активировать купон", callback_data="act_coupon"))
    b.row(InlineKeyboardButton(text="Пополнить баланс", callback_data="deposit"),
          InlineKeyboardButton(text="История пополнения", callback_data="history_deposits"))
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return b.as_markup()


# --- ОБРАБОТЧИКИ ---

@dp.message(Command("start"))
async def start(m: Message):
    uid = m.from_user.id
    for db in [USER_BALANCES, ORDER_HISTORY, DEPOSIT_HISTORY]:
        if uid not in db: db[uid] = 0 if db is USER_BALANCES else []
    await m.answer("Blade Shop - Лучший магазин софтов!", reply_markup=get_main_menu(uid))


@dp.message(F.text == "👤 Профиль")
async def profile(m: Message):
    uid = m.from_user.id
    bal = USER_BALANCES.get(uid, 0)
    await m.answer(f"👤 Имя: @{m.from_user.username}\n🆔 ID: {uid}\n💰 Баланс: {bal} руб.", reply_markup=get_profile_kb())


# --- ИСТОРИЯ ---
@dp.callback_query(F.data == "history_orders")
async def h_orders(c: CallbackQuery):
    h = ORDER_HISTORY.get(c.from_user.id, [])
    await c.message.edit_text("📦 История заказов:\n\n" + ("\n".join(h) if h else "Пусто"),
                              reply_markup=get_profile_kb())


@dp.callback_query(F.data == "history_deposits")
async def h_deps(c: CallbackQuery):
    h = DEPOSIT_HISTORY.get(c.from_user.id, [])
    await c.message.edit_text("📜 История пополнений:\n\n" + ("\n".join(h) if h else "Пусто"),
                              reply_markup=get_profile_kb())


# --- МАГАЗИН И ПОКУПКА ---
@dp.message(F.text == "🔑 Купить ключ")
async def shop(m: Message):
    kb = InlineKeyboardBuilder()
    if not PRODUCTS:
        return await m.answer("🛒 В магазине пока нет товаров.")
    for i, p in enumerate(PRODUCTS):
        kb.row(InlineKeyboardButton(text=f"{p['game']} | {p['key_type']} | {p['price']}₽", callback_data=f"show_{i}"))
    await m.answer("🛒 Выберите софт:", reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("show_"))
async def show_item(c: CallbackQuery):
    idx = int(c.data.split("_")[1])
    if idx >= len(PRODUCTS): return await c.answer("Товар уже продан!")
    item = PRODUCTS[idx]
    text = f"🎮 Игра: {item['game']}\n⚙️ Тип: {item['key_type']}\n💵 Цена: {item['price']} руб."
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💳 Купить", callback_data=f"buy_{idx}"))
    kb.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_shop"))
    await c.message.edit_text(text, reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("buy_"))
async def buy_process(c: CallbackQuery):
    uid = c.from_user.id
    idx = int(c.data.split("_")[1])
    if idx >= len(PRODUCTS): return await c.answer("Ошибка: товар уже куплен!")

    item = PRODUCTS[idx]
    price = int(item['price'])

    if USER_BALANCES.get(uid, 0) >= price:
        USER_BALANCES[uid] -= price
        secret = item['secret_code']
        # Удаляем товар из магазина (т.к. ключ продан)
        PRODUCTS.pop(idx)

        date = time.strftime("%d.%m %H:%M")
        ORDER_HISTORY[uid].append(f"✅ {date} | {item['game']} - {price}₽")

        await c.message.edit_text(
            f"🎁 Покупка завершена!\n\n🎮 Игра: {item['game']}\n🔑 Ваш ключ: `{secret}`\n\nСпасибо за покупку!",
            parse_mode="Markdown")
    else:
        await c.answer("❌ Недостаточно средств!", show_alert=True)


@dp.callback_query(F.data == "back_to_shop")
async def b_shop(c: CallbackQuery):
    await c.message.delete()
    await shop(c.message)


# --- АДМИНКА (ДОБАВЛЕНИЕ КЛЮЧА - 4 ЭТАПА) ---
@dp.message(F.text == "🔑 Добавить ключ")
async def add_1(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await m.answer("1️⃣ Введите название Игры:")
    await state.set_state(AddKey.game)


@dp.message(AddKey.game)
async def add_2(m: Message, state: FSMContext):
    await state.update_data(game=m.text)
    await m.answer("2️⃣ Введите описание ключа (например: 1 День / Private):")
    await state.set_state(AddKey.key_type)


@dp.message(AddKey.key_type)
async def add_3(m: Message, state: FSMContext):
    await state.update_data(key_type=m.text)
    await m.answer("3️⃣ Введите Цену (только число):")
    await state.set_state(AddKey.price)


@dp.message(AddKey.price)
async def add_4(m: Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Введите число!")
    await state.update_data(price=m.text)
    await m.answer("4️⃣ Теперь введите сам СЕКРЕТНЫЙ КЛЮЧ (код), который получит покупатель:")
    await state.set_state(AddKey.secret_code)


@dp.message(AddKey.secret_code)
async def add_5(m: Message, state: FSMContext):
    data = await state.get_data()
    PRODUCTS.append({
        "game": data['game'],
        "key_type": data['key_type'],
        "price": data['price'],
        "secret_code": m.text  # Сохраняем код
    })
    await m.answer(f"✅ Товар `{data['game']}` успешно добавлен в магазин!", reply_markup=get_main_menu(m.from_user.id))
    await state.clear()


# --- ЛОГИКА ПОПОЛНЕНИЯ (CRYPTBOT) ---
@dp.callback_query(F.data == "deposit")
async def dep_1(c: CallbackQuery, state: FSMContext):
    await c.message.edit_text("💰 Сумма пополнения (от 100 руб):")
    await state.set_state(Deposit.amount)


@dp.message(Deposit.amount)
async def dep_2(m: Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Число!")
    amt = int(m.text)
    url, inv_id = await create_crypto_invoice(amt)
    if not url: return await m.answer("Ошибка CryptoBot!")
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="💎 Оплатить", url=url))
    kb.row(InlineKeyboardButton(text="✅ Проверить", callback_data=f"check_cb_{inv_id}_{amt}"))
    await m.answer(f"Счет на {amt}₽ создан:", reply_markup=kb.as_markup())
    await state.clear()


@dp.callback_query(F.data.startswith("check_cb_"))
async def verify(c: CallbackQuery):
    _, _, inv_id, amt = c.data.split("_")
    if await check_crypto_invoice(inv_id):
        USER_BALANCES[c.from_user.id] += int(amt)
        DEPOSIT_HISTORY[c.from_user.id].append(f"✅ {time.strftime('%d.%m')} | +{amt}₽")
        await c.message.edit_text(f"✅ Зачислено {amt} руб.!")
    else:
        await c.answer("Не оплачено!", show_alert=True)


# --- КУПОНЫ ---
@dp.message(F.text == "🎟 Создать Купон")
async def cp_1(m: Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await m.answer("Название купона:")
    await state.set_state(CreateCoupon.name)


@dp.message(CreateCoupon.name)
async def cp_2(m: Message, state: FSMContext):
    await state.update_data(name=m.text.upper())
    await m.answer("Кол-во активаций:")
    await state.set_state(CreateCoupon.activations)


@dp.message(CreateCoupon.activations)
async def cp_3(m: Message, state: FSMContext):
    await state.update_data(act=int(m.text))
    await m.answer("Сумма награды:")
    await state.set_state(CreateCoupon.reward)


@dp.message(CreateCoupon.reward)
async def cp_4(m: Message, state: FSMContext):
    d = await state.get_data()
    COUPONS[d['name']] = {"reward": int(m.text), "act": d['act'], "users": []}
    await m.answer(f"✅ Купон `{d['name']}` создан!")
    await state.clear()


@dp.callback_query(F.data == "act_coupon")
async def act_c(c: CallbackQuery, state: FSMContext):
    await c.message.edit_text("🎫 Введите купон:")
    await state.set_state(ProcessCoupon.code)


@dp.message(ProcessCoupon.code)
async def pr_c(m: Message, state: FSMContext):
    code, uid = m.text.upper(), m.from_user.id
    if code in COUPONS and uid not in COUPONS[code]['users'] and COUPONS[code]['act'] > 0:
        rew = COUPONS[code]['reward']
        USER_BALANCES[uid] += rew
        COUPONS[code]['act'] -= 1
        COUPONS[code]['users'].append(uid)
        DEPOSIT_HISTORY[uid].append(f"🎫 {time.strftime('%d.%m')} | +{rew}₽")
        await m.answer(f"✅ +{rew} руб.!")
    else:
        await m.answer("❌ Ошибка купона!")
    await state.clear()


@dp.callback_query(F.data == "back_to_main")
async def back(c: CallbackQuery):
    await c.message.delete()
    await c.message.answer("Главное меню", reply_markup=get_main_menu(c.from_user.id))


async def main(): await dp.start_polling(bot)


if __name__ == "__main__": asyncio.run(main())