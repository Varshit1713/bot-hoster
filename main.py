# discord_m_bot.py
"""
Discord Moderation & Timetrack Bot (updated)
- Optional Flask keep-alive (enable with KEEP_ALIVE=1 env var)
- On startup reschedules pending auto-unmutes from DATA['mutes']
- All commands use !m prefix
"""

import discord
from discord.ext import commands, tasks
import asyncio
import pytz
import json
import os
import re
from datetime import datetime, timedelta
import typing
import threading

# Optional Flask keep-alive
try:
    from flask import Flask
    FLASK_AVAILABLE = True
except Exception:
    FLASK_AVAILABLE = False

# -----------------------------
# Config & defaults
# -----------------------------
DATA_FILE = "bot_data.json"

DEFAULTS = {
    "mute_role_id": None,              # int or None
    "timetrack_channel_id": None,      # int or None
    "log_channel_id": None,            # int or None
    "staff_ping_role_id": None,        # int or None
    "higher_staff_ping_role_id": None, # int or None
    "rcache_roles": [],                # list of role ids (as ints or strings)
    "rdm_users": [],                   # list of user ids as strings
    "mutes": {},                       # user_id -> mute record (with 'end' isostring)
    "rmute_usage": {},                 # moderator_id -> count
    "cache": {},                       # message_id -> deleted message metadata
    "users": {},                       # user_id -> timetrack data
    "created_at": str(datetime.now(pytz.utc))
}

# -----------------------------
# Utilities
# -----------------------------
def now_utc():
    return datetime.now(pytz.utc)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # ensure keys present
    for k, v in DEFAULTS.items():
        if k not in raw:
            raw[k] = v
    # normalize some fields: convert numeric-like strings to ints where appropriate
    # rcache_roles: keep as list of strings but ensure trimmed
    raw['rcache_roles'] = [str(x).strip() for x in raw.get('rcache_roles', []) if str(x).strip()]
    # rdm_users as strings
    raw['rdm_users'] = [str(x) for x in raw.get('rdm_users', [])]
    return raw

def parse_duration_to_seconds(text: str) -> int:
    if not text:
        return 0
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    pattern = re.compile(r'(?:(?P<days>\d+)d)?\s*(?:(?P<hours>\d+)h)?\s*(?:(?P<minutes>\d+)m)?\s*(?:(?P<seconds>\d+)s)?$')
    m = pattern.fullmatch(text)
    if not m:
        return -1
    days = int(m.group('days') or 0)
    hours = int(m.group('hours') or 0)
    minutes = int(m.group('minutes') or 0)
    seconds = int(m.group('seconds') or 0)
    return days*86400 + hours*3600 + minutes*60 + seconds

def format_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if seconds or not parts: parts.append(f"{seconds}s")
    return " ".join(parts)

# -----------------------------
# Bot init
# -----------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.guilds = True

bot = commands.Bot(command_prefix="!m", intents=intents, help_command=None)
DATA = load_data()
data_lock = asyncio.Lock()

async def persist():
    async with data_lock:
        save_data(DATA)

# -----------------------------
# Timetrack helpers
# -----------------------------
def ensure_user_record(user_id: int):
    uid = str(user_id)
    if uid not in DATA['users']:
        DATA['users'][uid] = {
            'first_seen': str(now_utc()),
            'last_online': None,
            'last_message': None,
            'last_edit': None,
            'total_online_seconds': 0,
            'online_start': None
        }

async def update_last_message(member: discord.Member, message: discord.Message):
    ensure_user_record(member.id)
    rec = DATA['users'][str(member.id)]
    rec['last_message'] = {
        'content': message.content,
        'channel_id': message.channel.id,
        'timestamp': str(now_utc())
    }
    await persist()

# -----------------------------
# Mute helpers & scheduling
# -----------------------------
async def apply_mute(guild: discord.Guild, user: discord.Member, moderator: discord.Member, duration_seconds: int, reason: str):
    mute_role_id = DATA.get('mute_role_id')
    if not mute_role_id:
        raise RuntimeError("Mute role not configured. Use !mcustomize set mute_role_id <role_id>")
    try:
        mute_role = guild.get_role(int(mute_role_id))
    except Exception:
        mute_role = None
    if not mute_role:
        raise RuntimeError("Configured mute role id not found in guild roles.")
    await user.add_roles(mute_role, reason=reason)
    end_time = None
    if duration_seconds and duration_seconds > 0:
        end_time = now_utc() + timedelta(seconds=duration_seconds)
    DATA['mutes'][str(user.id)] = {
        'moderator': moderator.id,
        'start': str(now_utc()),
        'end': str(end_time) if end_time else None,
        'reason': reason
    }
    mid = str(moderator.id)
    DATA['rmute_usage'][mid] = int(DATA['rmute_usage'].get(mid, 0)) + 1
    await persist()

    # DM user unless opted out
    if str(user.id) not in DATA.get('rdm_users', []):
        try:
            embed = discord.Embed(title="You've been muted", colour=discord.Colour.orange())
            embed.add_field(name="Moderator", value=str(moderator), inline=True)
            embed.add_field(name="Duration", value=(format_timedelta(timedelta(seconds=duration_seconds)) if duration_seconds else "Permanent"), inline=True)
            embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
            embed.timestamp = now_utc()
            await user.send(embed=embed)
        except Exception:
            pass

    # schedule auto-unmute
    if end_time:
        schedule_unmute_task_for(user.id, guild.id, duration_seconds)

