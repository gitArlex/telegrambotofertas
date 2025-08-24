#!/usr/bin/env python3
import os
import sqlite3
import logging
import unicodedata
from fastapi import FastAPI, Request
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
import uvicorn

DB_PATH = "bot.db"
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- DB ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, subscribed INTEGER)")
    cur.execute("CREATE TABLE IF NOT EXISTS keywords (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, keyword TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS watched_chats (chat_id INTEGER PRIMARY KEY, title TEXT, username TEXT)")
    conn.commit()
    conn.close()

def get_conn():
    return sqlite3.connect(DB_PATH)

# ---------- Text normalization ----------
def normalize(s: str) -> str:
    if not s: return ""
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def text_contains_all_tokens(text: str, keyword: str) -> bool:
    norm_text = normalize(text)
    tokens = [t for t in normalize(keyword).split() if t]
    return all(tok in norm_text for tok in tokens)

# ---------- User operations ----------
def ensure_user(user_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, subscribed) VALUES (?, ?)", (user_id, 0))
    conn.commit(); conn.close()

def set_subscribed(user_id: int, subscribed: bool):
    ensure_user(user_id)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET subscribed=? WHERE user_id=?", (1 if subscribed else 0, user_id))
    conn.commit(); conn.close()

def get_keywords(user_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT keyword FROM keywords WHERE user_id=?", (user_id,))
    rows = [r[0] for r in cur.fetchall()]
    conn.close(); return rows

def add_keyword(user_id: int, keyword: str):
    ensure_user(user_id)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM keywords WHERE user_id=? AND keyword=?", (user_id, keyword))
    if cur.fetchone(): conn.close(); return False
    cur.execute("INSERT INTO keywords (user_id, keyword) VALUES (?,?)", (user_id, keyword))
    conn.commit(); conn.close(); return True

def del_keyword(user_id: int, keyword: str):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM keywords WHERE user_id=? AND keyword=?", (user_id, keyword))
    changes = cur.rowcount
    conn.commit(); conn.close()
    return changes > 0

def del_all_keywords(user_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM keywords WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

# ---------- Watched chats ----------
def add_watched_chat(chat):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO watched_chats (chat_id, title, username) VALUES (?,?,?)",
                (chat.id, chat.title or chat.full_name or "", chat.username or ""))
    conn.commit(); conn.close()

def list_watched_chats():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT chat_id,title,username FROM watched_chats")
    rows = cur.fetchall(); conn.close(); return rows

def remove_watched_chat(chat_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM watched_chats WHERE chat_id=?", (chat_id,))
    conn.commit(); conn.close()

def remove_all_watched_chats():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM watched_chats"); conn.commit(); conn.close()

def get_subscribed_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE subscribed=1")
    rows = [r[0] for r in cur.fetchall()]; conn.close(); return rows

# ---------- Bot setup ----------
TOKEN = os.environ.get("TELEGRAM_TOKEN")
if not TOKEN: raise RuntimeError("Defina TELEGRAM_TOKEN")
bot = Bot(TOKEN)
init_db()
app_telegram = ApplicationBuilder().bot(bot).build()

# ---------- Commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Olá! Use /help para ver comandos.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "/notifyme - receber notificações\n"
        "/removeme - parar notificações\n"
        "/addp <texto> - adicionar palavra-chave\n"
        "/listp - listar palavras-chave\n"
        "/delp <texto> - remover palavra-chave\n"
        "/delpall - apagar todas as palavras-chave\n"
        "/addgc - registrar grupo/canal\n"
        "/listgc - listar grupos/canais\n"
        "/sairgc <id|@username|nome> - sair de grupo/canal\n"
        "/sairgcall - sair de todos os grupos/canais"
    )
    await update.message.reply_text(txt)

async def notifyme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_subscribed(update.effective_user.id, True)
    await update.message.reply_text("Você foi inscrito para notificações.")

async def removeme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_subscribed(update.effective_user.id, False)
    await update.message.reply_text("Você foi removido das notificações.")

async def addp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = update.message.text.partition(" ")[2].strip()
    if not keyword: await update.message.reply_text("Uso: /addp <palavra>"); return
    ok = add_keyword(update.effective_user.id, keyword)
    await update.message.reply_text("Adicionado ✅" if ok else "Já existia ❌")

async def listp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kws = get_keywords(update.effective_user.id)
    if not kws: await update.message.reply_text("Nenhuma palavra-chave."); return
    text = "Suas palavras-chave:\n" + "\n".join(f"{i+1}- {k}" for i,k in enumerate(kws))
    await update.message.reply_text(text)

async def delp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = update.message.text.partition(" ")[2].strip()
    if not keyword: await update.message.reply_text("Uso: /delp <palavra>"); return
    ok = del_keyword(update.effective_user.id, keyword)
    await update.message.reply_text("Removida ✅" if ok else "Não encontrada ❌")

async def delpall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    del_all_keywords(update.effective_user.id)
    await update.message.reply_text("Todas palavras-chave apagadas.")

async def addgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type in ("group","supergroup","channel"):
        add_watched_chat(chat)
        await update.message.reply_text("Grupo/canal registrado ✅")
    else:
        await update.message.reply_text("Use este comando dentro de um grupo/canal.")

async def listgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_watched_chats()
    if not rows: await update.message.reply_text("Nenhum grupo/canal registrado."); return
    lines = [f"- {title} (id={cid}, @{user})" for cid,title,user in rows]
    await update.message.reply_text("\n".join(lines))

async def sairgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    arg = update.message.text.partition(" ")[2].strip()
    if not arg: await update.message.reply_text("Uso: /sairgc <id>"); return
    rows = list_watched_chats()
    found = None
    for cid,title,user in rows:
        if str(cid)==arg or title.lower()==arg.lower() or ("@"+(user or "")).lower()==arg.lower(): found=cid; break
    if not found: await update.message.reply_text("Não encontrado."); return
    try: await bot.leave_chat(found)
    except: pass
    remove_watched_chat(found)
    await update.message.reply_text("Saiu do grupo/canal.")

async def sairgcall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = list_watched_chats()
    for cid,title,user in rows:
        try: await bot.leave_chat(cid)
        except: pass
    remove_all_watched_chats()
    await update.message.reply_text("Saiu de todos os grupos/canais.")

# ---------- Message handler ----------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.channel_post
    if not msg: return
    chat = msg.chat
    rows = list_watched_chats()
    chat_ids = [cid for cid,_,_ in rows]
    if chat.id not in chat_ids: return
    text = msg.text or msg.caption or ""
    if not text: return
    users = get_subscribed_users()
    for uid in users:
        kws = get_keywords(uid)
        matched = None
        for kw in kws:
            if text_contains_all_tokens(text, kw):
                matched = kw
                break
        if matched:
            try:
                header = f"📣 Oferta em {chat.title or chat.full_name}\nPalavra-chave: {matched}"
                await bot.send_message(chat_id=uid, text=header)
                await bot.forward_message(chat_id=uid, from_chat_id=chat.id, message_id=msg.message_id)
            except Exception as e:
                logger.warning(f"Falha ao enviar para {uid}: {e}")

# ---------- Config Telegram Handlers ----------
app_telegram.add_handler(CommandHandler("start", start))
app_telegram.add_handler(CommandHandler("help", help_cmd))
app_telegram.add_handler(CommandHandler("notifyme", notifyme))
app_telegram.add_handler(CommandHandler("removeme", removeme))
app_telegram.add_handler(CommandHandler("addp", addp))
app_telegram.add_handler(CommandHandler("listp", listp))
app_telegram.add_handler(CommandHandler("delp", delp))
app_telegram.add_handler(CommandHandler("delpall", delpall))
app_telegram.add_handler(CommandHandler("addgc", addgc))
app_telegram.add_handler(CommandHandler("listgc", listgc))
app_telegram.add_handler(CommandHandler("sairgc", sairgc))
app_telegram.add_handler(CommandHandler("sairgcall", sairgcall))
app_telegram.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), on_message))

# ---------- FastAPI ----------
app = FastAPI()

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, bot)
    await app_telegram.update_queue.put(update)
    return {"ok": True}

# ---------- Set Webhook ----------
async def set_webhook():
    url = os.environ.get("WEBHOOK_URL")  # exemplo: https://meuapp.onrender.com/webhook
    if not url: raise RuntimeError("Defina WEBHOOK_URL")
    await bot.set_webhook(url)
    logger.info(f"Webhook setado em {url}")

# ---------- Run ----------
if __name__ == "__main__":
    import asyncio
    port = int(os.environ.get("PORT", 10000))
    asyncio.run(set_webhook())
    uvicorn.run(app, host="0.0.0.0", port=port)
