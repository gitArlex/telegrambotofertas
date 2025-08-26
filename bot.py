import os
import json
import unicodedata
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.ext import ContextTypes

# ---------- Config ----------
TOKEN = os.environ.get("BOT_TOKEN")  # defina no Render
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # ex: https://meubot.onrender.com/webhook
DATA_FILE = "data.json"

app = Flask(__name__)
bot = Bot(TOKEN)

# ---------- Persistência ----------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "keywords": {}, "groups": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

data = load_data()

# ---------- Helpers ----------
def normalize_text(text):
    text = text.lower()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return text

def check_keywords(text, keyword_list):
    norm_text = normalize_text(text)
    for phrase in keyword_list:
        words = phrase.split()
        if all(w in norm_text for w in normalize_text(phrase).split()):
            continue
        else:
            return False
    return True

# ---------- Commands ----------
async def notifyme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data["users"][str(user_id)] = True
    save_data(data)
    await update.message.reply_text("✅ Você receberá notificações.")

async def removeme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data["users"].pop(str(user_id), None)
    save_data(data)
    await update.message.reply_text("❌ Você foi removido da lista.")

async def addp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    phrase = " ".join(context.args)
    if not phrase:
        await update.message.reply_text("Uso: /addp <palavra ou conjunto>")
        return
    data["keywords"].setdefault(user_id, [])
    if phrase not in data["keywords"][user_id]:
        data["keywords"][user_id].append(phrase)
        save_data(data)
    await update.message.reply_text(f"✅ Palavra-chave adicionada: {phrase}")

async def listp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    kws = data["keywords"].get(user_id, [])
    if not kws:
        await update.message.reply_text("Nenhuma palavra-chave cadastrada.")
        return
    msg = "Suas palavras-chave cadastradas são:\n" + "\n".join([f"{i+1}- {k}" for i, k in enumerate(kws)])
    await update.message.reply_text(msg)

async def delp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    phrase = " ".join(context.args)
    if phrase in data["keywords"].get(user_id, []):
        data["keywords"][user_id].remove(phrase)
        save_data(data)
        await listp(update, context)
    else:
        await update.message.reply_text("Essa palavra não está cadastrada.")

async def delpall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    data["keywords"][user_id] = []
    save_data(data)
    await update.message.reply_text("Todas as palavras-chave foram apagadas.")

async def addgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /addgc <link ou @nome>")
        return
    gc = context.args[0]
    if gc not in data["groups"]:
        data["groups"].append(gc)
        save_data(data)
    await update.message.reply_text(f"✅ Grupo/Canal adicionado: {gc}")

async def listgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not data["groups"]:
        await update.message.reply_text("Nenhum grupo ou canal ativo.")
        return
    msg = "Grupos/Canais ativos:\n" + "\n".join([f"{i+1}- {g}" for i, g in enumerate(data['groups'])])
    await update.message.reply_text(msg)

async def sairgc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /sairgc <link ou @nome>")
        return
    gc = context.args[0]
    if gc in data["groups"]:
        data["groups"].remove(gc)
        save_data(data)
        await update.message.reply_text(f"❌ Sai do grupo/canal: {gc}")
    else:
        await update.message.reply_text("Esse grupo/canal não está cadastrado.")

async def sairgcall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data["groups"] = []
    save_data(data)
    await update.message.reply_text("Sai de todos os grupos/canais.")

# ---------- Mensagens ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    sender = update.effective_chat.title or update.effective_chat.username or "Origem desconhecida"
    for user_id, active in data["users"].items():
        if active:
            kws = data["keywords"].get(user_id, [])
            if kws and any(all(w in normalize_text(text) for w in normalize_text(k).split()) for k in kws):
                try:
                    await bot.send_message(chat_id=user_id, text=f"📢 Nova oferta de {sender}:\n\n{text}")
                except Exception:
                    pass

# ---------- Inicialização ----------
application = Application.builder().token(TOKEN).build()

application.add_handler(CommandHandler("notifyme", notifyme))
application.add_handler(CommandHandler("removeme", removeme))
application.add_handler(CommandHandler("addp", addp))
application.add_handler(CommandHandler("listp", listp))
application.add_handler(CommandHandler("delp", delp))
application.add_handler(CommandHandler("delpall", delpall))
application.add_handler(CommandHandler("addgc", addgc))
application.add_handler(CommandHandler("listgc", listgc))
application.add_handler(CommandHandler("sairgc", sairgc))
application.add_handler(CommandHandler("sairgcall", sairgcall))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

# ---------- Webhook ----------
@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    application.update_queue.put_nowait(update)
    return "ok", 200

@app.route("/")
def index():
    return "Bot rodando com Webhook", 200

if __name__ == "__main__":
    # Ativa webhook no Render
    bot.delete_webhook()
    bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
