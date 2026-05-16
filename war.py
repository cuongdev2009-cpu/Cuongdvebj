#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Module Wormgpt 2.0 – Big Dad Cuongdepzaivcl create
# Telegram: @truongphuhaokhithaylonquenloi

import asyncio, random, re, logging, json, os, psutil, tempfile
from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.functions.channels import (
    JoinChannelRequest, LeaveChannelRequest,
    DeleteMessagesRequest as DeleteChannelMessagesRequest,
    InviteToChannelRequest
)
from telethon.tl.functions.messages import (
    ImportChatInviteRequest, AddChatUserRequest, DeleteMessagesRequest,
    CreateChatRequest
)
from telethon.tl.types import InputPeerUser, InputPeerChannel, Chat, InputUser
from telethon.errors import (
    ChatAdminRequiredError, UserAlreadyParticipantError, FloodWaitError,
    PeerIdInvalidError
)
import edge_tts

# ========== CẤU HÌNH ==========
API_ID = 20741854
API_HASH = 'd9079116154158a9dbb02d076706b5eb'
ADMIN_IDS_FILE = "admins.json"
BOT_TOKENS_FILE = "bot_tokens.json"
CLONE_SESSIONS_FILE = "clone_sessions.json"

# Load / tạo file config
if os.path.exists(ADMIN_IDS_FILE):
    with open(ADMIN_IDS_FILE, 'r') as f: ADMIN_IDS = json.load(f)
else:
    ADMIN_IDS = [8746174329, 8001225219]
    with open(ADMIN_IDS_FILE, 'w') as f: json.dump(ADMIN_IDS, f)

if os.path.exists(BOT_TOKENS_FILE):
    with open(BOT_TOKENS_FILE, 'r') as f: BOT_TOKENS = json.load(f)
else: BOT_TOKENS = []

if os.path.exists(CLONE_SESSIONS_FILE):
    with open(CLONE_SESSIONS_FILE, 'r') as f: CLONE_SESSIONS = json.load(f)
else: CLONE_SESSIONS = []

WAR_FILE = "war.txt"
LAG_FILE = "nhay.txt"
DELAY_SPAM = 0.005

# ========== BIẾN TOÀN CỤC ==========
war_phrases = []
lag_phrases = []
bot_clients = []
clone_clients = []
spam_tasks = {}                # stop_key -> list[asyncio.Task]
stop_events = {}               # stop_key -> asyncio.Event (dùng để ra hiệu dừng)
sent_messages = {}             # (client_id, chat_id) -> list[msg_id]
auto_delete_users = set()
auto_delete_group_users = {}

# ========== TIỆN ÍCH CƠ BẢN ==========
def load_phrases(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]
            return lines if lines else default
    except: return default

def reload_phrases():
    global war_phrases, lag_phrases
    war_phrases = load_phrases(WAR_FILE, ["dm con chó", "chien tranh di", "7080184508"])
    lag_phrases = load_phrases(LAG_FILE, ["lagggg", "cuongdevgpt"])

def get_war(): return random.choice(war_phrases) if war_phrases else "WAR"
def get_lag(): return random.choice(lag_phrases) if lag_phrases else "LAG"
def is_admin(uid): return uid in ADMIN_IDS

def save_admins(): 
    with open(ADMIN_IDS_FILE, 'w') as f: json.dump(ADMIN_IDS, f)
def save_bot_tokens():
    with open(BOT_TOKENS_FILE, 'w') as f: json.dump(BOT_TOKENS, f)
def save_clone_sessions():
    with open(CLONE_SESSIONS_FILE, 'w') as f: json.dump(CLONE_SESSIONS, f)

async def safe_send(c, chat_id, text, parse_mode=None):
    """Gửi tin nhắn an toàn, tự retry khi FloodWait, lưu message id để xoá sau."""
    try:
        msg = await c.send_message(chat_id, text, parse_mode=parse_mode)
        sent_messages.setdefault((id(c), chat_id), []).append(msg.id)
        return msg
    except FloodWaitError as e:
        print(f"🛑 FLOOD WAIT {e.seconds}s")
        await asyncio.sleep(e.seconds)
        return await safe_send(c, chat_id, text, parse_mode)
    except Exception as e:
        print(f"Lỗi gửi: {e}")
        return None

