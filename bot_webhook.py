import os
import json
import unicodedata
import re
from flask import Flask, request, jsonify
from telegram import Bot, Update
from telegram.error import TelegramError
from threading import Lock

# Config
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL')  # ex: https://meuapp.onrender.com/webhook
DATA_FILE = os.environ.get('DATA_FILE', 'data.json')

if not TELEGRAM_TOKEN:
    raise RuntimeError('Defina a variável de ambiente TELEGRAM_TOKEN')
if not WEBHOOK_URL:
    raise RuntimeError('Defina a variável de ambiente WEBHOOK_URL')

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)
lock = Lock()

# Estrutura do arquivo data.json
# {
#   "subscribers": {
#       "<chat_id>": {
#           "username": "nome_do_usuario",
#           "keywords": ["monitor", "Teclado Magnético"]
#       }
#   },
#   "groups": {
#       "<chat_id>": {
#           "title": "Nome do Grupo",
#           "link": null
#       }
#   }
# }


def load_data():
    if not os.path.exists(DATA_FILE):
        return {"subscribers": {}, "groups": {}}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_data(data):
    with lock:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


data = load_data()


# util: normaliza texto removendo acentos e transformando em minusculas

def normalize_text(s: str) -> str:
    if not s:
        return ''
    s = s.lower()
    s = unicodedata.normalize('NFKD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    return s


def tokens_of_entry(entry: str):
    # divide a entrada em tokens por espaços, ignorando vazios
    return [t for t in re.split(r"\s+", normalize_text(entry).strip()) if t]


# Comandos de gerenciamento

def add_subscriber(chat_id, username):
    s = data.setdefault('subscribers', {})
    if str(chat_id) not in s:
        s[str(chat_id)] = {"username": username or '', "keywords": []}
        save_data(data)
        return True
    return False


def remove_subscriber(chat_id):
    s = data.get('subscribers', {})
    if str(chat_id) in s:
        del s[str(chat_id)]
        save_data(data)
        return True
    return False


def add_keyword_for(chat_id, entry_raw: str):
    s = data.setdefault('subscribers', {})
    key = str(chat_id)
    if key not in s:
        return False, 'Você não está inscrito. Use /notifyme primeiro.'
    entry_raw = entry_raw.strip()
    if not entry_raw:
        return False, 'Nenhuma palavra-chave fornecida.'
    if entry_raw in s[key]['keywords']:
        return False, 'Entrada já cadastrada.'
    s[key]['keywords'].append(entry_raw)
    save_data(data)
    return True, None


def del_keyword_for(chat_id, entry_raw: str):
    s = data.get('subscribers', {})
    key = str(chat_id)
    if key not in s:
        return False, 'Você não está inscrito.'
    try:
        s[key]['keywords'].remove(entry_raw)
        save_data(data)
        return True, None
    except ValueError:
        return False, 'Essa palavra-chave não existe na sua lista.'


def del_all_keywords(chat_id):
    s = data.get('subscribers', {})
    key = str(chat_id)
    if key in s:
        s[key]['keywords'] = []
        save_data(data)
        return True
    return False


# Grupos monitorados (só adiciona quando o comando for executado dentro do próprio grupo
# ou quando o bot já for membro do grupo)

def add_group(chat_id, title=None, link=None):
    g = data.setdefault('groups', {})
    key = str(chat_id)
    if key not in g:
        g[key] = {"title": title or '', "link": link}
        save_data(data)
        return True
    return False


def del_group(chat_id):
    g = data.get('groups', {})
    key = str(chat_id)
    if key in g:
        del g[key]
        save_data(data)
        return True
    return False


def del_all_groups():
    data['groups'] = {}
    save_data(data)


# Função de matching: verifica se uma entrada (conjunto de tokens) está toda contida na mensagem

def entry_matches_message(entry_raw: str, message_text: str) -> bool:
    if not message_text:
        return False
    msg = normalize_text(message_text)
    tokens = tokens_of_entry(entry_raw)
    # all tokens must be present somewhere in msg (order not important)
    for t in tokens:
        if t not in msg:
            return False
    return True


# Encaminha a mensagem para os inscritos que têm entrada correspondente

def process_group_message(chat, message):
    chat_id = chat.get('id')
    chat_title = chat.get('title') or chat.get('username') or str(chat_id)
    msg_text = message.get('text') or message.get('caption') or ''
    # also include other fields by serializing message briefly for context

    # iterate subscribers
    for sub_id, sub in data.get('subscribers', {}).items():
        for entry in sub.get('keywords', []):
            try:
                if entry_matches_message(entry, msg_text):
                    # forward original message
                    try:
                        bot.forward_message(chat_id=int(sub_id), from_chat_id=chat_id, message_id=message['message_id'])
                    except TelegramError as e:
                        # if forward fails, at least notify about the match
                        bot.send_message(chat_id=int(sub_id), text=(f"Houve uma correspondência no grupo '{chat_title}' (ID: {chat_id})\n"
                                                                    f"Palavra(s): {entry}\n"
                                                                    f"Obs: não foi possível encaminhar a mensagem original. Erro: {e}"))
                    else:
                        # send a small header so user knows origin
                        bot.send_message(chat_id=int(sub_id), text=(f"Mensagem encaminhada do grupo: {chat_title} (ID: {chat_id})\n"
                                                                    f"Palavra(s) correspondentes: {entry}"))
                    # once forwarded for this entry -> don't forward again for the same entry
                    break
            except Exception:
                continue


# Rota principal para webhook
@app.route('/webhook', methods=['POST'])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)

    # only handle messages and commands we care about
    if update.message:
        message = update.message.to_dict()
        chat = update.effective_chat
        user = update.effective_user
        text = update.message.text or update.message.caption or ''

        # Commands handling
        if text and text.startswith('/'):
            cmd_parts = text.split(' ', 1)
            cmd = cmd_parts[0].lower()
            arg = cmd_parts[1].strip() if len(cmd_parts) > 1 else ''

            # /notifyme
            if cmd == '/notifyme':
                added = add_subscriber(chat.id, getattr(user, 'username', None) or user.full_name)
                if added:
                    bot.send_message(chat_id=chat.id, text='Você foi inscrito para receber notificações. Use /addp para adicionar palavras-chave.')
                else:
                    bot.send_message(chat_id=chat.id, text='Você já está inscrito.')
                return jsonify({'ok': True})

            # /removeme
            if cmd == '/removeme':
                removed = remove_subscriber(chat.id)
                if removed:
                    bot.send_message(chat_id=chat.id, text='Você foi removido da lista de notificações.')
                else:
                    bot.send_message(chat_id=chat.id, text='Você não estava inscrito.')
                return jsonify({'ok': True})

            # /addp <palavras>
            if cmd == '/addp':
                # only allow in private chats (to identify user)
                if chat.type != 'private':
                    bot.send_message(chat_id=chat.id, text='Use este comando em uma conversa privada com o bot (DM).')
                    return jsonify({'ok': True})
                ok, err = add_keyword_for(chat.id, arg)
                if ok:
                    bot.send_message(chat_id=chat.id, text=f'Entrada adicionada: "{arg}"')
                else:
                    bot.send_message(chat_id=chat.id, text=f'Erro: {err}')
                return jsonify({'ok': True})

            # /delp <palavras>
            if cmd == '/delp':
                if chat.type != 'private':
                    bot.send_message(chat_id=chat.id, text='Use este comando em uma conversa privada com o bot (DM).')
                    return jsonify({'ok': True})
                ok, err = del_keyword_for(chat.id, arg)
                if ok:
                    bot.send_message(chat_id=chat.id, text=f'Entrada removida: "{arg}"')
                else:
                    bot.send_message(chat_id=chat.id, text=f'Erro: {err}')
                return jsonify({'ok': True})

            # /listp
            if cmd == '/listp':
                if chat.type != 'private':
                    bot.send_message(chat_id=chat.id, text='Use este comando em uma conversa privada com o bot (DM).')
                    return jsonify({'ok': True})
                subs = data.get('subscribers', {})
                s = subs.get(str(chat.id), None)
                if not s or not s.get('keywords'):
                    bot.send_message(chat_id=chat.id, text='Você não tem palavras-chave cadastradas.')
                else:
                    out = 'Suas palavras-chave cadastradas são:\n'
                    for i, e in enumerate(s['keywords'], start=1):
                        out += f"{i}- {e}\n"
                    bot.send_message(chat_id=chat.id, text=out)
                return jsonify({'ok': True})

            # /delpall
            if cmd == '/delpall':
                if chat.type != 'private':
                    bot.send_message(chat_id=chat.id, text='Use este comando em uma conversa privada com o bot (DM).')
                    return jsonify({'ok': True})
                ok = del_all_keywords(chat.id)
                if ok:
                    bot.send_message(chat_id=chat.id, text='Todas as palavras-chave foram apagadas.')
                else:
                    bot.send_message(chat_id=chat.id, text='Você não estava inscrito.')
                return jsonify({'ok': True})

            # /addgc - deve ser executado no grupo a ser adicionado (ou manualmente pelo administrador)
            if cmd == '/addgc':
                # if command executed inside a group or supergroup/channel, register it
                if chat.type in ('group', 'supergroup', 'channel'):
                    added = add_group(chat.id, title=chat.title or chat.username)
                    if added:
                        bot.send_message(chat_id=chat.id, text='Grupo/canal registrado para monitoramento.')
                    else:
                        bot.send_message(chat_id=chat.id, text='Este grupo/canal já está registrado.')
                else:
                    bot.send_message(chat_id=chat.id, text=('Para adicionar um grupo ou canal: adicione o bot ao grupo/canal e execute /addgc dentro dele, '
                                                              'ou execute o comando neste chat contendo o link de invite (não garantido).'))
                return jsonify({'ok': True})

            # /listgc
            if cmd == '/listgc':
                g = data.get('groups', {})
                if not g:
                    bot.send_message(chat_id=chat.id, text='Nenhum grupo ou canal registrado para monitoramento.')
                else:
                    out = 'Grupos/canais ativos:\n'
                    for i, (gid, info) in enumerate(g.items(), start=1):
                        out += f"{i}- {info.get('title') or 'Sem título'} (ID: {gid})\n"
                    bot.send_message(chat_id=chat.id, text=out)
                return jsonify({'ok': True})

            # /sairgc <id> - deixar o grupo (somente se o bot estiver no grupo)
            if cmd == '/sairgc':
                # can be used in private or group; accept arg as id
                if arg:
                    try:
                        gid = int(arg)
                    except ValueError:
                        bot.send_message(chat_id=chat.id, text='Forneça o ID numérico do grupo/canal ou execute /sairgc dentro do grupo.')
                        return jsonify({'ok': True})
                    removed = del_group(gid)
                    if removed:
                        # try to leave the chat
                        try:
                            bot.leave_chat(chat_id=gid)
                        except Exception:
                            pass
                        bot.send_message(chat_id=chat.id, text=f'Bot saiu do grupo/canal (ID: {gid}) e removido da lista.')
                    else:
                        bot.send_message(chat_id=chat.id, text='Esse grupo não estava registrado.')
                else:
                    # if called inside a group, leave and remove
                    if chat.type in ('group', 'supergroup', 'channel'):
                        removed = del_group(chat.id)
                        try:
                            bot.send_message(chat_id=chat.id, text='Bot deixando o grupo...')
                        except Exception:
                            pass
                        try:
                            bot.leave_chat(chat.id)
                        except Exception:
                            pass
                    else:
                        bot.send_message(chat_id=chat.id, text='Use /sairgc dentro do grupo para fazer o bot sair, ou passe o ID numerico.')
                return jsonify({'ok': True})

            # /sairgcall - sai de todos os grupos
            if cmd == '/sairgcall':
                g = list(data.get('groups', {}).keys())
                for gid in g:
                    try:
                        bot.leave_chat(chat_id=int(gid))
                    except Exception:
                        pass
                del_all_groups()
                bot.send_message(chat_id=chat.id, text='O bot saiu de todos os grupos e limpou a lista.')
                return jsonify({'ok': True})

        # Se não for comando, pode ser mensagem normal vinda de grupo/canal
        # Se for mensagem em grupo/canal monitorado -> checar palavras
        if chat.type in ('group', 'supergroup', 'channel'):
            # process only if this group is registered
            if str(chat.id) in data.get('groups', {}):
                process_group_message({'id': chat.id, 'title': chat.title or chat.username}, message)

    return jsonify({'ok': True})


# Ao inicializar, tenta setar webhook
with app.app_context():
    try:
        bot.set_webhook(WEBHOOK_URL)
        print('Webhook setado em', WEBHOOK_URL)
    except Exception as e:
        print('Falha ao setar webhook:', e)

if __name__ == '__main__':
    # Para debug local
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
