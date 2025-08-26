#!/usr/bin/env python3
"""
Bot Telegram usando WEBHOOK (Application.run_webhook).
Persistência: SQLite (bot.db).
Destinado a rodar como Web Service (Render) com variáveis de ambiente TELEGRAM_TOKEN e WEBHOOK_URL.
"""
import os
import sqlite3
import logging
import unicodedata
from typing import Optional

from telegram import Update, Chat, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

# ---------- Config ----------
DB_PATH = os.environ.get("BOT_DB_PATH", "bot.db")
PORT = int(os.environ.get("PORT", os.environ.get("SERVER_PORT", 8443)))
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # EX: https://meuservico.onrender.com/webhook
MAX_WEBHOOK_CONNECTIONS = int(os.environ.get("MAX_WEBHOOK_CONNECTIONS", "40"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

if not TELEGRAM_TOKEN:
    raise RuntimeError("Defina a variável de ambiente TELEGRAM_TOKEN")
if not WEBHOOK_URL:
    raise RuntimeError("Defina a variável de ambiente WEBHOOK_URL (ex: https://meuservico.onrender.com/webhook)")

# ---------- DB helpers ----------
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, subscribed INTEGER DEFAULT 0)")
    cur.execute(
        "CREATE TABLE IF NOT EXISTS keywords (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, keyword TEXT)"
    )
    cur.execute(
        "CREATE TABLE IF NOT EXISTS watched_chats (chat_id INTEGER PRIMARY KEY, title TEXT, username TEXT)"
    )
    conn.commit()
    conn.close()

def get_conn():
    return sqlite3.connect(DB_PATH, timeout=10)

# ---------- Normalização / busca ----------
def normalize(s: Optional[str]) -> str:
    if not s:
        return ""
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))

def text_contains_all_tokens(text: str, keyword: str) -> bool:
    norm_text = normalize(text)
    tokens = [t for t in normalize(keyword).split() if t]
    return all(tok in norm_text for tok in tokens)

# ---------- Users / keywords ----------
def ensure_user(user_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, subscribed) VALUES (?,0)", (user_id,))
    conn.commit(); conn.close()

def set_subscribed(user_id: int, subscribed: bool):
    ensure_user(user_id)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("UPDATE users SET subscribed=? WHERE user_id=?", (1 if subscribed else 0, user_id))
    conn.commit(); conn.close()

def get_subscribed_users():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE subscribed=1")
    rows = [r[0] for r in cur.fetchall()]
    conn.close(); return rows

