import asyncio
import os
import re
import mimetypes

import aiosqlite
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import User, Message, Channel, Chat
from db import save_user_to_db, save_chat_to_db, save_message_to_db, DB_FILE, get_user_session

VOICE_DIR = "voices"
PHOTO_DIR = "photos"
MEDIA_UNDER_50_DIR = "media_under_50"
MEDIA_PLUS_50_DIR = "media_plus_50"

def slugify(text: str) -> str:
    return re.sub(r'\W+', '_', text).strip("_").lower()


async def save_media(message: Message, base_folder: str):
    if not message.media:
        return None
    media = message.media
    if hasattr(media, 'photo'):
        filename = f"{message.id}_photo.jpg"
        folder = os.path.join(base_folder, PHOTO_DIR)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        await message.download_media(file=path)
        return os.path.join(PHOTO_DIR, filename)

    if hasattr(media, 'document'):
        doc = media.document
        size = doc.size or 0
        mime = (doc.mime_type or "").lower()

        if mime.startswith("audio/"):
            folder_name = VOICE_DIR
            ext = mimetypes.guess_extension(mime) or ".oga"
            filename = f"{message.id}{ext}"
        else:
            folder_name = MEDIA_UNDER_50_DIR if size <= 50*1024*1024 else MEDIA_PLUS_50_DIR
            filename = None
            for attr in doc.attributes:
                if hasattr(attr, 'file_name'):
                    filename = attr.file_name
                    break
            if not filename:
                filename = f"{message.id}_{doc.id}"
        folder = os.path.join(base_folder, folder_name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, filename)
        await message.download_media(file=path)
        return os.path.join(folder_name, filename)
    return None

async def update_user_data(client, me, folder):
    username = os.path.basename(folder)
    phone = getattr(me, "phone", "")
    user_id = await save_user_to_db(username, phone)
    await save_contacts_with_phone(client, user_id)

    async for dialog in client.iter_dialogs():
        entity = await client.get_entity(dialog.id)
        if not hasattr(entity, "id"):
            continue

        chat_type = None

        # приватний чат
        if isinstance(entity, User):
            if getattr(entity, "bot", False):
                continue
            if entity.username and entity.username.lower().endswith("bot"):
                continue
            if entity.id in {777000, 1087968824, 136817688}:  # службові акаунти
                continue
            chat_type = "private"

        # приватна група (звичайна)
        elif isinstance(entity, Chat):
            chat_type = "group"

        # приватна супергрупа
        elif isinstance(entity, Channel):
            if entity.megagroup and not entity.broadcast and not entity.username:
                chat_type = "group"
            else:
                continue

        else:
            continue

        chat_name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or "Unknown"
        chat_slug = slugify(f"{chat_name}_{entity.id}")
        chat_id_db = await save_chat_to_db(user_id, entity.id, chat_name, chat_slug, chat_type)

        batch_size = 50
        tasks = []
        count = 0

        async for msg in client.iter_messages(entity, limit=None):
            tasks.append(process_and_save_message(msg, chat_id_db, folder))
            count += 1
            if count % batch_size == 0:
                await asyncio.gather(*tasks)
                tasks = []

        if tasks:
            await asyncio.gather(*tasks)


async def process_and_save_message(msg, chat_id_db, folder):
    local_path = await save_media(msg, folder)

    if msg.out:
        sender_name = "You"
    else:
        sender = await msg.get_sender()
        if sender is None:
            sender_name = str(msg.sender_id)
        else:
            if hasattr(sender, "username") and sender.username:
                sender_name = f"@{sender.username}"
            else:
                first = getattr(sender, "first_name", "") or ""
                last = getattr(sender, "last_name", "") or ""
                sender_name = f"{first} {last}".strip() or str(sender.id)

    timestamp = msg.date.strftime("%Y-%m-%d %H:%M:%S")

    await save_message_to_db(
        chat_id_db,
        msg.id,
        sender_name,
        msg.out,
        msg.text or "",
        local_path,
        timestamp
    )

async def save_contacts_with_phone(client, user_id: int):
    seen_ids = set()

    async with aiosqlite.connect(DB_FILE) as db:
        result = await client(GetContactsRequest(hash=0))
        for contact in result.users:
            if isinstance(contact, User):
                if getattr(contact, "bot", False):
                    continue
                if contact.id in (777000, 80509513, 174183446):  # Telegram, GroupAnonymousBot, VoteBot
                    continue
                tg_id = contact.id
                if tg_id in seen_ids:
                    continue
                seen_ids.add(tg_id)

                await db.execute("""
                    INSERT OR IGNORE INTO contacts (user_id, tg_id, username, first_name, last_name, phone)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    tg_id,
                    contact.username,
                    contact.first_name,
                    contact.last_name,
                    getattr(contact, "phone", None)
                ))

        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, User):
                if getattr(entity, "bot", False):
                    continue
                if entity.id in (777000, 80509513, 174183446):  # Telegram, GroupAnonymousBot, VoteBot
                    continue
                tg_id = entity.id
                if tg_id in seen_ids:
                    continue
                seen_ids.add(tg_id)

                await db.execute("""
                    INSERT OR IGNORE INTO contacts (user_id, tg_id, username, first_name, last_name, phone)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    user_id,
                    tg_id,
                    entity.username,
                    entity.first_name,
                    entity.last_name,
                    getattr(entity, "phone", None)
                ))

        await db.commit()


async def periodic_update(user_folder, api_id, api_hash):
    while True:
        for folder_name in os.listdir(user_folder):
            folder_path = os.path.join(user_folder, folder_name)
            if not os.path.isdir(folder_path):
                continue

            session_string = await get_user_session(f"+{folder_name.split('_')[-1]}")
            client = TelegramClient(StringSession(session_string), api_id, api_hash)
            await client.connect()
            if not await client.is_user_authorized():
                print('NOT AUTH')
                await client.disconnect()
                continue

            async with aiosqlite.connect(DB_FILE) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id FROM users WHERE username = ?", (folder_name,)
                ) as cursor:
                    row = await cursor.fetchone()
                    if not row:
                        break
                    user_id = row["id"]

            async for dialog in client.iter_dialogs():
                entity = await client.get_entity(dialog.id)
                if not hasattr(entity, "id"):
                    continue

                chat_type = None
                if isinstance(entity, User):
                    if getattr(entity, "bot", False):
                        continue
                    if entity.username and entity.username.lower().endswith("bot"):
                        continue
                    if entity.id in {777000, 1087968824, 136817688}:  # службові акаунти
                        continue
                    chat_type = "private"

                elif isinstance(entity, Chat):
                    chat_type = "group"

                elif isinstance(entity, Channel):
                    if entity.megagroup and not entity.broadcast and not entity.username:
                        chat_type = "group"
                    else:
                        continue
                else:
                    continue

                chat_name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or "Unknown"
                chat_slug = slugify(f"{chat_name}_{entity.id}")
                chat_id_db = await save_chat_to_db(user_id, entity.id, chat_name, chat_slug, chat_type)

                async for msg in client.iter_messages(entity, limit=None):
                    await process_and_save_message(msg, chat_id_db, folder_path)

            await client.disconnect()
        await asyncio.sleep(60 * int(os.getenv('HOW_OFTEN_UPDATE_IN_MINUTE')))