async def delete_all_spam_for_client(c, chat_id):
    key = (id(c), chat_id)
    if key not in sent_messages: return 0
    ids = sent_messages[key][:]
    sent_messages[key] = []
    count = 0
    for i in range(0, len(ids), 100):
        batch = ids[i:i+100]
        try:
            if isinstance(chat_id, int) and chat_id < 0:
                await c(DeleteChannelMessagesRequest(channel=chat_id, id=batch))
            else:
                await c.delete_messages(chat_id, batch)
            count += len(batch)
        except: pass
    return count

async def delete_all_spam_in_chat(chat_id):
    total = 0
    for bot in bot_clients: total += await delete_all_spam_for_client(bot, chat_id)
    for clone in clone_clients: total += await delete_all_spam_for_client(clone, chat_id)
    return total

async def delete_cmd_msg(event):
    try: await event.delete()
    except: pass

async def temp_reply(event, text, parse_mode=None, ttl=3):
    """Trả lời tạm thời rồi tự xoá."""
    try:
        msg = await event.respond(text, parse_mode=parse_mode)
        asyncio.create_task(_del_after(msg, ttl))
    except: pass

async def _del_after(msg, t):
    await asyncio.sleep(t)
    try: await msg.delete()
    except: pass

def mention_by_id(uid, text):
    return f"[{text}](tg://user?id={uid})"

async def get_input_user(client, user_id):
    try:
        # Ép client phải tìm entity từ database của Telegram
        entity = await client.get_input_entity(user_id)
        return entity
    except ValueError:
        try:
            # Nếu không thấy, thử lấy thông tin chi tiết (chỉ hoạt động nếu ID đã từng xuất hiện)
            entity = await client.get_entity(user_id)
            return await client.get_input_entity(entity)
        except Exception:
            return None
    except Exception:
        return None

async def resolve_user_by_username(client, username):
    """Thử lấy InputUser qua @username."""
    try:
        entity = await client.get_input_entity(username)
        if isinstance(entity, InputUser): return entity
        if hasattr(entity, 'user_id') and hasattr(entity, 'access_hash'):
            return InputUser(user_id=entity.user_id, access_hash=entity.access_hash)
    except: return None

# Đảm bảo bạn đã import các Class này ở đầu file hoặc ngay trong hàm
from telethon.tl.types import Channel, Chat, InputPeerChannel, InputPeerChat

async def smart_add_user(clone, chat_id, user_id, is_bot=False):
    """Thêm user vào chat, tự động nhận diện chính xác loại nhóm (Chat/Channel/Megagroup)."""
    try:
        # 1. Lấy thực thể Chat/Channel
        chat = await clone.get_entity(chat_id)
        
        # 2. Xử lý lấy InputUser
        input_user = None
        if is_bot:
            for b in bot_clients:
                try:
                    bot_me = await b.get_me()
                    if bot_me.id == user_id:
                        input_user = await clone.get_input_entity(bot_me.username)
                        break
                except: pass
        
        if not input_user:
            input_user = await get_input_user(clone, user_id)

        if not input_user:
            return False

        # 3. KIỂM TRA LOẠI NHÓM ĐỂ DÙNG API PHÙ HỢP
        # Trong Telethon, Supergroup được coi là một loại Channel
        if isinstance(chat, Channel):
            # Dùng cho Supergroup (Megagroup) và Broadcast Channel
            await clone(InviteToChannelRequest(channel=chat, users=[input_user]))
        else:
            # Dùng cho Nhóm thường (Small Chat)
            await clone(AddChatUserRequest(chat_id=chat.id, user_id=input_user, fwd_limit=0))
        
        return True

    except UserAlreadyParticipantError:
        return True
    except FloodWaitError as fw:
        print(f"⚠️ Chờ {fw.seconds}s do FloodWait...")
        await asyncio.sleep(fw.seconds)
        return False
    except Exception as e:
        # Nếu lỗi do sai loại object, thử ép dùng InviteToChannelRequest một lần nữa
        if "InviteToChannelRequest" in str(e) or "AddChatUserRequest" in str(e):
            try:
                await clone(InviteToChannelRequest(channel=chat_id, users=[input_user]))
                return True
            except: pass
        print(f"❌ Lỗi thêm user {user_id}: {e}")
        return False

