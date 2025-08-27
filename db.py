from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite

DB_FILE = "telegram_data.db"

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            phone TEXT,
            last_updated TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            chat_id INTEGER,
            chat_name TEXT,
            slug TEXT,
            chat_type TEXT,
            UNIQUE(user_id, chat_id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            msg_id INTEGER,
            sender TEXT,
            out BOOLEAN,
            text TEXT,
            media_path TEXT,
            time_str TEXT,
            is_read BOOLEAN DEFAULT 0,
            UNIQUE(chat_id, msg_id),
            FOREIGN KEY(chat_id) REFERENCES chats(id)
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            tg_id INTEGER UNIQUE,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """)
        await db.commit()


async def save_user_to_db(username: str, phone: str) -> int:
    async with aiosqlite.connect(DB_FILE) as db:
        try:
            tz = ZoneInfo("Europe/Kyiv")
        except Exception:
            tz = ZoneInfo("Europe/Kiev")

        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            "INSERT OR IGNORE INTO users (username, phone, last_updated) VALUES (?, ?, ?)", (username, phone, now)
        )
        await db.commit()
        async with db.execute("SELECT id FROM users WHERE username=?", (username,)) as cur:
            row = await cur.fetchone()
            return row[0]


async def save_chat_to_db(user_id: int, chat_id: int, chat_name: str, slug: str, chat_type: str) -> int:
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO chats (user_id, chat_id, chat_name, slug, chat_type) VALUES (?, ?, ?, ?, ?)",
            (user_id, chat_id, chat_name, slug, chat_type)
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM chats WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0]


async def save_message_to_db(chat_id: int, msg_id: int, sender: str, out: bool, text: str, media_path: str, time_str: str):
    async with aiosqlite.connect(DB_FILE) as db:
        # Вставляємо повідомлення
        cursor = await db.execute(
            "INSERT OR IGNORE INTO messages (chat_id, msg_id, sender, out, text, media_path, time_str) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, msg_id, sender, out, text, media_path, time_str)
        )

        # Перевіряємо, чи реально додано рядок
        if cursor.rowcount > 0:
            try:
                tz = ZoneInfo("Europe/Kyiv")
            except Exception:
                tz = ZoneInfo("Europe/Kiev")

            now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
            await db.execute("""
                UPDATE users
                SET last_updated = ?
                WHERE id = (SELECT user_id FROM chats WHERE id = ?)
            """, (now, chat_id))

        await db.commit()
