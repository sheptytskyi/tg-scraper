import os
import re
import mimetypes
from telethon.tl.types import User, Chat, Channel, Message

# Папки для медіа всередині профілю
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
    <style>
        body {{
            background-color: #eaeaea;
            font-family: 'Segoe UI', sans-serif;
            padding: 20px;
        }}
        .chat-container {{
            max-width: 800px;
            margin: auto;
            background: white;
            border-radius: 10px;
            padding: 20px;
        }}
        .dialog-title {{
            font-weight: bold;
            font-size: 18px;
            margin-top: 30px;
            padding-bottom: 4px;
            border-bottom: 1px solid #ccc;
        }}
        .message {{
            display: flex;
            margin: 10px 0;
        }}
        .message-in .bubble {{
            background-color: #fff;
            margin-right: auto;
        }}
        .message-out .bubble {{
            background-color: #dcf8c6;
            margin-left: auto;
        }}
        .bubble {{
            padding: 10px 14px;
            border-radius: 18px;
            max-width: 70%;
            font-size: 14px;
            line-height: 1.4;
        }}
        .meta {{
            font-size: 10px;
            color: #999;
            text-align: right;
            margin-top: 4px;
        }}
        img, video, audio {{
            max-width: 100%;
            border-radius: 8px;
            margin: 5px 0;
        }}
        a {{
            color: #007bff;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
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

    url_path = local_path.replace("\\", "/")  # заміна для Windows
    file_name = os.path.basename(local_path)

    # Абсолютний URL для доступу через браузер:
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

    # Документ
    if hasattr(media, 'document'):
        doc = media.document
        size = doc.size or 0
        mime = (doc.mime_type or "").lower()

        # Визначення папки
        if mime.startswith("audio/"):
            folder_name = VOICE_DIR
            ext = mimetypes.guess_extension(mime) or ".oga"
            filename = f"{message.id}{ext}"
        else:
            if size <= 50 * 1024 * 1024:
                folder_name = MEDIA_UNDER_50_DIR
            else:
                folder_name = MEDIA_PLUS_50_DIR

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

        return os.path.join(folder_name, filename)  # повертаємо відносний шлях

    return None


async def format_message_with_media(message: Message, from_me: bool, sender: str, base_folder: str, username: str):
    time_str = message.date.strftime("%H:%M")
    text_html = ""
    if message.text:
        text_html = sanitize_text(message.text).replace("\n", "<br>")

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


async def export_user_chat_html(client, me, folder: str):
    username = os.path.basename(folder)
    chats_folder = os.path.join(folder, "chats")
    os.makedirs(chats_folder, exist_ok=True)

    index_path = os.path.join(folder, "index.html")
    chat_links = []

    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if not isinstance(entity, (User, Chat, Channel)):
            continue

        chat_name = getattr(entity, "title", None) or getattr(entity, "first_name", None)
        if not chat_name:
            continue

        chat_slug = slugify(f"{chat_name}_{entity.id}")
        chat_filename = f"{chat_slug}.html"
        chat_path = os.path.join(chats_folder, chat_filename)

        with open(chat_path, "w", encoding="utf-8") as chat_file:
            chat_file.write(generate_chat_html_header(chat_name))
            async for msg in client.iter_messages(entity, limit=100):
                from_me = msg.out
                sender = "You" if from_me else str(msg.sender_id)
                chat_file.write(await format_message_with_media(msg, from_me, sender, folder, username))
            chat_file.write(generate_chat_html_footer())

        chat_links.append(f"<li><a href='chats/{chat_filename}'>{chat_name}</a></li>")

    with open(index_path, "w", encoding="utf-8") as index:
        index.write(f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Telegram Chat List - {me.first_name}</title>
    <style>
        body {{
            background: #f4f4f4;
            font-family: 'Segoe UI', sans-serif;
            padding: 40px;
        }}
        .container {{
            max-width: 800px;
            margin: auto;
            background: #fff;
            border-radius: 10px;
            padding: 30px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }}
        h1 {{
            margin-top: 0;
        }}
        ul {{
            list-style: none;
            padding: 0;
        }}
        li {{
            padding: 12px 0;
            border-bottom: 1px solid #eee;
        }}
        a {{
            text-decoration: none;
            color: #007bff;
            font-size: 18px;
        }}
        a:hover {{
            text-decoration: underline;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Telegram Chats for {me.first_name}</h1>
        <ul>
            {''.join(chat_links)}
        </ul>
    </div>
</body>
</html>""")


async def update_user_data(client, me, folder):
    """
    Послідовно оновлюємо дані з пріоритетом:
    контакти -> чати -> медіа <=50МБ -> голосові -> медіа >50МБ
    """

    # 1. Контакти (з номерами)
    await save_contacts_with_phone(client, folder)

    # 2. Чати та повідомлення
    await export_user_chat_html(client, me, folder)



async def save_contacts_with_phone(client, folder):
    contacts_path = os.path.join(folder, "contacts.txt")
    with open(contacts_path, "w", encoding="utf-8") as f:
        # 1) Отримуємо контакти з client.iter_contacts()
        async for contact in client.iter_contacts():
            if isinstance(contact, User):
                phone = getattr(contact, "phone", "N/A")
                line = f"{contact.first_name or ''} {contact.last_name or ''} (@{contact.username or 'no_username'}) - ID: {contact.id} - Phone: {phone}\n"
                f.write(line)

        # 2) Також додатково пробігаємось по діалогах, щоб не пропустити контакти, яких немає в списку контактів
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, User):
                phone = getattr(entity, "phone", "N/A")
                line = f"{entity.first_name or ''} {entity.last_name or ''} (@{entity.username or 'no_username'}) - ID: {entity.id} - Phone: {phone}\n"
                f.write(line)