# ========== SPAM LOOP (dùng asyncio.Event) ==========
async def spam_loop(c, chat_id, msg_func, stop_key):
    event = stop_events.get(stop_key)
    while event and not event.is_set():
        try:
            text, parse_mode = await msg_func()
            await safe_send(c, chat_id, text, parse_mode)
            # Chờ một khoảng ngắn, nhưng vẫn kiểm tra event
            await asyncio.sleep(DELAY_SPAM + random.uniform(0, 0.3))
        except Exception as e:
            print(f"Lỗi vòng spam: {e}")
            await asyncio.sleep(3)

def start_spam_all(chat_id, msg_func, prefix, use_bots=True, use_clones=False):
    stop_key = f"{prefix}_{chat_id}"
    # Dừng luồng cũ nếu có
    if stop_key in stop_events:
        stop_events[stop_key].set()          # ra hiệu dừng
        if stop_key in spam_tasks:
            for t in spam_tasks[stop_key]:
                t.cancel()
    # Tạo event mới
    ev = asyncio.Event()
    ev.clear()
    stop_events[stop_key] = ev
    tasks = []
    if use_bots:
        for bot in bot_clients:
            tasks.append(asyncio.create_task(spam_loop(bot, chat_id, msg_func, stop_key)))
    if use_clones:
        for clone in clone_clients:
            tasks.append(asyncio.create_task(spam_loop(clone, chat_id, msg_func, stop_key)))
    spam_tasks[stop_key] = tasks

