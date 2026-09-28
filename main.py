"""
============================================================
   GHOST AUTO-REACTOR v4.0
   Whitelist-Based Multi-Channel Auto-Reaction Bot
   Real Sessions Only • Auto-Join • Per-User Config
   + Auto-Cleanup Expired Sessions
   + @username Support
   + Full Order Notifications
   + Owner as User
   + Quick Reaction (post link)
   + Smart Emoji Fallback (invalid emoji → try another from pool)
   + Session Retry (failed session → next session until count done)
   Credit: @Anonymous_User_37
============================================================
"""
import os, sys, time, random, asyncio, re, hashlib, json
import sqlite3, requests, traceback, logging
from datetime import datetime, timedelta

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession
from telethon.tl.functions.messages import (
    ImportChatInviteRequest, SendReactionRequest,
    CheckChatInviteRequest)
from telethon.tl.functions.channels import (
    JoinChannelRequest, GetParticipantRequest)
from telethon.tl.types import (
    Channel, Chat, ReactionEmoji)

try:
    from telethon.tl.types import KeyboardButtonStyle
    HAS_BUTTON_STYLE = True
except ImportError:
    KeyboardButtonStyle = None
    HAS_BUTTON_STYLE = False

from telethon.errors import FloodWaitError
from telethon.errors.rpcerrorlist import (
    MessageIdInvalidError, MessageNotModifiedError,
    UserAlreadyParticipantError)

from aiohttp import web

# ══════════════════════ GITHUB SYNC (Optional) ══════════════════════
try:
    import github_sync
    HAS_GH_SYNC = True
except ImportError:
    HAS_GH_SYNC = False
    class _DummyGH:
        LOCAL_DB_PATH = "ghost_users.db"
        def start_sync_thread(self): pass
        def mark_dirty(self): pass
        def is_enabled(self): return False
        def download_sessions(self): return False, "disabled"
        def get_stats(self):
            return {"downloads": 0, "errors": 0}
    github_sync = _DummyGH()

# ══════════════════════ LOGGING ══════════════════════
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ══════════════════════ TELEGRAM API ══════════════════════
API_ID = 30217812
API_HASH = "d21066a90786cf2dd348b907ece69d24"

# ✅ NAYA BOT TOKEN
BOT_TOKEN = "8901488334:AAHRyiuFXcyvdM_cd_BnZ1lGQ_hhHQOOWUs"

# ══════════════════════ OWNER CONFIG ══════════════════════
OWNER_USERNAME_DEFAULT = "Anonymous_User_37"
OWNER_ID_DEFAULT = 8762845215

# ══════════════════════ PATHS ══════════════════════
SESSIONS_DIR = os.getenv("SESSIONS_DIR", "sessions")
MAX_CLIENTS = int(os.getenv("MAX_CLIENTS", "30"))
DB_FILE = "ghost_auto.db"

# ══════════════════════ INTERVALS ══════════════════════
CHECK_INTERVAL = 600          # 10 minutes
CLEANUP_INTERVAL = 60         # 1 min
HEALTH_INTERVAL = 300         # 5 min
DAILY_SUMMARY_TIME = "21:00"
SESSION_AUTO_CLEANUP_INTERVAL = 1800  # 30 min

# ══════════════════════ REACTIONS ══════════════════════
DEFAULT_REACTIONS = ["❤️", "👍", "🔥"]

FALLBACK_REACTIONS = ["❤️", "👍", "🔥", "🥰", "🎉", "💯", "😍", "👏"]

ALL_REACTIONS = [
    "❤️","🔥","🥰","😍","👍","😇","👀","😎","💯","🎉","🤩","🥳","😁","😂",
    "🤣","😊","🙏","👏","💪","⚡","🌚","🌭","🍾","💋","🖕","😈","🤝","🎃",
    "👻","🤡","🤔","🤨","😐","😑","😶","🙄","😏","😣","😥","😮","🤐","😯",
    "😪","😫","🥱","😴","😌","😛","😜","🤪"
]

# ══════════════════════ LIMITS ══════════════════════
MIN_REACTIONS = 1
MAX_REACTIONS = 100
REAL_COOLDOWN = 1.5
REAL_ENTITY_TIMEOUT = 15
REAL_REACTION_TIMEOUT = 15
FLOOD_SAFETY = 5
MAX_AUTO_RETRIES = 2

# ══════════════════════ LANGUAGES ══════════════════════
LANGUAGES = {
    "en": "🇬🇧 English",
    "ur": "🇵🇰 اردو",
    "hi": "🇮🇳 हिन्दी",
    "ar": "🇸🇦 العربية",
    "ru": "🇷🇺 Русский",
    "es": "🇪🇸 Español",
    "id": "🇮🇩 Indonesia"
}

# ══════════════════════ GLOBAL STATE ══════════════════════
admin_client = None
bot = TelegramClient("ghost_auto_bot", API_ID, API_HASH)

REAL_CLIENTS = {}
REAL_CLIENT_LOCK = asyncio.Lock()
REAL_FLOOD_UNTIL = {}

TASK_RUNNING = False
TASK_USER_ID = None

ENTITY_CACHE = {}
ENTITY_CACHE_TTL = 3600
_LAST_EDIT_TIME = {}

USER_STATES = {}
FAILED_TRACKER = {}

_start_time = time.time()


# ══════════════════════ HELPERS ══════════════════════
def DIV():
    return "━━━━━━━━━━━━━━━━━━━━━━━━━"


STAR_LINE = "✦ ─────────── ✦ ─────────── ✦"
SPARKLE = "✨"


def progress_bar(cur, total, width=12):
    if total <= 0:
        return "░" * width
    f = min(int((cur / total) * width), width)
    return "█" * f + "░" * (width - f)


def DBG(msg, level="info"):
    ts = datetime.now().strftime("%H:%M:%S")
    ic = {
        "info": "🔍", "ok": "✅", "fail": "❌", "warn": "⚠️",
        "api": "🌐", "react": "💫", "join": "🚪", "sync": "🔄",
        "db": "💾", "flood": "🌊", "watch": "📡", "task": "🎯",
        "clean": "🧹"
    }
    print(f"[{ts}] {ic.get(level, '•')} [DBG] {msg}", flush=True)


def D_sep(t):
    print(f"\n{'='*60}\n  {t}\n{'='*60}", flush=True)


def D_err(e, ctx=""):
    if ctx in ("startup", "boot"):
        return
    print(f"\n⚠️ {ctx}: {str(e)[:120]}", flush=True)


def sanitize_title(title, limit=60):
    if not title:
        return "Unknown"
    t = str(title).replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return t[:limit]


