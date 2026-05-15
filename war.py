#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Module Wormgpt 2.0 – Big Dad Cuongdepzaivcl create
# Telegram: @truongphuhaokhithaylonquenloi

import asyncio, random, re, logging, json, os, psutil, tempfile
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest, DeleteMessagesRequest as DeleteChannelMessagesRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, AddChatUserRequest, DeleteMessagesRequest, CreateChatRequest
from telethon.tl.types import InputPeerUser, InputPeerChannel, Chat, InputUser
from telethon.errors import ChatAdminRequiredError, UserAlreadyParticipantError, FloodWaitError
import edge_tts

# ========== CẤU HÌNH ==========
API_ID = 20741854
API_HASH = 'd9079116154158a9dbb02d076706b5eb'
ADMIN_IDS_FILE = "admins.json"
BOT_TOKENS_FILE = "bot_tokens.json"
CLONE_SESSIONS_FILE = "clone_sessions.json"

# Load hoặc khởi tạo danh sách admin
if os.path.exists(ADMIN_IDS_FILE):
    with open(ADMIN_IDS_FILE, 'r') as f:
        ADMIN_IDS = json.load(f)
else:
    ADMIN_IDS = [8746174329,8001225219]   # ID admin mặc định
    with open(ADMIN_IDS_FILE, 'w') as f:
        json.dump(ADMIN_IDS, f)

# Load bot tokens
if os.path.exists(BOT_TOKENS_FILE):
    with open(BOT_TOKENS_FILE, 'r') as f:
        BOT_TOKENS = json.load(f)
else:
    BOT_TOKENS = []
    with open(BOT_TOKENS_FILE, 'w') as f:
        json.dump(BOT_TOKENS, f)

# Load clone sessions (string session)
if os.path.exists(CLONE_SESSIONS_FILE):
    with open(CLONE_SESSIONS_FILE, 'r') as f:
        CLONE_SESSIONS = json.load(f)
else:
    CLONE_SESSIONS = []
    with open(CLONE_SESSIONS_FILE, 'w') as f:
        json.dump(CLONE_SESSIONS, f)

WAR_FILE = "war.txt"
LAG_FILE = "lag.txt"
DELAY_SPAM = 0.5

# ========== BIẾN TOÀN CỤC ==========
war_phrases = []
lag_phrases = []
bot_clients = []              # list các bot client
clone_clients = []            # list các acc clone (userbot từ session)
spam_tasks = {}               # key: stop_key -> list[asyncio.Task]
stop_flags = {}               # key: stop_key -> bool
sent_messages = {}            # key: (client_id, chat_id) -> list[msg_id]
auto_delete_users = set()
auto_delete_group_users = {}

# ========== TIỆN ÍCH ==========
def load_phrases(file_path, default_list):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]
            return lines if lines else default_list
    except:
        return default_list

def reload_phrases():
    global war_phrases, lag_phrases
    war_phrases = load_phrases(WAR_FILE, ["dm con chó", "chien tranh di", "7080184508"])
    lag_phrases = load_phrases(LAG_FILE, ["lagggg", "cuongdevgpt"])

def get_war():   return random.choice(war_phrases) if war_phrases else "WAR"
def get_lag():   return random.choice(lag_phrases) if lag_phrases else "LAG"

def is_admin(uid): return uid in ADMIN_IDS

def save_admins():
    with open(ADMIN_IDS_FILE, 'w') as f:
        json.dump(ADMIN_IDS, f)

def save_bot_tokens():
    with open(BOT_TOKENS_FILE, 'w') as f:
        json.dump(BOT_TOKENS, f)

def save_clone_sessions():
    with open(CLONE_SESSIONS_FILE, 'w') as f:
        json.dump(CLONE_SESSIONS, f)

async def safe_send(c, chat_id, text, parse_mode=None):
    try:
        msg = await c.send_message(chat_id, text, parse_mode=parse_mode)
        key = (id(c), chat_id)
        sent_messages.setdefault(key, []).append(msg.id)
        return msg
    except FloodWaitError as e:
        print(f"🛑 FLOOD WAIT {e.seconds}s – ĐỢI")
        await asyncio.sleep(e.seconds)
        return await safe_send(c, chat_id, text, parse_mode)
    except Exception as e:
        print(f"Lỗi gửi: {e}")
        return None

