#!/usr/bin/env python3
"""
Bot Telegram para encaminhar ofertas com base em palavras-chave por usuário.
Persistência: db.json (fallback: db.txt)
Destinado a rodar em Render.com (start: python bot.py)
"""
import os
import json
import asyncio
import logging
import unicodedata
from typing import Dict, Any, List

from telegram import Update, Chat, Message
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
)

DB_PATH = "db.json"
DB_TXT = "db.txt"   # fallback / legível se desejar

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- DB helpers ----------
def load_db() -> Dict[str, Any]:
    if os.path.exists(DB_PATH):
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    # fallback to txt
    if os.path.exists(DB_TXT):
        try:
            with open(DB_TXT, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception:
            pass
    # default structure
    return {"users": {}, "watched_chats": {}}

def save_db(db: Dict[str, Any]):
    tmp = DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DB_PATH)
    # also write a human-readable txt copy
    with open(DB_TXT, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

db = load_db()

# ---------- text normalization ----------
def normalize(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s

def text_contains_all_tokens(text: str, keyword: str) -> bool:
    # keyword may be a phrase: require all tokens in the phrase exist in the text
    norm_text = normalize(text)
    tokens = [t for t in normalize(keyword).split() if t]
    return all(tok in norm_text for tok in tokens)

# ---------- user helpers ----------
def ensure_user_record(user_id: int):
    if str(user_id) not in db["users"]:
        db["users"][str(user_id)] = {"subscribed": False, "keywords": []}

def add_keyword_for_user(user_id: int, keyword: str) -> bool:
    ensure_user_record(user_id)
    kw_list = db["users"][str(user_id)]["keywords"]
    if keyword not in kw_list:
        kw_list.append(keyword)
        save_db(db)
        return True
    return False

def del_keyword_for_user(user_id: int, keyword: str) -> bool:
    ensure_user_record(user_id)
    kw_list = db["users"][str(user_id)]["keywords"]
    if keyword in kw_list:
        kw_list.remove(keyword)
        save_db(db)
        return True
    return False

# ---------- watched chats helpers ----------
def add_watched_chat(chat: Chat):
    db["watched_chats"][str(chat.id)] = {
        "id": chat.id,
        "title": chat.title or chat.full_name or "",
        "username": chat.username or "",
    }
    save_db(db)

def remove_watched_chat(chat_id: int):
    k = str(chat_id)
    if k in db["watched_chats"]:
        del db["watched_chats"][k]
        save_db(db)

# ---------- commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Olá! Sou o bot de notificações. Use /help para ver comandos."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Comandos disponíveis:\n"
        "/notifyme - receber notificações (ativa sua inscrição)\n"
        "/removeme - remove você das notificações\n"
        "/addp <palavra ou frase> - adiciona palavra-chave (separada por espaço são tokens que devem aparecer todos)\n"
        "/delp <palavra ou frase> - remove palavra-chave\n"
        "/listp - lista suas palavras-chave\n"
        "/delpall - apaga todas suas palavras-chave\n"
        "/addgc - (no grupo) registra o grupo/canal para monitoramento; (na DM) use /addgc <@username or chat_id>\n"
        "/listgc - lista os grupos/canais que o bot está monitorando\n"
        "/sairgc <id|@username|nome> - faz o bot sair do grupo/canal e para de monitorar\n"
        "/sairgcall - sai de todos os grupos e limpa a lista\n"
    )
    await update.message.reply_text(txt)

async def notifyme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id)
    db["users"][str(user.id)]["subscribed"] = True
    save_db(db)
    await update.message.reply_text("Você foi inscrito para receber notificações (receberá em DM).")

async def removeme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id)
    db["users"][str(user.id)]["subscribed"] = False
    save_db(db)
    await update.message.reply_text("Você foi removido das notificações.")

async def addp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    args = text.partition(" ")[2].strip()
    if not args:
        await update.message.reply_text("Uso: /addp <palavra ou frase>\nEx: /addp Teclado Magnético")
        return
    added = add_keyword_for_user(user.id, args)
    if added:
        await update.message.reply_text(f"Palavra-chave adicionada: {args}")
    else:
        await update.message.reply_text(f"Palavra-chave já existe: {args}")

async def listp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id)
    kws = db["users"][str(user.id)]["keywords"]
    if not kws:
        await update.message.reply_text("Você não tem palavras-chave cadastradas.")
        return
    text = "Suas palavras-chave cadastradas são:\n" + "\n".join(f"{i+1}- {k}" for i,k in enumerate(kws))
    await update.message.reply_text(text)

async def delp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = update.message.text.partition(" ")[2].strip()
    if not args:
        await update.message.reply_text("Uso: /delp <palavra ou frase>")
        return
    removed = del_keyword_for_user(user.id, args)
    if removed:
        kws = db["users"][str(user.id)]["keywords"]
        if not kws:
            await update.message.reply_text("Palavra removida. Você não tem mais palavras cadastradas.")
        else:
            text = "Suas palavras-chave cadastradas são:\n" + "\n".join(f"{i+1}- {k}" for i,k in enumerate(kws))
            await update.message.reply_text(text)
    else:
        await update.message.reply_text("Palavra não encontrada nas suas palavras-chave.")

async def delpall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ensure_user_record(user.id)
    db["users"][str(user.id)]["keywords"] = []
    save_db(db)
    await update.message.reply_text("Todas as suas palavras-chave foram apagadas.")