# ══════════════════════ CONFIG HELPERS ══════════════════════
def cfg_get(k, d=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            r = conn.execute("SELECT value FROM config WHERE key=?",
                             (k,)).fetchone()
            return r[0] if r else d
        finally:
            conn.close()
    except Exception:
        return d


def cfg_set(k, v):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute("INSERT OR REPLACE INTO config(key, value) VALUES(?, ?)",
                         (k, str(v)))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def get_owner_username():
    return cfg_get("owner_username", OWNER_USERNAME_DEFAULT)


def get_owner_id():
    try:
        return int(cfg_get("owner_id", str(OWNER_ID_DEFAULT)))
    except Exception:
        return OWNER_ID_DEFAULT


def OWNER_IS(uid):
    return uid == get_owner_id()


# ══════════════════════ SESSION HELPERS ══════════════════════
def discover_sessions():
    if not os.path.isdir(SESSIONS_DIR):
        return []
    try:
        return sorted([
            f for f in os.listdir(SESSIONS_DIR)
            if f.endswith(".session")
        ])
    except Exception:
        return []


def is_session_flooded(sf):
    u = REAL_FLOOD_UNTIL.get(sf)
    if u is None:
        return False
    if datetime.now() >= u:
        REAL_FLOOD_UNTIL.pop(sf, None)
        return False
    return True


def mark_session_flooded(sf, sec):
    REAL_FLOOD_UNTIL[sf] = datetime.now() + timedelta(seconds=sec)


def count_available_sessions():
    return sum(1 for f in discover_sessions() if not is_session_flooded(f))


def count_flooded_sessions():
    return sum(1 for f in discover_sessions() if is_session_flooded(f))


def get_sessions_status():
    files = discover_sessions()
    return {
        "total": len(files),
        "available": len(files) - count_flooded_sessions(),
        "flooded": count_flooded_sessions(),
        "loaded": len(REAL_CLIENTS),
        "files": files
    }


# ══════════════════════ DATABASE INIT ══════════════════════
def db_init():
    if "/" in DB_FILE:
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, value TEXT)""")

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        added_by INTEGER,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 1,
        notify INTEGER DEFAULT 1,
        language TEXT DEFAULT 'en',
        total_reactions INTEGER DEFAULT 0,
        notes TEXT)""")

    c.execute("""CREATE TABLE IF NOT EXISTS user_channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        chat_id INTEGER,
        chat_link TEXT,
        chat_title TEXT,
        reaction_count INTEGER DEFAULT 5,
        emoji_mode TEXT DEFAULT 'default',
        custom_emojis TEXT,
        is_active INTEGER DEFAULT 1,
        joined INTEGER DEFAULT 0,
        last_post_id INTEGER DEFAULT 0,
        last_run TEXT,
        total_reactions INTEGER DEFAULT 0,
        fail_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, chat_id))""")

    c.execute("""CREATE TABLE IF NOT EXISTS auto_reactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        channel_id INTEGER,
        chat_title TEXT,
        post_id INTEGER,
        reactions_sent INTEGER,
        status TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    c.execute("""CREATE TABLE IF NOT EXISTS sessions_log (
        filename TEXT PRIMARY KEY,
        is_flooded INTEGER DEFAULT 0,
        flood_until TEXT,
        last_used TEXT,
        total_used INTEGER DEFAULT 0,
        added_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    c.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        is_read INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("PRAGMA cache_size=2000")
        c.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        pass

    indices = [
        "CREATE INDEX IF NOT EXISTS idx_channels_user ON user_channels(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_channels_active ON user_channels(is_active)",
        "CREATE INDEX IF NOT EXISTS idx_reactions_user ON auto_reactions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_reactions_date ON auto_reactions(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_notifs_user ON notifications(user_id, is_read)",
    ]
    for sql in indices:
        try:
            c.execute(sql)
        except Exception:
            pass

    defaults = [
        ("owner_username", OWNER_USERNAME_DEFAULT),
        ("owner_id", str(OWNER_ID_DEFAULT)),
        ("notify_user_default", "1"),
        ("multi_lang_enabled", "1"),
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO config(key, value) VALUES(?, ?)", (k, v))

    conn.commit()
    conn.close()
    DBG("Database initialized", "db")


def cfg_bool(k, d=True):
    return cfg_get(k, "1" if d else "0") == "1"


def cfg_toggle(k):
    n = not cfg_bool(k)
    cfg_set(k, "1" if n else "0")
    return n


# ══════════════════════ WHITELIST — USERS ══════════════════════
def db_add_user(user_id, username=None, first_name=None, added_by=None):
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        ex = c.execute("SELECT user_id FROM users WHERE user_id=?",
                       (user_id,)).fetchone()
        if ex:
            c.execute("""UPDATE users SET username=COALESCE(?, username),
                first_name=COALESCE(?, first_name),
                is_active=1 WHERE user_id=?""",
                (username, first_name, user_id))
            result = "updated"
        else:
            c.execute("""INSERT INTO users (user_id, username, first_name,
                added_by) VALUES (?, ?, ?, ?)""",
                (user_id, username, first_name, added_by))
            result = "added"
        conn.commit()
        return True, result
    finally:
        conn.close()


def db_remove_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM user_channels WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM auto_reactions WHERE user_id=?", (user_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def db_get_user(user_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        return conn.execute("""SELECT user_id, username, first_name,
            added_by, added_at, is_active, notify, language,
            total_reactions, notes FROM users WHERE user_id=?""",
            (user_id,)).fetchone()
    finally:
        conn.close()


def db_is_whitelisted(user_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        r = conn.execute("""SELECT is_active FROM users WHERE user_id=?""",
                         (user_id,)).fetchone()
        return bool(r and r[0] == 1)
    finally:
        conn.close()


def db_list_users(limit=50, offset=0):
    conn = sqlite3.connect(DB_FILE)
    try:
        return conn.execute("""SELECT user_id, username, first_name,
            is_active, added_at FROM users
            ORDER BY added_at DESC LIMIT ? OFFSET ?""",
            (limit, offset)).fetchall()
    finally:
        conn.close()


def db_count_users():
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


def db_count_active_users():
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            return conn.execute("SELECT COUNT(*) FROM users WHERE is_active=1"
                                ).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


def db_toggle_user_active(user_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        r = c.execute("SELECT is_active FROM users WHERE user_id=?",
                      (user_id,)).fetchone()
        if not r:
            return False
        new_state = 0 if r[0] else 1
        c.execute("UPDATE users SET is_active=? WHERE user_id=?",
                  (new_state, user_id))
        conn.commit()
        return new_state == 1
    finally:
        conn.close()


def db_toggle_user_notify(user_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        r = c.execute("SELECT notify FROM users WHERE user_id=?",
                      (user_id,)).fetchone()
        if not r:
            return False
        new_state = 0 if r[0] else 1
        c.execute("UPDATE users SET notify=? WHERE user_id=?",
                  (new_state, user_id))
        conn.commit()
        return new_state == 1
    finally:
        conn.close()


def db_set_user_lang(user_id, lang):
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("UPDATE users SET language=? WHERE user_id=?",
                     (lang, user_id))
        conn.commit()
    finally:
        conn.close()


def db_set_user_notes(user_id, notes):
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("UPDATE users SET notes=? WHERE user_id=?",
                     (notes[:500], user_id))
        conn.commit()
    finally:
        conn.close()


def db_find_user_by_username(username):
    if not username:
        return None
    username = username.lstrip("@")
    conn = sqlite3.connect(DB_FILE)
    try:
        r = conn.execute("""SELECT user_id FROM users
            WHERE LOWER(username)=LOWER(?)""", (username,)).fetchone()
        return r[0] if r else None
    finally:
        conn.close()


# ══════════════════════ USER CHANNELS ══════════════════════
def db_add_channel(user_id, chat_link, chat_title=None,
                   chat_id=None, reaction_count=5,
                   emoji_mode="default", custom_emojis=None):
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        es = ",".join(custom_emojis) if custom_emojis else None
        c.execute("""INSERT INTO user_channels
            (user_id, chat_id, chat_link, chat_title, reaction_count,
             emoji_mode, custom_emojis)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, chat_id, chat_link, chat_title,
             reaction_count, emoji_mode, es))
        conn.commit()
        return c.lastrowid
    finally:
        conn.close()


def db_list_channels(user_id=None, active_only=False):
    conn = sqlite3.connect(DB_FILE)
    try:
        base = """SELECT id, user_id, chat_id, chat_link, chat_title,
            reaction_count, emoji_mode, custom_emojis, is_active,
            joined, last_post_id, last_run, total_reactions, fail_count,
            created_at FROM user_channels"""
        if user_id and active_only:
            return conn.execute(base + """ WHERE user_id=? AND is_active=1
                ORDER BY id DESC""", (user_id,)).fetchall()
        if user_id:
            return conn.execute(base + " WHERE user_id=? ORDER BY id DESC",
                                (user_id,)).fetchall()
        if active_only:
            return conn.execute(base + " WHERE is_active=1 ORDER BY user_id",
                                ).fetchall()
        return conn.execute(base + " ORDER BY user_id, id DESC").fetchall()
    finally:
        conn.close()


def db_get_channel(channel_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        return conn.execute("""SELECT id, user_id, chat_id, chat_link,
            chat_title, reaction_count, emoji_mode, custom_emojis,
            is_active, joined, last_post_id, last_run, total_reactions,
            fail_count, created_at FROM user_channels WHERE id=?""",
            (channel_id,)).fetchone()
    finally:
        conn.close()


def db_update_channel(channel_id, **kw):
    if not kw:
        return False
    fs, vs = [], []
    for k, v in kw.items():
        fs.append(f"{k}=?")
        vs.append(v)
    vs.append(channel_id)
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute(f"UPDATE user_channels SET {', '.join(fs)} WHERE id=?",
                     tuple(vs))
        conn.commit()
        return True
    finally:
        conn.close()


def db_delete_channel(channel_id, user_id=None):
    conn = sqlite3.connect(DB_FILE)
    try:
        if user_id:
            conn.execute("DELETE FROM user_channels WHERE id=? AND user_id=?",
                         (channel_id, user_id))
        else:
            conn.execute("DELETE FROM user_channels WHERE id=?", (channel_id,))
        conn.commit()
    finally:
        conn.close()


def db_toggle_channel(channel_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        r = c.execute("SELECT is_active FROM user_channels WHERE id=?",
                      (channel_id,)).fetchone()
        if not r:
            return False
        new_state = 0 if r[0] else 1
        c.execute("UPDATE user_channels SET is_active=? WHERE id=?",
                  (new_state, channel_id))
        conn.commit()
        return new_state == 1
    finally:
        conn.close()


def db_count_channels(user_id=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            if user_id:
                return conn.execute("""SELECT COUNT(*) FROM user_channels
                    WHERE user_id=?""", (user_id,)).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM user_channels"
                                ).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


def db_count_active_channels(user_id=None):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            if user_id:
                return conn.execute("""SELECT COUNT(*) FROM user_channels
                    WHERE user_id=? AND is_active=1""",
                    (user_id,)).fetchone()[0]
            return conn.execute("""SELECT COUNT(*) FROM user_channels
                WHERE is_active=1""").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


def db_update_channel_last_post(channel_id, post_id, reactions_sent=0):
    conn = sqlite3.connect(DB_FILE)
    try:
        c = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""UPDATE user_channels SET last_post_id=?,
            last_run=?, total_reactions = total_reactions + ?
            WHERE id=?""", (post_id, now_str, reactions_sent, channel_id))
        r = c.execute("SELECT user_id FROM user_channels WHERE id=?",
                      (channel_id,)).fetchone()
        if r:
            c.execute("""UPDATE users SET total_reactions =
                total_reactions + ? WHERE user_id=?""",
                (reactions_sent, r[0]))
        conn.commit()
    finally:
        conn.close()


def db_channel_already_exists(user_id, chat_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        r = conn.execute("""SELECT 1 FROM user_channels
            WHERE user_id=? AND chat_id=?""", (user_id, chat_id)).fetchone()
        return r is not None
    finally:
        conn.close()


# ══════════════════════ AUTO-REACTIONS LOG ══════════════════════
def db_log_reaction(user_id, channel_id, chat_title, post_id,
                    reactions_sent, status="ok"):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            c = conn.cursor()
            safe_title = sanitize_title(chat_title)
            c.execute("""INSERT INTO auto_reactions
                (user_id, channel_id, chat_title, post_id,
                 reactions_sent, status)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, channel_id, safe_title, post_id,
                 reactions_sent, status))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def db_reactions_today():
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            t = datetime.now().strftime("%Y-%m-%d")
            return conn.execute("""SELECT COALESCE(SUM(reactions_sent),0)
                FROM auto_reactions
                WHERE status='ok' AND DATE(created_at)=?""",
                (t,)).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


def db_total_reactions():
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            return conn.execute("""SELECT COALESCE(SUM(reactions_sent),0)
                FROM auto_reactions WHERE status='ok'""").fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


def db_reactions_by_user(user_id, limit=10):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            return conn.execute("""SELECT chat_title, post_id,
                reactions_sent, status, created_at FROM auto_reactions
                WHERE user_id=? ORDER BY id DESC LIMIT ?""",
                (user_id, limit)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []


def db_reactions_by_channel(channel_id, limit=10):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            return conn.execute("""SELECT chat_title, post_id,
                reactions_sent, status, created_at FROM auto_reactions
                WHERE channel_id=? ORDER BY id DESC LIMIT ?""",
                (channel_id, limit)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []


def db_recent_reactions(limit=15):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            return conn.execute("""SELECT user_id, chat_title,
                post_id, reactions_sent, status, created_at
                FROM auto_reactions ORDER BY id DESC LIMIT ?""",
                (limit,)).fetchall()
        finally:
            conn.close()
    except Exception:
        return []


# ══════════════════════ NOTIFICATIONS ══════════════════════
def db_add_notification(user_id, msg):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            conn.execute("""INSERT INTO notifications (user_id, message)
                VALUES (?, ?)""", (user_id, msg))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def db_get_notifications(user_id, limit=10):
    conn = sqlite3.connect(DB_FILE)
    try:
        return conn.execute("""SELECT id, message, is_read, created_at
            FROM notifications WHERE user_id=?
            ORDER BY id DESC LIMIT ?""", (user_id, limit)).fetchall()
    finally:
        conn.close()


def db_mark_notifications_read(user_id):
    conn = sqlite3.connect(DB_FILE)
    try:
        conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",
                     (user_id,))
        conn.commit()
    finally:
        conn.close()


def db_count_unread(user_id):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            return conn.execute("""SELECT COUNT(*) FROM notifications
                WHERE user_id=? AND is_read=0""", (user_id,)).fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


# ══════════════════════ SESSIONS LOG ══════════════════════
def db_log_session_use(filename, success=True):
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            c = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute("""INSERT OR IGNORE INTO sessions_log
                (filename, last_used, total_used)
                VALUES (?, ?, 0)""", (filename, now_str))
            c.execute("""UPDATE sessions_log SET last_used=?,
                total_used = total_used + 1 WHERE filename=?""",
                (now_str, filename))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def db_sessions_stats():
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            total = conn.execute("SELECT COUNT(*) FROM sessions_log"
                                 ).fetchone()[0]
            used = conn.execute("""SELECT COALESCE(SUM(total_used),0)
                FROM sessions_log""").fetchone()[0]
            return {"total": total, "total_used": used}
        finally:
            conn.close()
    except Exception:
        return {"total": 0, "total_used": 0}


# ══════════════════════ END OF PART 1 ══════════════════════
print(f"[STARTUP] Part 1 loaded", flush=True)
print(f"[STARTUP] Sessions dir: {SESSIONS_DIR}", flush=True)
print(f"[STARTUP] Sessions found: {len(discover_sessions())}", flush=True)
# ══════════════════════ LANGUAGE STRINGS ══════════════════════
LANG_STRINGS = {
    "en": {
        "welcome": "Welcome back", "denied": "Access Denied",
        "paid_bot": "This is a paid service",
        "contact_owner": "Contact Owner", "my_settings": "My Settings",
        "my_channels": "My Channels", "notifications": "Notifications",
        "language": "Language", "info": "Info", "support": "Support",
        "home": "Home", "back": "Back", "cancel": "Cancel",
        "send": "Send", "choose_language": "Choose Language",
        "language_changed": "Language changed!",
        "no_channels": "No channels configured yet",
        "waiting_approval": "Waiting for owner approval",
        "active": "Active", "paused": "Paused",
        "reactions_per_post": "reactions/post",
        "contact_for_changes": "Contact owner to change settings",
        "added_on": "Added on", "total_sent": "Total Sent",
        "last_post": "Last Post", "last_run": "Last Run",
        "never": "Never", "history": "History",
        "no_history": "No history yet",
        "you_are_whitelisted": "You are whitelisted",
        "not_whitelisted": "You are not whitelisted",
        "notif_on": "Notifications ON", "notif_off": "Notifications OFF",
        "backup_owner": "Backup Owner",
    },
    "ur": {
        "welcome": "خوش آمدید", "denied": "رسائی مسترد",
        "paid_bot": "یہ ایک پڈ سروس ہے",
        "contact_owner": "اونر سے رابطہ", "my_settings": "میری سیٹنگز",
        "my_channels": "میرے چینلز", "notifications": "اطلاعات",
        "language": "زبان", "info": "معلومات", "support": "سپورٹ",
        "home": "مین مینو", "back": "واپس", "cancel": "کینسل",
        "send": "بھیجیں", "choose_language": "زبان منتخب کریں",
        "language_changed": "زبان تبدیل ہو گئی!",
        "no_channels": "ابھی کوئی چینل سیٹ نہیں ہوا",
        "waiting_approval": "اونر کی منظوری کا انتظار",
        "active": "فعال", "paused": "روکا ہوا",
        "reactions_per_post": "ری ایکشن فی پوسٹ",
        "contact_for_changes": "سیٹنگ بدلنے کے لیے اونر سے رابطہ کریں",
        "added_on": "شامل کیا گیا", "total_sent": "کل بھیجے",
        "last_post": "آخری پوسٹ", "last_run": "آخری بار",
        "never": "کبھی نہیں", "history": "ہسٹری",
        "no_history": "ابھی کوئی ہسٹری نہیں",
        "you_are_whitelisted": "آپ وائٹ لسٹ میں ہیں",
        "not_whitelisted": "آپ وائٹ لسٹ میں نہیں ہیں",
        "notif_on": "اطلاعات آن", "notif_off": "اطلاعات آف",
        "backup_owner": "بیک اپ اونر",
    },
    "hi": {
        "welcome": "वापसी पर स्वागत", "denied": "पहुंच अस्वीकृत",
        "paid_bot": "यह एक पेड सेवा है",
        "contact_owner": "मालिक से संपर्क", "my_settings": "मेरी सेटिंग्स",
        "my_channels": "मेरे चैनल", "notifications": "सूचनाएं",
        "language": "भाषा", "info": "जानकारी", "support": "सहायता",
        "home": "मुख्य मेनू", "back": "वापस", "cancel": "रद्द",
        "send": "भेजें", "choose_language": "भाषा चुनें",
        "language_changed": "भाषा बदल गई!",
        "no_channels": "अभी कोई चैनल सेट नहीं है",
        "waiting_approval": "मालिक की मंजूरी का इंतज़ार",
        "active": "सक्रिय", "paused": "रुका हुआ",
        "reactions_per_post": "रिएक्शन प्रति पोस्ट",
        "contact_for_changes": "सेटिंग बदलने के लिए मालिक से संपर्क करें",
        "added_on": "जोड़ा गया", "total_sent": "कुल भेजे",
        "last_post": "आखिरी पोस्ट", "last_run": "आखिरी बार",
        "never": "कभी नहीं", "history": "इतिहास",
        "no_history": "अभी कोई इतिहास नहीं",
        "you_are_whitelisted": "आप व्हाइटलिस्ट में हैं",
        "not_whitelisted": "आप व्हाइटलिस्ट में नहीं हैं",
        "notif_on": "सूचनाएं चालू", "notif_off": "सूचनाएं बंद",
        "backup_owner": "बैकअप मालिक",
    },
    "ar": {
        "welcome": "مرحباً بعودتك", "denied": "تم رفض الوصول",
        "paid_bot": "هذه خدمة مدفوعة", "contact_owner": "اتصل بالمالك",
        "my_settings": "إعداداتي", "my_channels": "قنواتي",
        "notifications": "الإشعارات", "language": "اللغة",
        "info": "معلومات", "support": "الدعم", "home": "القائمة",
        "back": "رجوع", "cancel": "إلغاء", "send": "إرسال",
        "choose_language": "اختر اللغة", "language_changed": "تم التغيير!",
        "no_channels": "لا توجد قنوات بعد",
        "waiting_approval": "في انتظار موافقة المالك",
        "active": "نشط", "paused": "متوقف",
        "reactions_per_post": "تفاعلات لكل منشور",
        "contact_for_changes": "اتصل بالمالك لتغيير الإعدادات",
        "added_on": "أضيف في", "total_sent": "إجمالي المرسل",
        "last_post": "آخر منشور", "last_run": "آخر تشغيل",
        "never": "أبداً", "history": "السجل",
        "no_history": "لا يوجد سجل بعد",
        "you_are_whitelisted": "أنت في القائمة",
        "not_whitelisted": "أنت لست في القائمة",
        "notif_on": "الإشعارات مفعلة", "notif_off": "الإشعارات معطلة",
        "backup_owner": "المالك الاحتياطي",
    },
    "ru": {
        "welcome": "С возвращением", "denied": "Доступ запрещён",
        "paid_bot": "Это платный сервис",
        "contact_owner": "Связаться с владельцем",
        "my_settings": "Мои настройки", "my_channels": "Мои каналы",
        "notifications": "Уведомления", "language": "Язык",
        "info": "Инфо", "support": "Поддержка", "home": "Меню",
        "back": "Назад", "cancel": "Отмена", "send": "Отправить",
        "choose_language": "Выбрать язык", "language_changed": "Язык изменён!",
        "no_channels": "Каналы ещё не настроены",
        "waiting_approval": "Ожидание одобрения владельца",
        "active": "Активно", "paused": "Приостановлено",
        "reactions_per_post": "реакций на пост",
        "contact_for_changes": "Свяжитесь с владельцем для изменений",
        "added_on": "Добавлено", "total_sent": "Всего отправлено",
        "last_post": "Последний пост", "last_run": "Последний запуск",
        "never": "Никогда", "history": "История",
        "no_history": "Истории пока нет",
        "you_are_whitelisted": "Вы в белом списке",
        "not_whitelisted": "Вас нет в белом списке",
        "notif_on": "Уведомления вкл", "notif_off": "Уведомления выкл",
        "backup_owner": "Резервный владелец",
    },
    "es": {
        "welcome": "Bienvenido", "denied": "Acceso Denegado",
        "paid_bot": "Este es un servicio de pago",
        "contact_owner": "Contactar al dueño",
        "my_settings": "Mis Ajustes", "my_channels": "Mis Canales",
        "notifications": "Notificaciones", "language": "Idioma",
        "info": "Info", "support": "Soporte", "home": "Inicio",
        "back": "Atrás", "cancel": "Cancelar", "send": "Enviar",
        "choose_language": "Elegir idioma", "language_changed": "¡Idioma cambiado!",
        "no_channels": "Sin canales configurados",
        "waiting_approval": "Esperando aprobación del dueño",
        "active": "Activo", "paused": "Pausado",
        "reactions_per_post": "reacciones/post",
        "contact_for_changes": "Contacta al dueño para cambios",
        "added_on": "Añadido el", "total_sent": "Total Enviado",
        "last_post": "Último Post", "last_run": "Última Ejecución",
        "never": "Nunca", "history": "Historial",
        "no_history": "Sin historial aún",
        "you_are_whitelisted": "Estás en la whitelist",
        "not_whitelisted": "No estás en la whitelist",
        "notif_on": "Notificaciones ON", "notif_off": "Notificaciones OFF",
        "backup_owner": "Dueño de Respaldo",
    },
    "id": {
        "welcome": "Selamat Datang", "denied": "Akses Ditolak",
        "paid_bot": "Ini adalah layanan berbayar",
        "contact_owner": "Hubungi Pemilik",
        "my_settings": "Pengaturan Saya", "my_channels": "Channel Saya",
        "notifications": "Notifikasi", "language": "Bahasa",
        "info": "Info", "support": "Dukungan", "home": "Menu",
        "back": "Kembali", "cancel": "Batal", "send": "Kirim",
        "choose_language": "Pilih Bahasa", "language_changed": "Bahasa diubah!",
        "no_channels": "Belum ada channel",
        "waiting_approval": "Menunggu persetujuan pemilik",
        "active": "Aktif", "paused": "Dijeda",
        "reactions_per_post": "reaksi/post",
        "contact_for_changes": "Hubungi pemilik untuk mengubah",
        "added_on": "Ditambahkan", "total_sent": "Total Terkirim",
        "last_post": "Post Terakhir", "last_run": "Terakhir Jalan",
        "never": "Tidak pernah", "history": "Riwayat",
        "no_history": "Belum ada riwayat",
        "you_are_whitelisted": "Anda di whitelist",
        "not_whitelisted": "Anda tidak di whitelist",
        "notif_on": "Notifikasi AKTIF", "notif_off": "Notifikasi MATI",
        "backup_owner": "Pemilik Cadangan",
    },
}


def L(uid, key):
    if uid is None:
        return LANG_STRINGS["en"].get(key, key)
    try:
        u = db_get_user(uid)
        lang = "en"
        if u and len(u) > 7 and u[7] and u[7] in LANG_STRINGS:
            lang = u[7]
        return LANG_STRINGS.get(lang, LANG_STRINGS["en"]).get(key, key)
    except Exception:
        return LANG_STRINGS["en"].get(key, key)


def L_static(lang, key):
    return LANG_STRINGS.get(lang, LANG_STRINGS["en"]).get(key, key)


# ══════════════════════ MESSAGE TEMPLATES ══════════════════════
def get_denied_message(uid=None):
    owner = get_owner_username()
    owner_id = get_owner_id()
    return (
        f"{STAR_LINE}\n"
        f"🔒 **{L(uid, 'denied')}** 🔒\n"
        f"{STAR_LINE}\n\n"
        f"❌ **{L(uid, 'paid_bot')}**\n\n"
        f"Ye bot **private/paid service** hai. "
        f"Sirf whitelisted users hi ise use kar sakte hain.\n\n"
        f"{DIV()}\n"
        f"👑 **Owner:** @{owner}\n"
        f"🆔 **ID:** `{owner_id}`\n"
        f"{DIV()}\n\n"
        f"💎 **Features:**\n"
        f"   • Auto-react to new posts\n"
        f"   • Multi-channel support\n"
        f"   • Custom reactions per channel\n"
        f"   • Real accounts (no admin needed)\n"
        f"   • 24/7 automatic\n\n"
        f"{DIV()}\n"
        f"📞 **{L(uid, 'contact_owner')}** ke liye "
        f"neeche button dabaayein 👇"
    )


def get_welcome_message(uid):
    u = db_get_user(uid)
    if not u:
        return get_denied_message(uid)

    user_id, username, first_name, added_by, added_at, is_active, \
        notify, lang, total_reactions, notes = u

    name = first_name or f"User {user_id}"
    channels = db_list_channels(uid)
    active_channels = [c for c in channels if c[8] == 1]

    channels_txt = ""
    if not channels:
        channels_txt = f"_{L(uid, 'no_channels')}_"
    else:
        for i, ch in enumerate(channels[:5], 1):
            ch_title = ch[4] or ch[3] or "Unknown"
            cnt = ch[5]
            st = "🟢" if ch[8] else "🔴"
            channels_txt += f"{st} **{i}.** {ch_title[:30]}\n"
            channels_txt += f"      💫 {cnt}/post\n"
        if len(channels) > 5:
            channels_txt += f"\n_... +{len(channels) - 5} more_"

    total = total_reactions or 0

    return (
        f"{STAR_LINE}\n"
        f"👻 **GHOST AUTO-REACTOR** 👻\n"
        f"{STAR_LINE}\n\n"
        f"👋 {L(uid, 'welcome')}, **{name}**!\n\n"
        f"✅ **{L(uid, 'you_are_whitelisted')}**\n\n"
        f"{DIV()}\n"
        f"📊 **{L(uid, 'my_settings')}**\n"
        f"{DIV()}\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"📢 {L(uid, 'my_channels')}: **{len(active_channels)}** active "
        f"/ **{len(channels)}** total\n"
        f"💫 {L(uid, 'total_sent')}: **{total}**\n"
        f"🔔 {L(uid, 'notifications')}: "
        f"**{'✅ ON' if notify else '❌ OFF'}**\n"
        f"🌍 {L(uid, 'language')}: **{LANGUAGES.get(lang, 'English')}**\n\n"
        f"{DIV()}\n"
        f"📢 **{L(uid, 'my_channels')}**\n"
        f"{DIV()}\n\n"
        f"{channels_txt}\n\n"
        f"{DIV()}\n"
        f"⚠️ **{L(uid, 'contact_for_changes')}**\n"
        f"👑 @{get_owner_username()}\n"
        f"{DIV()}"
    )


def get_settings_message(uid):
    u = db_get_user(uid)
    if not u:
        return get_denied_message(uid)

    user_id, username, first_name, added_by, added_at, is_active, \
        notify, lang, total_reactions, notes = u

    channels = db_list_channels(uid)

    txt = (
        f"{STAR_LINE}\n"
        f"📊 **{L(uid, 'my_settings')}**\n"
        f"{STAR_LINE}\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"📛 Name: **{first_name or 'User'}**\n"
        f"📝 Username: @{username or 'no_username'}\n"
        f"📅 {L(uid, 'added_on')}: {added_at[:16] if added_at else '—'}\n"
        f"💫 {L(uid, 'total_sent')}: **{total_reactions or 0}**\n"
        f"🔔 {L(uid, 'notifications')}: "
        f"**{'✅ ON' if notify else '❌ OFF'}**\n"
        f"🌍 {L(uid, 'language')}: "
        f"**{LANGUAGES.get(lang, 'English')}**\n\n"
        f"{DIV()}\n"
        f"📢 **{L(uid, 'my_channels')}** ({len(channels)})\n"
        f"{DIV()}\n\n"
    )

    if not channels:
        txt += f"_{L(uid, 'no_channels')}_"
    else:
        for i, ch in enumerate(channels[:10], 1):
            ch_title = ch[4] or ch[3] or "Unknown"
            cnt = ch[5]
            emo = ch[6]
            is_act = ch[8]
            joined = ch[9]
            last_post = ch[10]
            last_run = ch[11]
            total_sent = ch[12]

            st = "🟢" if is_act else "🔴"
            jn = "✅" if joined else "⏳"

            txt += (
                f"{st} **{i}. {ch_title[:30]}**\n"
                f"      🎯 {cnt} {L(uid, 'reactions_per_post')}\n"
                f"      ✏️ Emoji: {emo}\n"
                f"      {jn} Joined\n"
                f"      💫 {L(uid, 'total_sent')}: {total_sent or 0}\n"
                f"      📩 {L(uid, 'last_post')}: "
                f"#{last_post if last_post else '—'}\n"
                f"      🕒 {L(uid, 'last_run')}: "
                f"{last_run[:16] if last_run else L(uid, 'never')}\n\n"
            )
        if len(channels) > 10:
            txt += f"_... +{len(channels) - 10} more channels_\n"

    txt += (
        f"{DIV()}\n"
        f"⚠️ **{L(uid, 'contact_for_changes')}**\n"
        f"👑 @{get_owner_username()}"
    )

    return txt


def get_channel_info_message(uid, channel_id):
    ch = db_get_channel(channel_id)
    if not ch:
        return "❌ Channel not found"

    cid, owner_id, chat_id, chat_link, chat_title, rc, emo, ce, \
        is_act, joined, last_post, last_run, total_reactions, \
        fail_count, created = ch

    if owner_id != uid:
        return "❌ Not your channel"

    st = "🟢 Active" if is_act else "🔴 Paused"
    jn = "✅ Joined" if joined else "⏳ Not joined"
    emo_disp = emo
    if ce:
        emo_disp += f" ({ce[:30]})"

    return (
        f"{STAR_LINE}\n"
        f"📢 **{chat_title[:40]}**\n"
        f"{STAR_LINE}\n\n"
        f"🔗 {chat_link}\n\n"
        f"{DIV()}\n"
        f"🎯 Reactions: **{rc}** per post\n"
        f"✏️ Emoji: **{emo_disp}**\n"
        f"🔄 Status: **{st}**\n"
        f"👥 {jn}\n\n"
        f"💫 Total Sent: **{total_reactions or 0}**\n"
        f"❌ Failed: **{fail_count or 0}**\n"
        f"📩 Last Post: **#{last_post if last_post else '—'}**\n"
        f"🕒 Last Run: **{last_run[:16] if last_run else 'Never'}**\n"
        f"📅 Added: **{created[:16] if created else '—'}**\n\n"
        f"{DIV()}\n"
        f"⚠️ **{L(uid, 'contact_for_changes')}**\n"
        f"👑 @{get_owner_username()}"
    )


def get_owner_dashboard_message():
    users = db_count_users()
    active_users = db_count_active_users()
    channels = db_count_channels()
    active_channels = db_count_active_channels()
    total_reactions = db_total_reactions()
    today_reactions = db_reactions_today()
    sessions = get_sessions_status()

    return (
        f"{STAR_LINE}\n"
        f"👑 **OWNER DASHBOARD**\n"
        f"{STAR_LINE}\n\n"
        f"👥 **Users**\n"
        f"   Total: **{users}**\n"
        f"   Active: **{active_users}**\n\n"
        f"📢 **Channels**\n"
        f"   Total: **{channels}**\n"
        f"   Active: **{active_channels}**\n\n"
        f"💫 **Reactions**\n"
        f"   Today: **{today_reactions}**\n"
        f"   Total: **{total_reactions}**\n\n"
        f"🔐 **Sessions**\n"
        f"   Total: **{sessions['total']}**\n"
        f"   Available: **{sessions['available']}**\n"
        f"   Flooded: **{sessions['flooded']}**\n"
        f"   Loaded: **{sessions['loaded']}**\n\n"
        f"⏱️ Uptime: {int(time.time() - _start_time)}s"
    )


def get_owner_user_view(user_id):
    u = db_get_user(user_id)
    if not u:
        return "❌ User not found"

    uid, username, first_name, added_by, added_at, is_active, \
        notify, lang, total_reactions, notes = u

    channels = db_list_channels(user_id)

    txt = (
        f"{STAR_LINE}\n"
        f"👤 **User: {first_name or 'Unknown'}**\n"
        f"{STAR_LINE}\n\n"
        f"🆔 `{user_id}`\n"
        f"📛 @{username or 'no_username'}\n"
        f"📅 Added: {added_at[:16] if added_at else '—'}\n"
        f"🟢 Active: **{'Yes' if is_active else 'No'}**\n"
        f"🔔 Notify: **{'Yes' if notify else 'No'}**\n"
        f"🌍 Lang: {LANGUAGES.get(lang, 'English')}\n"
        f"💫 Total: **{total_reactions or 0}**\n\n"
        f"{DIV()}\n"
        f"📢 **Channels ({len(channels)})**\n"
        f"{DIV()}\n\n"
    )

    if not channels:
        txt += "_No channels_"
    else:
        for i, ch in enumerate(channels[:8], 1):
            ch_title = ch[4] or ch[3] or "Unknown"
            st = "🟢" if ch[8] else "🔴"
            txt += (f"{st} **{i}.** {ch_title[:30]}\n"
                    f"      🎯 {ch[5]}/post | "
                    f"💫 {ch[12] or 0}\n")

    return txt


def get_help_text(uid=None):
    owner = get_owner_username()
    return (
        f"{STAR_LINE}\n"
        f"📖 **HELP**\n"
        f"{STAR_LINE}\n\n"
        f"ℹ️ Ye bot **private/paid service** hai.\n\n"
        f"{DIV()}\n"
        f"**Kaise kaam karta hai:**\n"
        f"{DIV()}\n\n"
        f"1️⃣ Owner aapko whitelist mein add karta hai\n"
        f"2️⃣ Owner aapke channels set karta hai\n"
        f"3️⃣ Aap ke channel mein jab bhi new post aaye\n"
        f"4️⃣ Bot **automatically** reactions bhejta hai\n"
        f"5️⃣ Aapko notifications milti hain\n\n"
        f"{DIV()}\n"
        f"**Settings change karne ke liye:**\n"
        f"{DIV()}\n"
        f"👑 @{owner}\n\n"
        f"{DIV()}\n"
        f"⚠️ Users khud settings change nahi kar sakte.\n"
        f"Sirf owner hi change kar sakta hai."
    )


# ══════════════════════ SESSION MANAGER ══════════════════════
async def get_real_client(sf):
    async with REAL_CLIENT_LOCK:
        if len(REAL_CLIENTS) >= MAX_CLIENTS:
            to_remove = list(REAL_CLIENTS.keys())[
                :max(1, len(REAL_CLIENTS) - MAX_CLIENTS + 1)]
            for k in to_remove:
                try:
                    await REAL_CLIENTS[k].disconnect()
                except Exception:
                    pass
                REAL_CLIENTS.pop(k, None)
                DBG(f"Client evicted (RAM limit): {k}", "warn")

        if sf in REAL_CLIENTS:
            c = REAL_CLIENTS[sf]
            try:
                if c.is_connected():
                    return c
            except Exception:
                pass
            try:
                await c.disconnect()
            except Exception:
                pass
            REAL_CLIENTS.pop(sf, None)

        path = os.path.join(SESSIONS_DIR, sf.replace(".session", ""))
        if not os.path.exists(path + ".session"):
            DBG(f"Session file not found: {sf}", "warn")
            return None

        try:
            client = TelegramClient(path, API_ID, API_HASH)
            await asyncio.wait_for(client.connect(), timeout=25)
            if not await client.is_user_authorized():
                await client.disconnect()
                DBG(f"Session not authorized: {sf}", "warn")
                return None
            REAL_CLIENTS[sf] = client
            DBG(f"Session loaded: {sf}", "ok")
            return client
        except Exception as e:
            DBG(f"Session load fail {sf}: {str(e)[:80]}", "fail")
            return None


async def close_all_real_clients():
    async with REAL_CLIENT_LOCK:
        for sf, c in list(REAL_CLIENTS.items()):
            try:
                await c.disconnect()
            except Exception:
                pass
        REAL_CLIENTS.clear()


# ══════════════════════ JOIN CHAT ══════════════════════
async def session_join_chat(sf, chat_ref, invite_hash=None):
    if is_session_flooded(sf):
        return False, "flooded"

    client = await get_real_client(sf)
    if not client:
        return False, "no_client"

    try:
        if invite_hash:
            try:
                await asyncio.wait_for(
                    client(ImportChatInviteRequest(invite_hash)),
                    timeout=REAL_ENTITY_TIMEOUT)
                DBG(f"{sf} joined private: {invite_hash[:10]}", "join")
                return True, "joined"
            except UserAlreadyParticipantError:
                return True, "already"
            except Exception as e:
                return False, str(e)[:80]

        try:
            entity = await asyncio.wait_for(
                client.get_entity(chat_ref),
                timeout=REAL_ENTITY_TIMEOUT)
        except Exception as e:
            return False, f"resolve: {str(e)[:60]}"

        try:
            await asyncio.wait_for(
                client(GetParticipantRequest(
                    channel=entity, participant="me")),
                timeout=REAL_ENTITY_TIMEOUT)
            return True, "already"
        except Exception:
            pass

        try:
            await asyncio.wait_for(
                client(JoinChannelRequest(channel=entity)),
                timeout=REAL_ENTITY_TIMEOUT)
            DBG(f"{sf} joined: {chat_ref}", "join")
            return True, "joined"
        except UserAlreadyParticipantError:
            return True, "already"
        except FloodWaitError as e:
            mark_session_flooded(sf, e.seconds + 10)
            return False, f"flood:{e.seconds}"
        except Exception as e:
            return False, f"join: {str(e)[:60]}"
    except Exception as e:
        return False, str(e)[:100]


async def join_chat_with_sessions(chat_link, chat_id=None,
                                   max_sessions=20):
    chat_ref, invite_hash = parse_chat_link(chat_link)
    if not chat_ref and not invite_hash:
        return 0, 0, 0

    sessions = discover_sessions()
    if not sessions:
        return 0, 0, 0

    available = [s for s in sessions if not is_session_flooded(s)]
    if not available:
        return 0, 0, 0

    to_try = available[:max_sessions]
    joined = already = failed = 0

    DBG(f"Joining {chat_link} with {len(to_try)} sessions", "join")

    for i, sf in enumerate(to_try, 1):
        ok, reason = await session_join_chat(sf, chat_ref, invite_hash)
        if ok:
            if reason == "joined":
                joined += 1
            else:
                already += 1
        else:
            failed += 1

        if i % 5 == 0:
            DBG(f"Join progress: {i}/{len(to_try)} | "
                f"✅{joined} ⚡{already} ❌{failed}", "join")
        await asyncio.sleep(0.5)

    DBG(f"Join done: ✅{joined} ⚡{already} ❌{failed}", "join")
    return joined, already, failed


def parse_chat_link(link):
    if not link:
        return None, None
    link = link.strip()
    if "t.me/+" in link:
        h = link.split("+")[-1].split("?")[0]
        return None, h
    if "t.me/joinchat/" in link:
        h = link.split("joinchat/")[-1].split("?")[0]
        return None, h
    if "t.me/" in link:
        u = link.split("t.me/")[-1].split("/")[0].split("?")[0]
        if u.startswith("+"):
            return None, u.lstrip("+")
        return f"@{u}", None
    if link.startswith("@"):
        return link, None
    if link.lstrip("-").isdigit():
        return int(link), None
    return link, None


# ══════════════════════ ✅ SMART EMOJI FALLBACK ══════════════════════
async def session_send_reaction(sf, chat_ref, msg_id, emoji,
                                 emoji_pool=None):
    """
    Send a reaction from a session.
    SMART: Agar user ka emoji invalid ho to USI POOL se doosra try karo
    Jab tak koi emoji lage ya sab try ho jayein.
    Returns (ok, used_emoji, error)
    """
    if is_session_flooded(sf):
        return False, emoji, "flooded"

    client = await get_real_client(sf)
    if not client:
        return False, emoji, "no_client"

    try:
        entity = await asyncio.wait_for(
            client.get_entity(chat_ref),
            timeout=REAL_ENTITY_TIMEOUT)
    except Exception as e:
        return False, emoji, f"resolve: {str(e)[:60]}"

    # ── Build fallback list (user's pool first, then shuffle) ──
    if emoji_pool and len(emoji_pool) > 0:
        fb_pool = list(emoji_pool)
    else:
        fb_pool = FALLBACK_REACTIONS.copy()

    random.shuffle(fb_pool)

    # Pehle user ka emoji, phir baaki pool
    try_list = [emoji] + [e for e in fb_pool if e != emoji]

    # ── Try each emoji until one works ──
    last_err = ""
    for try_emoji in try_list:
        try:
            await asyncio.wait_for(
                client(SendReactionRequest(
                    peer=entity,
                    msg_id=msg_id,
                    reaction=[ReactionEmoji(emoticon=try_emoji)])),
                timeout=REAL_REACTION_TIMEOUT)

            if try_emoji != emoji:
                DBG(f"⚠️ {sf}: {emoji} failed → used {try_emoji}", "react")
            return True, try_emoji, ""

        except FloodWaitError as e:
            mark_session_flooded(sf, e.seconds + 10)
            return False, emoji, f"flood:{e.seconds}"

        except Exception as e:
            err_str = str(e)
            last_err = err_str[:80]

            # Sirf invalid emoji pe next try karo
            if ("Invalid reaction" in err_str or
                "only emoji" in err_str or
                "REACTION_INVALID" in err_str):
                DBG(f"↻ {sf}: {try_emoji} invalid, trying next", "react")
                continue

            # Baaki errors (chat restricted, etc.) — stop
            return False, emoji, last_err

    # Saare emojis try kiye, koi nahi laga
    return False, emoji, f"all_emojis_failed: {last_err[:60]}"


# ══════════════════════ ✅ SEND REACTIONS (WITH RETRY) ══════════════════════
async def send_reactions_from_sessions(chat_link, msg_id, count,
                                        emoji_mode="default",
                                        custom_emojis=None,
                                        on_progress=None):
    """
    Send N reactions using multiple sessions.
    - Failed session → next session try
    - Invalid emoji → next emoji from pool
    - Full count poora karne ki koshish
    """
    result = {"ok": 0, "fail": 0, "flooded": 0, "total": 0}

    if count <= 0:
        return result

    chat_ref, invite_hash = parse_chat_link(chat_link)

    sessions = discover_sessions()
    if not sessions:
        DBG("No sessions available", "warn")
        return result

    available = [s for s in sessions if not is_session_flooded(s)]
    if not available:
        DBG("All sessions flooded", "flood")
        return result

    random.shuffle(available)
    result["total"] = count

    DBG(f"Sending {count} reactions to {chat_link} #{msg_id}", "react")

    # ── Build emoji pool ──
    if emoji_mode == "custom" and custom_emojis:
        valid = [e for e in custom_emojis if e in ALL_REACTIONS]
        base_pool = valid if valid else FALLBACK_REACTIONS.copy()
    else:
        base_pool = DEFAULT_REACTIONS.copy()

    # Full emoji plan (user's emojis first, then repeat from pool)
    full_emojis = []
    full_emojis.extend(base_pool)
    while len(full_emojis) < count:
        full_emojis.append(random.choice(base_pool))
    full_emojis = full_emojis[:count]

    DBG(f"Emoji plan ({len(full_emojis)}): {full_emojis[:15]}...", "react")

    # ── Try sessions until count reached ──
    tried_sessions = set()
    ok_count = 0
    fail_count = 0
    flood_count = 0

    max_attempts = len(available) * 3
    attempts = 0

    while ok_count < count and attempts < max_attempts:
        attempts += 1

        untried = [s for s in available if s not in tried_sessions]
        if not untried:
            DBG(f"All sessions tried. Sent {ok_count}/{count}", "react")
            break

        sf = untried[0]
        tried_sessions.add(sf)

        if is_session_flooded(sf):
            flood_count += 1
            continue

        # Pick emoji
        if ok_count < len(full_emojis):
            emoji = full_emojis[ok_count]
        else:
            emoji = random.choice(base_pool)

        success, used_emoji, err = await session_send_reaction(
            sf, chat_ref, msg_id, emoji,
            emoji_pool=base_pool)

        if success:
            ok_count += 1
            result["ok"] = ok_count
            db_log_session_use(sf, True)
            DBG(f"✅ {sf}: {used_emoji} ({ok_count}/{count})", "react")
        elif "flood" in err:
            flood_count += 1
        else:
            fail_count += 1
            DBG(f"❌ {sf}: {err[:60]}", "fail")

        if on_progress and ok_count > 0 and ok_count % 5 == 0:
            try:
                await on_progress(ok_count, count, ok_count)
            except Exception:
                pass

        await asyncio.sleep(REAL_COOLDOWN)

    result["fail"] = fail_count
    result["flooded"] = flood_count

    DBG(f"Reactions done: ✅{ok_count}/{count} ❌{fail_count} "
        f"🌊{flood_count}", "react")

    return result


# ══════════════════════ VERIFY POST ══════════════════════
async def verify_post_exists(chat_ref, msg_id, invite_hash=None):
    if admin_client:
        try:
            entity = await asyncio.wait_for(
                admin_client.get_entity(chat_ref),
                timeout=REAL_ENTITY_TIMEOUT)
            msgs = await asyncio.wait_for(
                admin_client.get_messages(entity, ids=msg_id),
                timeout=REAL_ENTITY_TIMEOUT)
            if msgs:
                return True, entity
        except Exception:
            pass

    for sf in discover_sessions()[:3]:
        if is_session_flooded(sf):
            continue
        try:
            client = await get_real_client(sf)
            if not client:
                continue
            entity = await asyncio.wait_for(
                client.get_entity(chat_ref),
                timeout=REAL_ENTITY_TIMEOUT)
            msgs = await asyncio.wait_for(
                client.get_messages(entity, ids=msg_id),
                timeout=REAL_ENTITY_TIMEOUT)
            if msgs:
                return True, entity
        except Exception:
            continue

    return False, None


# ══════════════════════ GET LATEST POSTS ══════════════════════
async def get_latest_posts(chat_id, limit=5):
    if admin_client:
        try:
            entity = await asyncio.wait_for(
                admin_client.get_entity(chat_id),
                timeout=REAL_ENTITY_TIMEOUT)
            msgs = await asyncio.wait_for(
                admin_client.get_messages(entity, limit=limit),
                timeout=REAL_ENTITY_TIMEOUT)
            return msgs or [], entity
        except Exception:
            pass

    for sf in discover_sessions()[:5]:
        if is_session_flooded(sf):
            continue
        try:
            client = await get_real_client(sf)
            if not client:
                continue
            entity = await asyncio.wait_for(
                client.get_entity(chat_id),
                timeout=REAL_ENTITY_TIMEOUT)
            msgs = await asyncio.wait_for(
                client.get_messages(entity, limit=limit),
                timeout=REAL_ENTITY_TIMEOUT)
            if msgs:
                return msgs, entity
        except Exception:
            continue

    return [], None


def get_newest_post_id(msgs):
    if not msgs:
        return 0
    try:
        return max(m.id for m in msgs if m.id > 0)
    except Exception:
        return 0


# ══════════════════════ VALIDATION ══════════════════════
def is_admin(uid):
    return OWNER_IS(uid)


def is_whitelisted(uid):
    return db_is_whitelisted(uid)


def can_use_bot(uid):
    if OWNER_IS(uid):
        return True
    return db_is_whitelisted(uid)


# ══════════════════════ BUTTON HELPER ══════════════════════
def btn(text, data=None, url=None, style=None):
    if url:
        b = Button.url(text, url)
    else:
        b = Button.inline(text, data if data else b"none")
    if style and HAS_BUTTON_STYLE and KeyboardButtonStyle is not None:
        try:
            if style == "primary":
                b.style = KeyboardButtonStyle(bg_primary=True)
            elif style == "success":
                b.style = KeyboardButtonStyle(bg_success=True)
            elif style == "danger":
                b.style = KeyboardButtonStyle(bg_danger=True)
        except Exception:
            pass
    return b


# ══════════════════════ SAFE EDIT / ANSWER ══════════════════════
async def safe_edit(event, text, buttons=None, alert=None):
    try:
        if alert:
            try:
                await event.answer(alert, alert=True)
            except Exception:
                pass

        key = getattr(event, "chat_id", 0)
        now = time.time()
        wait = 1.5 - (now - _LAST_EDIT_TIME.get(key, 0))
        if 0 < wait < 5:
            await asyncio.sleep(wait)

        if buttons is not None:
            await event.edit(text, buttons=buttons)
        else:
            await event.edit(text)

        _LAST_EDIT_TIME[key] = time.time()
        return True
    except MessageNotModifiedError:
        return True
    except MessageIdInvalidError:
        try:
            if buttons is not None:
                await event.client.send_message(
                    event.chat_id, text, buttons=buttons)
            else:
                await event.client.send_message(event.chat_id, text)
            return True
        except Exception:
            return False
    except FloodWaitError as e:
        await asyncio.sleep(min(e.seconds, 30))
        return False
    except Exception as e:
        DBG(f"safe_edit error: {str(e)[:80]}", "warn")
        return False


async def safe_answer(event, text=None, alert=False):
    try:
        if text:
            await event.answer(text, alert=alert)
        else:
            await event.answer()
    except Exception:
        pass


# ══════════════════════ ENTITY CACHE ══════════════════════
async def safe_get_entity(ref, cache_key=None, cache_store=None):
    if cache_key is None:
        cache_key = str(ref)
    if cache_store is None:
        cache_store = ENTITY_CACHE

    now = datetime.now()
    cached = cache_store.get(cache_key)
    if cached:
        entity, expires_at = cached
        if now < expires_at:
            return entity
        cache_store.pop(cache_key, None)

    if admin_client:
        try:
            entity = await asyncio.wait_for(
                admin_client.get_entity(ref),
                timeout=REAL_ENTITY_TIMEOUT)
            cache_store[cache_key] = (
                entity, now + timedelta(seconds=ENTITY_CACHE_TTL))
            DBG(f"Resolved via ADMIN: {ref}", "ok")
            return entity
        except FloodWaitError:
            DBG(f"Admin flood on resolve: {ref}", "flood")
        except Exception:
            pass

    for sf in discover_sessions()[:5]:
        if is_session_flooded(sf):
            continue
        try:
            client = await get_real_client(sf)
            if not client:
                continue
            entity = await asyncio.wait_for(
                client.get_entity(ref),
                timeout=REAL_ENTITY_TIMEOUT)
            cache_store[cache_key] = (
                entity, now + timedelta(seconds=ENTITY_CACHE_TTL))
            DBG(f"Resolved via {sf}: {ref}", "ok")
            return entity
        except Exception:
            continue

    raise Exception("Could not resolve entity")


# ══════════════════════ RESOLVE USERNAME ══════════════════════
async def resolve_username_to_id(username):
    if not username:
        return None, None, None
    username = username.lstrip("@").strip()
    if not username:
        return None, None, None

    for sf in discover_sessions()[:10]:
        if is_session_flooded(sf):
            continue
        try:
            client = await get_real_client(sf)
            if not client:
                continue
            ent = await asyncio.wait_for(
                client.get_entity(f"@{username}"),
                timeout=REAL_ENTITY_TIMEOUT)
            if ent and hasattr(ent, "id"):
                DBG(f"Resolved @{username} → {ent.id} via {sf}", "ok")
                return (
                    ent.id,
                    getattr(ent, "first_name", None),
                    getattr(ent, "username", username)
                )
        except Exception:
            continue
    return None, None, None


# ══════════════════════ RESOLVE CHAT INFO ══════════════════════
async def resolve_chat_info(chat_link):
    chat_ref, invite_hash = parse_chat_link(chat_link)
    if not chat_ref and not invite_hash:
        return None, None, None, None

    try:
        entity = await safe_get_entity(chat_ref or f"+{invite_hash}")
        chat_id = entity.id
        chat_title = sanitize_title(getattr(entity, "title", "Unknown"))
        if isinstance(entity, Channel):
            chat_type = "channel" if getattr(entity, "broadcast", False) else "group"
        else:
            chat_type = "group"
        return chat_id, chat_title, chat_type, invite_hash
    except Exception as e:
        DBG(f"Resolve chat info failed: {str(e)[:80]}", "fail")
        return None, None, None, invite_hash


# ══════════════════════ CHECK SESSION ACCESS ══════════════════════
async def check_session_access(chat_ref, invite_hash=None):
    accessible = 0
    sessions = discover_sessions()[:5]
    for sf in sessions:
        if is_session_flooded(sf):
            continue
        try:
            client = await get_real_client(sf)
            if not client:
                continue
            await asyncio.wait_for(
                client.get_entity(chat_ref),
                timeout=REAL_ENTITY_TIMEOUT)
            accessible += 1
        except Exception:
            continue
    return accessible


# ══════════════════════ TASK LOCK ══════════════════════
def is_task_running():
    global TASK_RUNNING
    return TASK_RUNNING


def set_task_running(running, user_id=None):
    global TASK_RUNNING, TASK_USER_ID
    TASK_RUNNING = running
    TASK_USER_ID = user_id if running else None


# ══════════════════════ PARSE INPUTS ══════════════════════
def parse_user_input_id(text):
    text = (text or "").strip()
    if not text:
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    return None


def parse_emoji_input(text):
    if not text:
        return []
    parts = re.split(r"[,\s]+", text.strip())
    valid = []
    for p in parts:
        if p and p in ALL_REACTIONS and p not in valid:
            valid.append(p)
    return valid[:30]


def parse_reaction_count(text, min_val=1, max_val=100):
    text = (text or "").strip()
    if not text.isdigit():
        return None
    v = int(text)
    if v < min_val or v > max_val:
        return None
    return v


# ══════════════════════ FORMAT HELPERS ══════════════════════
def fmt_num(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n)


def fmt_time_ago(dt_str):
    if not dt_str:
        return "Never"
    try:
        dt = datetime.strptime(dt_str[:19], "%Y-%m-%d %H:%M:%S")
        now = datetime.now()
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return dt_str[:16] if dt_str else "—"


def get_user_lang(uid):
    try:
        u = db_get_user(uid)
        if u and len(u) > 7 and u[7]:
            return u[7]
    except Exception:
        pass
    return "en"


def get_max_reactions_possible():
    return count_available_sessions()


def can_send_reactions():
    return count_available_sessions() > 0


# ══════════════════════ END OF PART 2 ══════════════════════
print("[STARTUP] Part 2 loaded (Languages + Templates + Smart Sessions)", flush=True)
   # ══════════════════════ BACKGROUND WORKER — MAIN LOOP ══════════════════════
async def auto_watch_loop():
    global TASK_RUNNING, TASK_USER_ID

    await asyncio.sleep(15)
    DBG("Auto-watch loop started (10 min interval)", "watch")

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)

            if TASK_RUNNING:
                DBG("Skipping cycle (task running)", "watch")
                continue

            DBG("═══ Watch cycle starting ═══", "watch")
            cycle_start = time.time()

            channels = db_list_channels(active_only=True)
            if not channels:
                DBG("No active channels to check", "watch")
                continue

            DBG(f"Checking {len(channels)} channels", "watch")

            processed = 0
            new_posts_found = 0
            reactions_sent = 0
            errors = 0

            for ch in channels:
                if TASK_RUNNING:
                    DBG("Task started — pausing watch", "watch")
                    break

                try:
                    result = await process_channel_check(ch)
                    processed += 1
                    if result.get("new_post"):
                        new_posts_found += 1
                        reactions_sent += result.get("reactions", 0)
                except Exception as e:
                    errors += 1
                    DBG(f"Channel check error: {str(e)[:80]}", "fail")

                await asyncio.sleep(1)

            elapsed = int(time.time() - cycle_start)
            DBG(f"Cycle done in {elapsed}s | "
                f"Checked: {processed} | New: {new_posts_found} | "
                f"Reactions: {reactions_sent} | Errors: {errors}", "watch")

        except asyncio.CancelledError:
            DBG("Watch loop cancelled", "watch")
            return
        except Exception as e:
            DBG(f"Watch loop error: {str(e)[:100]}", "fail")
            await asyncio.sleep(60)


# ══════════════════════ PROCESS ONE CHANNEL ══════════════════════
async def process_channel_check(ch):
    (channel_id, user_id, chat_id, chat_link, chat_title,
     reaction_count, emoji_mode, custom_emojis, is_active, joined,
     last_post_id, last_run, total_reactions, fail_count, created) = ch

    result = {"new_post": False, "reactions": 0}

    if not db_is_whitelisted(user_id):
        DBG(f"User {user_id} not whitelisted — skipping", "watch")
        return result

    if not chat_id:
        try:
            cid, title, ctype, invite = await resolve_chat_info(chat_link)
            if cid:
                chat_id = cid
                chat_title = title or chat_title
                db_update_channel(channel_id,
                                  chat_id=cid,
                                  chat_title=title)
                DBG(f"Resolved chat_id: {cid} ({title})", "watch")
        except Exception as e:
            DBG(f"Resolve fail ch#{channel_id}: {str(e)[:60]}", "fail")

    if not chat_id:
        DBG(f"No chat_id for ch#{channel_id}", "warn")
        return result

    msgs, entity = await get_latest_posts(chat_id, limit=5)
    if not msgs:
        DBG(f"No posts fetched ch#{channel_id}", "warn")
        return result

    newest = get_newest_post_id(msgs)
    if newest <= 0:
        return result

    if newest <= (last_post_id or 0):
        return result

    DBG(f"🆕 New post #{newest} in {chat_title} "
        f"(user: {user_id})", "watch")
    result["new_post"] = True

    if not joined:
        DBG(f"Auto-joining {chat_title} with sessions", "join")
        try:
            j, a, f = await join_chat_with_sessions(
                chat_link, chat_id, max_sessions=20)
            if j + a > 0:
                db_update_channel(channel_id, joined=1)
                DBG(f"Join success: ✅{j} ⚡{a}", "join")
            else:
                DBG(f"Join failed entirely (j=0 a=0 f={f})", "fail")
                db_update_channel(channel_id, fail_count=(fail_count or 0) + 1)
                return result
        except Exception as e:
            DBG(f"Join exception: {str(e)[:80]}", "fail")
            return result

    TASK_RUNNING = True
    TASK_USER_ID = user_id
    try:
        chat_str = str(chat_id).replace("-100", "")
        post_link = f"https://t.me/c/{chat_str}/{newest}"

        # ✅ Parse custom emojis properly
        ce_list = None
        if custom_emojis:
            if isinstance(custom_emojis, str):
                ce_list = [e.strip() for e in custom_emojis.split(",")
                           if e.strip() and e.strip() in ALL_REACTIONS]
            elif isinstance(custom_emojis, (list, tuple)):
                ce_list = [e for e in custom_emojis
                           if e in ALL_REACTIONS]

        emode = emoji_mode
        if emode == "custom" and not ce_list:
            emode = "default"
            DBG(f"Custom emojis empty for ch#{channel_id} — using default",
                "warn")

        async def on_prog(i, total, ok):
            pass

        reactions_result = await send_reactions_from_sessions(
            chat_link=chat_link,
            msg_id=newest,
            count=reaction_count,
            emoji_mode=emode,
            custom_emojis=ce_list,
            on_progress=on_prog)

        sent = reactions_result.get("ok", 0)
        result["reactions"] = sent

        db_update_channel_last_post(channel_id, newest, reactions_sent=sent)

        db_log_reaction(
            user_id=user_id,
            channel_id=channel_id,
            chat_title=chat_title,
            post_id=newest,
            reactions_sent=sent,
            status="ok" if sent > 0 else "fail")

        await notify_user_reactions(
            user_id, chat_title, newest, sent, reaction_count,
            post_link=post_link)

        DBG(f"✅ Sent {sent}/{reaction_count} to #{newest}", "react")

    except Exception as e:
        DBG(f"Reaction cycle error: {str(e)[:100]}", "fail")
        db_update_channel(channel_id,
                          fail_count=(fail_count or 0) + 1)
    finally:
        TASK_RUNNING = False
        TASK_USER_ID = None

    return result


# ══════════════════════ FULL ORDER NOTIFICATION ══════════════════════
async def notify_user_reactions(user_id, chat_title, post_id,
                                 sent, requested, post_link=None):
    try:
        u = db_get_user(user_id)
        if not u:
            return
        notify = u[6]
        if not notify:
            return

        # Build FULL message first
        fail_count = max(0, requested - sent)
        success_rate = int((sent / requested) * 100) if requested > 0 else 0

        full_msg = (
            f"📡 **AUTO-REACTION COMPLETE**\n"
            f"{STAR_LINE}\n\n"
            f"📢 **Channel:** {chat_title[:60]}\n"
            f"📩 **Post:** #{post_id}\n"
        )

        if post_link:
            full_msg += f"🔗 **Link:** {post_link}\n"

        full_msg += (
            f"\n{DIV()}\n"
            f"📊 **REACTION REPORT**\n"
            f"{DIV()}\n\n"
            f"🎯 Requested: **{requested}**\n"
            f"✅ Sent: **{sent}**\n"
            f"❌ Failed: **{fail_count}**\n"
            f"📈 Success Rate: **{success_rate}%**\n"
        )

        bar = progress_bar(sent, requested, width=15)
        full_msg += f"\n{bar}\n"

        full_msg += (
            f"\n{DIV()}\n"
            f"⏰ **Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🖥️ **Engine:** Real Sessions\n"
            f"👤 **User ID:** `{user_id}`\n"
            f"{DIV()}\n\n"
            f"✅ **Status:** Complete"
        )

        # Short DB save
        short_msg = (f"📡 {chat_title[:40]} | "
                     f"#{post_id} | {sent}/{requested}")
        db_add_notification(user_id, short_msg)

        # ✅ Try bot first, then via sessions if fails
        sent_ok = False
        try:
            await bot.send_message(
                user_id,
                full_msg,
                buttons=[
                    [btn("🏠 Home", data=b"home", style="primary"),
                     btn("📜 History", data=b"u:history",
                         style="success")]
                ])
            sent_ok = True
        except Exception as e:
            DBG(f"Bot send failed {user_id}: {str(e)[:60]} — trying via session",
                "warn")

        if not sent_ok:
            # Fallback: via sessions
            for sf in discover_sessions()[:5]:
                if is_session_flooded(sf):
                    continue
                try:
                    client = await get_real_client(sf)
                    if not client:
                        continue
                    await asyncio.wait_for(
                        client.send_message(user_id, full_msg),
                        timeout=15)
                    DBG(f"✅ Sent via {sf} to {user_id}", "ok")
                    sent_ok = True
                    break
                except Exception:
                    continue

        if not sent_ok:
            DBG(f"❌ Could not notify user {user_id}", "warn")

    except Exception as e:
        DBG(f"notify_user_reactions error: {str(e)[:80]}", "warn")


# ══════════════════════ CLEANUP LOOP ══════════════════════
async def cleanup_loop():
    await asyncio.sleep(30)
    DBG("Cleanup loop started", "sync")

    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)

            now = datetime.now()
            expired = []
            for k, (entity, exp) in list(ENTITY_CACHE.items()):
                if now >= exp:
                    expired.append(k)
            for k in expired:
                ENTITY_CACHE.pop(k, None)

            if len(REAL_CLIENTS) > MAX_CLIENTS:
                DBG(f"Too many clients: {len(REAL_CLIENTS)}", "warn")

            cleaned_floods = 0
            for sf in list(REAL_FLOOD_UNTIL.keys()):
                if now >= REAL_FLOOD_UNTIL[sf]:
                    REAL_FLOOD_UNTIL.pop(sf, None)
                    cleaned_floods += 1
            if cleaned_floods > 0:
                DBG(f"Cleared {cleaned_floods} session floods", "flood")

        except asyncio.CancelledError:
            return
        except Exception as e:
            DBG(f"Cleanup error: {str(e)[:80]}", "warn")


