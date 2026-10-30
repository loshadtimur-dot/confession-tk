import asyncio
import html
import logging
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

# ===================== НАСТРОЙКИ =====================
BOT_TOKEN = "9160302753:AAGgVI8q1cZynCg3j5FMxB5OmRll08DdsII"

# Telegram ID тебя и твоих друзей (узнать у @userinfobot).
# Добавляй сколько нужно, через запятую.
ADMIN_IDS = [6263900255]

CHANNEL = "@tk_confession_real"
DB_PATH = "bot.db"
# =====================================================

WELCOME_TEXT = (
    "👋 <b>Добро пожаловать в ТК анонимные сообщения!</b>\n\n"
    "Пишите сюда любое сообщение или отправляйте фото — "
    f"всё будет появляться в канале {CHANNEL} без указания вашего имени."
)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# ---------- База (чтобы кнопки работали и после перезапуска) ----------
db = sqlite3.connect(DB_PATH)
db.execute(
    "CREATE TABLE IF NOT EXISTS submissions ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "status TEXT NOT NULL DEFAULT 'pending')"
)
db.execute(
    "CREATE TABLE IF NOT EXISTS copies ("
    "sub_id INTEGER, chat_id INTEGER, message_id INTEGER)"
)
db.commit()


def admin_keyboard(sub_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"publish:{sub_id}"),
                InlineKeyboardButton(text="🗑 Отклонить", callback_data=f"reject:{sub_id}"),
            ]
        ]
    )


def status_keyboard(text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data="noop")]]
    )


async def close_for_all(sub_id: int, text: str):
    """Заменяет кнопки у всех админов на статус (чтобы никто не опубликовал дважды)."""
    rows = db.execute(
        "SELECT chat_id, message_id FROM copies WHERE sub_id = ?", (sub_id,)
    ).fetchall()
    for chat_id, message_id in rows:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=status_keyboard(text),
            )
        except Exception as e:
            logging.warning("Не удалось обновить кнопки у %s: %s", chat_id, e)


# ---------- /start ----------
@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(WELCOME_TEXT)


# ---------- Сообщения от обычных людей ----------
@dp.message(F.chat.type == "private", ~F.from_user.id.in_(ADMIN_IDS))
async def from_user(message: Message):
    u = message.from_user
    full_name = html.escape(u.full_name)
    username = f"@{html.escape(u.username)}" if u.username else "нет username"

    header = (
        "📩 <b>Новое анонимное сообщение</b>\n\n"
        f"👤 Отправил: <b>{full_name}</b>\n"
        f"🔗 Username: {username}\n"
        f"🆔 ID: <code>{u.id}</code>"
    )

    sub_id = db.execute("INSERT INTO submissions (status) VALUES ('pending')").lastrowid
    delivered = 0

    # Рассылаем всем админам (тебе и друзьям)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, header)
            copied = await message.copy_to(admin_id, reply_markup=admin_keyboard(sub_id))
            db.execute(
                "INSERT INTO copies (sub_id, chat_id, message_id) VALUES (?, ?, ?)",
                (sub_id, admin_id, copied.message_id),
            )
            delivered += 1
        except Exception as e:
            # например, друг ещё не нажал /start в боте
            logging.warning("Не удалось отправить админу %s: %s", admin_id, e)
    db.commit()

    if delivered:
        await message.answer("✅ Ваше сообщение отправлено!")
    else:
        await message.answer("⚠️ Не удалось отправить сообщение. Попробуйте позже.")


# ---------- Если пишет кто-то из админов ----------
@dp.message(F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
async def from_admin(message: Message):
    await message.answer(
        "Ты админ 😎 Сюда будут приходить сообщения от людей.\n"
        "Под каждым будут кнопки «Опубликовать» и «Отклонить»."
    )


# ---------- Кнопки админов ----------
@dp.callback_query(F.data.startswith("publish:"), F.from_user.id.in_(ADMIN_IDS))
async def publish(call: CallbackQuery):
    sub_id = int(call.data.split(":")[1])

    # Забираем сообщение себе: если кто-то уже нажал — второй раз не сработает
    cur = db.execute(
        "UPDATE submissions SET status = 'published' WHERE id = ? AND status = 'pending'",
        (sub_id,),
    )
    db.commit()
    if cur.rowcount == 0:
        await call.answer("Это сообщение уже обработано другим админом", show_alert=True)
        return

    try:
        await bot.copy_message(
            chat_id=CHANNEL,
            from_chat_id=call.message.chat.id,
            message_id=call.message.message_id,
        )
    except Exception as e:
        logging.exception("Ошибка публикации: %s", e)
        db.execute("UPDATE submissions SET status = 'pending' WHERE id = ?", (sub_id,))
        db.commit()
        await call.answer("Ошибка! Бот добавлен в канал админом?", show_alert=True)
        return

    await close_for_all(sub_id, f"✅ Опубликовал(а): {call.from_user.first_name}")
    await call.answer("Опубликовано в канале ✅")


@dp.callback_query(F.data.startswith("reject:"), F.from_user.id.in_(ADMIN_IDS))
async def reject(call: CallbackQuery):
    sub_id = int(call.data.split(":")[1])

    cur = db.execute(
        "UPDATE submissions SET status = 'rejected' WHERE id = ? AND status = 'pending'",
        (sub_id,),
    )
    db.commit()
    if cur.rowcount == 0:
        await call.answer("Это сообщение уже обработано другим админом", show_alert=True)
        return

    await close_for_all(sub_id, f"🗑 Отклонил(а): {call.from_user.first_name}")
    await call.answer("Отклонено")


@dp.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
