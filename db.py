import aiosqlite
import os

DB_FILE = "telegram_data.db"

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            phone TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            chat_id INTEGER,
            chat_name TEXT,
            slug TEXT,
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
            FOREIGN KEY(chat_id) REFERENCES chats(id)
        )
        """)
        await db.commit()


async def save_user_to_db(username: str, phone: str) -> int:
    print('save user to db')
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (username, phone) VALUES (?, ?)", (username, phone)
        )
        await db.commit()
        async with db.execute("SELECT id FROM users WHERE username=?", (username,)) as cur:
            row = await cur.fetchone()
            return row[0]


async def save_chat_to_db(user_id: int, chat_id: int, chat_name: str, slug: str) -> int:
    print('save chat to db')
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO chats (user_id, chat_id, chat_name, slug) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, chat_name, slug)
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM chats WHERE user_id=? AND chat_id=?", (user_id, chat_id)
        ) as cur:
            row = await cur.fetchone()
            return row[0]


async def save_message_to_db(chat_id: int, msg_id: int, sender: str, out: bool, text: str, media_path: str, time_str: str):
    print('save msg to db')
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (chat_id, msg_id, sender, out, text, media_path, time_str) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, msg_id, sender, out, text, media_path, time_str)
        )
        await db.commit()