async def delete_all_spam_for_client(c, chat_id):
    key = (id(c), chat_id)
    if key not in sent_messages or not sent_messages[key]:
        return 0
    msg_ids = sent_messages[key][:]
    sent_messages[key] = []
    count = 0
    for i in range(0, len(msg_ids), 100):
        batch = msg_ids[i:i+100]
        try:
            if isinstance(chat_id, int) and chat_id < 0:
                await c(DeleteChannelMessagesRequest(channel=chat_id, id=batch))
            else:
                await c.delete_messages(chat_id, batch)
            count += len(batch)
        except:
            pass
    return count

async def delete_all_spam_in_chat(chat_id):
    total = 0
    for bot in bot_clients:
        total += await delete_all_spam_for_client(bot, chat_id)
    for clone in clone_clients:
        total += await delete_all_spam_for_client(clone, chat_id)
    return total

async def delete_command_msg(event):
    try: await event.delete()
    except: pass

async def temp_reply(event, text, parse_mode=None, ttl=3):
    try:
        msg = await event.respond(text, parse_mode=parse_mode)
        async def d():
            await asyncio.sleep(ttl)
            try: await msg.delete()
            except: pass
        asyncio.create_task(d())
    except:
        pass

def mention_by_id(uid, text):
    return f"[{text}](tg://user?id={uid})"

async def get_input_user(client, user_identifier):
    try:
        entity = await client.get_input_entity(user_identifier)
        if hasattr(entity, 'user_id') and hasattr(entity, 'access_hash'):
            return InputUser(user_id=entity.user_id, access_hash=entity.access_hash)
        elif isinstance(entity, InputUser):
            return entity
    except:
        pass
    if isinstance(user_identifier, int):
        return InputUser(user_id=user_identifier, access_hash=0)
    return None

# ========== SPAM LOOP ==========
async def spam_loop(c, chat_id, msg_func, stop_key):
    while not stop_flags.get(stop_key, True):
        try:
            text, parse_mode = await msg_func()
            await safe_send(c, chat_id, text, parse_mode)
            await asyncio.sleep(DELAY_SPAM + random.uniform(0, 0.3))
        except Exception as e:
            print(f"Lỗi vòng spam: {e}")
            await asyncio.sleep(3)

def start_spam_all(chat_id, msg_func, prefix, use_bots=True, use_clones=False):
    stop_key = f"{prefix}_{chat_id}"
    if stop_key in spam_tasks:
        stop_flags[stop_key] = True
        for t in spam_tasks[stop_key]:
            t.cancel()
    stop_flags[stop_key] = False
    tasks = []
    if use_bots:
        for bot in bot_clients:
            tasks.append(asyncio.create_task(spam_loop(bot, chat_id, msg_func, stop_key)))
    if use_clones:
        for clone in clone_clients:
            tasks.append(asyncio.create_task(spam_loop(clone, chat_id, msg_func, stop_key)))
    spam_tasks[stop_key] = tasks