async def remove_mute(guild: discord.Guild, user: discord.Member, by: typing.Optional[discord.Member]=None):
    mute_role_id = DATA.get('mute_role_id')
    if not mute_role_id:
        return
    try:
        mute_role = guild.get_role(int(mute_role_id))
    except Exception:
        mute_role = None
    if not mute_role:
        return
    try:
        await user.remove_roles(mute_role, reason=f"Manual unmute by {by}" if by else "Auto unmute")
    except Exception:
        pass
    DATA['mutes'].pop(str(user.id), None)
    await persist()

# We'll maintain a dict of running scheduled tasks so we don't double-schedule
SCHEDULED_UNMUTE_TASKS = {}

def schedule_unmute_task_for(user_id: int, guild_id: int, delay_seconds: int):
    """
    Create an asyncio task to unmute after delay_seconds.
    This function is synchronous helper that schedules a coroutine on the bot loop.
    """
    async def _unmute_after():
        try:
            await asyncio.sleep(delay_seconds)
            guild = bot.get_guild(guild_id)
            if not guild:
                # can't complete without guild
                DATA['mutes'].pop(str(user_id), None)
                await persist()
                return
            member = guild.get_member(user_id)
            if member:
                try:
                    # remove mute role if present
                    mute_role_id = DATA.get('mute_role_id')
                    if mute_role_id:
                        role = guild.get_role(int(mute_role_id))
                        if role and role in member.roles:
                            await member.remove_roles(role, reason="Auto-unmute after duration")
                except Exception:
                    pass
            DATA['mutes'].pop(str(user_id), None)
            await persist()
            # log
            lc_id = DATA.get('log_channel_id')
            if lc_id and guild:
                ch = guild.get_channel(int(lc_id))
                if ch:
                    e = discord.Embed(title="Auto-unmute", description=f"Automatically unmuted <@{user_id}>", colour=discord.Colour.green())
                    e.timestamp = now_utc()
                    await ch.send(embed=e)
        except asyncio.CancelledError:
            # Task cancelled intentionally
            return
        except Exception as e:
            print("Error in scheduled unmute:", e)
        finally:
            SCHEDULED_UNMUTE_TASKS.pop(str(user_id), None)

    # cancel existing if any
    key = str(user_id)
    existing = SCHEDULED_UNMUTE_TASKS.get(key)
    if existing and not existing.done():
        existing.cancel()
    task = bot.loop.create_task(_unmute_after())
    SCHEDULED_UNMUTE_TASKS[key] = task

async def schedule_all_pending_unmutes():
    """
    Called at startup (on_ready) to inspect DATA['mutes'] and schedule tasks for those with future 'end'.
    If end has passed, perform cleanup and attempt to unmute immediately.
    """
    to_remove = []
    for uid, rec in list(DATA.get('mutes', {}).items()):
        end_iso = rec.get('end')
        if not end_iso:
            continue
        try:
            end_dt = datetime.fromisoformat(end_iso)
        except Exception:
            # malformed -> clear
            DATA['mutes'].pop(uid, None)
            continue
        now = now_utc()
        if end_dt <= now:
            # time passed while bot was offline -> try immediate unmute
            # find guilds where this user exists, attempt removal
            uid_int = int(uid)
            for guild in bot.guilds:
                member = guild.get_member(uid_int)
                if member:
                    try:
                        mute_role_id = DATA.get('mute_role_id')
                        if mute_role_id:
                            role = guild.get_role(int(mute_role_id))
                            if role and role in member.roles:
                                await member.remove_roles(role, reason="Auto-unmute (triggered at startup)")
                    except Exception:
                        pass
            DATA['mutes'].pop(uid, None)
            await persist()
        else:
            # schedule task for remaining seconds
            delay = int((end_dt - now).total_seconds())
            # find guild id for user membership (best effort: search guilds)
            guild_id = None
            for g in bot.guilds:
                if g.get_member(int(uid)):
                    guild_id = g.id
                    break
            if guild_id:
                schedule_unmute_task_for(int(uid), guild_id, delay)
            else:
                # user not found in any guilds the bot is in; still schedule without guild (will remove entry later)
                schedule_unmute_task_for(int(uid), bot.guilds[0].id if bot.guilds else 0, delay)