# ══════════════════════ HEALTH LOOP ══════════════════════
async def health_loop():
    while True:
        try:
            await asyncio.sleep(HEALTH_INTERVAL)
            uptime = int(time.time() - _start_time)
            sessions = get_sessions_status()
            DBG(f"HEALTH | uptime={uptime}s | "
                f"users={db_count_active_users()} | "
                f"channels={db_count_active_channels()} | "
                f"sessions={sessions['available']}/{sessions['total']} | "
                f"loaded={len(REAL_CLIENTS)} | "
                f"task={'RUNNING' if TASK_RUNNING else 'IDLE'}", "ok")
        except asyncio.CancelledError:
            return
        except Exception:
            pass


# ══════════════════════ AUTO-CLEANUP EXPIRED SESSIONS ══════════════════════
async def auto_cleanup_sessions():
    await asyncio.sleep(180)
    DBG("Auto-cleanup sessions loop started", "clean")

    while True:
        try:
            await asyncio.sleep(SESSION_AUTO_CLEANUP_INTERVAL)

            sessions = discover_sessions()
            if not sessions:
                continue

            DBG(f"Auto-cleanup: checking {len(sessions)} sessions", "clean")

            removed = 0
            kept = 0
            errors = 0
            removed_names = []

            for sf in sessions:
                try:
                    path = os.path.join(SESSIONS_DIR,
                                        sf.replace(".session", ""))
                    session_file = path + ".session"

                    if not os.path.exists(session_file):
                        continue

                    client = None
                    try:
                        client = TelegramClient(path, API_ID, API_HASH)
                        await asyncio.wait_for(
                            client.connect(), timeout=15)

                        authorized = await asyncio.wait_for(
                            client.is_user_authorized(), timeout=10)

                        if not authorized:
                            await client.disconnect()
                            try:
                                os.remove(session_file)
                                journal = session_file + "-journal"
                                if os.path.exists(journal):
                                    os.remove(journal)
                            except Exception:
                                pass
                            if sf in REAL_CLIENTS:
                                try:
                                    await REAL_CLIENTS[sf].disconnect()
                                except Exception:
                                    pass
                                REAL_CLIENTS.pop(sf, None)
                            removed += 1
                            removed_names.append(sf)
                            DBG(f"🗑️ Removed expired: {sf}", "clean")
                        else:
                            try:
                                await asyncio.wait_for(
                                    client.get_me(), timeout=8)
                                kept += 1
                            except Exception:
                                await client.disconnect()
                                try:
                                    os.remove(session_file)
                                    journal = session_file + "-journal"
                                    if os.path.exists(journal):
                                        os.remove(journal)
                                except Exception:
                                    pass
                                if sf in REAL_CLIENTS:
                                    try:
                                        await REAL_CLIENTS[sf].disconnect()
                                    except Exception:
                                        pass
                                    REAL_CLIENTS.pop(sf, None)
                                removed += 1
                                removed_names.append(sf)
                                DBG(f"🗑️ Removed banned: {sf}", "clean")
                                continue

                            await client.disconnect()
                    except Exception as e:
                        errors += 1
                        DBG(f"Cleanup check fail {sf}: {str(e)[:60]}",
                            "warn")
                        if client:
                            try:
                                await client.disconnect()
                            except Exception:
                                pass
                except Exception as e:
                    errors += 1
                    DBG(f"Cleanup error {sf}: {str(e)[:60]}", "warn")

            DBG(f"Auto-cleanup done: 🗑️ removed={removed} "
                f"✅ kept={kept} ⚠️ errors={errors}", "clean")

            if removed > 0:
                try:
                    sample = ", ".join(removed_names[:5])
                    if len(removed_names) > 5:
                        sample += f" (+{len(removed_names) - 5} more)"

                    await bot.send_message(
                        get_owner_id(),
                        f"🧹 **Auto-Cleanup Report**\n"
                        f"{STAR_LINE}\n\n"
                        f"🗑️ Removed: **{removed}**\n"
                        f"✅ Kept: **{kept}**\n"
                        f"⚠️ Errors: **{errors}**\n\n"
                        f"📋 **Removed files:**\n"
                        f"`{sample}`\n\n"
                        f"📊 Total remaining: **{len(discover_sessions())}**")
                except Exception:
                    pass

        except asyncio.CancelledError:
            return
        except Exception as e:
            DBG(f"Auto-cleanup loop error: {str(e)[:100]}", "fail")
            await asyncio.sleep(300)