# ========== LỆNH SPAM CŨ (GIỮ NGUYÊN) ==========
@events.register(events.NewMessage(pattern=r'^/qd1$'))
async def qd1(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    async def msg_func(): return get_war(), None
    start_spam_all(chat_id, msg_func, "qd1", use_bots=True, use_clones=False)
    await temp_reply(event, "🔥 **QĐ1 – TOÀN BỘ BOT SPAM WAR**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/qd2\s+(@\w+)$'))
async def qd2(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    username = event.pattern_match.group(1)
    async def msg_func(): return f"{get_war()} {username}", None
    start_spam_all(chat_id, msg_func, "qd2", use_bots=True, use_clones=False)
    await temp_reply(event, f"💢 **QĐ2 – TAG {username}**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/qd3\s+(.+)$'))
async def qd3(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    raw = event.pattern_match.group(1).strip()
    usernames = re.findall(r'@\w+', raw)
    if not usernames:
        await temp_reply(event, "❌ **Thiếu username, vd: /qd3 @a @b**"); return
    tags = " ".join(usernames)
    async def msg_func(): return f"{get_war()} {tags}", None
    start_spam_all(chat_id, msg_func, "qd3", use_bots=True, use_clones=False)
    await temp_reply(event, f"💥 **QĐ3 – TAG {tags}**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/qd4\s+(.+?)\s+(\d+)$'))
async def qd4(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    text = event.pattern_match.group(1)
    count = min(int(event.pattern_match.group(2)), 500)
    await temp_reply(event, f"🔁 **QĐ4 – {count} LẦN: {text}**", parse_mode='markdown')
    stop_key = f"qd4_{chat_id}"
    if stop_key in spam_tasks:
        stop_flags[stop_key] = True
        for t in spam_tasks[stop_key]: t.cancel()
    stop_flags[stop_key] = False
    async def repeat_all():
        for i in range(count):
            tasks = []
            for bot in bot_clients:
                tasks.append(safe_send(bot, chat_id, text))
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(DELAY_SPAM)
            if stop_flags.get(stop_key, True): break
    task = asyncio.create_task(repeat_all())
    spam_tasks[stop_key] = [task]

@events.register(events.NewMessage(pattern=r'^/qd5\s+(\d+)$'))
async def qd5(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    try:
        uid = int(event.pattern_match.group(1))
    except:
        await temp_reply(event, "❌ **ID không hợp lệ**"); return
    async def msg_func():
        text = mention_by_id(uid, get_war())
        return text, 'markdown'
    start_spam_all(chat_id, msg_func, "qd5", use_bots=True, use_clones=False)
    await temp_reply(event, f"🆔 **QĐ5 – TAG ID {uid}**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/qd6$'))
async def qd6(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    async def msg_func(): return get_lag(), None
    start_spam_all(chat_id, msg_func, "qd6", use_bots=True, use_clones=False)
    await temp_reply(event, "🌀 **QĐ6 – LAG SPAM**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/stop$'))
async def stop_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    stopped = 0
    for key in list(stop_flags.keys()):
        if str(chat_id) in key:
            stop_flags[key] = True
            if key in spam_tasks:
                for t in spam_tasks[key]: t.cancel()
                del spam_tasks[key]
            stopped += 1
    await temp_reply(event, f"⏹️ **ĐÃ DỪNG {stopped} TIẾN TRÌNH SPAM**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/delete$'))
async def delete_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    count = await delete_all_spam_in_chat(chat_id)
    await temp_reply(event, f"🧹 **ĐÃ XÓA {count} TIN NHẮN SPAM**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/delay\s+(\d+\.?\d*)$'))
async def delay_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    global DELAY_SPAM
    try:
        nd = float(event.pattern_match.group(1))
        if nd < 0.5: nd = 0.5
        DELAY_SPAM = nd
        await temp_reply(event, f"⏱️ **DELAY = {DELAY_SPAM}s**", parse_mode='markdown')
    except:
        await temp_reply(event, "❌ **Sai định dạng**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/cam\s+(\d+)$'))
async def cam_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    uid = int(event.pattern_match.group(1))
    auto_delete_users.add(uid)
    await temp_reply(event, f"👁‍🗨 **ĐÃ CAM ID {uid}**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/cut\s+(\d+)$'))
async def cut_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    uid = int(event.pattern_match.group(1))
    chat_id = event.chat_id
    if chat_id > 0:
        await temp_reply(event, "❌ Chỉ dùng trong group!"); return
    auto_delete_group_users.setdefault(chat_id, set()).add(uid)
    await temp_reply(event, f"✂️ **ĐÃ CUT ID {uid} TRONG GROUP**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/uncam\s+(\d+)$'))
async def uncam_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    uid = int(event.pattern_match.group(1))
    auto_delete_users.discard(uid)
    await temp_reply(event, f"✅ **ĐÃ UNCAM ID {uid}**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/uncut\s+(\d+)$'))
async def uncut_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    uid = int(event.pattern_match.group(1))
    chat_id = event.chat_id
    if chat_id > 0: return
    if chat_id in auto_delete_group_users:
        auto_delete_group_users[chat_id].discard(uid)
    await temp_reply(event, f"✅ **ĐÃ UNCUT ID {uid}**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/join\s+(.+)$'))
async def join_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    link = event.pattern_match.group(1).strip()
    if not clone_clients:
        await temp_reply(event, "❌ **Không có acc clone nào! Dùng /addclone trước**"); return
    await temp_reply(event, "🔄 Đang join và thêm bot...", parse_mode='markdown')
    clone = clone_clients[0]
    try:
        if 'joinchat' in link or '+' in link:
            hash_part = link.split('/')[-1].lstrip('+')
            await clone(ImportChatInviteRequest(hash_part))
        else:
            username = link.replace('https://t.me/', '').replace('@', '')
            await clone(JoinChannelRequest(username))
        try:
            chat_entity = await clone.get_entity(link)
            chat_id = chat_entity.id
        except:
            try:
                chat_entity = await clone.get_entity(username)
                chat_id = chat_entity.id
            except:
                await temp_reply(event, "❌ Không thể lấy chat_id sau khi join."); return
        added = 0
        for bot in bot_clients:
            try:
                me = await bot.get_me()
                input_user = await get_input_user(clone, me.id)
                if input_user:
                    await clone(AddChatUserRequest(chat_id=chat_id, user_id=input_user, fwd_limit=0))
                    added += 1
                    await asyncio.sleep(2)
            except FloodWaitError as fw:
                await asyncio.sleep(fw.seconds)
            except Exception as e:
                print(f"Lỗi thêm bot {me.id}: {e}")
        await temp_reply(event, f"✅ **Đã join và thêm {added}/{len(bot_clients)} bot vào group**", parse_mode='markdown')
    except Exception as e:
        await temp_reply(event, f"❌ **Lỗi join:** {e}", parse_mode='markdown')

# ========== LỆNH MỚI ==========
@events.register(events.NewMessage(pattern=r'^/addbot\s+(.+)$'))
async def addbot_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    token = event.pattern_match.group(1).strip()
    if token in BOT_TOKENS:
        await temp_reply(event, "⚠️ Token đã tồn tại!")
        return
    try:
        bot = TelegramClient(StringSession(), API_ID, API_HASH)
        await bot.start(bot_token=token)
        me = await bot.get_me()
        BOT_TOKENS.append(token)
        save_bot_tokens()
        bot_clients.append(bot)
        # Gán toàn bộ handler cho bot mới
        for handler in [qd1, qd2, qd3, qd4, qd5, qd6, stop_cmd, delete_cmd, delay_cmd,
                        cam_cmd, cut_cmd, uncam_cmd, uncut_cmd, join_cmd,
                        addbot_cmd, lbot_cmd, voice_cmd, cpuram_cmd, noi_cmd, tao_cmd,
                        addadmin_cmd, boadmin_cmd, addclone_cmd, lclone_cmd, locclone_cmd,
                        sp1, sp2, sp3, sp4, sp5, sp6, sp7, help_cmd]:
            bot.add_event_handler(handler)
        await temp_reply(event, f"✅ **Đã thêm bot @{me.username}**", parse_mode='markdown')
    except Exception as e:
        await temp_reply(event, f"❌ Lỗi: {e}")

@events.register(events.NewMessage(pattern=r'^/lbot$'))
async def lbot_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    if not bot_clients:
        await temp_reply(event, "📭 Chưa có bot nào.")
        return
    text = "**DANH SÁCH BOT:**\n"
    for i, bot in enumerate(bot_clients, 1):
        try:
            me = await bot.get_me()
            text += f"{i}. @{me.username} – {me.first_name}\n"
        except:
            text += f"{i}. Bot không xác định\n"
    await temp_reply(event, text, parse_mode='markdown', ttl=15)

@events.register(events.NewMessage(pattern=r'^/voice\s+(.+)$'))
async def voice_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    text = event.pattern_match.group(1).strip()
    if not text:
        await temp_reply(event, "❌ Nhập nội dung: /voice Hello")
        return
    if not bot_clients:
        await temp_reply(event, "❌ Chưa có bot nào hoạt động")
        return
    await temp_reply(event, "🎤 Đang tạo voice...")
    try:
        communicate = edge_tts.Communicate(text, "vi-VN-HoaiMyNeural")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    tmp.write(chunk["data"])
            tmp_path = tmp.name
        await bot_clients[0].send_file(event.chat_id, tmp_path, voice_note=True)
        os.unlink(tmp_path)
    except Exception as e:
        await temp_reply(event, f"❌ Lỗi voice: {e}")

@events.register(events.NewMessage(pattern=r'^/cpuram$'))
async def cpuram_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    text = f"🖥️ **CPU:** {cpu}%\n💾 **RAM:** {ram}%"
    await temp_reply(event, text, parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/noi\s+(.+?)\s+(\-?\d+)$'))
async def noi_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    text = event.pattern_match.group(1).strip()
    chat_id = int(event.pattern_match.group(2))
    if not bot_clients:
        await temp_reply(event, "❌ Không có bot")
        return
    try:
        await bot_clients[0].send_message(chat_id, text)
        await temp_reply(event, f"✅ Đã gửi tin nhắn đến `{chat_id}`", parse_mode='markdown')
    except Exception as e:
        await temp_reply(event, f"❌ Lỗi: {e}")

@events.register(events.NewMessage(pattern=r'^/tao\s+(\d+)$'))
async def tao_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    user_id = int(event.pattern_match.group(1))
    if not clone_clients:
        await temp_reply(event, "❌ Cần ít nhất 1 clone để tạo group")
        return
    clone = clone_clients[0]
    try:
        group_name = f"Group_{random.randint(1000,9999)}"
        new_chat = await clone.create_group(group_name, [user_id])
        chat_id = new_chat.id
        for admin in ADMIN_IDS:
            try:
                input_user = await get_input_user(clone, admin)
                if input_user:
                    await clone(AddChatUserRequest(chat_id=chat_id, user_id=input_user, fwd_limit=0))
                    await asyncio.sleep(0.5)
            except Exception as e:
                print(f"Lỗi thêm admin {admin}: {e}")
        for bot in bot_clients:
            try:
                me = await bot.get_me()
                input_user = await get_input_user(clone, me.id)
                if input_user:
                    await clone(AddChatUserRequest(chat_id=chat_id, user_id=input_user, fwd_limit=0))
                    await asyncio.sleep(1)
            except Exception as e:
                print(f"Lỗi thêm bot {me.id}: {e}")
        await temp_reply(event, f"✅ Đã tạo group `{group_name}` id: `{chat_id}`", parse_mode='markdown')
    except Exception as e:
        await temp_reply(event, f"❌ Lỗi: {e}")

@events.register(events.NewMessage(pattern=r'^/addadmin\s+(\d+)$'))
async def addadmin_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    uid = int(event.pattern_match.group(1))
    if uid in ADMIN_IDS:
        await temp_reply(event, "⚠️ Đã là admin rồi")
        return
    ADMIN_IDS.append(uid)
    save_admins()
    await temp_reply(event, f"✅ Đã thêm admin `{uid}`", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/boadmin\s+(\d+)$'))
async def boadmin_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    uid = int(event.pattern_match.group(1))
    if uid not in ADMIN_IDS:
        await temp_reply(event, "⚠️ Không phải admin")
        return
    ADMIN_IDS.remove(uid)
    save_admins()
    await temp_reply(event, f"✅ Đã xóa admin `{uid}`", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/addclone\s+(.+)$'))
async def addclone_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    session_str = event.pattern_match.group(1).strip()
    if session_str in CLONE_SESSIONS:
        await temp_reply(event, "⚠️ Session đã tồn tại")
        return
    try:
        clone = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await clone.start()
        me = await clone.get_me()
        CLONE_SESSIONS.append(session_str)
        save_clone_sessions()
        clone_clients.append(clone)
        # Gắn toàn bộ handler, bao gồm auto_delete_handler
        for handler in [qd1, qd2, qd3, qd4, qd5, qd6, stop_cmd, delete_cmd, delay_cmd,
                        cam_cmd, cut_cmd, uncam_cmd, uncut_cmd, join_cmd,
                        addbot_cmd, lbot_cmd, voice_cmd, cpuram_cmd, noi_cmd, tao_cmd,
                        addadmin_cmd, boadmin_cmd, addclone_cmd, lclone_cmd, locclone_cmd,
                        sp1, sp2, sp3, sp4, sp5, sp6, sp7, auto_delete_handler, help_cmd]:
            clone.add_event_handler(handler)
        await temp_reply(event, f"✅ Đã thêm clone @{me.username}", parse_mode='markdown')
    except Exception as e:
        await temp_reply(event, f"❌ Lỗi: {e}")

@events.register(events.NewMessage(pattern=r'^/lclone$'))
async def lclone_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    if not clone_clients:
        await temp_reply(event, "📭 Chưa có clone nào.")
        return
    text = "**DANH SÁCH CLONE:**\n"
    for i, clone in enumerate(clone_clients, 1):
        try:
            me = await clone.get_me()
            status = "🟢 Live"
        except:
            status = "🔴 Die"
        text += f"{i}. @{me.username if 'me' in dir() else '?'} – {status}\n"
    await temp_reply(event, text, parse_mode='markdown', ttl=15)

@events.register(events.NewMessage(pattern=r'^/locclone$'))
async def locclone_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    await temp_reply(event, "🔄 Đang lọc clone chết...")
    alive_indices = []
    for i, clone in enumerate(clone_clients):
        try:
            await clone.get_me()
            alive_indices.append(i)
        except:
            pass
    new_clients = [clone_clients[i] for i in alive_indices]
    new_sessions = [CLONE_SESSIONS[i] for i in alive_indices]
    clone_clients.clear()
    clone_clients.extend(new_clients)
    CLONE_SESSIONS.clear()
    CLONE_SESSIONS.extend(new_sessions)
    save_clone_sessions()
    await temp_reply(event, f"✅ Đã lọc xong. Còn {len(clone_clients)} clone hoạt động.", parse_mode='markdown')

# ========== SPAM CHO CLONE (sp1-sp6, sp7) ==========
@events.register(events.NewMessage(pattern=r'^/sp1$'))
async def sp1(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    async def msg_func(): return get_war(), None
    start_spam_all(chat_id, msg_func, "sp1", use_bots=False, use_clones=True)
    await temp_reply(event, "🔥 **SP1 – WAR**", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/sp2\s+(@\w+)$'))
async def sp2(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    username = event.pattern_match.group(1)
    async def msg_func(): return f"{get_war()} {username}", None
    start_spam_all(chat_id, msg_func, "sp2", use_bots=False, use_clones=True)
    await temp_reply(event, f"💢 **SP2 – TAG {username} **", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/sp3\s+(.+)$'))
async def sp3(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    raw = event.pattern_match.group(1).strip()
    usernames = re.findall(r'@\w+', raw)
    if not usernames:
        await temp_reply(event, "❌ Thiếu username"); return
    tags = " ".join(usernames)
    async def msg_func(): return f"{get_war()} {tags}", None
    start_spam_all(chat_id, msg_func, "sp3", use_bots=False, use_clones=True)
    await temp_reply(event, f"💥 **SP3 – TAG {tags} **", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/sp4\s+(.+?)\s+(\d+)$'))
async def sp4(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    text = event.pattern_match.group(1)
    count = min(int(event.pattern_match.group(2)), 500)
    await temp_reply(event, f"🔁 **SP4 – {count} **", parse_mode='markdown')
    stop_key = f"sp4_{chat_id}"
    if stop_key in spam_tasks:
        stop_flags[stop_key] = True
        for t in spam_tasks[stop_key]: t.cancel()
    stop_flags[stop_key] = False
    async def repeat_all():
        for i in range(count):
            tasks = [safe_send(clone, chat_id, text) for clone in clone_clients]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(DELAY_SPAM)
            if stop_flags.get(stop_key, True): break
    task = asyncio.create_task(repeat_all())
    spam_tasks[stop_key] = [task]

@events.register(events.NewMessage(pattern=r'^/sp5\s+(\d+)$'))
async def sp5(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    try:
        uid = int(event.pattern_match.group(1))
    except:
        await temp_reply(event, "❌ ID không hợp lệ"); return
    async def msg_func():
        return mention_by_id(uid, get_war()), 'markdown'
    start_spam_all(chat_id, msg_func, "sp5", use_bots=False, use_clones=True)
    await temp_reply(event, f"🆔 **SP5 – TAG ID {uid} **", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/sp6$'))
async def sp6(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    async def msg_func(): return get_lag(), None
    start_spam_all(chat_id, msg_func, "sp6", use_bots=False, use_clones=True)
    await temp_reply(event, "🌀 **SP6 **", parse_mode='markdown')

@events.register(events.NewMessage(pattern=r'^/sp7\s+(\d+)$'))
async def sp7(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    chat_id = event.chat_id
    target_id = int(event.pattern_match.group(1))
    async def msg_func():
        return mention_by_id(target_id, get_war()), 'markdown'
    start_spam_all(chat_id, msg_func, "sp7", use_bots=False, use_clones=True)
    await temp_reply(event, f"🎯 **SP7 {target_id} **", parse_mode='markdown')

# ========== AUTO DELETE ==========
@events.register(events.NewMessage(incoming=True))
async def auto_delete_handler(event):
    if not clone_clients:
        return
    if event.is_private:
        if event.sender_id in auto_delete_users:
            try: await event.delete()
            except: pass
    else:
        chat_id = event.chat_id
        if chat_id in auto_delete_group_users and event.sender_id in auto_delete_group_users[chat_id]:
            try: await event.delete()
            except: pass

# ========== HELP MỚI ==========
@events.register(events.NewMessage(pattern=r'^/help$|^/start$'))
async def help_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_command_msg(event)
    help_text = (
        "/qd1, /qd2 @user, /qd3 @user1 @user2, /qd4 text count, /qd5 id, /qd6\n"
        "/sp1, /sp2 @user, /sp3 @user1 @user2, /sp4 text count, /sp5 id, /sp6, /sp7 id\n"
        "/addbot token, /lbot, /addclone session, /lclone, /locclone\n"
        "/addadmin id, /boadmin id\n"
        "/voice text, /cpuram, /noi text chat_id, /tao user_id, /join link\n"
        "/stop, /delete, /delay seconds, /cam id, /cut id, /uncam, /uncut"
    )
    await temp_reply(event, help_text, ttl=15)

# ========== KHỞI ĐỘNG ==========
async def main():
    reload_phrases()
    print("🔥 CUONGDEVGPT ĐA BOT + CLONE KHỞI ĐỘNG...")

    # Start tất cả bot từ token
    global bot_clients
    for idx, token in enumerate(BOT_TOKENS):
        try:
            bot = TelegramClient(StringSession(), API_ID, API_HASH)
            await bot.start(bot_token=token)
            me = await bot.get_me()
            print(f"✅ BOT {idx+1}: @{me.username}")
            bot_clients.append(bot)
            # Gán handler cho bot khởi động
            for handler in [qd1, qd2, qd3, qd4, qd5, qd6, stop_cmd, delete_cmd, delay_cmd,
                            cam_cmd, cut_cmd, uncam_cmd, uncut_cmd, join_cmd,
                            addbot_cmd, lbot_cmd, voice_cmd, cpuram_cmd, noi_cmd, tao_cmd,
                            addadmin_cmd, boadmin_cmd, addclone_cmd, lclone_cmd, locclone_cmd,
                            sp1, sp2, sp3, sp4, sp5, sp6, sp7, help_cmd]:
                bot.add_event_handler(handler)
        except Exception as e:
            print(f"❌ BOT LỖI: {e}")

    # Start tất cả clone từ session
    global clone_clients
    for idx, sess in enumerate(CLONE_SESSIONS):
        try:
            clone = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await clone.start()
            me = await clone.get_me()
            print(f"✅ CLONE {idx+1}: @{me.username}")
            clone_clients.append(clone)
            for handler in [qd1, qd2, qd3, qd4, qd5, qd6, stop_cmd, delete_cmd, delay_cmd,
                            cam_cmd, cut_cmd, uncam_cmd, uncut_cmd, join_cmd,
                            addbot_cmd, lbot_cmd, voice_cmd, cpuram_cmd, noi_cmd, tao_cmd,
                            addadmin_cmd, boadmin_cmd, addclone_cmd, lclone_cmd, locclone_cmd,
                            sp1, sp2, sp3, sp4, sp5, sp6, sp7, auto_delete_handler, help_cmd]:
                clone.add_event_handler(handler)
        except Exception as e:
            print(f"❌ CLONE LỖI: {e}")

    if not bot_clients and not clone_clients:
        print("❌ Không có bot hay clone nào hoạt động. Thoát.")
        return

    print("⚔️ SẴN SÀNG CHIẾN ĐẤU! GÕ /help")
    all_clients = bot_clients + clone_clients
    try:
        await asyncio.gather(*[client.run_until_disconnected() for client in all_clients])
    except KeyboardInterrupt:
        print("👋 TẠM BIỆT!")
    except Exception as e:
        print(f"LỖI: {e}")

if __name__ == "__main__":
    asyncio.run(main())

#Wormgpt Cường Dev Don't Delete for copyright