def get_keywords(user_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT keyword FROM keywords WHERE user_id=?", (user_id,))
    rows = [r[0] for r in cur.fetchall()]
    conn.close(); return rows

def add_keyword(user_id: int, keyword: str) -> bool:
    ensure_user(user_id)
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM keywords WHERE user_id=? AND keyword=?", (user_id, keyword))
    if cur.fetchone():
        conn.close(); return False
    cur.execute("INSERT INTO keywords (user_id, keyword) VALUES (?,?)", (user_id, keyword))
    conn.commit(); conn.close(); return True

def del_keyword(user_id: int, keyword: str) -> bool:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM keywords WHERE user_id=? AND keyword=?", (user_id, keyword))
    ok = cur.rowcount > 0
    conn.commit(); conn.close(); return ok

def del_all_keywords(user_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM keywords WHERE user_id=?", (user_id,))
    conn.commit(); conn.close()

# ---------- watched chats ----------
def add_watched_chat(chat: Chat):
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO watched_chats (chat_id, title, username) VALUES (?,?,?)",
        (chat.id, chat.title or chat.full_name or "", chat.username or "")
    )
    conn.commit(); conn.close()

def list_watched_chats():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT chat_id, title, username FROM watched_chats")
    rows = cur.fetchall(); conn.close(); return rows

def remove_watched_chat(chat_id: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM watched_chats WHERE chat_id=?", (chat_id,))
    conn.commit(); conn.close()

def remove_all_watched_chats():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("DELETE FROM watched_chats")
    conn.commit(); conn.close()

# ---------- Handlers ----------
async def cmd_start(update: Update, context):
    await update.message.reply_text("Olá! Sou o bot de notificações. Use /help para ver comandos.")

async def cmd_help(update: Update, context):
    txt = (
        "/notifyme - receber notificações\n"
        "/removeme - parar notificações\n"
        "/addp <texto> - adicionar palavra-chave\n"
        "/listp - listar palavras-chave\n"
        "/delp <texto> - remover palavra-chave\n"
        "/delpall - apagar todas as palavras-chave\n"
        "/addgc - (no grupo) registrar o grupo/canal\n"
        "/listgc - listar grupos/canais\n"
        "/sairgc <id|@username|nome> - sair de grupo/canal\n"
        "/sairgcall - sair de todos os grupos/canais\n"
    )
    await update.message.reply_text(txt)

async def cmd_notifyme(update: Update, context):
    set_subscribed(update.effective_user.id, True)
    await update.message.reply_text("Você foi inscrito para notificações (receberá em DM).")

async def cmd_removeme(update: Update, context):
    set_subscribed(update.effective_user.id, False)
    await update.message.reply_text("Você foi removido das notificações.")

async def cmd_addp(update: Update, context):
    keyword = update.message.text.partition(" ")[2].strip()
    if not keyword:
        await update.message.reply_text("Uso: /addp <palavra ou frase>")
        return
    ok = add_keyword(update.effective_user.id, keyword)
    await update.message.reply_text("Palavra adicionada ✅" if ok else "Palavra já existe ❌")

async def cmd_listp(update: Update, context):
    kws = get_keywords(update.effective_user.id)
    if not kws:
        await update.message.reply_text("Nenhuma palavra-chave cadastrada.")
    else:
        await update.message.reply_text("Suas palavras-chave:\n" + "\n".join(f"{i+1}- {k}" for i,k in enumerate(kws)))

async def cmd_delp(update: Update, context):
    keyword = update.message.text.partition(" ")[2].strip()
    if not keyword:
        await update.message.reply_text("Uso: /delp <palavra ou frase>")
        return
    ok = del_keyword(update.effective_user.id, keyword)
    await update.message.reply_text("Removida ✅" if ok else "Não encontrada ❌")

async def cmd_delpall(update: Update, context):
    del_all_keywords(update.effective_user.id)
    await update.message.reply_text("Todas as suas palavras-chave foram apagadas.")

async def cmd_addgc(update: Update, context):
    chat = update.effective_chat
    if chat.type in ("group", "supergroup", "channel"):
        add_watched_chat(chat)
        await update.message.reply_text("Grupo/canal registrado para monitoramento ✅")
    else:
        await update.message.reply_text("Use este comando dentro de um grupo/canal para registrar.")

async def cmd_listgc(update: Update, context):
    rows = list_watched_chats()
    if not rows:
        await update.message.reply_text("Nenhum grupo/canal registrado.")
        return
    lines = [f"- {title} (id={cid}, @{user})" for cid,title,user in rows]
    await update.message.reply_text("\n".join(lines))

async def cmd_sairgc(update: Update, context):
    arg = update.message.text.partition(" ")[2].strip()
    if not arg:
        await update.message.reply_text("Uso: /sairgc <id | @username | nome>")
        return
    rows = list_watched_chats()
    found = None
    for cid,title,user in rows:
        if str(cid)==arg or title.lower()==arg.lower() or ("@"+(user or "")).lower()==arg.lower():
            found = cid; break
    if not found:
        await update.message.reply_text("Grupo/canal não encontrado na lista do bot.")
        return
    try:
        await context.bot.leave_chat(found)
    except Exception:
        pass
    remove_watched_chat(found)
    await update.message.reply_text("Bot saiu do grupo/canal e removeu do monitoramento.")

async def cmd_sairgcall(update: Update, context):
    rows = list_watched_chats()
    for cid,title,user in rows:
        try:
            await context.bot.leave_chat(cid)
        except Exception:
            pass
    remove_all_watched_chats()
    await update.message.reply_text("Bot saiu de todos os grupos/canais.")

# ---------- Message processing ----------
async def on_message(update: Update, context):
    msg: Message = update.message or update.channel_post
    if not msg:
        return
    chat = msg.chat
    # only from registered watched chats
    rows = list_watched_chats()
    chat_ids = [cid for cid,_,_ in rows]
    if chat.id not in chat_ids:
        return
    text = msg.text or msg.caption or ""
    if not text:
        return
    users = get_subscribed_users()
    for uid in users:
        kws = get_keywords(uid)
        matched = None
        for kw in kws:
            if text_contains_all_tokens(text, kw):
                matched = kw; break
        if matched:
            try:
                origin = f"{chat.title or chat.full_name} ({'t.me/'+chat.username if chat.username else 'id:'+str(chat.id)})"
                header = f"📣 Oferta encontrada em: {origin}\nPalavra-chave: {matched}\nMensagem original encaminhada abaixo:"
                await context.bot.send_message(chat_id=uid, text=header)
                await context.bot.forward_message(chat_id=uid, from_chat_id=chat.id, message_id=msg.message_id)
            except Exception as e:
                logger.exception("Falha ao encaminhar para %s: %s", uid, e)

# ---------- Bootstrap / main ----------
def build_app():
    application = Application.builder().token(TELEGRAM_TOKEN).concurrent_updates(True).build()

    # handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("notifyme", cmd_notifyme))
    application.add_handler(CommandHandler("removeme", cmd_removeme))
    application.add_handler(CommandHandler("addp", cmd_addp))
    application.add_handler(CommandHandler("listp", cmd_listp))
    application.add_handler(CommandHandler("delp", cmd_delp))
    application.add_handler(CommandHandler("delpall", cmd_delpall))
    application.add_handler(CommandHandler("addgc", cmd_addgc))
    application.add_handler(CommandHandler("listgc", cmd_listgc))
    application.add_handler(CommandHandler("sairgc", cmd_sairgc))
    application.add_handler(CommandHandler("sairgcall", cmd_sairgcall))

    application.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), on_message))
    return application

if __name__ == "__main__":
    init_db()
    app = build_app()
    logger.info("Iniciando webhook: %s (porta %s)", WEBHOOK_URL, PORT)

    # run_webhook will set the webhook for you and run an aiohttp server internally
    # webhook_url_path must match the path portion of WEBHOOK_URL
    # ex: WEBHOOK_URL = https://meuservico.onrender.com/webhook  -> webhook_url_path="/webhook"
    webhook_path = "/" + WEBHOOK_URL.rstrip("/").split("/", 3)[-1] if "/" in WEBHOOK_URL.rstrip("/") else "/webhook"
    # If user provided full path with query, we still try to detect last component; fallback to "/webhook"
    if not webhook_path.startswith("/"):
        webhook_path = "/" + webhook_path

    try:
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            webhook_url_path=webhook_path,
            webhook_url=WEBHOOK_URL,
            max_concurrent_connections=MAX_WEBHOOK_CONNECTIONS,
        )
    except Exception as exc:
        logger.exception("Falha ao iniciar run_webhook: %s", exc)
        raise