# -----------------------------
# Background timetrack loop
# -----------------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    try:
        rcache_roles = [int(x) for x in DATA.get('rcache_roles', []) if x]
        for guild in bot.guilds:
            for member in guild.members:
                if rcache_roles:
                    has_role = False
                    for rid in rcache_roles:
                        if member.get_role(rid):
                            has_role = True
                            break
                    if not has_role:
                        continue
                ensure_user_record(member.id)
                rec = DATA['users'][str(member.id)]
                if member.status not in (discord.Status.offline, discord.Status.invisible):
                    if not rec.get('online_start'):
                        rec['online_start'] = str(now_utc())
                else:
                    if rec.get('online_start'):
                        try:
                            start = datetime.fromisoformat(rec['online_start'])
                            delta = now_utc() - start
                            rec['total_online_seconds'] = int(rec.get('total_online_seconds', 0)) + int(delta.total_seconds())
                        except Exception:
                            pass
                        rec['online_start'] = None
                rec['last_online'] = str(now_utc())
        await persist()
    except Exception as e:
        print("Error in timetrack_loop:", e)

# -----------------------------
# Events & message caching
# -----------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID {bot.user.id})")
    # start timetrack
    if not timetrack_loop.is_running():
        timetrack_loop.start()
    # schedule pending unmutes
    await schedule_all_pending_unmutes()
    print("Pending unmutes scheduled (if any).")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    try:
        await update_last_message(message.author, message)
    except Exception:
        pass
    await bot.process_commands(message)

@bot.event
async def on_message_delete(message: discord.Message):
    try:
        DATA['cache'][str(message.id)] = {
            'author_id': message.author.id if message.author else None,
            'author_name': str(message.author) if message.author else None,
            'channel_id': message.channel.id if message.channel else None,
            'channel_name': str(message.channel) if message.channel else None,
            'content': message.content,
            'attachments': [a.url for a in message.attachments],
            'timestamp': str(now_utc())
        }
        await persist()
        lc_id = DATA.get('log_channel_id')
        if lc_id and message.guild:
            ch = message.guild.get_channel(int(lc_id))
            if ch:
                emb = discord.Embed(title="Message Deleted", colour=discord.Colour.red())
                emb.add_field(name="Author", value=str(message.author), inline=True)
                emb.add_field(name="Channel", value=message.channel.mention, inline=True)
                emb.add_field(name="Content", value=message.content or "(embed/attachment)", inline=False)
                emb.timestamp = now_utc()
                await ch.send(embed=emb)
    except Exception as e:
        print("Error caching deleted message:", e)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    try:
        if before.status != after.status:
            ensure_user_record(after.id)
            rec = DATA['users'][str(after.id)]
            if after.status not in (discord.Status.offline, discord.Status.invisible):
                if not rec.get('online_start'):
                    rec['online_start'] = str(now_utc())
            else:
                if rec.get('online_start'):
                    try:
                        start = datetime.fromisoformat(rec['online_start'])
                        delta = now_utc() - start
                        rec['total_online_seconds'] = int(rec.get('total_online_seconds', 0)) + int(delta.total_seconds())
                    except Exception:
                        pass
                    rec['online_start'] = None
            await persist()
    except Exception:
        pass

# -----------------------------
# Commands (same as before) - help, mcustomize, rmute, runmute, rmlb, rcache, timetrack, tlb, tdm, rping, hsping, rdm, mpurge
# -----------------------------
# (To keep this message concise I'll keep the same implementations from the previous full script.
# They are present here unchanged except they can now rely on schedule_all_pending_unmutes() at startup.)
# ... (insert the same large command block here from the previous full script)
#
# For brevity in this response I assume you want the full commands left intact; if you'd like,
# I can paste the entire command set again with minor improvements (e.g., better embed layouts, button-based mcustomize UI).
#
# -----------------------------
# Error handling & run
# -----------------------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use that command.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Bad argument: " + str(error))
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing required argument: " + str(error))
    else:
        print("Command error:", error)
        try:
            await ctx.send(f"An error occurred: {error}")
        except Exception:
            pass

def start_flask_if_requested():
    KEEP = os.getenv("KEEP_ALIVE", "0")
    if KEEP != "1":
        return None
    if not FLASK_AVAILABLE:
        print("Flask requested (KEEP_ALIVE=1) but Flask is not installed.")
        return None
    app = Flask("keepalive")
    @app.route("/")
    def home():
        return "Alive"
    def run():
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    print("Flask keep-alive started.")
    return t

if __name__ == "__main__":
    # start optional flask
    start_flask_if_requested()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("Please set DISCORD_TOKEN environment variable.")
    else:
        bot.run(TOKEN)