# ══════════════════════ QUICK REACTION (Post Link) ══════════════════════
async def send_reactions_to_link(post_link, count=10,
                                  emoji_mode="default",
                                  custom_emojis=None):
    try:
        post_link = post_link.strip().split("?")[0]

        chat_ref = None
        msg_id = None

        m = re.search(r"t\.me/c/(\d+)/(\d+)", post_link)
        if m:
            chat_id = int(m.group(1))
            chat_ref = int(f"-100{chat_id}")
            msg_id = int(m.group(2))
        else:
            m = re.search(r"t\.me/\+([A-Za-z0-9_-]+)/(\d+)", post_link)
            if m:
                invite_hash = m.group(1)
                msg_id = int(m.group(2))
                for sf in discover_sessions()[:5]:
                    if is_session_flooded(sf):
                        continue
                    try:
                        client = await get_real_client(sf)
                        if not client:
                            continue
                        inv = await asyncio.wait_for(
                            client(CheckChatInviteRequest(invite_hash)),
                            timeout=REAL_ENTITY_TIMEOUT)
                        if hasattr(inv, "chat") and inv.chat:
                            chat_ref = inv.chat.id
                            break
                    except Exception:
                        continue

                if not chat_ref:
                    return 0, 0, 0, "Invite link resolve nahi ho saka"
            else:
                m = re.search(r"t\.me/([A-Za-z0-9_]+)/(\d+)", post_link)
                if m:
                    chat_ref = f"@{m.group(1)}"
                    msg_id = int(m.group(2))
                else:
                    m = re.search(r"@?([A-Za-z0-9_]+)/(\d+)", post_link)
                    if m:
                        chat_ref = f"@{m.group(1)}"
                        msg_id = int(m.group(2))

        if not chat_ref or not msg_id:
            return 0, 0, 0, "Invalid post link format"

        DBG(f"Quick reaction: {chat_ref} / #{msg_id}", "react")

        sessions = discover_sessions()
        if not sessions:
            return 0, 0, 0, "No sessions available"

        available = [s for s in sessions if not is_session_flooded(s)]
        if not available:
            return 0, 0, 0, "All sessions flooded"

        random.shuffle(available)
        senders = available[:min(count, len(available))]
        total = len(senders)

        if emoji_mode == "custom" and custom_emojis:
            valid = [e for e in custom_emojis if e in ALL_REACTIONS]
            pool = valid if valid else DEFAULT_REACTIONS.copy()
        else:
            pool = DEFAULT_REACTIONS.copy()

        ok = fail = flooded = 0

        for sf in senders:
            if is_session_flooded(sf):
                flooded += 1
                continue

            emoji = random.choice(pool)
            success, used, err = await session_send_reaction(
                sf, chat_ref, msg_id, emoji, emoji_pool=pool)

            if success:
                ok += 1
                db_log_session_use(sf, True)
            elif "flood" in err:
                flooded += 1
            else:
                fail += 1

            await asyncio.sleep(REAL_COOLDOWN)

        return ok, fail, total, ""

    except Exception as e:
        DBG(f"send_reactions_to_link error: {str(e)[:100]}", "fail")
        return 0, 0, 0, str(e)[:100]


# ══════════════════════ DAILY SUMMARY LOOP ══════════════════════
async def daily_summary_loop():
    last_sent = None
    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            cur = now.strftime("%H:%M")
            if cur == DAILY_SUMMARY_TIME and last_sent != today:
                last_sent = today
                await send_daily_summary()
        except asyncio.CancelledError:
            return
        except Exception as e:
            DBG(f"Daily summary loop error: {str(e)[:80]}", "warn")


async def send_daily_summary():
    try:
        today_rx = db_reactions_today()
        total_rx = db_total_reactions()
        users = db_count_active_users()
        channels = db_count_active_channels()
        sessions = get_sessions_status()
        recent = db_recent_reactions(10)

        txt = (
            f"📊 **DAILY SUMMARY — {datetime.now().strftime('%Y-%m-%d')}**\n"
            f"{STAR_LINE}\n\n"
            f"👥 Active Users: **{users}**\n"
            f"📢 Active Channels: **{channels}**\n\n"
            f"💫 Today: **{today_rx}**\n"
            f"💫 Total: **{total_rx}**\n\n"
            f"🔐 Sessions:\n"
            f"   Total: **{sessions['total']}**\n"
            f"   Available: **{sessions['available']}**\n"
            f"   Flooded: **{sessions['flooded']}**\n"
            f"   Loaded: **{sessions['loaded']}**\n\n"
            f"{DIV()}\n"
            f"📜 **Recent Activity**\n"
            f"{DIV()}\n\n"
        )
        if not recent:
            txt += "_No activity_"
        else:
            for r in recent[:5]:
                u_id, chat, post, sent, status, dt = r
                icon = "✅" if status == "ok" else "❌"
                txt += (f"{icon} `{u_id}` — {chat[:20]}\n"
                        f"   💫 {sent} | 🕒 {dt[:16]}\n")

        await bot.send_message(get_owner_id(), txt)
        DBG("Daily summary sent", "ok")
    except Exception as e:
        DBG(f"Daily summary error: {str(e)[:80]}", "fail")


# ══════════════════════ SESSION HEALTH CHECK ══════════════════════
async def session_health_check():
    while True:
        try:
            await asyncio.sleep(1800)
            sessions = discover_sessions()
            if not sessions:
                continue
            sample = random.sample(sessions, min(3, len(sessions)))
            for sf in sample:
                if is_session_flooded(sf):
                    continue
                try:
                    client = await get_real_client(sf)
                    if client:
                        try:
                            me = await asyncio.wait_for(
                                client.get_me(), timeout=8)
                            DBG(f"Session OK: {sf} → "
                                f"{getattr(me, 'first_name', '?')}", "ok")
                        except Exception:
                            pass
                except Exception:
                    pass
        except asyncio.CancelledError:
            return
        except Exception as e:
            DBG(f"Session health error: {str(e)[:80]}", "warn")


# ══════════════════════ USER KEYBOARDS ══════════════════════
def kb_user_home(uid):
    u = db_get_user(uid)
    notify_on = (u[6] == 1) if u else False

    return [
        [btn(f"📊 My Settings", data=b"u:settings", style="primary")],
        [btn(f"📢 My Channels", data=b"u:channels", style="primary"),
         btn(f"📜 History", data=b"u:history", style="primary")],
        [btn(f"🔔 Notifications {'✅' if notify_on else '❌'}",
             data=b"u:toggle_notify",
             style="success" if notify_on else "danger")],
        [btn(f"🌍 Language", data=b"u:language", style="primary"),
         btn(f"❓ Help", data=b"u:help", style="success")],
        [btn(f"💬 Contact Owner",
             url=f"https://t.me/{get_owner_username()}",
             style="success")],
    ]


def kb_user_back(uid=None):
    return [[btn("🔙 Back", data=b"u:home", style="primary")]]


def kb_user_settings(uid):
    return [
        [btn("📢 View Channels", data=b"u:channels", style="success")],
        [btn("🔙 Back", data=b"u:home", style="primary")],
    ]


def kb_user_channels(uid):
    channels = db_list_channels(uid)

    if not channels:
        return [
            [btn("➕ Add Channel", data=b"u:add_ch", style="success")],
            [btn("💬 Contact Owner",
                 url=f"https://t.me/{get_owner_username()}",
                 style="success")],
            [btn("🔙 Back", data=b"u:home", style="primary")],
        ]

    rows = []
    for ch in channels[:8]:
        cid = ch[0]
        chat_title = ch[4] or ch[3] or "Unknown"
        st = "🟢" if ch[8] else "🔴"
        title_short = chat_title[:28]
        rows.append([
            btn(f"{st} {title_short}",
                data=f"u:ch:{cid}".encode(),
                style="primary")
        ])

    if len(channels) > 8:
        rows.append([
            btn(f"... +{len(channels) - 8} more",
                data=b"u:channels_more", style="primary")
        ])

    rows.append([
        btn("➕ Add Channel", data=b"u:add_ch", style="success")
    ])
    rows.append([
        btn("🔙 Back", data=b"u:home", style="primary")
    ])

    return rows


def kb_user_channel_detail(uid, channel_id):
    return [
        [btn("📜 History",
             data=f"u:ch_hist:{channel_id}".encode(), style="primary")],
        [btn("✏️ Edit Count",
             data=f"u:ch_count:{channel_id}".encode(), style="primary"),
         btn("✏️ Edit Emojis",
             data=f"u:ch_emoji:{channel_id}".encode(), style="primary")],
        [btn("✏️ Edit Link",
             data=f"u:ch_link:{channel_id}".encode(), style="primary"),
         btn("🔄 Re-join",
             data=f"u:ch_rejoin:{channel_id}".encode(), style="success")],
        [btn("🗑️ Remove",
             data=f"u:ch_del:{channel_id}".encode(), style="danger")],
        [btn("💬 Contact Owner",
             url=f"https://t.me/{get_owner_username()}", style="success")],
        [btn("🔙 Back", data=b"u:channels", style="primary")],
    ]


def kb_user_channel_history(uid, channel_id):
    return [
        [btn("🔄 Refresh",
             data=f"u:ch_hist:{channel_id}".encode(), style="success")],
        [btn("🔙 Back",
             data=f"u:ch:{channel_id}".encode(), style="primary")],
    ]


def kb_user_language(uid):
    u = db_get_user(uid)
    cur = u[7] if u else "en"

    rows = []
    for code, name in LANGUAGES.items():
        pre = "✅ " if code == cur else ""
        rows.append([
            btn(f"{pre}{name}",
                data=f"u:lang:{code}".encode(),
                style="success" if code == cur else "primary")
        ])

    rows.append([btn("🔙 Back", data=b"u:home", style="primary")])
    return rows


def kb_user_history(uid):
    return [
        [btn("🔄 Refresh", data=b"u:history", style="success")],
        [btn("🔙 Back", data=b"u:home", style="primary")],
    ]


def kb_user_help(uid):
    return [
        [btn("💬 Contact Owner",
             url=f"https://t.me/{get_owner_username()}", style="success")],
        [btn("🔙 Back", data=b"u:home", style="primary")],
    ]


def kb_user_notifications(uid):
    return [
        [btn("🔄 Refresh", data=b"u:notif", style="success"),
         btn("✅ Mark Read", data=b"u:notif_read", style="primary")],
        [btn("🔙 Back", data=b"u:home", style="primary")],
    ]


def kb_denied(uid=None):
    return [
        [btn("💬 Contact Owner",
             url=f"https://t.me/{get_owner_username()}", style="success")],
        [btn("ℹ️ About This Bot", data=b"d:about", style="primary")],
    ]


# ══════════════════════ USER TEXT BUILDERS ══════════════════════
def build_user_settings_text(uid):
    return get_settings_message(uid)


def build_user_channels_text(uid):
    u = db_get_user(uid)
    if not u:
        return get_denied_message(uid)

    channels = db_list_channels(uid)

    txt = (
        f"{STAR_LINE}\n"
        f"📢 **{L(uid, 'my_channels')}** ({len(channels)})\n"
        f"{STAR_LINE}\n\n"
    )

    if not channels:
        txt += (
            f"_{L(uid, 'no_channels')}_\n\n"
            f"{DIV()}\n"
            f"⚠️ Add channel neeche button se\n"
            f"👑 @{get_owner_username()}"
        )
        return txt

    for i, ch in enumerate(channels[:8], 1):
        cid = ch[0]
        chat_title = ch[4] or ch[3] or "Unknown"
        cnt = ch[5]
        emo = ch[6]
        is_act = ch[8]
        joined = ch[9]
        total_sent = ch[12]

        st = "🟢" if is_act else "🔴"
        jn = "✅" if joined else "⏳"

        txt += (
            f"{st} **{i}. {chat_title[:30]}**\n"
            f"      🎯 {cnt} reactions/post\n"
            f"      ✏️ {emo}\n"
            f"      {jn} Joined\n"
            f"      💫 Sent: {total_sent or 0}\n\n"
        )

    if len(channels) > 8:
        txt += f"_... +{len(channels) - 8} more channels_\n\n"

    txt += (
        f"{DIV()}\n"
        f"➕ Add channel neeche button se\n"
        f"👑 @{get_owner_username()}"
    )

    return txt


def build_user_history_text(uid):
    rows = db_reactions_by_user(uid, 15)

    txt = (
        f"{STAR_LINE}\n"
        f"📜 **{L(uid, 'history')}**\n"
        f"{STAR_LINE}\n\n"
    )

    if not rows:
        txt += f"_{L(uid, 'no_history')}_"
        return txt

    for i, r in enumerate(rows, 1):
        chat, post, sent, status, dt = r
        icon = "✅" if status == "ok" else "❌"
        txt += (
            f"{icon} **{i}.** {chat[:30]}\n"
            f"      📩 #{post} | 💫 {sent}\n"
            f"      🕒 {dt[:16]}\n\n"
        )

    return txt


# ══════════════════════ END OF PART 3 ══════════════════════
print("[STARTUP] Part 3 loaded (Workers + Auto-Cleanup + Quick React + Keyboards)", flush=True)
# ══════════════════════ OWNER MAIN PANEL ══════════════════════
def kb_owner_home():
    users = db_count_users()
    channels = db_count_channels()
    sessions = get_sessions_status()
    owner_channels = db_list_channels(get_owner_id())

    return [
        [btn(f"👥 Users ({users})", data=b"o:users", style="primary"),
         btn(f"📢 Channels ({channels})", data=b"o:channels", style="primary")],
        [btn(f"🔐 Sessions ({sessions['available']}/{sessions['total']})",
             data=b"o:sessions", style="success"),
         btn("📊 Analytics", data=b"o:analytics", style="success")],
        [btn("⚡ Quick Reaction", data=b"o:quick_react", style="danger"),
         btn("📜 Recent Activity", data=b"o:recent", style="primary")],
        [btn("🔄 Run Check Now", data=b"o:run_check", style="success"),
         btn("📢 Broadcast", data=b"o:broadcast", style="danger")],
        [btn("📋 Notifications", data=b"o:notifications", style="primary"),
         btn(f"👤 My Channels ({len(owner_channels)})",
             data=b"u:channels", style="success")],
        [btn("⚙️ Settings", data=b"o:settings", style="primary"),
         btn("🌍 Owner Info", data=b"o:owner_info", style="success")],
        [btn("🔙 Main", data=b"home", style="danger")],
    ]


def kb_owner_back():
    return [[btn("🔙 Back", data=b"o:home", style="primary")]]


def kb_owner_users(page=0):
    per_page = 10
    users = db_list_users(limit=per_page, offset=page * per_page)
    total = db_count_users()
    total_pages = max(1, (total + per_page - 1) // per_page)

    rows = []
    for u in users:
        u_id, username, first_name, is_active, added = u
        st = "🟢" if is_active else "🔴"
        name = (first_name or "User")[:20]
        rows.append([
            btn(f"{st} {name} — `{u_id}`",
                data=f"o:user:{u_id}".encode(), style="primary")
        ])

    if not users:
        rows.append([
            btn("❌ No users yet", data=b"o:home", style="danger")
        ])

    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev",
                       data=f"o:users:{page - 1}".encode(), style="primary"))
    if page < total_pages - 1:
        nav.append(btn("Next ➡️",
                       data=f"o:users:{page + 1}".encode(), style="primary"))
    if nav:
        rows.append(nav)

    rows.append([
        btn(f"📄 {page + 1}/{total_pages}",
            data=f"o:users:{page}".encode(), style="success")
    ])
    rows.append([
        btn("➕ Add User", data=b"o:add_user", style="success"),
        btn("🔙 Back", data=b"o:home", style="primary")
    ])

    return rows


def kb_owner_user_detail(user_id):
    u = db_get_user(user_id)
    if not u:
        return [[btn("❌ Not found", data=b"o:users", style="danger")]]

    is_active = u[5] == 1
    notify = u[6] == 1
    channels = db_list_channels(user_id)

    return [
        [btn(f"{'🟢 Active' if is_active else '🔴 Inactive'}",
             data=f"o:user_toggle:{user_id}".encode(),
             style="success" if is_active else "danger")],
        [btn(f"📢 Channels ({len(channels)})",
             data=f"o:user_channels:{user_id}".encode(), style="primary")],
        [btn(f"➕ Add Channel",
             data=f"o:user_add_ch:{user_id}".encode(), style="success")],
        [btn(f"🔔 Notify {'✅' if notify else '❌'}",
             data=f"o:user_notify:{user_id}".encode(), style="primary")],
        [btn(f"📝 Notes",
             data=f"o:user_notes:{user_id}".encode(), style="primary")],
        [btn(f"🗑️ Remove User",
             data=f"o:user_del:{user_id}".encode(), style="danger")],
        [btn("🔙 Back", data=b"o:users", style="primary")],
    ]