# ========== HANDLERS (SPAM BOT) ==========
@events.register(events.NewMessage(pattern=r'^/qd1$'))
async def qd1(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    async def m(): return get_war(), None
    start_spam_all(event.chat_id, m, "qd1", use_bots=True)
    await temp_reply(event, "🔥 QĐ1 – BOT SPAM WAR")

@events.register(events.NewMessage(pattern=r'^/qd2\s+(@\w+)$'))
async def qd2(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    username = event.pattern_match.group(1)
    async def m(): return f"{get_war()} {username}", None
    start_spam_all(event.chat_id, m, "qd2", use_bots=True)
    await temp_reply(event, f"💢 QĐ2 TAG {username}")

@events.register(events.NewMessage(pattern=r'^/qd3\s+(.+)$'))
async def qd3(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    usernames = re.findall(r'@\w+', event.pattern_match.group(1))
    if not usernames: await temp_reply(event, "❌ Thiếu username"); return
    tags = " ".join(usernames)
    async def m(): return f"{get_war()} {tags}", None
    start_spam_all(event.chat_id, m, "qd3", use_bots=True)
    await temp_reply(event, f"💥 QĐ3 TAG {tags}")

@events.register(events.NewMessage(pattern=r'^/qd4\s+(.+?)\s+(\d+)$'))
async def qd4(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    text, count = event.pattern_match.group(1), min(int(event.pattern_match.group(2)), 500)
    await temp_reply(event, f"🔁 QĐ4 {count} lần")
    stop_key = f"qd4_{event.chat_id}"
    if stop_key in stop_events: stop_events[stop_key].set()
    ev = asyncio.Event(); ev.clear(); stop_events[stop_key] = ev
    async def repeat():
        for i in range(count):
            tasks = [safe_send(bot, event.chat_id, text) for bot in bot_clients]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(DELAY_SPAM)
            if ev.is_set(): break
    spam_tasks[stop_key] = [asyncio.create_task(repeat())]

@events.register(events.NewMessage(pattern=r'^/qd5\s+(\d+)$'))
async def qd5(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    async def m(): return mention_by_id(uid, get_war()), 'markdown'
    start_spam_all(event.chat_id, m, "qd5", use_bots=True)
    await temp_reply(event, f"🆔 QĐ5 TAG ID {uid}")

@events.register(events.NewMessage(pattern=r'^/qd6$'))
async def qd6(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    async def m(): return get_lag(), None
    start_spam_all(event.chat_id, m, "qd6", use_bots=True)
    await temp_reply(event, "🌀 QĐ6 LAG SPAM")

# ========== STOP & DELETE & DELAY ==========
@events.register(events.NewMessage(pattern=r'^/stop$'))
async def stop_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    chat_id = event.chat_id
    stopped = 0
    for key in list(stop_events.keys()):
        # Kiểm tra key kết thúc bằng "_chat_id" (chính xác)
        if key.endswith(f"_{chat_id}"):
            stop_events[key].set()
            if key in spam_tasks:
                for t in spam_tasks[key]: t.cancel()
                del spam_tasks[key]
            stopped += 1
    await temp_reply(event, f"⏹️ Đã dừng {stopped} tiến trình spam")

@events.register(events.NewMessage(pattern=r'^/delete$'))
async def delete_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    count = await delete_all_spam_in_chat(event.chat_id)
    await temp_reply(event, f"🧹 Đã xoá {count} tin spam")

@events.register(events.NewMessage(pattern=r'^/delay\s+(\d+\.?\d*)$'))
async def delay_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    global DELAY_SPAM
    try:
        nd = float(event.pattern_match.group(1))
        if nd < 0.5: nd = 0.5
        DELAY_SPAM = nd
        await temp_reply(event, f"⏱️ DELAY = {DELAY_SPAM}s")
    except:
        await temp_reply(event, "❌ Sai định dạng")

# ========== CAM/CUT (chỉ clone đảm nhận xoá) ==========
@events.register(events.NewMessage(pattern=r'^/cam\s+(\d+)$'))
async def cam_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    auto_delete_users.add(uid)
    await temp_reply(event, f"👁️ Đã CAM ID {uid}")

@events.register(events.NewMessage(pattern=r'^/cut\s+(\d+)$'))
async def cut_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    if event.chat_id > 0: await temp_reply(event, "❌ Chỉ dùng trong group"); return
    auto_delete_group_users.setdefault(event.chat_id, set()).add(uid)
    await temp_reply(event, f"✂️ Đã CUT ID {uid}")

@events.register(events.NewMessage(pattern=r'^/uncam\s+(\d+)$'))
async def uncam_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    auto_delete_users.discard(uid)
    await temp_reply(event, f"✅ Đã UNCAM {uid}")

@events.register(events.NewMessage(pattern=r'^/uncut\s+(\d+)$'))
async def uncut_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    if event.chat_id > 0: return
    auto_delete_group_users.get(event.chat_id, set()).discard(uid)
    await temp_reply(event, f"✅ Đã UNCUT {uid}")

# ========== QUẢN LÝ CLONE/BOT ==========
@events.register(events.NewMessage(pattern=r'^/addbot\s+(.+)$'))
async def addbot_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    token = event.pattern_match.group(1).strip()
    if token in BOT_TOKENS: await temp_reply(event, "⚠️ Token đã tồn tại"); return
    try:
        global bot_clients
        bot = TelegramClient(StringSession(), API_ID, API_HASH)
        await bot.start(bot_token=token)
        me = await bot.get_me()
        BOT_TOKENS.append(token); save_bot_tokens()
        bot_clients.append(bot)
        # Gán handler (không có auto_delete_handler cho bot)
        for h in [qd1, qd2, qd3, qd4, qd5, qd6, stop_cmd, delete_cmd, delay_cmd,
                  cam_cmd, cut_cmd, uncam_cmd, uncut_cmd, join_cmd,
                  addbot_cmd, lbot_cmd, voice_cmd, cpuram_cmd, noi_cmd, tao_cmd,
                  addadmin_cmd, boadmin_cmd, addclone_cmd, lclone_cmd, locclone_cmd,
                  sp1, sp2, sp3, sp4, sp5, sp6, sp7, help_cmd]:
            bot.add_event_handler(h)
        await temp_reply(event, f"✅ Đã thêm bot @{me.username}")
    except Exception as e:
        await temp_reply(event, f"❌ Lỗi: {e}")

@events.register(events.NewMessage(pattern=r'^/lbot$'))
async def lbot_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    if not bot_clients: await temp_reply(event, "📭 Chưa có bot"); return
    txt = "**DANH SÁCH BOT:**\n"
    for i, bot in enumerate(bot_clients, 1):
        try:
            me = await bot.get_me()
            txt += f"{i}. @{me.username} – {me.first_name}\n"
        except: txt += f"{i}. Bot lỗi\n"
    await temp_reply(event, txt, parse_mode='markdown', ttl=15)

@events.register(events.NewMessage(pattern=r'^/addclone\s+(.+)$'))
async def addclone_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    session_str = event.pattern_match.group(1).strip()
    if session_str in CLONE_SESSIONS: await temp_reply(event, "⚠️ Session đã tồn tại"); return
    try:
        global clone_clients
        clone = TelegramClient(StringSession(session_str), API_ID, API_HASH)
        await clone.start()
        me = await clone.get_me()
        CLONE_SESSIONS.append(session_str); save_clone_sessions()
        clone_clients.append(clone)
        # Gán handler + auto_delete_handler cho clone
        handlers = [qd1, qd2, qd3, qd4, qd5, qd6, stop_cmd, delete_cmd, delay_cmd,
                    cam_cmd, cut_cmd, uncam_cmd, uncut_cmd, join_cmd,
                    addbot_cmd, lbot_cmd, voice_cmd, cpuram_cmd, noi_cmd, tao_cmd,
                    addadmin_cmd, boadmin_cmd, addclone_cmd, lclone_cmd, locclone_cmd,
                    sp1, sp2, sp3, sp4, sp5, sp6, sp7, auto_delete_handler, help_cmd]
        for h in handlers: clone.add_event_handler(h)
        await temp_reply(event, f"✅ Đã thêm clone @{me.username}")
    except Exception as e:
        await temp_reply(event, f"❌ Lỗi: {e}")

@events.register(events.NewMessage(pattern=r'^/lclone$'))
async def lclone_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    if not clone_clients: await temp_reply(event, "📭 Chưa có clone"); return
    txt = "**DANH SÁCH CLONE:**\n"
    for i, clone in enumerate(clone_clients, 1):
        try:
            me = await clone.get_me()
            txt += f"{i}. @{me.username} – 🟢 Live\n"
        except: txt += f"{i}. 🔴 Die\n"
    await temp_reply(event, txt, parse_mode='markdown', ttl=15)

@events.register(events.NewMessage(pattern=r'^/locclone$'))
async def locclone_cmd(event):
    global clone_clients
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    await temp_reply(event, "🔄 Đang lọc clone chết...")
    alive, alive_sess = [], []
    for i, clone in enumerate(clone_clients):
        try:
            await clone.get_me()
            alive.append(clone)
            alive_sess.append(CLONE_SESSIONS[i])
        except: pass
    
    clone_clients = alive
    CLONE_SESSIONS.clear(); CLONE_SESSIONS.extend(alive_sess)
    save_clone_sessions()
    await temp_reply(event, f"✅ Còn {len(clone_clients)} clone sống")

# ========== TIỆN ÍCH KHÁC ==========
@events.register(events.NewMessage(pattern=r'^/voice\s+(.+)$'))
async def voice_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    text = event.pattern_match.group(1).strip()
    if not text: await temp_reply(event, "❌ Nhập nội dung"); return
    if not bot_clients: await temp_reply(event, "❌ Chưa có bot"); return
    await temp_reply(event, "🎤 Đang tạo voice...")
    try:
        communicate = edge_tts.Communicate(text, "vi-VN-HoaiMyNeural")
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio": tmp.write(chunk["data"])
            tmp_path = tmp.name
        await bot_clients[0].send_file(event.chat_id, tmp_path, voice_note=True)
        os.unlink(tmp_path)
    except Exception as e: await temp_reply(event, f"❌ {e}")

@events.register(events.NewMessage(pattern=r'^/cpuram$'))
async def cpuram_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory().percent
    await temp_reply(event, f"🖥️ CPU: {cpu}% | 💾 RAM: {ram}%")

@events.register(events.NewMessage(pattern=r'^/noi\s+(.+?)\s+(\-?\d+)$'))
async def noi_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    text, target = event.pattern_match.group(1), int(event.pattern_match.group(2))
    if not bot_clients: await temp_reply(event, "❌ Không có bot"); return
    try:
        await bot_clients[0].send_message(target, text)
        await temp_reply(event, f"✅ Đã gửi đến {target}")
    except Exception as e: await temp_reply(event, f"❌ {e}")

@events.register(events.NewMessage(pattern=r'^/tao\s+(\d+)$'))
async def tao_cmd(event):
    if not is_admin(event.sender_id): return
    # Chỉ cho phép clone đầu tiên thực hiện để tránh tạo nhiều group
    if not clone_clients or event.client != clone_clients[0]: return 
    
    await delete_cmd_msg(event)
    user_id = int(event.pattern_match.group(1))
    clone = clone_clients[0]
    
    try:
        # BƯỚC 1: "MỒI" DỮ LIỆU ĐỂ LẤY ENTITY
        try:
            await clone.send_message(user_id, ".") 
            await asyncio.sleep(1.5) 
        except: pass 

        title = f"War_{random.randint(10000, 99999)}_{user_id}"
        
        # BƯỚC 2: TẠO NHÓM
        await clone(CreateChatRequest(title=title, users=[user_id]))
        await asyncio.sleep(2) 
        
        # BƯỚC 3: QUÉT ID BẰNG DIALOGS (CHÍNH XÁC 100%)
        chat_id = None
        async for dialog in clone.iter_dialogs(limit=15):
            if dialog.name == title:
                chat_id = dialog.id
                break
        
        if not chat_id:
            await temp_reply(event, "❌ Tạo thành công nhưng không lấy được ID.")
            return

        # BƯỚC 4: KÉO ADMIN & BOT
        for admin_id in ADMIN_IDS:
            await smart_add_user(clone, chat_id, admin_id)
            await asyncio.sleep(0.5)
            
        for bot in bot_clients:
            try:
                bot_me = await bot.get_me()
                await smart_add_user(clone, chat_id, bot_me.id, is_bot=True)
                await asyncio.sleep(0.8)
            except: continue
                
        await temp_reply(event, f"✅ Đã tạo group cho ID {user_id}!\n🆔 ID: `{chat_id}`")
        
    except Exception as e: 
        await temp_reply(event, f"❌ Lỗi: {str(e)}")
        
@events.register(events.NewMessage(pattern=r'^/tao2\s+(@\w+)$'))
async def tao2_cmd(event):
    if not is_admin(event.sender_id): return
    if not clone_clients or event.client != clone_clients[0]: return 
    
    await delete_cmd_msg(event)
    username = event.pattern_match.group(1).strip()
    clone = clone_clients[0]
    
    try:
        # BƯỚC 1: TÌM KIẾM USERNAME
        user_entity = await resolve_user_by_username(clone, username)
        if not user_entity:
            await temp_reply(event, f"❌ Không tìm thấy Username {username}")
            return

        title = f"War_{random.randint(10000, 99999)}_{username.replace('@','')}"
        
        # BƯỚC 2: TẠO NHÓM
        await clone(CreateChatRequest(title=title, users=[user_entity]))
        await asyncio.sleep(2)
        
        # BƯỚC 3: QUÉT ID
        chat_id = None
        async for dialog in clone.iter_dialogs(limit=15):
            if dialog.name == title:
                chat_id = dialog.id
                break
        
        if not chat_id:
            await temp_reply(event, "❌ Tạo thành công nhưng không lấy được ID.")
            return

        # BƯỚC 4: KÉO ADMIN & BOT
        for admin_id in ADMIN_IDS:
            await smart_add_user(clone, chat_id, admin_id)
            await asyncio.sleep(0.5)
            
        for bot in bot_clients:
            try:
                bot_me = await bot.get_me()
                await smart_add_user(clone, chat_id, bot_me.id, is_bot=True)
                await asyncio.sleep(0.8)
            except: continue
                
        await temp_reply(event, f"✅ Đã tạo nhóm cho {username} thành công!\n🆔 ID: `{chat_id}`")
        
    except Exception as e: 
        await temp_reply(event, f"❌ Lỗi: {str(e)}")

@events.register(events.NewMessage(pattern=r'^/join\s+(.+)$'))
async def join_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    link = event.pattern_match.group(1).strip()
    if not clone_clients: await temp_reply(event, "❌ Cần clone"); return
    clone = clone_clients[0]
    await temp_reply(event, "🔄 Đang join...")
    try:
        if 'joinchat' in link or '+' in link:
            await clone(ImportChatInviteRequest(link.split('/')[-1].lstrip('+')))
        else:
            username = link.replace('https://t.me/', '').replace('@', '')
            await clone(JoinChannelRequest(username))
        # Lấy chat entity
        try: chat = await clone.get_entity(link)
        except: chat = await clone.get_entity(username)
        chat_id = chat.id
        added = 0
        for bot in bot_clients:
            me = await bot.get_me()
            if await smart_add_user(clone, chat_id, me.id, is_bot=True):
                added += 1
            await asyncio.sleep(2)
        await temp_reply(event, f"✅ Đã thêm {added}/{len(bot_clients)} bot")
    except Exception as e: await temp_reply(event, f"❌ {e}")

@events.register(events.NewMessage(pattern=r'^/addadmin\s+(\d+)$'))
async def addadmin_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    if uid in ADMIN_IDS: await temp_reply(event, "⚠️ Đã là admin"); return
    ADMIN_IDS.append(uid); save_admins()
    await temp_reply(event, f"✅ Đã thêm admin {uid}")

@events.register(events.NewMessage(pattern=r'^/boadmin\s+(\d+)$'))
async def boadmin_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    if uid not in ADMIN_IDS: await temp_reply(event, "⚠️ Không phải admin"); return
    ADMIN_IDS.remove(uid); save_admins()
    await temp_reply(event, f"✅ Đã xoá admin {uid}")

# ========== SPAM CLONE (SP1..SP7) ==========
@events.register(events.NewMessage(pattern=r'^/sp1$'))
async def sp1(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    async def m(): return get_war(), None
    start_spam_all(event.chat_id, m, "sp1", use_clones=True)
    await temp_reply(event, "🔥 SP1 – WAR")

@events.register(events.NewMessage(pattern=r'^/sp2\s+(@\w+)$'))
async def sp2(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    username = event.pattern_match.group(1)
    async def m(): return f"{get_war()} {username}", None
    start_spam_all(event.chat_id, m, "sp2", use_clones=True)
    await temp_reply(event, f"💢 SP2 TAG {username}")

@events.register(events.NewMessage(pattern=r'^/sp3\s+(.+)$'))
async def sp3(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    usernames = re.findall(r'@\w+', event.pattern_match.group(1))
    if not usernames: await temp_reply(event, "❌ Thiếu username"); return
    tags = " ".join(usernames)
    async def m(): return f"{get_war()} {tags}", None
    start_spam_all(event.chat_id, m, "sp3", use_clones=True)
    await temp_reply(event, f"💥 SP3 TAG {tags}")

@events.register(events.NewMessage(pattern=r'^/sp4\s+(.+?)\s+(\d+)$'))
async def sp4(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    text, count = event.pattern_match.group(1), min(int(event.pattern_match.group(2)), 500)
    await temp_reply(event, f"🔁 SP4 {count} lần")
    stop_key = f"sp4_{event.chat_id}"
    if stop_key in stop_events: stop_events[stop_key].set()
    ev = asyncio.Event(); ev.clear(); stop_events[stop_key] = ev
    async def repeat():
        for i in range(count):
            tasks = [safe_send(clone, event.chat_id, text) for clone in clone_clients]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(DELAY_SPAM)
            if ev.is_set(): break
    spam_tasks[stop_key] = [asyncio.create_task(repeat())]

@events.register(events.NewMessage(pattern=r'^/sp5\s+(\d+)$'))
async def sp5(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    async def m(): return mention_by_id(uid, get_war()), 'markdown'
    start_spam_all(event.chat_id, m, "sp5", use_clones=True)
    await temp_reply(event, f"🆔 SP5 TAG ID {uid}")

@events.register(events.NewMessage(pattern=r'^/sp6$'))
async def sp6(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    async def m(): return get_lag(), None
    start_spam_all(event.chat_id, m, "sp6", use_clones=True)
    await temp_reply(event, "🌀 SP6 LAG")

@events.register(events.NewMessage(pattern=r'^/sp7\s+(\d+)$'))
async def sp7(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    uid = int(event.pattern_match.group(1))
    async def m(): return mention_by_id(uid, get_war()), 'markdown'
    start_spam_all(event.chat_id, m, "sp7", use_clones=True)
    await temp_reply(event, f"🎯 SP7 TAG ID {uid}")

# ========== AUTO DELETE (CHỈ CLONE THỰC HIỆN) ==========
@events.register(events.NewMessage(incoming=True))
async def auto_delete_handler(event):
    # Chỉ xóa nếu là clone (client thuộc clone_clients)
    if event.client not in clone_clients:
        return
    if event.is_private:
        if event.sender_id in auto_delete_users:
            try: await event.delete()
            except: pass
    else:
        if event.chat_id in auto_delete_group_users and event.sender_id in auto_delete_group_users[event.chat_id]:
            try: await event.delete()
            except: pass

# ========== HELP ==========
@events.register(events.NewMessage(pattern=r'^/help$|^/start$'))
async def help_cmd(event):
    if not is_admin(event.sender_id): return
    await delete_cmd_msg(event)
    text = (
        "**🤖 CUONGDEVGPT - MENU LỆNH**\n\n"
        "**🔥 SPAM BOT (QĐ)**\n"
        "`/qd1` - Spam war\n`/qd2 @user` - Tag 1\n`/qd3 @a @b` - Tag nhiều\n"
        "`/qd4 text số_lần` - Spam lặp\n`/qd5 ID` - Tag by ID\n`/qd6` - Lag\n\n"
        "**💀 SPAM CLONE (SP)**\n"
        "`/sp1` `/sp2` `/sp3` `/sp4` `/sp5` `/sp6` `/sp7`\n\n"
        "**🛠️ QUẢN LÝ**\n"
        "`/addbot token` `/lbot` `/addclone session` `/lclone` `/locclone`\n"
        "`/addadmin ID` `/boadmin ID`\n\n"
        "**📢 TIỆN ÍCH**\n"
        "`/voice text` `/cpuram` `/noi text chat_id` `/tao user_id` `/join link`\n\n"
        "**⚙️ ĐIỀU KHIỂN**\n"
        "`/stop` `/delete` `/delay giây` `/cam ID` `/cut ID` `/uncam` `/uncut`"
    )
    await temp_reply(event, text, parse_mode='markdown', ttl=25)

# ========== KHỞI ĐỘNG ==========
async def main():
    global bot_clients, clone_clients
    reload_phrases()
    print("🔥 CUONGDEVGPT ĐA BOT + CLONE KHỞI ĐỘNG...")

    # Khởi động bot
    for token in BOT_TOKENS:
        try:
            bot = TelegramClient(StringSession(), API_ID, API_HASH)
            await bot.start(bot_token=token)
            me = await bot.get_me()
            print(f"✅ BOT @{me.username}")
            bot_clients.append(bot)
            # Gán handler (không auto_delete)
            for h in [qd1, qd2, qd3, qd4, qd5, qd6, stop_cmd, delete_cmd, delay_cmd,
                      cam_cmd, cut_cmd, uncam_cmd, uncut_cmd, join_cmd,
                      addbot_cmd, lbot_cmd, voice_cmd, cpuram_cmd, noi_cmd, tao_cmd,
                      addadmin_cmd, boadmin_cmd, addclone_cmd, lclone_cmd, locclone_cmd,
                      sp1, sp2, sp3, sp4, sp5, sp6, sp7, help_cmd,tao2_cmd]:
                bot.add_event_handler(h)
        except Exception as e: print(f"❌ BOT LỖI: {e}")

    # Khởi động clone
    for sess in CLONE_SESSIONS:
        try:
            clone = TelegramClient(StringSession(sess), API_ID, API_HASH)
            await clone.start()
            me = await clone.get_me()
            print(f"✅ CLONE @{me.username}")
            clone_clients.append(clone)
            for h in [qd1, qd2, qd3, qd4, qd5, qd6, stop_cmd, delete_cmd, delay_cmd,
                      cam_cmd, cut_cmd, uncam_cmd, uncut_cmd, join_cmd,
                      addbot_cmd, lbot_cmd, voice_cmd, cpuram_cmd, noi_cmd, tao_cmd,
                      addadmin_cmd, boadmin_cmd, addclone_cmd, lclone_cmd, locclone_cmd,
                      sp1, sp2, sp3, sp4, sp5, sp6, sp7, auto_delete_handler, help_cmd,tao2_cmd]:
                clone.add_event_handler(h)
        except errors.FloodWaitError as e:
            print(f"⚠️ Bot dính FloodWait: Cần chờ {e.seconds} giây. Đang bỏ qua bot này...")
            continue # Bỏ qua bot bị chặn để các bot/clone khác vẫn có thể chạy
        except Exception as e: 
            print(f"❌ BOT LỖI: {e}")

    if not bot_clients and not clone_clients:
        print("❌ Không có bot/clone hoạt động."); return

    print("⚔️ SẴN SÀNG! GÕ /help")
    all_clients = bot_clients + clone_clients
    try:
        await asyncio.gather(*[c.run_until_disconnected() for c in all_clients])
    except KeyboardInterrupt: print("👋 TẠM BIỆT!")
    except Exception as e: print(f"Lỗi: {e}")

if __name__ == "__main__":
    asyncio.run(main())

#Wormgpt Cường Dev Don't Delete for copyright