async def addgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # If called in group: add that group
    chat = update.effective_chat
    bot = context.bot
    if chat.type in ("group", "supergroup", "channel"):
        add_watched_chat(chat)
        await update.message.reply_text("Grupo/canal registrado para monitoramento ✅")
        return
    # called in private: need an argument (username or id)
    arg = update.message.text.partition(" ")[2].strip()
    if not arg:
        await update.message.reply_text("Em privado: envie /addgc <@username ou chat_id>")
        return
    try:
        target = await bot.get_chat(arg)
        add_watched_chat(target)
        await update.message.reply_text(f"Adicionado: {target.title or target.full_name} (id={target.id})")
    except Exception as e:
        logger.exception("addgc failed")
        await update.message.reply_text(f"Não foi possível adicionar: {e}")

async def listgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    items = db.get("watched_chats", {})
    if not items:
        await update.message.reply_text("Nenhum grupo/canal cadastrado para monitoramento.")
        return
    lines = []
    for k, info in items.items():
        chat_id = info.get("id")
        title = info.get("title") or ""
        username = info.get("username") or ""
        status = "ok"
        try:
            c = await bot.get_chat(chat_id)
            # if the bot is not in a private group it may raise
        except Exception:
            status = "bot provavelmente removido/expulso ou inacessível"
        link = f"t.me/{username}" if username else str(chat_id)
        lines.append(f"- {title} ({link}) -> {status}")
    await update.message.reply_text("Grupos/canais monitorados:\n" + "\n".join(lines))

async def sairgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    arg = update.message.text.partition(" ")[2].strip()
    if not arg:
        await update.message.reply_text("Uso: /sairgc <id | @username | nome>")
        return
    # try find by id or username or name
    found = None
    for k, info in list(db.get("watched_chats", {}).items()):
        if arg == str(info.get("id")) or arg == info.get("username") or arg.lower() in (info.get("title") or "").lower():
            found = info
            break
    if not found:
        await update.message.reply_text("Grupo/canal não encontrado na lista do bot.")
        return
    chat_id = found["id"]
    try:
        await bot.leave_chat(chat_id)
    except Exception as e:
        logger.exception("leave_chat failed")
        # continue to remove from list anyway
    remove_watched_chat(chat_id)
    await update.message.reply_text("Bot saiu do grupo/canal e removeu do monitoramento.")

async def sairgcall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    items = list(db.get("watched_chats", {}).items())
    if not items:
        await update.message.reply_text("Nenhum grupo/canal para sair.")
        return
    for k, info in items:
        chat_id = info.get("id")
        try:
            await bot.leave_chat(chat_id)
        except Exception:
            pass
        remove_watched_chat(chat_id)
    await update.message.reply_text("Bot saiu de todos os grupos/canais e limpou a lista.")

# ---------- message handler ----------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message: Message = update.message or update.channel_post
    if message is None:
        return
    chat = message.chat
    # Only process messages from watched_chats (optional: allow detecting and adding)
    if str(chat.id) not in db.get("watched_chats", {}):
        return

    # obtain text to check: text or caption (for media)
    text = message.text or message.caption or ""
    if not text:
        return

    # For each subscribed user, check their keywords
    for user_id_str, user_info in db.get("users", {}).items():
        if not user_info.get("subscribed", False):
            continue
        kws = user_info.get("keywords", [])
        if not kws:
            continue
        matched = False
        for kw in kws:
            if text_contains_all_tokens(text, kw):
                matched = True
                break
        if matched:
            # forward original message to the subscriber
            to_chat_id = int(user_id_str)
            try:
                # send a header first
                title = chat.title or chat.full_name or str(chat.id)
                username = chat.username or ""
                origin = f"{title} ({'t.me/'+username if username else 'id:'+str(chat.id)})"
                header = f"📣 Oferta encontrada em: {origin}\nDo usuário: {message.from_user.full_name if message.from_user else 'desconhecido'}\nPalavra-chave casada: {kw}\nMensagem original encaminhada abaixo:"
                await context.bot.send_message(chat_id=to_chat_id, text=header)
                # forward preserves original author and content
                await context.bot.forward_message(chat_id=to_chat_id, from_chat_id=chat.id, message_id=message.message_id)
            except Exception as e:
                logger.exception("Erro ao encaminhar mensagem")
                # se erro (usuário bloqueou bot), desinscrever automaticamente
                try:
                    # test sending a message to the user to detect blocked status
                    await context.bot.send_chat_action(chat_id=to_chat_id, action="typing")
                except Exception:
                    # probable blocked -> mark unsubscribed
                    if str(to_chat_id) in db["users"]:
                        db["users"][str(to_chat_id)]["subscribed"] = False
                        save_db(db)

# ---------- main ----------
def build_app(token: str):
    return (ApplicationBuilder()
            .token(token)
            .concurrent_updates(True)
            .build())

async def run():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("Defina a variável de ambiente TELEGRAM_TOKEN")

    app = build_app(token)

    # handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("notifyme", notifyme))
    app.add_handler(CommandHandler("removeme", removeme))
    app.add_handler(CommandHandler("addp", addp))
    app.add_handler(CommandHandler("listp", listp))
    app.add_handler(CommandHandler("delp", delp))
    app.add_handler(CommandHandler("delpall", delpall))
    app.add_handler(CommandHandler("addgc", addgc))
    app.add_handler(CommandHandler("listgc", listgc))
    app.add_handler(CommandHandler("sairgc", sairgc))
    app.add_handler(CommandHandler("sairgcall", sairgcall))

    # message handler: catch text messages and posts from groups/channels
    app.add_handler(MessageHandler(filters.ALL & (~filters.COMMAND), on_message))

    # run polling (suitable for Render background worker)
    logger.info("Starting bot (polling)...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    # keep running
    try:
        while True:
            await asyncio.sleep(60)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