def kb_owner_user_channels(user_id, page=0):
    channels = db_list_channels(user_id)
    per_page = 8
    start = page * per_page
    page_chs = channels[start:start + per_page]
    total = len(channels)
    total_pages = max(1, (total + per_page - 1) // per_page)

    rows = []
    for ch in page_chs:
        cid = ch[0]
        ch_title = (ch[4] or ch[3] or "Unknown")[:22]
        cnt = ch[5]
        st = "🟢" if ch[8] else "🔴"
        rows.append([
            btn(f"{st} {ch_title} — {cnt}/post",
                data=f"o:ch:{cid}".encode(), style="primary")
        ])

    if not page_chs:
        rows.append([
            btn("❌ No channels",
                data=f"o:user_add_ch:{user_id}".encode(), style="danger")
        ])

    nav = []
    if page > 0:
        nav.append(btn("⬅️",
                       data=f"o:user_channels:{user_id}:{page - 1}".encode(),
                       style="primary"))
    if page < total_pages - 1:
        nav.append(btn("➡️",
                       data=f"o:user_channels:{user_id}:{page + 1}".encode(),
                       style="primary"))
    if nav:
        rows.append(nav)

    rows.append([
        btn("➕ Add Channel",
            data=f"o:user_add_ch:{user_id}".encode(), style="success")
    ])
    rows.append([
        btn("🔙 Back", data=f"o:user:{user_id}".encode(), style="primary")
    ])

    return rows


def kb_owner_channels(page=0):
    channels = db_list_channels()
    per_page = 8
    start = page * per_page
    page_chs = channels[start:start + per_page]
    total = len(channels)
    total_pages = max(1, (total + per_page - 1) // per_page)

    rows = []
    for ch in page_chs:
        cid = ch[0]
        user_id = ch[1]
        ch_title = (ch[4] or ch[3] or "Unknown")[:20]
        cnt = ch[5]
        st = "🟢" if ch[8] else "🔴"
        rows.append([
            btn(f"{st} {ch_title} — {cnt} | u:{user_id}",
                data=f"o:ch:{cid}".encode(), style="primary")
        ])

    if not page_chs:
        rows.append([
            btn("❌ No channels yet", data=b"o:home", style="danger")
        ])

    nav = []
    if page > 0:
        nav.append(btn("⬅️ Prev",
                       data=f"o:channels:{page - 1}".encode(), style="primary"))
    if page < total_pages - 1:
        nav.append(btn("Next ➡️",
                       data=f"o:channels:{page + 1}".encode(), style="primary"))
    if nav:
        rows.append(nav)

    rows.append([
        btn(f"📄 {page + 1}/{total_pages}",
            data=f"o:channels:{page}".encode(), style="success")
    ])
    rows.append([btn("🔙 Back", data=b"o:home", style="primary")])

    return rows


def kb_owner_channel_detail(channel_id):
    ch = db_get_channel(channel_id)
    if not ch:
        return [[btn("❌ Not found", data=b"o:channels", style="danger")]]

    is_act = ch[8] == 1
    joined = ch[9] == 1

    return [
        [btn(f"{'🟢 Active' if is_act else '🔴 Paused'}",
             data=f"o:ch_toggle:{channel_id}".encode(),
             style="success" if is_act else "danger")],
        [btn(f"✏️ Count: {ch[5]}",
             data=f"o:ch_count:{channel_id}".encode(), style="primary")],
        [btn(f"✏️ Emoji: {ch[6]}",
             data=f"o:ch_emoji:{channel_id}".encode(), style="primary")],
        [btn(f"✏️ Change Link",
             data=f"o:ch_link:{channel_id}".encode(), style="primary")],
        [btn(f"🔄 {'Re-join' if joined else 'Join Now'}",
             data=f"o:ch_rejoin:{channel_id}".encode(), style="success")],
        [btn("📜 History",
             data=f"o:ch_hist:{channel_id}".encode(), style="primary"),
         btn("🧪 Test Now",
             data=f"o:ch_test:{channel_id}".encode(), style="success")],
        [btn("🗑️ Remove Channel",
             data=f"o:ch_del:{channel_id}".encode(), style="danger")],
        [btn("🔙 Back", data=b"o:channels", style="primary")],
    ]


def kb_owner_channel_history(channel_id):
    return [
        [btn("🔄 Refresh",
             data=f"o:ch_hist:{channel_id}".encode(), style="success")],
        [btn("🔙 Back",
             data=f"o:ch:{channel_id}".encode(), style="primary")],
    ]


def kb_owner_sessions():
    status = get_sessions_status()

    return [
        [btn(f"📊 Total: {status['total']}",
             data=b"o:sessions", style="primary"),
         btn(f"🟢 Available: {status['available']}",
             data=b"o:sessions", style="success")],
        [btn(f"🌊 Flooded: {status['flooded']}",
             data=b"o:sessions_clear", style="danger"),
         btn(f"💾 Loaded: {status['loaded']}",
             data=b"o:sessions", style="primary")],
        [btn("📋 View All Sessions", data=b"o:sessions_list", style="primary")],
        [btn("🌊 Clear All Floods", data=b"o:sessions_clear", style="danger")],
        [btn("🔌 Disconnect All", data=b"o:sessions_disconnect", style="danger")],
        [btn("🧪 Test Sessions", data=b"o:sessions_test", style="success")],
        [btn("📥 Reload Sessions", data=b"o:sessions_reload", style="success")],
        [btn("🧹 Cleanup Now", data=b"o:sessions_cleanup", style="danger")],
        [btn("🔙 Back", data=b"o:home", style="primary")],
    ]


def kb_owner_sessions_list(page=0):
    sessions = discover_sessions()
    per_page = 10
    start = page * per_page
    page_sess = sessions[start:start + per_page]
    total = len(sessions)
    total_pages = max(1, (total + per_page - 1) // per_page)

    rows = []
    for i, sf in enumerate(page_sess, start=start + 1):
        flooded = "🌊" if is_session_flooded(sf) else "🟢"
        loaded = "💾" if sf in REAL_CLIENTS else "📁"
        rows.append([
            btn(f"{flooded}{loaded} {i}. {sf[:24]}",
                data=f"o:sess_info:{sf}".encode(),
                style="danger" if flooded == "🌊" else "primary")
        ])

    if not page_sess:
        rows.append([
            btn("❌ No sessions found", data=b"o:sessions", style="danger")
        ])

    nav = []
    if page > 0:
        nav.append(btn("⬅️",
                       data=f"o:sessions_list:{page - 1}".encode(),
                       style="primary"))
    if page < total_pages - 1:
        nav.append(btn("➡️",
                       data=f"o:sessions_list:{page + 1}".encode(),
                       style="primary"))
    if nav:
        rows.append(nav)

    rows.append([
        btn(f"📄 {page + 1}/{total_pages}",
            data=f"o:sessions_list:{page}".encode(), style="success")
    ])
    rows.append([btn("🔙 Back", data=b"o:sessions", style="primary")])

    return rows


def kb_owner_session_info(sf):
    flooded = is_session_flooded(sf)
    loaded = sf in REAL_CLIENTS
    flood_until = REAL_FLOOD_UNTIL.get(sf)

    flood_txt = "—"
    if flood_until:
        remaining = int((flood_until - datetime.now()).total_seconds())
        flood_txt = f"{remaining}s" if remaining > 0 else "expired"

    return [
        [btn(f"📁 {sf[:30]}", data=b"o:sessions", style="primary")],
        [btn(f"🌊 Flooded: {'✅' if flooded else '❌'}",
             data=f"o:sess_flood:{sf}".encode(),
             style="danger" if flooded else "success")],
        [btn(f"💾 Loaded: {'✅' if loaded else '❌'}",
             data=f"o:sess_load:{sf}".encode(), style="primary")],
        [btn(f"⏱️ Flood till: {flood_txt}",
             data=b"o:sessions", style="primary")],
        [btn("🧪 Test",
             data=f"o:sess_test:{sf}".encode(), style="success"),
         btn("🔄 Reset Flood",
             data=f"o:sess_reset:{sf}".encode(), style="primary")],
        [btn("🔌 Disconnect",
             data=f"o:sess_disc:{sf}".encode(), style="danger")],
        [btn("🔙 Back", data=b"o:sessions_list", style="primary")],
    ]


def kb_owner_analytics():
    return [
        [btn("📜 Recent Reactions", data=b"o:recent", style="primary")],
        [btn("📊 Today Stats", data=b"o:analytics_today", style="success")],
        [btn("🏆 Top Users", data=b"o:top_users", style="primary")],
        [btn("📢 Top Channels", data=b"o:top_channels", style="primary")],
        [btn("🔙 Back", data=b"o:home", style="primary")],
    ]


def kb_owner_settings():
    def yn(v):
        return "✅" if v else "❌"

    multi_lang = cfg_bool("multi_lang_enabled")
    notify_default = cfg_bool("notify_user_default")

    return [
        [btn(f"{yn(multi_lang)} Multi-Language",
             data=b"o:toggle:multi_lang_enabled", style="primary")],
        [btn(f"{yn(notify_default)} Default Notify",
             data=b"o:toggle:notify_user_default", style="success")],
        [btn("✏️ Change Owner Username",
             data=b"o:edit_uname", style="primary")],
        [btn("✏️ Change Owner ID",
             data=b"o:edit_oid", style="primary")],
        [btn("🔙 Back", data=b"o:home", style="primary")],
    ]


def kb_confirm_delete_user(user_id):
    return [
        [btn("⚠️ YES, Remove",
             data=f"o:user_del_confirm:{user_id}".encode(), style="danger")],
        [btn("❌ Cancel",
             data=f"o:user:{user_id}".encode(), style="primary")],
    ]


def kb_confirm_delete_channel(channel_id):
    return [
        [btn("⚠️ YES, Remove",
             data=f"o:ch_del_confirm:{channel_id}".encode(), style="danger")],
        [btn("❌ Cancel",
             data=f"o:ch:{channel_id}".encode(), style="primary")],
    ]


def kb_owner_broadcast():
    return [
        [btn("👥 To All Users", data=b"o:bc_users", style="primary")],
        [btn("💎 To Active Users", data=b"o:bc_active", style="success")],
        [btn("🔙 Cancel", data=b"o:home", style="danger")],
    ]


def kb_owner_notifications():
    return [
        [btn("📤 Send to User", data=b"o:notif_user", style="primary")],
        [btn("📢 Broadcast Notification",
             data=b"o:notif_broadcast", style="success")],
        [btn("🔙 Back", data=b"o:home", style="primary")],
    ]


# ══════════════════════ OWNER TEXT BUILDERS ══════════════════════
def build_owner_users_text(page=0):
    per_page = 10
    users = db_list_users(limit=per_page, offset=page * per_page)
    total = db_count_users()
    active = db_count_active_users()
    total_pages = max(1, (total + per_page - 1) // per_page)

    txt = (
        f"{STAR_LINE}\n"
        f"👥 **USERS — Page {page + 1}/{total_pages}**\n"
        f"{STAR_LINE}\n\n"
        f"📊 Total: **{total}**\n"
        f"🟢 Active: **{active}**\n\n"
        f"{DIV()}\n\n"
    )

    if not users:
        txt += "_No users yet_"
    else:
        for u in users:
            u_id, username, first_name, is_active, added = u
            st = "🟢" if is_active else "🔴"
            name = first_name or "User"
            uname = f"@{username}" if username else "—"
            txt += (
                f"{st} **{name}**\n"
                f"   🆔 `{u_id}`\n"
                f"   📛 {uname}\n"
                f"   📅 {added[:16] if added else '—'}\n\n"
            )

    return txt


def build_owner_channel_detail(channel_id):
    ch = db_get_channel(channel_id)
    if not ch:
        return "❌ Not found"

    (cid, user_id, chat_id, chat_link, chat_title, rc, emo, ce,
     is_act, joined, last_post, last_run, total_reactions,
     fail_count, created) = ch

    st = "🟢 Active" if is_act else "🔴 Paused"
    jn = "✅ Joined" if joined else "⏳ Not joined"
    emo_disp = emo
    if ce:
        emo_disp += f" ({ce[:40]})"

    return (
        f"{STAR_LINE}\n"
        f"📢 **CHANNEL DETAIL**\n"
        f"{STAR_LINE}\n\n"
        f"📢 {chat_title[:50]}\n"
        f"🔗 {chat_link}\n\n"
        f"{DIV()}\n"
        f"👤 User: `{user_id}`\n"
        f"🆔 Chat ID: `{chat_id or '—'}`\n"
        f"🎯 Reactions: **{rc}**/post\n"
        f"✏️ Emoji: **{emo_disp}**\n"
        f"🔄 Status: **{st}**\n"
        f"👥 {jn}\n\n"
        f"💫 Total Sent: **{total_reactions or 0}**\n"
        f"❌ Fails: **{fail_count or 0}**\n"
        f"📩 Last Post: **#{last_post if last_post else '—'}**\n"
        f"🕒 Last Run: **{last_run[:16] if last_run else 'Never'}**\n"
        f"📅 Added: **{created[:16] if created else '—'}**"
    )


def build_owner_analytics_text():
    today = db_reactions_today()
    total = db_total_reactions()
    users = db_count_active_users()
    channels = db_count_active_channels()
    sessions = get_sessions_status()

    return (
        f"{STAR_LINE}\n"
        f"📊 **ANALYTICS**\n"
        f"{STAR_LINE}\n\n"
        f"👥 Active Users: **{users}**\n"
        f"📢 Active Channels: **{channels}**\n\n"
        f"💫 Today: **{today}**\n"
        f"💫 Total: **{total}**\n\n"
        f"🔐 Sessions:\n"
        f"   Available: **{sessions['available']}/{sessions['total']}**\n"
        f"   Flooded: **{sessions['flooded']}**\n"
        f"   Loaded: **{sessions['loaded']}**"
    )


def build_owner_recent_text():
    rows = db_recent_reactions(15)

    txt = (
        f"{STAR_LINE}\n"
        f"📜 **RECENT ACTIVITY**\n"
        f"{STAR_LINE}\n\n"
    )

    if not rows:
        txt += "_No activity yet_"
        return txt

    for i, r in enumerate(rows, 1):
        u_id, chat, post, sent, status, dt = r
        icon = "✅" if status == "ok" else "❌"
        txt += (
            f"{icon} **{i}.** `{u_id}`\n"
            f"   📢 {chat[:30]}\n"
            f"   📩 #{post} | 💫 {sent}\n"
            f"   🕒 {dt[:16]}\n\n"
        )

    return txt


def build_owner_top_users_text():
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            rows = conn.execute("""SELECT user_id, first_name,
                total_reactions FROM users
                WHERE total_reactions > 0
                ORDER BY total_reactions DESC LIMIT 10""").fetchall()
        finally:
            conn.close()
    except Exception:
        rows = []

    txt = f"{STAR_LINE}\n🏆 **TOP USERS**\n{STAR_LINE}\n\n"

    if not rows:
        txt += "_No data yet_"
        return txt

    medals = ["🥇", "🥈", "🥉"]
    for i, r in enumerate(rows, 1):
        u_id, name, total = r
        medal = medals[i - 1] if i <= 3 else f"{i}."
        txt += f"{medal} **{name or 'User'}** — **{total}**\n"
        txt += f"   🆔 `{u_id}`\n\n"

    return txt


def build_owner_top_channels_text():
    try:
        conn = sqlite3.connect(DB_FILE)
        try:
            rows = conn.execute("""SELECT chat_title, chat_id,
                SUM(reactions_sent) as total
                FROM auto_reactions WHERE status='ok'
                GROUP BY chat_id ORDER BY total DESC LIMIT 10""").fetchall()
        finally:
            conn.close()
    except Exception:
        rows = []

    txt = f"{STAR_LINE}\n📢 **TOP CHANNELS**\n{STAR_LINE}\n\n"

    if not rows:
        txt += "_No data yet_"
        return txt

    for i, r in enumerate(rows, 1):
        title, cid, total = r
        txt += f"**{i}.** {title[:35]}\n"
        txt += f"      🆔 `{cid}` | 💫 **{total}**\n\n"

    return txt


# ══════════════════════ /start COMMAND ══════════════════════
@bot.on(events.NewMessage(pattern="/start"))
async def on_start(event):
    try:
        if event.is_channel:
            return
        uid = event.sender_id
        if uid is None:
            return

        DBG(f"/start from {uid}", "info")

        try:
            sender = await event.get_sender()
            if sender and not isinstance(sender, (Channel, Chat)):
                fn = getattr(sender, "first_name", "User") or "User"
                un = getattr(sender, "username", None)
            else:
                fn, un = "User", None
        except Exception:
            fn, un = "User", None

        if OWNER_IS(uid):
            await event.reply(
                get_owner_dashboard_message(),
                buttons=kb_owner_home())
            return

        if db_is_whitelisted(uid):
            u = db_get_user(uid)
            if not u:
                db_add_user(uid, un, fn, added_by=get_owner_id())
            await event.reply(
                get_welcome_message(uid),
                buttons=kb_user_home(uid))
            return

        await event.reply(
            get_denied_message(uid),
            buttons=kb_denied(uid))

    except Exception as e:
        D_err(e, "on_start")


@bot.on(events.NewMessage(pattern="/help"))
async def on_help(event):
    try:
        if event.is_channel:
            return
        uid = event.sender_id
        if uid is None:
            return

        if OWNER_IS(uid):
            await event.reply(
                f"👑 **Owner Commands**\n"
                f"{STAR_LINE}\n\n"
                f"• /start — Owner panel\n"
                f"• /help — This message\n"
                f"• /stats — Quick stats\n"
                f"• /ping — Bot status\n",
                buttons=kb_owner_home())
            return

        if db_is_whitelisted(uid):
            await event.reply(
                get_help_text(uid),
                buttons=kb_user_help(uid))
            return

        await event.reply(
            get_denied_message(uid),
            buttons=kb_denied(uid))
    except Exception as e:
        D_err(e, "on_help")


@bot.on(events.NewMessage(pattern="/ping"))
async def on_ping(event):
    try:
        if event.is_channel:
            return
        uid = event.sender_id
        if uid is None:
            return

        uptime = int(time.time() - _start_time)
        sessions = get_sessions_status()

        await event.reply(
            f"🏓 **Pong!**\n"
            f"{DIV()}\n\n"
            f"⏱️ Uptime: **{uptime}s**\n"
            f"👥 Users: **{db_count_active_users()}**\n"
            f"📢 Channels: **{db_count_active_channels()}**\n"
            f"🔐 Sessions: **{sessions['available']}/{sessions['total']}**\n"
            f"🎯 Task: **{'🔴 RUNNING' if TASK_RUNNING else '🟢 IDLE'}**")
    except Exception as e:
        D_err(e, "on_ping")


@bot.on(events.NewMessage(pattern="/stats"))
async def on_stats(event):
    try:
        if event.is_channel:
            return
        uid = event.sender_id
        if uid is None or not OWNER_IS(uid):
            return

        await event.reply(
            build_owner_analytics_text(),
            buttons=kb_owner_analytics())
    except Exception as e:
        D_err(e, "on_stats")


# ══════════════════════ MAIN MESSAGE HANDLER ══════════════════════
@bot.on(events.NewMessage)
async def on_msg(event):
    try:
        if event.is_channel:
            return
        uid = event.sender_id
        if uid is None:
            return
        if event.text and event.text.startswith("/"):
            return

        text = (event.text or "").strip()
        if not text:
            return

        if OWNER_IS(uid) and uid in USER_STATES:
            await handle_owner_state(event, uid, text)
            return

        if uid in USER_STATES:
            await handle_user_state(event, uid, text)
            return

        if OWNER_IS(uid):
            await event.reply("👉 Send /start",
                              buttons=kb_owner_home())
        elif db_is_whitelisted(uid):
            await event.reply("👉 Send /start",
                              buttons=kb_user_home(uid))
        else:
            await event.reply(get_denied_message(uid),
                              buttons=kb_denied(uid))

    except Exception as e:
        D_err(e, "on_msg")


# ══════════════════════ OWNER STATE HANDLER ══════════════════════
async def handle_owner_state(event, uid, text):
    state = USER_STATES.get(uid, {})
    step = state.get("step")

    try:
        # ── Add User ──
        if step == "add_user_id":
            text_clean = text.strip()
            target_id = None
            fn = None
            un = None

            if text_clean.lstrip("-").isdigit():
                target_id = int(text_clean)
                try:
                    ent = await safe_get_entity(target_id)
                    fn = getattr(ent, "first_name", None)
                    un = getattr(ent, "username", None)
                except Exception:
                    pass

            elif text_clean.startswith("@"):
                await event.reply(
                    f"🔍 **Resolving @{text_clean.lstrip('@')}...**\n\n"
                    f"⏳ Please wait...")
                target_id, fn, un = await resolve_username_to_id(text_clean)

                if not target_id:
                    await event.reply(
                        f"❌ **Could not resolve username**\n\n"
                        f"`{text_clean}`\n\n"
                        f"**Wajahaat:**\n"
                        f"• Username exist nahi karta\n"
                        f"• Sessions us user ko nahi jaante\n"
                        f"• Username recent change hua hai\n\n"
                        f"➡️ **Numeric ID try karein:**\n"
                        f"User se @userinfobot se ID mangwayein",
                        buttons=kb_owner_back())
                    return
            else:
                await event.reply(
                    f"❌ **Invalid input**\n\n"
                    f"Send numeric user ID or @username\n\n"
                    f"**Examples:**\n"
                    f"• `123456789`\n"
                    f"• `@username`",
                    buttons=kb_owner_back())
                return

            ok, result = db_add_user(
                target_id, un, fn, added_by=get_owner_id())

            if ok:
                del USER_STATES[uid]
                await event.reply(
                    f"✅ **User {result}!**\n\n"
                    f"🆔 `{target_id}`\n"
                    f"📛 {fn or 'Unknown'}\n"
                    f"📝 @{un or 'no_username'}\n\n"
                    f"➡️ Ab unke liye channel add karo",
                    buttons=[
                        [btn("➕ Add Channel",
                             data=f"o:user_add_ch:{target_id}".encode(),
                             style="success")],
                        [btn("👤 View User",
                             data=f"o:user:{target_id}".encode(),
                             style="primary")],
                        [btn("🔙 Users", data=b"o:users",
                             style="primary")],
                    ])
            else:
                await event.reply(f"❌ Failed: {result}",
                                  buttons=kb_owner_back())
            return

        # ── Add Channel (owner adds for user) ──
        if step == "add_channel_link":
            target_user = state.get("target_user")
            if not target_user:
                del USER_STATES[uid]
                await event.reply("❌ Session expired",
                                  buttons=kb_owner_back())
                return

            link = text.strip()
            if not (link.startswith("@") or "t.me/" in link
                    or link.lstrip("-").isdigit()):
                await event.reply(
                    f"❌ **Invalid link**\n\n"
                    f"Send @username or t.me/... or -100XXXXX",
                    buttons=kb_owner_back())
                return

            try:
                cid, title, ctype, invite = await resolve_chat_info(link)
            except Exception as e:
                await event.reply(
                    f"❌ **Resolve failed**\n\n{str(e)[:100]}",
                    buttons=kb_owner_back())
                return

            if not cid:
                await event.reply(
                    f"❌ **Could not find chat**\n\n"
                    f"Try @username or numeric ID",
                    buttons=kb_owner_back())
                return

            if db_channel_already_exists(target_user, cid):
                await event.reply(
                    f"⚠️ Channel already added\n\n"
                    f"📢 {title}\n🆔 `{cid}`",
                    buttons=[[btn("🔙 Back",
                        data=f"o:user_channels:{target_user}".encode(),
                        style="primary")]])
                del USER_STATES[uid]
                return

            state["add_channel_id"] = cid
            state["add_channel_link"] = link
            state["add_channel_title"] = title
            state["step"] = "add_channel_count"
            USER_STATES[uid] = state

            await event.reply(
                f"✅ **Chat found!**\n\n"
                f"📢 {title}\n"
                f"🆔 `{cid}`\n"
                f"📁 {ctype or 'unknown'}\n\n"
                f"➡️ Reactions count (1-100):",
                buttons=[
                    [btn("5", data=b"o:qc:5", style="primary"),
                     btn("10", data=b"o:qc:10", style="primary"),
                     btn("20", data=b"o:qc:20", style="primary")],
                    [btn("30", data=b"o:qc:30", style="primary"),
                     btn("50", data=b"o:qc:50", style="primary"),
                     btn("100", data=b"o:qc:100", style="primary")],
                    [btn("🔙 Cancel",
                         data=f"o:user:{target_user}".encode(),
                         style="danger")],
                ])
            return

        if step == "add_channel_count":
            cnt = parse_reaction_count(text)
            if not cnt:
                await event.reply("❌ Number 1-100:",
                                  buttons=kb_owner_back())
                return
            state["add_channel_count"] = cnt
            state["step"] = "add_channel_emoji"
            USER_STATES[uid] = state
            await event.reply(
                f"✅ Count: **{cnt}**\n\n➡️ Emoji mode:",
                buttons=[
                    [btn("🎯 Default", data=b"o:qe:default",
                         style="success")],
                    [btn("✏️ Custom", data=b"o:qe:custom",
                         style="primary")],
                    [btn("🔙 Cancel",
                         data=f"o:user:{state.get('target_user')}".encode(),
                         style="danger")],
                ])
            return

        if step == "add_channel_emojis":
            emojis = parse_emoji_input(text)
            if not emojis:
                await event.reply("❌ No valid emojis",
                                  buttons=kb_owner_back())
                return
            state["add_channel_emojis"] = emojis
            state["step"] = "add_channel_confirm"
            USER_STATES[uid] = state
            await event.reply(
                f"✅ Emojis: {len(emojis)}\n\n"
                f"{' '.join(emojis[:20])}\n\n➡️ Confirm?",
                buttons=[
                    [btn("✅ Confirm", data=b"o:confirm_add_ch",
                         style="success")],
                    [btn("❌ Cancel",
                         data=f"o:user:{state.get('target_user')}".encode(),
                         style="danger")],
                ])
            return

        if step == "edit_ch_count":
            cnt = parse_reaction_count(text)
            if not cnt:
                await event.reply("❌ Number 1-100:",
                                  buttons=kb_owner_back())
                return
            channel_id = state.get("channel_id")
            if not channel_id:
                del USER_STATES[uid]
                return
            db_update_channel(channel_id, reaction_count=cnt)
            del USER_STATES[uid]
            await event.reply(
                f"✅ Count updated: **{cnt}**",
                buttons=[[btn("🔙 Back",
                    data=f"o:ch:{channel_id}".encode(),
                    style="primary")]])
            return

        if step == "edit_ch_emojis":
            emojis = parse_emoji_input(text)
            if not emojis:
                await event.reply("❌ No valid emojis",
                                  buttons=kb_owner_back())
                return
            channel_id = state.get("channel_id")
            if not channel_id:
                del USER_STATES[uid]
                return
            db_update_channel(channel_id, emoji_mode="custom",
                              custom_emojis=",".join(emojis[:30]))
            del USER_STATES[uid]
            await event.reply(
                f"✅ Emojis updated\n\n{' '.join(emojis[:20])}",
                buttons=[[btn("🔙 Back",
                    data=f"o:ch:{channel_id}".encode(),
                    style="primary")]])
            return

        if step == "edit_ch_link":
            link = text.strip()
            if not (link.startswith("@") or "t.me/" in link
                    or link.lstrip("-").isdigit()):
                await event.reply("❌ Invalid link",
                                  buttons=kb_owner_back())
                return
            try:
                cid, title, ctype, invite = await resolve_chat_info(link)
            except Exception:
                cid = None
            channel_id = state.get("channel_id")
            if not channel_id:
                del USER_STATES[uid]
                return
            db_update_channel(channel_id, chat_link=link, chat_id=cid,
                              chat_title=title or "Unknown", joined=0)
            del USER_STATES[uid]
            await event.reply(
                f"✅ Link updated\n\n"
                f"📢 {title or 'Unknown'}\n🆔 `{cid or '—'}`\n\n"
                f"⚠️ Re-join required",
                buttons=[
                    [btn("🔄 Re-join",
                         data=f"o:ch_rejoin:{channel_id}".encode(),
                         style="success")],
                    [btn("🔙 Back",
                         data=f"o:ch:{channel_id}".encode(),
                         style="primary")]])
            return

        if step == "edit_user_notes":
            target = state.get("target_user")
            if not target:
                del USER_STATES[uid]
                return
            db_set_user_notes(target, text[:500])
            del USER_STATES[uid]
            await event.reply(
                f"✅ Notes saved",
                buttons=[[btn("🔙 Back",
                    data=f"o:user:{target}".encode(),
                    style="primary")]])
            return

        if step == "edit_owner_uname":
            new_uname = text.lstrip("@").strip()
            if len(new_uname) < 3:
                await event.reply("❌ Too short",
                                  buttons=kb_owner_back())
                return
            cfg_set("owner_username", new_uname)
            del USER_STATES[uid]
            await event.reply(f"✅ Owner: @{new_uname}",
                              buttons=kb_owner_settings())
            return

        if step == "edit_owner_id":
            if not text.strip().isdigit():
                await event.reply("❌ Must be numeric",
                                  buttons=kb_owner_back())
                return
            cfg_set("owner_id", text.strip())
            del USER_STATES[uid]
            await event.reply(f"✅ Owner ID: `{text.strip()}`",
                              buttons=kb_owner_settings())
            return

        if step == "broadcast_msg":
            target_type = state.get("broadcast_type", "users")
            del USER_STATES[uid]
            await perform_broadcast(event, uid, text, target_type)
            return

        if step == "notify_user_id":
            target = parse_user_input_id(text)
            if not target:
                await event.reply("❌ Invalid ID",
                                  buttons=kb_owner_back())
                return
            state["target_user"] = target
            state["step"] = "notify_user_msg"
            USER_STATES[uid] = state
            await event.reply(
                f"📤 **Send message to `{target}`**\n\n➡️ Type message:",
                buttons=kb_owner_back())
            return

        if step == "notify_user_msg":
            target = state.get("target_user")
            if not target:
                del USER_STATES[uid]
                return
            try:
                await bot.send_message(
                    target,
                    f"📢 **Message from Owner**\n"
                    f"{STAR_LINE}\n\n{text}")
                del USER_STATES[uid]
                await event.reply(f"✅ Sent to `{target}`",
                                  buttons=kb_owner_back())
            except Exception as e:
                await event.reply(
                    f"❌ Send failed: {str(e)[:100]}",
                    buttons=kb_owner_back())
            return

        # ── Quick Reaction flows ──
        if step == "quick_react_link":
            link = text.strip()

            if "/" not in link:
                await event.reply(
                    f"❌ **Invalid link**\n\n"
                    f"**Example:**\n`https://t.me/channel/123`",
                    buttons=kb_owner_back())
                return

            valid = False
            if re.search(r"t\.me/(c/)?[\w+]+/\d+", link):
                valid = True
            elif re.search(r"@?[\w]+/\d+", link):
                valid = True

            if not valid:
                await event.reply(
                    f"❌ **Invalid format**\n\n"
                    f"**Supported:**\n"
                    f"• `https://t.me/channel/123`\n"
                    f"• `https://t.me/c/1234/123`\n"
                    f"• `@channel/123`",
                    buttons=kb_owner_back())
                return

            state["qr_link"] = link
            state["step"] = "quick_react_count"
            USER_STATES[uid] = state

            await event.reply(
                f"✅ **Link received**\n\n"
                f"🔗 `{link}`\n\n"
                f"➡️ Kitne reactions? (1-100)",
                buttons=[
                    [btn("5", data=b"o:qr_count:5", style="primary"),
                     btn("10", data=b"o:qr_count:10", style="primary"),
                     btn("20", data=b"o:qr_count:20", style="primary")],
                    [btn("30", data=b"o:qr_count:30", style="primary"),
                     btn("50", data=b"o:qr_count:50", style="primary"),
                     btn("100", data=b"o:qr_count:100", style="primary")],
                    [btn("🔙 Cancel", data=b"o:home", style="danger")],
                ])
            return

        if step == "quick_react_count":
            cnt = parse_reaction_count(text)
            if not cnt:
                await event.reply("❌ Number 1-100:",
                                  buttons=kb_owner_back())
                return
            state["qr_count"] = cnt
            state["step"] = "quick_react_emoji"
            USER_STATES[uid] = state
            await event.reply(
                f"✅ Count: **{cnt}**\n\n➡️ Emoji mode:",
                buttons=[
                    [btn("🎯 Default", data=b"o:qr_emoji:default",
                         style="success")],
                    [btn("✏️ Custom", data=b"o:qr_emoji:custom",
                         style="primary")],
                    [btn("🔙 Cancel", data=b"o:home", style="danger")],
                ])
            return

        if step == "quick_react_emojis":
            emojis = parse_emoji_input(text)
            if not emojis:
                await event.reply("❌ No valid emojis",
                                  buttons=kb_owner_back())
                return
            state["qr_emojis"] = emojis
            state["qr_emoji_mode"] = "custom"
            state["step"] = "quick_react_confirm"
            USER_STATES[uid] = state
            await event.reply(
                f"✅ Emojis: {len(emojis)}\n\n"
                f"{' '.join(emojis[:20])}\n\n➡️ Confirm?",
                buttons=[
                    [btn("✅ SEND NOW", data=b"o:qr_send",
                         style="success")],
                    [btn("❌ Cancel", data=b"o:home",
                         style="danger")],
                ])
            return

        del USER_STATES[uid]
        await event.reply("❌ Session expired",
                          buttons=kb_owner_back())

    except Exception as e:
        D_err(e, "handle_owner_state")
        USER_STATES.pop(uid, None)


# ══════════════════════ USER STATE HANDLER ══════════════════════
async def handle_user_state(event, uid, text):
    state = USER_STATES.get(uid, {})
    step = state.get("step")

    try:
        if step == "user_add_channel_link":
            link = text.strip()
            if not (link.startswith("@") or "t.me/" in link
                    or link.lstrip("-").isdigit()):
                await event.reply(
                    f"❌ **Invalid link**\n\n"
                    f"Send @username, t.me/..., or -100XXXXX",
                    buttons=kb_user_back())
                return

            try:
                cid, title, ctype, invite = await resolve_chat_info(link)
            except Exception as e:
                await event.reply(
                    f"❌ **Resolve failed**\n\n{str(e)[:100]}",
                    buttons=kb_user_back())
                return

            if not cid:
                await event.reply(
                    f"❌ **Chat not found**\n\nTry @username or numeric ID",
                    buttons=kb_user_back())
                return

            if db_channel_already_exists(uid, cid):
                del USER_STATES[uid]
                await event.reply(
                    f"⚠️ **Already added**\n\n📢 {title}\n🆔 `{cid}`",
                    buttons=kb_user_channels(uid))
                return

            state["add_channel_id"] = cid
            state["add_channel_link"] = link
            state["add_channel_title"] = title
            state["step"] = "user_add_channel_count"
            USER_STATES[uid] = state

            await event.reply(
                f"✅ **Chat found!**\n\n"
                f"📢 {title}\n🆔 `{cid}`\n"
                f"📁 {ctype or 'unknown'}\n\n"
                f"➡️ Reactions per post (1-100):",
                buttons=[
                    [btn("5", data=b"u:qc:5", style="primary"),
                     btn("10", data=b"u:qc:10", style="primary"),
                     btn("20", data=b"u:qc:20", style="primary")],
                    [btn("30", data=b"u:qc:30", style="primary"),
                     btn("50", data=b"u:qc:50", style="primary"),
                     btn("100", data=b"u:qc:100", style="primary")],
                    [btn("🔙 Cancel", data=b"u:home", style="danger")],
                ])
            return

        if step == "user_add_channel_count":
            cnt = parse_reaction_count(text)
            if not cnt:
                await event.reply("❌ Number 1-100:",
                                  buttons=kb_user_back())
                return
            state["add_channel_count"] = cnt
            state["step"] = "user_add_channel_emoji"
            USER_STATES[uid] = state
            await event.reply(
                f"✅ Count: **{cnt}**\n\n➡️ Emoji mode:",
                buttons=[
                    [btn("🎯 Default", data=b"u:qe:default",
                         style="success")],
                    [btn("✏️ Custom", data=b"u:qe:custom",
                         style="primary")],
                    [btn("🔙 Cancel", data=b"u:home", style="danger")],
                ])
            return

        if step == "user_add_channel_emojis":
            emojis = parse_emoji_input(text)
            if not emojis:
                await event.reply("❌ No valid emojis",
                                  buttons=kb_user_back())
                return
            state["add_channel_emojis"] = emojis
            state["step"] = "user_add_channel_confirm"
            USER_STATES[uid] = state
            await event.reply(
                f"✅ Emojis: {len(emojis)}\n\n"
                f"{' '.join(emojis[:20])}\n\n➡️ Confirm?",
                buttons=[
                    [btn("✅ Confirm", data=b"u:confirm_add_ch",
                         style="success")],
                    [btn("❌ Cancel", data=b"u:home", style="danger")],
                ])
            return

        if step == "user_edit_ch_count":
            cnt = parse_reaction_count(text)
            if not cnt:
                await event.reply("❌ Number 1-100:",
                                  buttons=kb_user_back())
                return
            cid = state.get("channel_id")
            if not cid:
                del USER_STATES[uid]
                return
            db_update_channel(cid, reaction_count=cnt)
            del USER_STATES[uid]
            await event.reply(
                f"✅ Count updated: **{cnt}**",
                buttons=[[btn("🔙 Back",
                    data=f"u:ch:{cid}".encode(), style="primary")]])
            return

        if step == "user_edit_ch_emojis":
            emojis = parse_emoji_input(text)
            if not emojis:
                await event.reply("❌ No valid emojis",
                                  buttons=kb_user_back())
                return
            cid = state.get("channel_id")
            if not cid:
                del USER_STATES[uid]
                return
            db_update_channel(cid, emoji_mode="custom",
                              custom_emojis=",".join(emojis[:30]))
            del USER_STATES[uid]
            await event.reply(
                f"✅ Emojis updated",
                buttons=[[btn("🔙 Back",
                    data=f"u:ch:{cid}".encode(), style="primary")]])
            return

        if step == "user_edit_ch_link":
            link = text.strip()
            if not (link.startswith("@") or "t.me/" in link
                    or link.lstrip("-").isdigit()):
                await event.reply("❌ Invalid link",
                                  buttons=kb_user_back())
                return
            try:
                cid, title, ctype, invite = await resolve_chat_info(link)
            except Exception:
                cid = None
            ch_id = state.get("channel_id")
            if not ch_id:
                del USER_STATES[uid]
                return
            db_update_channel(ch_id, chat_link=link, chat_id=cid,
                              chat_title=title or "Unknown", joined=0)
            del USER_STATES[uid]
            await event.reply(
                f"✅ Link updated\n\n"
                f"📢 {title or 'Unknown'}\n🆔 `{cid or '—'}`\n\n"
                f"⚠️ Re-join required",
                buttons=[
                    [btn("🔄 Re-join",
                         data=f"u:ch_rejoin:{ch_id}".encode(),
                         style="success")],
                    [btn("🔙 Back",
                         data=f"u:ch:{ch_id}".encode(), style="primary")]])
            return

        del USER_STATES[uid]
        await event.reply("❌ Session expired",
                          buttons=kb_user_back())

    except Exception as e:
        D_err(e, "handle_user_state")
        USER_STATES.pop(uid, None)


# ══════════════════════ BROADCAST ══════════════════════
async def perform_broadcast(event, uid, message, target_type):
    try:
        if target_type == "active":
            users = db_list_users(limit=1000)
            user_ids = [u[0] for u in users if u[3] == 1]
        else:
            users = db_list_users(limit=1000)
            user_ids = [u[0] for u in users]

        total = len(user_ids)
        if total == 0:
            await event.reply("❌ No users to broadcast",
                              buttons=kb_owner_home())
            return

        status_msg = await event.reply(
            f"📢 **Broadcasting...**\n\n"
            f"👥 Target: **{total}** users\n⏳ Starting...")

        ok = fail = 0
        for i, u_id in enumerate(user_ids, 1):
            try:
                await bot.send_message(
                    u_id,
                    f"📢 **Message from Owner**\n"
                    f"{STAR_LINE}\n\n{message}")
                ok += 1
            except Exception:
                fail += 1

            if i % 5 == 0:
                try:
                    await status_msg.edit(
                        f"📢 **Broadcasting...**\n\n"
                        f"✅ Sent: {ok}\n❌ Failed: {fail}\n"
                        f"📊 Progress: {i}/{total}")
                except Exception:
                    pass

            await asyncio.sleep(0.3)

        await status_msg.edit(
            f"✅ **Broadcast complete**\n\n"
            f"📊 Total: **{total}**\n"
            f"✅ Sent: **{ok}**\n❌ Failed: **{fail}**")

    except Exception as e:
        D_err(e, "perform_broadcast")


# ══════════════════════ CALLBACK HANDLER ══════════════════════
@bot.on(events.CallbackQuery)
async def on_cb(event):
    try:
        data = event.data.decode()
        uid = event.sender_id
    except Exception:
        return

    try:
        if data.startswith("o:"):
            if not OWNER_IS(uid):
                await safe_answer(event, "❌ Owner only", alert=True)
                return
            await handle_owner_cb(event, uid, data)

        elif data.startswith("u:"):
            if not db_is_whitelisted(uid) and not OWNER_IS(uid):
                await safe_answer(event, "🔒 Not whitelisted", alert=True)
                return
            await handle_user_cb(event, uid, data)

        elif data.startswith("d:"):
            await handle_denied_cb(event, uid, data)

        elif data == "home":
            if OWNER_IS(uid):
                await safe_edit(event,
                    get_owner_dashboard_message(),
                    buttons=kb_owner_home())
            elif db_is_whitelisted(uid):
                await safe_edit(event,
                    get_welcome_message(uid),
                    buttons=kb_user_home(uid))
            else:
                await safe_edit(event,
                    get_denied_message(uid),
                    buttons=kb_denied(uid))
        else:
            await safe_answer(event, "⚠️ Unknown", alert=True)

    except Exception as e:
        D_err(e, "on_cb")
        try:
            await safe_answer(event, "❌ Error", alert=True)
        except Exception:
            pass


# ══════════════════════ USER CALLBACKS ══════════════════════
async def handle_user_cb(event, uid, data):
    try:
        if data == "u:home":
            await safe_answer(event, "🏠")
            await safe_edit(event, get_welcome_message(uid),
                buttons=kb_user_home(uid))
            return

        if data == "u:settings":
            await safe_answer(event, "📊")
            await safe_edit(event, get_settings_message(uid),
                buttons=kb_user_settings(uid))
            return

        if data == "u:channels":
            await safe_answer(event, "📢")
            await safe_edit(event, build_user_channels_text(uid),
                buttons=kb_user_channels(uid))
            return

        if data == "u:channels_more":
            await safe_answer(event, "📢 +more", alert=True)
            return

        if data == "u:add_ch":
            USER_STATES[uid] = {"step": "user_add_channel_link"}
            await safe_answer(event, "➕")
            await safe_edit(event,
                f"{STAR_LINE}\n➕ **ADD CHANNEL**\n{STAR_LINE}\n\n"
                f"➡️ Channel/Group link bhejo:\n\n"
                f"**Examples:**\n"
                f"• `@channel_name`\n"
                f"• `https://t.me/channel_name`\n"
                f"• `https://t.me/+invite_hash`\n"
                f"• `-1001234567890`",
                buttons=[[btn("🔙 Cancel", data=b"u:home",
                              style="danger")]])
            return

        if data.startswith("u:qc:"):
            try:
                cnt = int(data.split(":")[2])
            except Exception:
                return
            state = USER_STATES.get(uid, {})
            if state.get("step") != "user_add_channel_count":
                await safe_answer(event, "❌ Session expired", alert=True)
                return
            state["add_channel_count"] = cnt
            state["step"] = "user_add_channel_emoji"
            USER_STATES[uid] = state
            await safe_answer(event, f"✅ {cnt}")
            await safe_edit(event,
                f"✅ Count: **{cnt}**\n\n➡️ Emoji mode:",
                buttons=[
                    [btn("🎯 Default", data=b"u:qe:default",
                         style="success")],
                    [btn("✏️ Custom", data=b"u:qe:custom",
                         style="primary")],
                    [btn("🔙 Cancel", data=b"u:home", style="danger")],
                ])
            return

        if data == "u:qe:default":
            state = USER_STATES.get(uid, {})
            if state.get("step") != "user_add_channel_emoji":
                await safe_answer(event, "❌", alert=True)
                return
            state["add_channel_emojis"] = None
            state["add_channel_emoji_mode"] = "default"
            state["step"] = "user_add_channel_confirm"
            USER_STATES[uid] = state
            await safe_answer(event, "🎯")
            await safe_edit(event,
                f"✅ Emoji: **Default**\n\n➡️ Confirm?",
                buttons=[
                    [btn("✅ Confirm", data=b"u:confirm_add_ch",
                         style="success")],
                    [btn("❌ Cancel", data=b"u:home", style="danger")],
                ])
            return

        if data == "u:qe:custom":
            state = USER_STATES.get(uid, {})
            if state.get("step") != "user_add_channel_emoji":
                await safe_answer(event, "❌", alert=True)
                return
            state["step"] = "user_add_channel_emojis"
            USER_STATES[uid] = state
            await safe_answer(event, "✏️")
            await safe_edit(event,
                f"✏️ **Custom Emojis**\n\n"
                f"➡️ Send emojis (space/comma):\n"
                f"_Example: ❤️ 🔥 😍_",
                buttons=[[btn("🔙 Cancel", data=b"u:home",
                              style="danger")]])
            return

        if data == "u:confirm_add_ch":
            state = USER_STATES.get(uid, {})
            if state.get("step") != "user_add_channel_confirm":
                await safe_answer(event, "❌ Session expired", alert=True)
                return

            link = state.get("add_channel_link")
            cid = state.get("add_channel_id")
            title = state.get("add_channel_title", "Unknown")
            cnt = state.get("add_channel_count", 5)
            emode = state.get("add_channel_emoji_mode", "default")
            emojis = state.get("add_channel_emojis")

            try:
                db_add_channel(user_id=uid, chat_link=link,
                               chat_title=title, chat_id=cid,
                               reaction_count=cnt, emoji_mode=emode,
                               custom_emojis=emojis)
                del USER_STATES[uid]
                await safe_answer(event, "✅ Added!", alert=True)
                await safe_edit(event,
                    f"✅ **Channel Added!**\n\n"
                    f"📢 {title}\n🆔 `{cid}`\n"
                    f"🎯 {cnt}/post\n✏️ {emode}\n\n"
                    f"➡️ Auto-join hoga pehle check pe",
                    buttons=[
                        [btn("📢 My Channels",
                             data=b"u:channels", style="success")],
                        [btn("🏠 Home", data=b"u:home", style="primary")]
                    ])
            except Exception as e:
                await safe_answer(event, "❌ Failed", alert=True)
                await safe_edit(event,
                    f"❌ Add failed\n\n{str(e)[:100]}",
                    buttons=kb_user_back())
            return

        if data.startswith("u:ch:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            ch = db_get_channel(cid)
            if not ch or ch[1] != uid:
                await safe_answer(event, "❌ Not yours", alert=True)
                return
            await safe_answer(event, "📢")
            await safe_edit(event,
                get_channel_info_message(uid, cid),
                buttons=kb_user_channel_detail(uid, cid))
            return

        if data.startswith("u:ch_hist:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            rows = db_reactions_by_channel(cid, 15)
            txt = f"{STAR_LINE}\n📜 **Channel History**\n{STAR_LINE}\n\n"
            if not rows:
                txt += "_No history yet_"
            else:
                for i, r in enumerate(rows, 1):
                    chat, post, sent, status, dt = r
                    icon = "✅" if status == "ok" else "❌"
                    txt += (f"{icon} **{i}.** #{post} — {sent}\n"
                            f"   🕒 {dt[:16]}\n\n")
            await safe_answer(event, "📜")
            await safe_edit(event, txt,
                buttons=kb_user_channel_history(uid, cid))
            return

        if data.startswith("u:ch_count:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            USER_STATES[uid] = {"step": "user_edit_ch_count",
                                "channel_id": cid}
            await safe_answer(event, "✏️")
            await safe_edit(event,
                f"✏️ **Edit Count**\n\n➡️ New count (1-100):",
                buttons=[[btn("🔙 Cancel",
                    data=f"u:ch:{cid}".encode(), style="danger")]])
            return

        if data.startswith("u:ch_emoji:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            USER_STATES[uid] = {"step": "user_edit_ch_emojis",
                                "channel_id": cid}
            await safe_answer(event, "✏️")
            await safe_edit(event,
                f"✏️ **Edit Emojis**\n\n➡️ Send emojis (space/comma):",
                buttons=[[btn("🔙 Cancel",
                    data=f"u:ch:{cid}".encode(), style="danger")]])
            return

        if data.startswith("u:ch_link:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            USER_STATES[uid] = {"step": "user_edit_ch_link",
                                "channel_id": cid}
            await safe_answer(event, "🔗")
            await safe_edit(event,
                f"🔗 **Edit Link**\n\n➡️ Send new chat link:",
                buttons=[[btn("🔙 Cancel",
                    data=f"u:ch:{cid}".encode(), style="danger")]])
            return

        if data.startswith("u:ch_rejoin:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            ch = db_get_channel(cid)
            if not ch or ch[1] != uid:
                await safe_answer(event, "❌ Not yours", alert=True)
                return
            await safe_answer(event, "🔄 Joining...", alert=True)
            await safe_edit(event, f"🔄 **Joining {ch[4] or ch[3]}...**")
            try:
                j, a, f = await join_chat_with_sessions(
                    ch[3], ch[2], max_sessions=20)
                if j + a > 0:
                    db_update_channel(cid, joined=1)
                    await safe_edit(event,
                        f"✅ **Joined!**\n\n🆕 {j} | ⚡ {a} | ❌ {f}",
                        buttons=kb_user_channel_detail(uid, cid))
                else:
                    await safe_edit(event,
                        f"❌ **All failed** (f={f})",
                        buttons=kb_user_channel_detail(uid, cid))
            except Exception as e:
                await safe_edit(event,
                    f"❌ Error: {str(e)[:100]}",
                    buttons=kb_user_channel_detail(uid, cid))
            return

        if data.startswith("u:ch_del:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            await safe_answer(event, "⚠️")
            await safe_edit(event,
                f"⚠️ **Confirm Remove**\n\n🆔 `{cid}`",
                buttons=[
                    [btn("🗑️ YES",
                         data=f"u:ch_del_confirm:{cid}".encode(),
                         style="danger")],
                    [btn("❌ Cancel",
                         data=f"u:ch:{cid}".encode(), style="primary")]])
            return

        if data.startswith("u:ch_del_confirm:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            db_delete_channel(cid, uid)
            await safe_answer(event, "🗑️ Removed", alert=True)
            await safe_edit(event, "📢 Removed",
                buttons=kb_user_channels(uid))
            return

        if data == "u:history":
            await safe_answer(event, "📜")
            await safe_edit(event, build_user_history_text(uid),
                buttons=kb_user_history(uid))
            return

        if data == "u:language":
            await safe_answer(event, "🌍")
            await safe_edit(event,
                f"🌍 **Choose Language**\n{DIV()}\n\n"
                f"Current: **{LANGUAGES.get(get_user_lang(uid), 'English')}**",
                buttons=kb_user_language(uid))
            return

        if data.startswith("u:lang:"):
            code = data.split(":")[2]
            if code not in LANGUAGES:
                return
            db_set_user_lang(uid, code)
            await safe_answer(event, f"✅ {LANGUAGES[code]}", alert=True)
            await safe_edit(event,
                f"✅ Language: **{LANGUAGES[code]}**!",
                buttons=kb_user_home(uid))
            return

        if data == "u:toggle_notify":
            new_state = db_toggle_user_notify(uid)
            await safe_answer(event,
                f"{'✅ ON' if new_state else '❌ OFF'}", alert=True)
            await safe_edit(event, get_welcome_message(uid),
                buttons=kb_user_home(uid))
            return

        if data == "u:notif":
            notifs = db_get_notifications(uid, 10)
            txt = f"{STAR_LINE}\n🔔 **Notifications**\n{STAR_LINE}\n\n"
            if not notifs:
                txt += "_None_"
            for n in notifs:
                icon = "📭" if n[2] else "📬"
                txt += f"{icon} {n[1][:100]}\n"
                txt += f"   🕒 {n[3][:16]}\n\n"
            await safe_answer(event, "🔔")
            await safe_edit(event, txt,
                buttons=kb_user_notifications(uid))
            return

        if data == "u:notif_read":
            db_mark_notifications_read(uid)
            await safe_answer(event, "✅", alert=True)
            await safe_edit(event, "✅ All marked read",
                buttons=kb_user_notifications(uid))
            return

        if data == "u:help":
            await safe_answer(event, "❓")
            await safe_edit(event, get_help_text(uid),
                buttons=kb_user_help(uid))
            return

        if data == "u:info_nochannels":
            await safe_answer(event,
                "ℹ️ Contact owner to add channels", alert=True)
            return

        await safe_answer(event, "⚠️ Unknown", alert=True)

    except Exception as e:
        D_err(e, "handle_user_cb")


# ══════════════════════ DENIED CALLBACKS ══════════════════════
async def handle_denied_cb(event, uid, data):
    try:
        if data == "d:about":
            await safe_answer(event, "ℹ️")
            await safe_edit(event,
                f"{STAR_LINE}\nℹ️ **About This Bot**\n{STAR_LINE}\n\n"
                f"🤖 **Ghost Auto-Reactor**\n\n"
                f"Ye bot **private/paid service** hai jo:\n\n"
                f"✅ Whitelisted users ke channels monitor karta hai\n"
                f"✅ New posts pe **automatically reactions** bhejta hai\n"
                f"✅ **Real accounts** use karta hai\n"
                f"✅ **24/7** chalta hai\n"
                f"✅ **Multi-channel** support\n\n"
                f"{DIV()}\n"
                f"💎 **Access lene ke liye:**\n"
                f"👑 @{get_owner_username()}\n"
                f"{DIV()}",
                buttons=kb_denied(uid))
            return
        await safe_answer(event, "⚠️", alert=True)
    except Exception as e:
        D_err(e, "handle_denied_cb")


# ══════════════════════ OWNER CALLBACKS ══════════════════════
async def handle_owner_cb(event, uid, data):
    try:
        if data == "o:home":
            await safe_answer(event, "👑")
            await safe_edit(event, get_owner_dashboard_message(),
                buttons=kb_owner_home())
            return

        # ═════ USERS ═════
        if data == "o:users":
            await safe_answer(event, "👥")
            await safe_edit(event, build_owner_users_text(0),
                buttons=kb_owner_users(0))
            return

        if data.startswith("o:users:"):
            try:
                page = int(data.split(":")[2])
            except Exception:
                page = 0
            await safe_answer(event, f"📄 {page + 1}")
            await safe_edit(event, build_owner_users_text(page),
                buttons=kb_owner_users(page))
            return

        if data == "o:add_user":
            USER_STATES[uid] = {"step": "add_user_id"}
            await safe_answer(event, "➕")
            await safe_edit(event,
                f"{STAR_LINE}\n➕ **ADD USER**\n{STAR_LINE}\n\n"
                f"➡️ User ID ya @username bhejo:\n\n"
                f"**Examples:**\n"
                f"• `123456789`\n"
                f"• `@username`",
                buttons=[[btn("🔙 Cancel", data=b"o:users",
                              style="danger")]])
            return

        if data.startswith("o:user:"):
            try:
                tuid = int(data.split(":")[2])
            except Exception:
                return
            u = db_get_user(tuid)
            if not u:
                await safe_answer(event, "❌ Not found", alert=True)
                return
            await safe_answer(event, "👤")
            await safe_edit(event, get_owner_user_view(tuid),
                buttons=kb_owner_user_detail(tuid))
            return

        if data.startswith("o:user_toggle:"):
            try:
                tuid = int(data.split(":")[2])
            except Exception:
                return
            new_state = db_toggle_user_active(tuid)
            await safe_answer(event,
                f"{'🟢' if new_state else '🔴'}", alert=True)
            await safe_edit(event, get_owner_user_view(tuid),
                buttons=kb_owner_user_detail(tuid))
            return

        if data.startswith("o:user_notify:"):
            try:
                tuid = int(data.split(":")[2])
            except Exception:
                return
            new_state = db_toggle_user_notify(tuid)
            await safe_answer(event,
                f"{'🔔' if new_state else '🔕'}", alert=True)
            await safe_edit(event, get_owner_user_view(tuid),
                buttons=kb_owner_user_detail(tuid))
            return

        if data.startswith("o:user_channels:"):
            parts = data.split(":")
            try:
                tuid = int(parts[2])
                page = int(parts[3]) if len(parts) > 3 else 0
            except Exception:
                return
            await safe_answer(event, "📢")
            await safe_edit(event,
                f"📢 **User `{tuid}` Channels**",
                buttons=kb_owner_user_channels(tuid, page))
            return

        if data.startswith("o:user_add_ch:"):
            try:
                tuid = int(data.split(":")[2])
            except Exception:
                return
            USER_STATES[uid] = {"step": "add_channel_link",
                                "target_user": tuid}
            await safe_answer(event, "➕")
            await safe_edit(event,
                f"{STAR_LINE}\n➕ **ADD CHANNEL**\n{STAR_LINE}\n\n"
                f"👤 User: `{tuid}`\n\n"
                f"➡️ Channel/Group link bhejo:",
                buttons=[[btn("🔙 Cancel",
                    data=f"o:user:{tuid}".encode(), style="danger")]])
            return

        if data.startswith("o:user_notes:"):
            try:
                tuid = int(data.split(":")[2])
            except Exception:
                return
            u = db_get_user(tuid)
            notes = u[9] if u and len(u) > 9 else None
            USER_STATES[uid] = {"step": "edit_user_notes",
                                "target_user": tuid}
            await safe_answer(event, "📝")
            await safe_edit(event,
                f"📝 **Edit Notes**\n\n"
                f"👤 `{tuid}`\n\n"
                f"Current: {notes or '_No notes_'}\n\n"
                f"➡️ New notes:",
                buttons=[[btn("🔙 Cancel",
                    data=f"o:user:{tuid}".encode(), style="danger")]])
            return

        if data.startswith("o:user_del:"):
            try:
                tuid = int(data.split(":")[2])
            except Exception:
                return
            await safe_answer(event, "⚠️")
            await safe_edit(event,
                f"⚠️ **CONFIRM REMOVE**\n\n"
                f"👤 User: `{tuid}`\n\n"
                f"⚠️ Ye action reversible nahi!",
                buttons=kb_confirm_delete_user(tuid))
            return

        if data.startswith("o:user_del_confirm:"):
            try:
                tuid = int(data.split(":")[2])
            except Exception:
                return
            db_remove_user(tuid)
            await safe_answer(event, "🗑️", alert=True)
            await safe_edit(event, build_owner_users_text(0),
                buttons=kb_owner_users(0))
            return

        # ═════ CHANNELS ═════
        if data == "o:channels":
            await safe_answer(event, "📢")
            await safe_edit(event,
                f"📢 **All Channels ({db_count_channels()})**",
                buttons=kb_owner_channels(0))
            return

        if data.startswith("o:channels:"):
            try:
                page = int(data.split(":")[2])
            except Exception:
                page = 0
            await safe_answer(event, f"📄 {page + 1}")
            await safe_edit(event,
                f"📢 **All Channels ({db_count_channels()})**",
                buttons=kb_owner_channels(page))
            return

        if data.startswith("o:ch:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            await safe_answer(event, "📢")
            await safe_edit(event, build_owner_channel_detail(cid),
                buttons=kb_owner_channel_detail(cid))
            return

        if data.startswith("o:ch_toggle:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            new_state = db_toggle_channel(cid)
            await safe_answer(event,
                f"{'🟢' if new_state else '🔴'}", alert=True)
            await safe_edit(event, build_owner_channel_detail(cid),
                buttons=kb_owner_channel_detail(cid))
            return

        if data.startswith("o:ch_count:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            USER_STATES[uid] = {"step": "edit_ch_count", "channel_id": cid}
            await safe_answer(event, "✏️")
            await safe_edit(event,
                f"✏️ **Edit Count**\n\n➡️ New count (1-100):",
                buttons=[[btn("🔙 Cancel",
                    data=f"o:ch:{cid}".encode(), style="danger")]])
            return

        if data.startswith("o:qc:"):
            try:
                cnt = int(data.split(":")[2])
            except Exception:
                return
            state = USER_STATES.get(uid, {})
            if state.get("step") != "add_channel_count":
                await safe_answer(event, "❌ Session expired", alert=True)
                return
            state["add_channel_count"] = cnt
            state["step"] = "add_channel_emoji"
            USER_STATES[uid] = state
            await safe_answer(event, f"✅ {cnt}")
            await safe_edit(event,
                f"✅ Count: **{cnt}**\n\n➡️ Emoji mode:",
                buttons=[
                    [btn("🎯 Default", data=b"o:qe:default",
                         style="success")],
                    [btn("✏️ Custom", data=b"o:qe:custom",
                         style="primary")],
                    [btn("🔙 Cancel",
                         data=f"o:user:{state.get('target_user')}".encode(),
                         style="danger")],
                ])
            return

        if data.startswith("o:ch_emoji:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            USER_STATES[uid] = {"step": "edit_ch_emojis", "channel_id": cid}
            await safe_answer(event, "✏️")
            await safe_edit(event,
                f"✏️ **Edit Emojis**\n\n➡️ Send emojis (space/comma):",
                buttons=[[btn("🔙 Cancel",
                    data=f"o:ch:{cid}".encode(), style="danger")]])
            return

        if data.startswith("o:ch_link:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            USER_STATES[uid] = {"step": "edit_ch_link", "channel_id": cid}
            await safe_answer(event, "🔗")
            await safe_edit(event,
                f"🔗 **Edit Link**\n\n➡️ Send new chat link:",
                buttons=[[btn("🔙 Cancel",
                    data=f"o:ch:{cid}".encode(), style="danger")]])
            return

        if data == "o:qe:default":
            state = USER_STATES.get(uid, {})
            if state.get("step") != "add_channel_emoji":
                await safe_answer(event, "❌", alert=True)
                return
            state["add_channel_emojis"] = None
            state["add_channel_emoji_mode"] = "default"
            state["step"] = "add_channel_confirm"
            USER_STATES[uid] = state
            await safe_answer(event, "🎯")
            await safe_edit(event,
                f"✅ Emoji: **Default**\n\n➡️ Confirm?",
                buttons=[
                    [btn("✅ Confirm", data=b"o:confirm_add_ch",
                         style="success")],
                    [btn("❌ Cancel",
                         data=f"o:user:{state.get('target_user')}".encode(),
                         style="danger")],
                ])
            return

        if data == "o:qe:custom":
            state = USER_STATES.get(uid, {})
            if state.get("step") != "add_channel_emoji":
                await safe_answer(event, "❌", alert=True)
                return
            state["step"] = "add_channel_emojis"
            USER_STATES[uid] = state
            await safe_answer(event, "✏️")
            await safe_edit(event,
                f"✏️ **Custom Emojis**\n\n"
                f"➡️ Send emojis (space/comma):",
                buttons=[[btn("🔙 Cancel",
                    data=f"o:user:{state.get('target_user')}".encode(),
                    style="danger")]])
            return

        if data == "o:confirm_add_ch":
            state = USER_STATES.get(uid, {})
            if state.get("step") != "add_channel_confirm":
                await safe_answer(event, "❌ Session expired", alert=True)
                return

            tuid = state.get("target_user")
            link = state.get("add_channel_link")
            cid = state.get("add_channel_id")
            title = state.get("add_channel_title", "Unknown")
            cnt = state.get("add_channel_count", 5)
            emode = state.get("add_channel_emoji_mode", "default")
            emojis = state.get("add_channel_emojis")

            try:
                db_add_channel(user_id=tuid, chat_link=link,
                               chat_title=title, chat_id=cid,
                               reaction_count=cnt, emoji_mode=emode,
                               custom_emojis=emojis)
                del USER_STATES[uid]
                await safe_answer(event, "✅ Added!", alert=True)
                await safe_edit(event,
                    f"✅ **Channel Added!**\n\n"
                    f"📢 {title}\n🆔 `{cid}`\n"
                    f"🎯 {cnt}/post\n✏️ {emode}",
                    buttons=[
                        [btn("📢 User Channels",
                             data=f"o:user_channels:{tuid}".encode(),
                             style="success")],
                        [btn("👤 User",
                             data=f"o:user:{tuid}".encode(),
                             style="primary")],
                        [btn("🔙 Users", data=b"o:users",
                             style="primary")]
                    ])
            except Exception as e:
                await safe_answer(event, "❌ Failed", alert=True)
                await safe_edit(event,
                    f"❌ Add failed\n\n{str(e)[:100]}",
                    buttons=kb_owner_back())
            return

        if data.startswith("o:ch_rejoin:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            ch = db_get_channel(cid)
            if not ch:
                await safe_answer(event, "❌", alert=True)
                return
            await safe_answer(event, "🔄 Joining...", alert=True)
            await safe_edit(event, f"🔄 **Joining {ch[4] or ch[3]}...**")
            try:
                j, a, f = await join_chat_with_sessions(
                    ch[3], ch[2], max_sessions=20)
                if j + a > 0:
                    db_update_channel(cid, joined=1)
                    await safe_edit(event,
                        f"✅ **Joined!**\n\n🆕 {j} | ⚡ {a} | ❌ {f}",
                        buttons=kb_owner_channel_detail(cid))
                else:
                    await safe_edit(event,
                        f"❌ All failed (f={f})",
                        buttons=kb_owner_channel_detail(cid))
            except Exception as e:
                await safe_edit(event,
                    f"❌ Error: {str(e)[:100]}",
                    buttons=kb_owner_channel_detail(cid))
            return

        if data.startswith("o:ch_hist:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            rows = db_reactions_by_channel(cid, 15)
            txt = f"{STAR_LINE}\n📜 **History**\n{STAR_LINE}\n\n"
            if not rows:
                txt += "_No history yet_"
            else:
                for i, r in enumerate(rows, 1):
                    chat, post, sent, status, dt = r
                    icon = "✅" if status == "ok" else "❌"
                    txt += (f"{icon} **{i}.** #{post} — {sent}\n"
                            f"   🕒 {dt[:16]}\n\n")
            await safe_answer(event, "📜")
            await safe_edit(event, txt,
                buttons=kb_owner_channel_history(cid))
            return

        if data.startswith("o:ch_test:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            ch = db_get_channel(cid)
            if not ch:
                await safe_answer(event, "❌", alert=True)
                return
            await safe_answer(event, "🧪 Testing...", alert=True)
            await safe_edit(event, f"🧪 **Testing...**")
            try:
                await process_channel_check(ch)
                await safe_edit(event, "✅ **Test complete**",
                    buttons=kb_owner_channel_detail(cid))
            except Exception as e:
                await safe_edit(event,
                    f"❌ Test failed\n\n{str(e)[:100]}",
                    buttons=kb_owner_channel_detail(cid))
            return

        if data.startswith("o:ch_del:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            await safe_answer(event, "⚠️")
            await safe_edit(event,
                f"⚠️ **Confirm Remove**\n\n🆔 `{cid}`",
                buttons=kb_confirm_delete_channel(cid))
            return

        if data.startswith("o:ch_del_confirm:"):
            try:
                cid = int(data.split(":")[2])
            except Exception:
                return
            db_delete_channel(cid)
            await safe_answer(event, "🗑️", alert=True)
            await safe_edit(event,
                f"📢 **All Channels ({db_count_channels()})**",
                buttons=kb_owner_channels(0))
            return

        # ═════ SESSIONS ═════
        if data == "o:sessions":
            await safe_answer(event, "🔐")
            await safe_edit(event, _build_sessions_overview(),
                buttons=kb_owner_sessions())
            return

        if data == "o:sessions_list":
            await safe_answer(event, "📋")
            await safe_edit(event, "📋 **Sessions List**",
                buttons=kb_owner_sessions_list(0))
            return

        if data.startswith("o:sessions_list:"):
            try:
                page = int(data.split(":")[2])
            except Exception:
                page = 0
            await safe_answer(event, f"📄 {page + 1}")
            await safe_edit(event, "📋 **Sessions List**",
                buttons=kb_owner_sessions_list(page))
            return

        if data.startswith("o:sess_info:"):
            sf = data[len("o:sess_info:"):]
            await safe_answer(event, "📁")
            await safe_edit(event, _build_session_detail(sf),
                buttons=kb_owner_session_info(sf))
            return

        if data.startswith("o:sess_test:"):
            sf = data[len("o:sess_test:"):]
            await safe_answer(event, "🧪", alert=True)
            try:
                client = await get_real_client(sf)
                if client:
                    me = await asyncio.wait_for(client.get_me(),
                                                timeout=10)
                    await safe_edit(event,
                        f"✅ **Session OK**\n\n"
                        f"📁 `{sf}`\n"
                        f"👤 {getattr(me, 'first_name', '?')}\n"
                        f"📛 @{getattr(me, 'username', '—')}\n"
                        f"🆔 `{me.id}`",
                        buttons=kb_owner_session_info(sf))
                else:
                    await safe_edit(event,
                        f"❌ **Failed**\n\n`{sf}`",
                        buttons=kb_owner_session_info(sf))
            except Exception as e:
                await safe_edit(event,
                    f"❌ **Error**\n\n`{sf}`\n{str(e)[:100]}",
                    buttons=kb_owner_session_info(sf))
            return

        if data.startswith("o:sess_reset:"):
            sf = data[len("o:sess_reset:"):]
            REAL_FLOOD_UNTIL.pop(sf, None)
            await safe_answer(event, "🔄", alert=True)
            await safe_edit(event, "✅ Flood reset",
                buttons=kb_owner_session_info(sf))
            return

        if data.startswith("o:sess_disc:"):
            sf = data[len("o:sess_disc:"):]
            client = REAL_CLIENTS.pop(sf, None)
            if client:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            await safe_answer(event, "🔌", alert=True)
            await safe_edit(event, "✅ Disconnected",
                buttons=kb_owner_session_info(sf))
            return

        if data == "o:sessions_clear":
            n = len(REAL_FLOOD_UNTIL)
            REAL_FLOOD_UNTIL.clear()
            await safe_answer(event, f"🌊 {n}", alert=True)
            await safe_edit(event, _build_sessions_overview(),
                buttons=kb_owner_sessions())
            return

        if data == "o:sessions_disconnect":
            n = len(REAL_CLIENTS)
            await close_all_real_clients()
            await safe_answer(event, f"🔌 {n}", alert=True)
            await safe_edit(event, _build_sessions_overview(),
                buttons=kb_owner_sessions())
            return

        if data == "o:sessions_test":
            await safe_answer(event, "🧪 Testing...", alert=True)
            sessions = discover_sessions()
            if not sessions:
                await safe_edit(event, "❌ No sessions",
                                buttons=kb_owner_sessions())
                return
            msg = await event.edit("🧪 Testing sessions...")
            results = []
            for i, sf in enumerate(sessions[:20], 1):
                try:
                    client = await get_real_client(sf)
                    if client:
                        me = await asyncio.wait_for(
                            client.get_me(), timeout=8)
                        results.append(
                            f"✅ `{sf[:20]}` — "
                            f"{getattr(me, 'first_name', '?')}")
                    else:
                        results.append(f"❌ `{sf[:20]}` — fail")
                except Exception:
                    results.append(f"❌ `{sf[:20]}` — error")
                if i % 3 == 0:
                    try:
                        await msg.edit(
                            f"🧪 Testing... {i}/{len(sessions[:20])}\n\n"
                            + "\n".join(results[-5:]))
                    except Exception:
                        pass
            final = f"🧪 **Test Results**\n{DIV()}\n\n" + "\n".join(results)
            try:
                await msg.edit(final[:4000],
                               buttons=kb_owner_sessions())
            except Exception:
                pass
            return

        if data == "o:sessions_reload":
            await safe_answer(event, "📥", alert=True)
            await safe_edit(event, _build_sessions_overview(),
                buttons=kb_owner_sessions())
            return

        if data == "o:sessions_cleanup":
            await safe_answer(event, "🧹 Cleaning...", alert=True)
            await safe_edit(event,
                "🧹 **Manual cleanup started...**\n\n⏳ Please wait...",
                buttons=kb_owner_back())
            asyncio.create_task(_manual_cleanup_sessions())
            return

        # ═════ ANALYTICS ═════
        if data == "o:analytics":
            await safe_answer(event, "📊")
            await safe_edit(event, build_owner_analytics_text(),
                buttons=kb_owner_analytics())
            return

        if data == "o:analytics_today":
            today = db_reactions_today()
            await safe_answer(event, f"💫 {today}", alert=True)
            return

        if data == "o:recent":
            await safe_answer(event, "📜")
            await safe_edit(event, build_owner_recent_text(),
                buttons=kb_owner_analytics())
            return

        if data == "o:top_users":
            await safe_answer(event, "🏆")
            await safe_edit(event, build_owner_top_users_text(),
                buttons=kb_owner_analytics())
            return

        if data == "o:top_channels":
            await safe_answer(event, "📢")
            await safe_edit(event, build_owner_top_channels_text(),
                buttons=kb_owner_analytics())
            return

        if data == "o:run_check":
            if TASK_RUNNING:
                await safe_answer(event, "⏳ Already running", alert=True)
                return
            await safe_answer(event, "🔄 Starting...", alert=True)
            await safe_edit(event,
                f"🔄 **Manual check started**\n\n⏳ Running...",
                buttons=kb_owner_home())
            asyncio.create_task(_manual_check_now())
            return

        # ═════ QUICK REACTION ═════
        if data == "o:quick_react":
            USER_STATES[uid] = {"step": "quick_react_link"}
            await safe_answer(event, "⚡")
            await safe_edit(event,
                f"{STAR_LINE}\n⚡ **QUICK REACTION**\n{STAR_LINE}\n\n"
                f"➡️ Post ka link bhejo:\n\n"
                f"**Supported formats:**\n"
                f"• `https://t.me/channel_name/123`\n"
                f"• `https://t.me/c/1234567890/123`\n"
                f"• `https://t.me/+invite_hash/123`\n"
                f"• `@channel_name/123`\n\n"
                f"**Example:**\n"
                f"`https://t.me/MR_GHOST_OFFICIAL/114`",
                buttons=[[btn("🔙 Cancel", data=b"o:home",
                              style="danger")]])
            return

        if data.startswith("o:qr_count:"):
            try:
                cnt = int(data.split(":")[2])
            except Exception:
                return
            state = USER_STATES.get(uid, {})
            if state.get("step") != "quick_react_count":
                await safe_answer(event, "❌ Session expired", alert=True)
                return
            state["qr_count"] = cnt
            state["step"] = "quick_react_emoji"
            USER_STATES[uid] = state
            await safe_answer(event, f"✅ {cnt}")
            await safe_edit(event,
                f"✅ Count: **{cnt}**\n\n➡️ Emoji mode:",
                buttons=[
                    [btn("🎯 Default", data=b"o:qr_emoji:default",
                         style="success")],
                    [btn("✏️ Custom", data=b"o:qr_emoji:custom",
                         style="primary")],
                    [btn("🔙 Cancel", data=b"o:home", style="danger")],
                ])
            return

        if data.startswith("o:qr_emoji:"):
            mode = data.split(":")[2]
            state = USER_STATES.get(uid, {})
            if state.get("step") != "quick_react_emoji":
                await safe_answer(event, "❌ Session expired", alert=True)
                return

            if mode == "default":
                state["qr_emoji_mode"] = "default"
                state["qr_emojis"] = None
                state["step"] = "quick_react_confirm"
                USER_STATES[uid] = state
                await safe_answer(event, "🎯")
                await safe_edit(event,
                    f"✅ Emoji: **Default**\n\n➡️ Confirm to send?",
                    buttons=[
                        [btn("✅ SEND NOW", data=b"o:qr_send",
                             style="success")],
                        [btn("❌ Cancel", data=b"o:home",
                             style="danger")],
                    ])
            else:
                state["step"] = "quick_react_emojis"
                USER_STATES[uid] = state
                await safe_answer(event, "✏️")
                await safe_edit(event,
                    f"✏️ **Custom Emojis**\n\n"
                    f"➡️ Send emojis (space/comma):",
                    buttons=[[btn("🔙 Cancel", data=b"o:home",
                                  style="danger")]])
            return

        if data == "o:qr_send":
            state = USER_STATES.get(uid, {})
            if state.get("step") != "quick_react_confirm":
                await safe_answer(event, "❌ Session expired", alert=True)
                return

            link = state.get("qr_link")
            cnt = state.get("qr_count", 10)
            mode = state.get("qr_emoji_mode", "default")
            emojis = state.get("qr_emojis")

            if not link:
                await safe_answer(event, "❌ No link", alert=True)
                return

            await safe_answer(event, "⚡ Sending...", alert=True)
            await safe_edit(event,
                f"⚡ **Sending {cnt} reactions...**\n\n"
                f"🔗 `{link}`\n\n⏳ Please wait...")

            ok, fail, total, err = await send_reactions_to_link(
                link, count=cnt, emoji_mode=mode, custom_emojis=emojis)

            del USER_STATES[uid]

            if err:
                await safe_edit(event,
                    f"❌ **Failed**\n\nReason: `{err}`",
                    buttons=kb_owner_back())
                return

            rate = int((ok / total) * 100) if total > 0 else 0
            bar = progress_bar(ok, total, width=15)

            await safe_edit(event,
                f"✅ **REACTIONS SENT**\n"
                f"{STAR_LINE}\n\n"
                f"🔗 `{link}`\n\n"
                f"{DIV()}\n"
                f"🎯 Requested: **{cnt}**\n"
                f"✅ Sent: **{ok}**\n"
                f"❌ Failed: **{fail}**\n"
                f"📈 Success: **{rate}%**\n\n"
                f"{bar}\n\n"
                f"{DIV()}\n"
                f"⏰ {datetime.now().strftime('%H:%M:%S')}",
                buttons=[
                    [btn("⚡ Again", data=b"o:quick_react",
                         style="success")],
                    [btn("🔙 Home", data=b"o:home", style="primary")],
                ])
            return

        # ═════ SETTINGS ═════
        if data == "o:settings":
            await safe_answer(event, "⚙️")
            await safe_edit(event,
                f"{STAR_LINE}\n⚙️ **SETTINGS**\n{STAR_LINE}\n\n"
                f"Owner: @{get_owner_username()}\n"
                f"ID: `{get_owner_id()}`",
                buttons=kb_owner_settings())
            return

        if data.startswith("o:toggle:"):
            key = data[len("o:toggle:"):]
            new_val = cfg_toggle(key)
            await safe_answer(event,
                f"{'✅' if new_val else '❌'}", alert=True)
            await safe_edit(event,
                f"{STAR_LINE}\n⚙️ **SETTINGS**\n{STAR_LINE}",
                buttons=kb_owner_settings())
            return

        if data == "o:edit_uname":
            USER_STATES[uid] = {"step": "edit_owner_uname"}
            await safe_answer(event, "✏️")
            await safe_edit(event,
                f"✏️ **Owner Username**\n\n"
                f"Current: @{get_owner_username()}\n\n"
                f"➡️ New username (without @):",
                buttons=kb_owner_back())
            return

        if data == "o:edit_oid":
            USER_STATES[uid] = {"step": "edit_owner_id"}
            await safe_answer(event, "✏️")
            await safe_edit(event,
                f"✏️ **Owner ID**\n\n"
                f"Current: `{get_owner_id()}`\n\n"
                f"➡️ New numeric ID:",
                buttons=kb_owner_back())
            return

        if data == "o:owner_info":
            await safe_answer(event, "👑")
            await safe_edit(event,
                f"👑 **Owner Info**\n\n"
                f"Username: @{get_owner_username()}\n"
                f"ID: `{get_owner_id()}`",
                buttons=kb_owner_back())
            return

        # ═════ BROADCAST ═════
        if data == "o:broadcast":
            await safe_answer(event, "📢")
            await safe_edit(event,
                f"📢 **Broadcast**\n\n"
                f"👥 Total: **{db_count_users()}**\n"
                f"🟢 Active: **{db_count_active_users()}**",
                buttons=kb_owner_broadcast())
            return

        if data in ("o:bc_users", "o:bc_active"):
            ttype = "active" if data == "o:bc_active" else "users"
            USER_STATES[uid] = {"step": "broadcast_msg",
                                "broadcast_type": ttype}
            await safe_answer(event, "📝")
            await safe_edit(event,
                f"📝 **Broadcast to {ttype}**\n\n➡️ Send message:",
                buttons=[[btn("🔙 Cancel", data=b"o:broadcast",
                              style="danger")]])
            return

        # ═════ NOTIFICATIONS ═════
        if data == "o:notifications":
            await safe_answer(event, "📋")
            await safe_edit(event, "📋 **Notification Tools**",
                buttons=kb_owner_notifications())
            return

        if data == "o:notif_user":
            USER_STATES[uid] = {"step": "notify_user_id"}
            await safe_answer(event, "📤")
            await safe_edit(event,
                f"📤 **Send to User**\n\n➡️ User ID:",
                buttons=kb_owner_back())
            return

        if data == "o:notif_broadcast":
            USER_STATES[uid] = {"step": "broadcast_msg",
                                "broadcast_type": "users"}
            await safe_answer(event, "📢")
            await safe_edit(event,
                f"📢 **Broadcast**\n\n➡️ Send message:",
                buttons=kb_owner_back())
            return

        await safe_answer(event, "⚠️ Unknown", alert=True)

    except Exception as e:
        D_err(e, "handle_owner_cb")
        try:
            await safe_answer(event, "❌ Error", alert=True)
        except Exception:
            pass


# ══════════════════════ SESSIONS OVERVIEW TEXT ══════════════════════
def _build_sessions_overview():
    status = get_sessions_status()
    stats = db_sessions_stats()

    return (
        f"{STAR_LINE}\n"
        f"🔐 **SESSIONS OVERVIEW**\n"
        f"{STAR_LINE}\n\n"
        f"📁 Total: **{status['total']}**\n"
        f"🟢 Available: **{status['available']}**\n"
        f"🌊 Flooded: **{status['flooded']}**\n"
        f"💾 Loaded: **{status['loaded']}**\n\n"
        f"{DIV()}\n"
        f"📊 Lifetime Usage: **{stats['total_used']}**\n\n"
        f"🧹 Auto-cleanup: every 30 min"
    )


def _build_session_detail(sf):
    flooded = is_session_flooded(sf)
    loaded = sf in REAL_CLIENTS
    flood_until = REAL_FLOOD_UNTIL.get(sf)

    flood_txt = "—"
    if flood_until:
        remaining = int((flood_until - datetime.now()).total_seconds())
        flood_txt = f"{remaining}s" if remaining > 0 else "expired"

    return (
        f"{STAR_LINE}\n"
        f"📁 **SESSION INFO**\n"
        f"{STAR_LINE}\n\n"
        f"📄 File: `{sf}`\n\n"
        f"🌊 Flooded: **{'✅' if flooded else '❌'}**\n"
        f"💾 Loaded: **{'✅' if loaded else '❌'}**\n"
        f"⏱️ Flood till: **{flood_txt}**"
    )


# ══════════════════════ MANUAL CHECK NOW ══════════════════════
async def _manual_check_now():
    try:
        channels = db_list_channels(active_only=True)
        DBG(f"Manual check: {len(channels)} channels", "watch")
        for ch in channels:
            if TASK_RUNNING:
                break
            try:
                await process_channel_check(ch)
            except Exception as e:
                DBG(f"Manual check error: {str(e)[:80]}", "fail")
            await asyncio.sleep(0.5)
        DBG("Manual check complete", "watch")
    except Exception as e:
        D_err(e, "_manual_check_now")


# ══════════════════════ MANUAL CLEANUP ══════════════════════
async def _manual_cleanup_sessions():
    try:
        sessions = discover_sessions()
        removed = 0
        kept = 0

        for sf in sessions:
            try:
                path = os.path.join(SESSIONS_DIR, sf.replace(".session", ""))
                session_file = path + ".session"
                if not os.path.exists(session_file):
                    continue

                client = TelegramClient(path, API_ID, API_HASH)
                await asyncio.wait_for(client.connect(), timeout=15)
                authorized = await asyncio.wait_for(
                    client.is_user_authorized(), timeout=10)

                if not authorized:
                    await client.disconnect()
                    os.remove(session_file)
                    journal = session_file + "-journal"
                    if os.path.exists(journal):
                        os.remove(journal)
                    if sf in REAL_CLIENTS:
                        try:
                            await REAL_CLIENTS[sf].disconnect()
                        except Exception:
                            pass
                        REAL_CLIENTS.pop(sf, None)
                    removed += 1
                else:
                    try:
                        await asyncio.wait_for(client.get_me(), timeout=8)
                        kept += 1
                    except Exception:
                        await client.disconnect()
                        os.remove(session_file)
                        if sf in REAL_CLIENTS:
                            REAL_CLIENTS.pop(sf, None)
                        removed += 1
                        continue
                    await client.disconnect()
            except Exception:
                pass

        try:
            await bot.send_message(
                get_owner_id(),
                f"🧹 **Manual Cleanup Done**\n"
                f"{STAR_LINE}\n\n"
                f"🗑️ Removed: **{removed}**\n"
                f"✅ Kept: **{kept}**\n"
                f"📊 Remaining: **{len(discover_sessions())}**")
        except Exception:
            pass
    except Exception as e:
        D_err(e, "_manual_cleanup_sessions")


# ══════════════════════ HTTP HEALTH SERVER ══════════════════════
async def start_health_server():
    async def health(request):
        return web.Response(text="OK", status=200)

    async def status(request):
        sessions = get_sessions_status()
        return web.json_response({
            "status": "online",
            "uptime_sec": int(time.time() - _start_time),
            "task_running": TASK_RUNNING,
            "task_user": TASK_USER_ID,
            "users_total": db_count_users(),
            "users_active": db_count_active_users(),
            "channels_total": db_count_channels(),
            "channels_active": db_count_active_channels(),
            "sessions": {
                "total": sessions["total"],
                "available": sessions["available"],
                "flooded": sessions["flooded"],
                "loaded": sessions["loaded"],
            },
            "reactions": {
                "today": db_reactions_today(),
                "total": db_total_reactions(),
            }
        })

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    DBG(f"HTTP server on port {port}", "ok")


# ══════════════════════ GLOBAL EXCEPTION HANDLER ══════════════════════
def global_exception_handler(loop, context):
    exc = context.get("exception")
    if not exc:
        return
    err_name = type(exc).__name__
    if err_name in ("QueryIdInvalidError",):
        return
    if "query ID is invalid" in str(exc):
        return
    print(f"\n⚠️ GLOBAL: {err_name}: {str(exc)[:120]}", flush=True)


# ══════════════════════ MAIN ══════════════════════
async def main():
    global admin_client, TASK_RUNNING, TASK_USER_ID

    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(global_exception_handler)
    except Exception:
        pass

    print("", flush=True)
    print("╔" + "═" * 58 + "╗", flush=True)
    print("║" + " " * 14 + "👻  GHOST AUTO-REACTOR" + " " * 21 + "║",
          flush=True)
    print("║" + " " * 18 + "Whitelist Edition v4.0" + " " * 17 + "║",
          flush=True)
    print("╚" + "═" * 58 + "╝", flush=True)
    print("", flush=True)

    DBG("Initializing database...", "db")
    db_init()
    DBG("Database ready", "ok")

    DBG("Ensuring owner is whitelisted as user...", "db")
    try:
        owner_u = db_get_user(get_owner_id())
        if not owner_u:
            ok, res = db_add_user(
                get_owner_id(), OWNER_USERNAME_DEFAULT, "Owner",
                added_by=get_owner_id())
            DBG(f"Owner added as user: {res}", "ok")
        else:
            DBG("Owner already exists as user", "ok")
    except Exception as e:
        DBG(f"Owner add failed: {str(e)[:80]}", "warn")

    REAL_CLIENTS.clear()
    REAL_FLOOD_UNTIL.clear()
    ENTITY_CACHE.clear()
    USER_STATES.clear()
    FAILED_TRACKER.clear()
    TASK_RUNNING = False
    TASK_USER_ID = None
    DBG("State cleared", "db")

    admin_session = os.getenv("ADMIN_SESSION", "")
    if admin_session and len(admin_session) > 100:
        try:
            admin_client = TelegramClient(
                StringSession(admin_session), API_ID, API_HASH)
            await admin_client.start()
            if await admin_client.is_user_authorized():
                me = await admin_client.get_me()
                DBG(f"Admin client: {me.first_name}", "ok")
            else:
                await admin_client.disconnect()
                admin_client = None
        except Exception as e:
            admin_client = None
            DBG(f"Admin client skipped: {str(e)[:80]}", "warn")
    else:
        DBG("No admin session (sessions-only mode)", "info")

    sessions = discover_sessions()
    DBG(f"Sessions found: {len(sessions)}", "ok")
    if not sessions:
        DBG("⚠️ No sessions in SESSIONS_DIR!", "warn")
        print(f"  ⚠️ Sessions dir empty: {SESSIONS_DIR}", flush=True)
    else:
        print(f"  ✅ Sessions loaded: {len(sessions)}", flush=True)

    if HAS_GH_SYNC and github_sync.is_enabled():
        try:
            DBG("Downloading sessions from GitHub...", "sync")
            ok, msg = github_sync.download_sessions()
            DBG(f"Sessions sync: {msg}", "sync")
        except Exception as e:
            DBG(f"Sessions download failed: {str(e)[:80]}", "warn")

    print(f"  👑 Owner: @{get_owner_username()}", flush=True)
    print(f"  🆔 Owner ID: {get_owner_id()}", flush=True)
    print(f"  👥 Whitelisted: {db_count_users()}", flush=True)
    print(f"  📢 Channels: {db_count_channels()}", flush=True)
    print("", flush=True)
    print("  ⚙️ Starting services...", flush=True)

    tasks = []

    try:
        tasks.append(asyncio.create_task(start_health_server()))
        DBG("Health server task", "ok")
    except Exception as e:
        DBG(f"Health server start failed: {e}", "warn")

    tasks.append(asyncio.create_task(auto_watch_loop()))
    DBG("Auto-watch loop task", "ok")

    tasks.append(asyncio.create_task(cleanup_loop()))
    DBG("Cleanup loop task", "ok")

    tasks.append(asyncio.create_task(health_loop()))
    DBG("Health check loop task", "ok")

    tasks.append(asyncio.create_task(daily_summary_loop()))
    DBG("Daily summary loop task", "ok")

    tasks.append(asyncio.create_task(session_health_check()))
    DBG("Session health task", "ok")

    tasks.append(asyncio.create_task(auto_cleanup_sessions()))
    DBG("Auto-cleanup sessions task", "ok")

    print(f"  ✅ {len(tasks)} background tasks started", flush=True)
    print("", flush=True)
    print("  🚀 Starting bot...", flush=True)

    while True:
        try:
            await bot.start(bot_token=BOT_TOKEN)
            DBG("Bot connected", "ok")
            break
        except FloodWaitError as e:
            wait_sec = e.seconds + 30
            print(f"  ⏳ Bot flood wait {wait_sec}s", flush=True)
            await asyncio.sleep(wait_sec)
        except Exception as e:
            DBG(f"Bot start error: {str(e)[:100]}", "fail")
            print(f"  ⚠️ Retry in 30s...", flush=True)
            await asyncio.sleep(30)

    print("", flush=True)
    print("╔" + "═" * 58 + "╗", flush=True)
    print("║" + " " * 15 + "✨  BOT IS READY  ✨" + " " * 24 + "║",
          flush=True)
    print("╚" + "═" * 58 + "╝", flush=True)
    print("", flush=True)

    try:
        await bot.run_until_disconnected()
    finally:
        print("\n🛑 Shutting down...", flush=True)
        try:
            for t in tasks:
                t.cancel()
        except Exception:
            pass
        try:
            await close_all_real_clients()
        except Exception:
            pass
        try:
            if admin_client:
                await admin_client.disconnect()
        except Exception:
            pass
        print("  ✅ Shutdown complete\n", flush=True)


# ══════════════════════ ENTRY POINT ══════════════════════
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⚠️ Stopped by user", flush=True)
    except Exception as e:
        print(f"\n❌ FATAL: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
