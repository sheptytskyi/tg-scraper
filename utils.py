import asyncio
import os
import re
import mimetypes

from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.types import User, Chat, Channel, Message
from db import save_user_to_db, save_chat_to_db, save_message_to_db

VOICE_DIR = "voices"
PHOTO_DIR = "photos"
MEDIA_UNDER_50_DIR = "media_under_50"
MEDIA_PLUS_50_DIR = "media_plus_50"

def sanitize_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
    )

def slugify(text: str) -> str:
    return re.sub(r'\W+', '_', text).strip("_").lower()

def generate_chat_html_header(user_name: str) -> str:
    return f"""<!DOCTYPE html>
        <html lang="en">
        <head>
        <meta charset="UTF-8">
        <title>Telegram Chat Export - {user_name}</title>
        <style>/* залишаємо стиль як раніше */</style>
        </head>
        <body>
        <div class="chat-container">
        <h2>Chat: {user_name}</h2>
    """

def generate_chat_html_footer() -> str:
    return "</div></body></html>"

def media_to_html_element(local_path: str, username: str):
    if not local_path:
        return ""
    url_path = local_path.replace("\\", "/")
    file_name = os.path.basename(local_path)
    return f'<a href="/users/{username}/{url_path}" target="_blank">{file_name}</a>'

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

async def format_message_with_media(message: Message, from_me: bool, sender: str, base_folder: str, username: str):
    time_str = message.date.strftime("%H:%M")
    text_html = sanitize_text(message.text) if message.text else ""
    media_html = ""
    local_path = await save_media(message, base_folder)
    if local_path:
        media_html = media_to_html_element(local_path, username)
    alignment = "message-out" if from_me else "message-in"
    return f"""
    <div class="message {alignment}">
        <div class="bubble">
            <div class="text">{text_html}</div>
            {media_html}
            <div class="meta">{sender}, {time_str}</div>
        </div>
    </div>
    """

async def update_user_data(client, me, folder):
    username = os.path.basename(folder)
    phone = getattr(me, "phone", "")
    await save_contacts_with_phone(client, folder)
    user_id = await save_user_to_db(username, phone)

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, (User, Chat, Channel)):
            continue
        chat_name = getattr(entity, "title", None) or getattr(entity, "first_name", None)
        chat_slug = slugify(f"{chat_name}_{entity.id}")
        chat_id_db = await save_chat_to_db(user_id, entity.id, chat_name, chat_slug)

        tasks = []
        async for msg in client.iter_messages(entity, limit=100):
            tasks.append(process_and_save_message(msg, chat_id_db, folder, username))
        if tasks:
            await asyncio.gather(*tasks)

async def process_and_save_message(msg, chat_id_db, folder, username):
    local_path = await save_media(msg, folder)
    await save_message_to_db(
        chat_id_db,
        msg.id,
        "You" if msg.out else str(msg.sender_id),
        msg.out,
        msg.text or "",
        local_path,
        msg.date.strftime("%H:%M")
    )

async def export_user_chat_html_from_db(username: str, folder: str):
    from db import DB_FILE
    import aiosqlite
    chats_folder = os.path.join(folder, "chats")
    os.makedirs(chats_folder, exist_ok=True)
    index_path = os.path.join(folder, "index.html")
    chat_links = []

    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT id, chat_name, slug FROM chats c JOIN users u ON c.user_id=u.id WHERE u.username=?", (username,)) as cur:
            async for chat_id, chat_name, slug in cur:
                chat_path = os.path.join(chats_folder, f"{slug}.html")
                async with db.execute("SELECT sender, out, text, media_path, time_str FROM messages WHERE chat_id=? ORDER BY id", (chat_id,)) as msg_cur:
                    with open(chat_path, "w", encoding="utf-8") as f:
                        f.write(generate_chat_html_header(chat_name))
                        async for sender, out, text, media_path, time_str in msg_cur:
                            alignment = "message-out" if out else "message-in"
                            media_html = media_to_html_element(media_path, username) if media_path else ""
                            f.write(f"""
                            <div class="message {alignment}">
                                <div class="bubble">
                                    <div class="text">{sanitize_text(text)}</div>
                                    {media_html}
                                    <div class="meta">{sender}, {time_str}</div>
                                </div>
                            </div>
                            """)
                        f.write(generate_chat_html_footer())
                chat_links.append(f"<li><a href='chats/{slug}.html'>{chat_name}</a></li>")

    with open(index_path, "w", encoding="utf-8") as index:
        index.write(f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Telegram Chats - {username}</title></head><body><div class="container"><h1>Telegram Chats for {username}</h1><ul>{''.join(chat_links)}</ul></div></body></html>""")


import os
from telethon.tl.types import User

async def save_contacts_with_phone(client, folder):
    os.makedirs(folder, exist_ok=True)
    contacts_path = os.path.join(folder, "contacts.txt")

    seen_ids = set()
    with open(contacts_path, "w", encoding="utf-8") as f:
        # 1) Отримуємо контакти напряму через GetContactsRequest
        result = await client(GetContactsRequest(hash=0))
        for contact in result.users:
            if isinstance(contact, User):
                phone = getattr(contact, "phone", None) or "N/A"
                line = f"{contact.first_name or ''} {contact.last_name or ''} (@{contact.username or 'no_username'}) - ID: {contact.id} - Phone: {phone}\n"
                f.write(line)
                seen_ids.add(contact.id)

        # 2) Пробігаємось по діалогах і додаємо юзерів, яких немає в контактах
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, User) and entity.id not in seen_ids:
                phone = getattr(entity, "phone", None) or "N/A"
                line = f"{entity.first_name or ''} {entity.last_name or ''} (@{entity.username or 'no_username'}) - ID: {entity.id} - Phone: {phone}\n"
                f.write(line)
                seen_ids.add(entity.id)


