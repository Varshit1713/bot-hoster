# mega_timetrack_with_audit_reconciliation_full.py
# Combined and extended from user's paste.
# Requirements: Python 3.9+, discord.py 2.x, pytz
# Set environment variable DISCORD_TOKEN before running.

import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import pytz
import json
import os
import threading
import re
import traceback
from typing import Optional, List, Dict, Any

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = 140335996236909773
TRACK_CHANNEL_ID = 1410458084874260592

# Roles & constants
TRACK_ROLES = [
    1410419924173848626, 1410420126003630122, 1410423594579918860,
    1410421466666631279, 1410421647265108038, 1410419345234067568,
    1410422029236047975, 1410458084874260592
]
RMUTE_ROLE_ID = 1410423854563721287
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
STAFF_NOTIFY_CHANNELS = [1403422664521023648, 1410458084874260592]

OFFLINE_DELAY = 53  # seconds for offline threshold
PRESENCE_CHECK_INTERVAL = 60  # per requirements (every 60 seconds)
AUTO_SAVE_INTERVAL = 120  # autosave interval (seconds)
DATA_FILE = "mega_bot_data.json"
BACKUP_DIR = "mega_bot_backups"
MAX_BACKUPS = 20
COMMAND_COOLDOWN = 4  # seconds
AUDIT_LOOKBACK_SECONDS = 3600

# ------------------ INTENTS & BOT ------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
data_lock = threading.Lock()
console_lock = threading.Lock()
command_cooldowns: Dict[int, float] = {}

# ------------------ SAFE PRINT ------------------
def safe_print(*args, **kwargs):
    with console_lock:
        print(*args, **kwargs)

# ------------------ PERSISTENCE ------------------
def init_data_structure() -> Dict[str, Any]:
    return {
        "users": {},  # per-user timetrack
        "mutes": {},  # keyed by mute_id (string)
        "images": {},  # cached deleted attachments/messages
        "logs": {},
        "rmute_usage": {},
        "last_audit_check": None,
        "rping_disabled_users": {},
        "rdm_users": {},  # opt-out from DMs mapping user_id -> True
        "rmute_active": {}  # user_id -> mute record id reference
    }

def load_data() -> Dict[str, Any]:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            safe_print("⚠️ Failed to load data file:", e)
            try:
                os.rename(DATA_FILE, DATA_FILE + ".corrupt")
            except:
                pass
            return init_data_structure()
    return init_data_structure()

def rotate_backups():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        files = sorted(os.listdir(BACKUP_DIR))
        while len(files) > MAX_BACKUPS:
            os.remove(os.path.join(BACKUP_DIR, files.pop(0)))
    except Exception as e:
        safe_print("⚠️ backup rotation error:", e)

def save_data(data: Dict[str, Any]):
    with data_lock:
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            ts = datetime.datetime.now(pytz.utc).strftime("%Y%m%d_%H%M%S")
            backup = os.path.join(BACKUP_DIR, f"backup_{ts}.json")
            with open(backup, "w", encoding="utf-8") as bf:
                json.dump(data, bf, indent=2, default=str)
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            rotate_backups()
        except Exception as e:
            safe_print("❌ Error saving data:", e)
            traceback.print_exc()

# ------------------ USER DATA HELPERS ------------------
def ensure_user_data(uid: str, data: Dict[str, Any]) -> None:
    if uid not in data["users"]:
        data["users"][uid] = {
            "status": "offline",
            "online_time": None,
            "offline_time": None,
            "last_message": None,
            "last_message_time": None,
            "last_edit": None,
            "last_edit_time": None,
            "last_delete": None,
            "last_online_times": {},
            "offline_timer": 0,
            "total_online_seconds": 0,
            "daily_seconds": {},
            "weekly_seconds": {},
            "monthly_seconds": {},
            "average_online": 0.0,
            "notify": True,
            "online_start": None  # timezone-aware ISO for session start
        }

def add_seconds_to_user(uid: str, seconds: int, data: Dict[str, Any]) -> None:
    ensure_user_data(uid, data)
    u = data["users"][uid]
    u["total_online_seconds"] = u.get("total_online_seconds", 0) + seconds
    today = datetime.datetime.now(pytz.utc).strftime("%Y-%m-%d")
    week = datetime.datetime.now(pytz.utc).strftime("%Y-W%U")
    month = datetime.datetime.now(pytz.utc).strftime("%Y-%m")
    u["daily_seconds"][today] = u["daily_seconds"].get(today, 0) + seconds
    u["weekly_seconds"][week] = u["weekly_seconds"].get(week, 0) + seconds
    u["monthly_seconds"][month] = u["monthly_seconds"].get(month, 0) + seconds
    total_time = u["total_online_seconds"]
    total_days = max(len(u.get("daily_seconds", {})), 1)
    u["average_online"] = total_time / total_days

# ------------------ TIME HELPERS ------------------
def tz_now_strings() -> Dict[str, str]:
    tzs = {
        "UTC": pytz.utc,
        "EST": pytz.timezone("US/Eastern"),
        "PST": pytz.timezone("US/Pacific"),
        "CET": pytz.timezone("Europe/Paris")
    }
    out = {}
    for k, v in tzs.items():
        out[k] = datetime.datetime.now(v).strftime("%Y-%m-%d %H:%M:%S")
    return out

def format_time(dt: datetime.datetime) -> str:
    if dt is None:
        return "N/A"
    return dt.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S %Z")

def format_duration_seconds(sec: int) -> str:
    sec = int(sec)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

def parse_duration(s: str) -> Optional[int]:
    if not s:
        return None
    m = re.match(r"^(\d+)([smhd])$", s.strip(), re.I)
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "s":
        return val
    if unit == "m":
        return val * 60
    if unit == "h":
        return val * 3600
    if unit == "d":
        return val * 86400
    return None

def ascii_progress_bar(current: int, total: int, length: int = 20) -> str:
    try:
        ratio = min(max(float(current) / float(total), 0.0), 1.0)
        filled = int(length * ratio)
        return "█" * filled + "░" * (length - filled)
    except:
        return "░" * length

# ------------------ EMBED BUILDERS ------------------
def build_timetrack_embed(member: discord.Member, user_data: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title=f"Timetrack — {member.display_name}", color=discord.Color.blue())
    embed.add_field(name="Status", value=f"**{user_data.get('status','offline')}**", inline=True)
    embed.add_field(name="Online Since", value=user_data.get("online_time") or "N/A", inline=True)
    embed.add_field(name="Offline Since", value=user_data.get("offline_time") or "N/A", inline=True)
    embed.add_field(name="Last Message", value=user_data.get("last_message") or "N/A", inline=False)
    embed.add_field(name="Last Edit", value=user_data.get("last_edit") or "N/A", inline=False)
    embed.add_field(name="Last Delete", value=user_data.get("last_delete") or "N/A", inline=False)
    tz_map = user_data.get("last_online_times", {})
    tz_lines = [f"{tz}: {tz_map.get(tz,'N/A')}" for tz in ("UTC", "EST", "PST", "CET")]
    embed.add_field(name="Last Online (4 TZ)", value="\n".join(tz_lines), inline=False)
    total = user_data.get("total_online_seconds", 0)
    avg = int(user_data.get("average_online", 0))
    embed.add_field(name="Total Online (forever)", value=format_duration_seconds(total), inline=True)
    embed.add_field(name="Average Daily Online", value=format_duration_seconds(avg), inline=True)
    today = datetime.datetime.now(pytz.utc).strftime("%Y-%m-%d")
    todays = user_data.get("daily_seconds", {}).get(today, 0)
    embed.add_field(name="Today's Activity", value=f"{ascii_progress_bar(todays,3600)} ({todays}s)", inline=False)
    embed.set_footer(text=f"Timetrack • offline-delay {OFFLINE_DELAY}s")
    return embed

def build_mute_dm_embed(target: discord.Member, moderator: discord.Member, duration_str: Optional[str], reason: str, auto: bool = False) -> discord.Embed:
    title = "You've been muted" if not auto else "You were auto-muted"
    embed = discord.Embed(title=title, color=discord.Color.dark_theme())
    embed.add_field(name="Server", value=f"{target.guild.name}", inline=False)
    embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
    if duration_str:
        embed.add_field(name="Duration", value=duration_str, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Appeal", value="If you believe this is incorrect, contact the moderation team.", inline=False)
    embed.set_footer(text="You may not receive DMs if you have DMs disabled.")
    return embed

def build_mute_log_embed(target: discord.Member, moderator: Optional[discord.Member], duration_str: Optional[str], reason: str, unmute_at: Optional[str] = None, source: Optional[str] = None) -> discord.Embed:
    embed = discord.Embed(title="Mute Log", color=discord.Color.orange())
    embed.add_field(name="User", value=f"{target} ({target.id})", inline=False)
    embed.add_field(name="Moderator/Source", value=f"{moderator if moderator else source}", inline=False)
    if duration_str:
        embed.add_field(name="Duration", value=duration_str, inline=True)
    if unmute_at:
        embed.add_field(name="Unmute At", value=unmute_at, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="Mute event logged")
    return embed

def build_unmute_log_embed(target: discord.Member, moderator: Optional[discord.Member], reason: Optional[str], auto: bool = False, source: Optional[str] = None) -> discord.Embed:
    title = "✅ Auto Unmute" if auto else "Unmute Log"
    embed = discord.Embed(title=title, color=discord.Color.green())
    embed.add_field(name="User", value=f"{target} ({target.id})", inline=False)
    if moderator:
        embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
    if source and not moderator:
        embed.add_field(name="Source", value=source, inline=False)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="Unmute event")
    return embed

def build_purge_embed(actor: Optional[discord.Member], channel: discord.TextChannel, count: int, preview: List[str], when: str) -> discord.Embed:
    embed = discord.Embed(title="Purge Detected", color=discord.Color.dark_red())
    embed.add_field(name="Channel", value=f"{channel.mention} ({channel.id})", inline=False)
    if actor:
        embed.add_field(name="Purged by", value=f"{actor} ({actor.id})", inline=True)
    else:
        embed.add_field(name="Purged by", value="Unknown / could be bot", inline=True)
    embed.add_field(name="Message count", value=str(count), inline=True)
    if preview:
        embed.add_field(name="Preview", value="\n".join(preview[:10]), inline=False)
    embed.set_footer(text=f"Purge at {when}")
    return embed

# ------------------ COOLDOWN HELPER ------------------
def can_execute_command(user_id: int) -> bool:
    last = command_cooldowns.get(user_id, 0.0)
    now = datetime.datetime.now(pytz.utc).timestamp()
    if now - last >= COMMAND_COOLDOWN:
        command_cooldowns[user_id] = now
        return True
    return False

# ------------------ GLOBAL DATA (loaded at runtime) ------------------
DATA = load_data()

# ------------------ PRESENCE TRACKER TASK (every 60s) ------------------
@tasks.loop(seconds=PRESENCE_CHECK_INTERVAL)
async def presence_tracker_task():
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        data = load_data()
        now = datetime.datetime.now(pytz.utc)
        for member in guild.members:
            # only track if they have a tracked role (RCACHE_ROLES)
            if not any(r.id in RCACHE_ROLES for r in member.roles):
                continue
            uid = str(member.id)
            ensure_user_data(uid, data)
            u = data["users"][uid]
            # Use presence status to calculate
            if member.status != discord.Status.offline:
                # became online
                if u.get("status") == "offline":
                    u["status"] = "online"
                    u["online_time"] = format_time(now)
                    u["online_start"] = now.isoformat()
                    u["offline_timer"] = 0
                    u["last_online_times"] = tz_now_strings()
                # credit online seconds (add PRESENCE_CHECK_INTERVAL)
                add_seconds_to_user(uid, PRESENCE_CHECK_INTERVAL, data)
            else:
                # offline presence
                if u.get("status") == "online":
                    u["offline_timer"] = u.get("offline_timer", 0) + PRESENCE_CHECK_INTERVAL
                    if u["offline_timer"] >= OFFLINE_DELAY:
                        u["status"] = "offline"
                        u["offline_time"] = format_time(now)
                        u["offline_timer"] = 0
                        u["last_online_times"] = tz_now_strings()
                        # calculate session duration if online_start exists
                        if u.get("online_start"):
                            try:
                                start_dt = datetime.datetime.fromisoformat(u["online_start"])
                                if start_dt.tzinfo is None:
                                    start_dt = start_dt.replace(tzinfo=pytz.utc)
                                session_seconds = int((now - start_dt).total_seconds())
                                add_seconds_to_user(uid, max(0, session_seconds), data)
                            except Exception:
                                pass
        save_data(data)
    except Exception as e:
        safe_print("❌ presence_tracker_task error:", e)
        traceback.print_exc()

# ------------------ AUTO SAVE ------------------
@tasks.loop(seconds=AUTO_SAVE_INTERVAL)
async def auto_save_task():
    try:
        save_data(DATA)
        safe_print("Auto-saved data.")
    except Exception as e:
        safe_print("❌ auto_save_task error:", e)
        traceback.print_exc()

# ------------------ MUTE MANAGEMENT ------------------
async def schedule_unmute(mute_id: str, unmute_at_ts: float):
    """
    Schedule an automatic unmute by mute_id at unmute_at_ts (epoch seconds).
    This uses asyncio.create_task so multiple scheduled unmute tasks can run.
    """
    async def _wait_and_unmute():
        try:
            now_ts = datetime.datetime.now(pytz.utc).timestamp()
            to_wait = max(0, unmute_at_ts - now_ts)
            await asyncio.sleep(to_wait)
            data = load_data()
            m = data.get("mutes", {}).get(mute_id)
            if not m:
                return
            guild = bot.get_guild(GUILD_ID)
            if not guild:
                return
            member = guild.get_member(int(m["target_id"]))
            if member:
                role = discord.Object(id=RMUTE_ROLE_ID)
                # remove role if still present
                r = discord.utils.get(member.roles, id=RMUTE_ROLE_ID)
                if r:
                    try:
                        await member.remove_roles(r, reason="Auto unmute")
                    except Exception:
                        pass
                # Log unmute
                channel = bot.get_channel(TRACK_CHANNEL_ID)
                embed = build_unmute_log_embed(member, None, f"Auto-unmute after {m.get('duration_str')}", auto=True)
                if channel:
                    await channel.send(embed=embed)
            # clear from records
            data["mutes"].pop(mute_id, None)
            # update rmute_active
            if data.get("rmute_active", {}).get(m.get("target_id")) == mute_id:
                data["rmute_active"].pop(m.get("target_id"), None)
            save_data(data)
        except Exception as e:
            safe_print("❌ schedule_unmute error:", e)
            traceback.print_exc()

    asyncio.create_task(_wait_and_unmute())

def make_mute_id(target_id: int) -> str:
    return f"mute_{target_id}_{int(datetime.datetime.now(pytz.utc).timestamp())}"

# ------------------ LISTENERS: message (cache & last-message), delete, bulk delete ------------------
@bot.event
async def on_ready():
    safe_print(f"Bot logged in as {bot.user} (ID {bot.user.id})")
    # start background tasks
    presence_tracker_task.start()
    auto_save_task.start()
    # reconcile audit logs since previous check (non-blocking)
    asyncio.create_task(reconcile_audit_logs_on_start())
    safe_print("Background tasks started.")

@bot.event
async def on_message(message: discord.Message):
    # cache messages for rcache and purge logs
    try:
        data = load_data()
        # store message content and attachments keyed by message id
        if message.author and not message.author.bot:
            msg_cache = {
                "id": message.id,
                "author_id": message.author.id,
                "author_name": str(message.author),
                "channel_id": message.channel.id if message.channel else None,
                "content": message.content,
                "attachments": [a.url for a in message.attachments],
                "created_at": message.created_at.astimezone(pytz.utc).isoformat() if message.created_at else datetime.datetime.now(pytz.utc).isoformat(),
                "referenced_message_id": message.reference.message_id if message.reference else None
            }
            data["images"][str(message.id)] = msg_cache
            # update last message timetrack
            uid = str(message.author.id)
            ensure_user_data(uid, data)
            u = data["users"][uid]
            u["last_message"] = (message.content[:1900] + '...') if message.content and len(message.content) > 1900 else message.content
            u["last_message_time"] = format_time(datetime.datetime.now(pytz.utc))
            save_data(data)
    except Exception:
        traceback.print_exc()
    await bot.process_commands(message)

@bot.event
async def on_message_delete(message: discord.Message):
    # Keep a cache of deleted messages in data["images"]
    try:
        data = load_data()
        if message and message.author and not message.author.bot:
            cached = {
                "id": message.id,
                "author_id": message.author.id,
                "author_name": str(message.author),
                "channel_id": message.channel.id if message.channel else None,
                "content": message.content,
                "attachments": [a.url for a in message.attachments],
                "deleted_at": datetime.datetime.now(pytz.utc).isoformat(),
                "referenced_message_id": message.reference.message_id if message.reference else None,
            }
            # store under "deleted_<msgid>"
            data["images"][f"deleted_{message.id}"] = cached
            # update last_delete on user
            uid = str(message.author.id)
            ensure_user_data(uid, data)
            u = data["users"][uid]
            u["last_delete"] = format_time(datetime.datetime.now(pytz.utc))
            save_data(data)
    except Exception:
        traceback.print_exc()

@bot.event
async def on_bulk_message_delete(messages):
    # messages: sequence of Message
    try:
        data = load_data()
        preview = []
        for message in messages:
            if message and message.author and not message.author.bot:
                cached = {
                    "id": message.id,
                    "author_id": message.author.id,
                    "author_name": str(message.author),
                    "channel_id": message.channel.id if message.channel else None,
                    "content": message.content,
                    "attachments": [a.url for a in message.attachments],
                    "deleted_at": datetime.datetime.now(pytz.utc).isoformat(),
                }
                data["images"][f"deleted_{message.id}"] = cached
                preview.append(f"{message.author}: {message.content[:200]}")
        # log purge to tracking channel
        channel = bot.get_channel(TRACK_CHANNEL_ID)
        when = format_time(datetime.datetime.now(pytz.utc))
        if channel:
            embed = build_purge_embed(None, messages[0].channel if messages else channel, len(messages), preview, when)
            await channel.send(embed=embed)
        save_data(data)
    except Exception:
        traceback.print_exc()

# ------------------ AUDIT LOG RECONCILIATION (on startup) ------------------
async def reconcile_audit_logs_on_start():
    try:
        data = load_data()
        guild = bot.get_guild(GUILD_ID)
        channel = bot.get_channel(TRACK_CHANNEL_ID)
        if not guild or not channel:
            return
        last_check_iso = data.get("last_audit_check")
        now = datetime.datetime.now(pytz.utc)
        lookback_since = now - datetime.timedelta(seconds=AUDIT_LOOKBACK_SECONDS)
        if last_check_iso:
            try:
                last_check_dt = datetime.datetime.fromisoformat(last_check_iso)
                if last_check_dt.tzinfo is None:
                    last_check_dt = last_check_dt.replace(tzinfo=pytz.utc)
            except Exception:
                last_check_dt = lookback_since
        else:
            last_check_dt = lookback_since

        actions_to_check = [
            discord.AuditLogAction.role_create, discord.AuditLogAction.role_delete, discord.AuditLogAction.role_update,
            discord.AuditLogAction.channel_create, discord.AuditLogAction.channel_delete, discord.AuditLogAction.channel_update,
            discord.AuditLogAction.message_bulk_delete, discord.AuditLogAction.member_role_update
        ]
        # iterate and post a brief summary of new audit entries
        for action in actions_to_check:
            try:
                async for entry in guild.audit_logs(limit=100, action=action):
                    if entry.created_at and entry.created_at.replace(tzinfo=pytz.utc) < last_check_dt:
                        break
                    # send a simple recon message to channel
                    try:
                        summary = f"{entry.user} performed {entry.action} at {format_time(entry.created_at.replace(tzinfo=pytz.utc))}"
                        await channel.send(summary)
                    except Exception:
                        pass
            except Exception:
                pass
        data["last_audit_check"] = now.isoformat()
        save_data(data)
    except Exception as e:
        safe_print("❌ reconcile_audit_logs_on_start error:", e)
        traceback.print_exc()

# ------------------ RMUTE / RUNMUTE Commands ------------------
def is_mod(ctx: commands.Context) -> bool:
    # Basic check: has any role in TRACK_ROLES
    return any(r.id in TRACK_ROLES for r in ctx.author.roles)

@bot.command(name="rmute")
@commands.guild_only()
async def cmd_rmute(ctx: commands.Context, users: commands.Greedy[discord.Member], duration: str = None, *, reason: Optional[str] = "No reason provided"):
    """!rmute [users...] [duration] [reason] - mutes multiple users with a role and logs it"""
    if not is_mod(ctx):
        return await ctx.reply("You don't have permission to use this command.")
    if not users:
        return await ctx.reply("Please mention at least one user to mute.")
    dur_seconds = parse_duration(duration) if duration else None
    data = load_data()
    channel = bot.get_channel(TRACK_CHANNEL_ID)
    for member in users:
        mute_id = make_mute_id(member.id)
        # assign role
        try:
            role = discord.utils.get(ctx.guild.roles, id=RMUTE_ROLE_ID)
            if role:
                await member.add_roles(role, reason=f"rmute by {ctx.author}")
        except Exception:
            safe_print("Failed to assign mute role to", member)
        # compute unmute_at
        unmute_at_iso = None
        unmute_ts = None
        if dur_seconds:
            unmute_ts = datetime.datetime.now(pytz.utc).timestamp() + dur_seconds
            unmute_at_iso = datetime.datetime.now(pytz.utc) + datetime.timedelta(seconds=dur_seconds)
            unmute_at_iso = unmute_at_iso.isoformat()
        # store mute record
        data["mutes"][mute_id] = {
            "target_id": str(member.id),
            "moderator_id": str(ctx.author.id),
            "duration": dur_seconds,
            "duration_str": duration,
            "reason": reason,
            "start_ts": datetime.datetime.now(pytz.utc).isoformat(),
            "unmute_at": unmute_at_iso
        }
        # track rmute usage
        data["rmute_usage"][str(ctx.author.id)] = data["rmute_usage"].get(str(ctx.author.id), 0) + 1
        data["rmute_active"][str(member.id)] = mute_id
        save_data(data)
        # DM the user unless opted-out
        rdm_users = data.get("rdm_users", {})
        if not rdm_users.get(str(member.id), False):
            try:
                await member.send(embed=build_mute_dm_embed(member, ctx.author, duration, reason))
            except Exception:
                pass
        # log to tracking channel
        if channel:
            embed = build_mute_log_embed(member, ctx.author, duration, reason, unmute_at=unmute_at_iso)
            await channel.send(embed=embed)
        # schedule unmute
        if unmute_ts:
            await schedule_unmute(mute_id, unmute_ts)
    await ctx.message.delete()
    await ctx.send(f"Muted {len(users)} user(s).", delete_after=8)

@bot.command(name="runmute")
@commands.guild_only()
async def cmd_runmute(ctx: commands.Context, user: discord.Member, duration: str = None, *, reason: Optional[str] = "No reason provided"):
    """!runmute [user] [duration] [reason] - single user rmute"""
    if not is_mod(ctx):
        return await ctx.reply("You don't have permission to use this command.")
    dur_seconds = parse_duration(duration) if duration else None
    data = load_data()
    channel = bot.get_channel(TRACK_CHANNEL_ID)

    mute_id = make_mute_id(user.id)
    try:
        role = discord.utils.get(ctx.guild.roles, id=RMUTE_ROLE_ID)
        if role:
            await user.add_roles(role, reason=f"runmute by {ctx.author}")
    except Exception:
        safe_print("Failed to assign mute role to", user)

    unmute_at_iso = None
    unmute_ts = None
    if dur_seconds:
        unmute_ts = datetime.datetime.now(pytz.utc).timestamp() + dur_seconds
        unmute_at_iso = (datetime.datetime.now(pytz.utc) + datetime.timedelta(seconds=dur_seconds)).isoformat()

    data["mutes"][mute_id] = {
        "target_id": str(user.id),
        "moderator_id": str(ctx.author.id),
        "duration": dur_seconds,
        "duration_str": duration,
        "reason": reason,
        "start_ts": datetime.datetime.now(pytz.utc).isoformat(),
        "unmute_at": unmute_at_iso
    }
    data["rmute_usage"][str(ctx.author.id)] = data["rmute_usage"].get(str(ctx.author.id), 0) + 1
    data["rmute_active"][str(user.id)] = mute_id
    save_data(data)
    # DM and log
    rdm_users = data.get("rdm_users", {})
    if not rdm_users.get(str(user.id), False):
        try:
            await user.send(embed=build_mute_dm_embed(user, ctx.author, duration, reason))
        except Exception:
            pass
    if channel:
        embed = build_mute_log_embed(user, ctx.author, duration, reason, unmute_at=unmute_at_iso)
        await channel.send(embed=embed)
    if unmute_ts:
        await schedule_unmute(mute_id, unmute_ts)
    await ctx.message.delete()
    await ctx.send(f"Muted {user} successfully.", delete_after=8)

@bot.command(name="rmlb")
@commands.guild_only()
async def cmd_rmlb(ctx: commands.Context):
    """!rmlb - top 10 users who used rmute most"""
    if not is_mod(ctx):
        return await ctx.reply("You don't have permission.")
    data = load_data()
    usage = data.get("rmute_usage", {})
    sorted_usage = sorted(usage.items(), key=lambda kv: kv[1], reverse=True)[:10]
    description_lines = []
    for uid, count in sorted_usage:
        member = ctx.guild.get_member(int(uid))
        name = str(member) if member else f"User {uid}"
        description_lines.append(f"{name} — {count} rmutes")
    embed = discord.Embed(title="Top RMute Users", description="\n".join(description_lines) or "No data", color=discord.Color.purple())
    await ctx.send(embed=embed)

# ------------------ RDM (DM Opt-out) ------------------
@bot.command(name="rdm")
@commands.guild_only()
async def cmd_rdm(ctx: commands.Context):
    """Toggle opt-out from bot DMs"""
    data = load_data()
    uid = str(ctx.author.id)
    data["rdm_users"][uid] = not data.get("rdm_users", {}).get(uid, False)
    save_data(data)
    state = "opted out of DMs" if data["rdm_users"][uid] else "opted in to DMs"
    await ctx.reply(f"You have {state}.")

# ------------------ TIMETRACK & LEADERBOARDS ------------------
@bot.command(name="timetrack")
@commands.guild_only()
async def cmd_timetrack(ctx: commands.Context, member: Optional[discord.Member] = None):
    """!timetrack [user] - show timetrack for a user"""
    target = member or ctx.author
    data = load_data()
    u = data.get("users", {}).get(str(target.id))
    if not u:
        return await ctx.send("No timetrack data for that user.")
    embed = build_timetrack_embed(target, u)
    await ctx.send(embed=embed)

@bot.command(name="tlb")
@commands.guild_only()
async def cmd_tlb(ctx: commands.Context):
    """!tlb - timetrack leaderboard (members with RCACHE_ROLES)"""
    data = load_data()
    users = data.get("users", {})
    leaderboard = []
    for uid, u in users.items():
        member = ctx.guild.get_member(int(uid))
        if not member:
            continue
        if not any(r.id in RCACHE_ROLES for r in member.roles):
            continue
        avg = u.get("average_online", 0)
        leaderboard.append((member, avg))
    leaderboard.sort(key=lambda kv: kv[1], reverse=True)
    lines = []
    for member, avg in leaderboard[:10]:
        lines.append(f"{member.display_name} — {format_duration_seconds(int(avg))}")
    embed = discord.Embed(title="Timetrack Leaderboard", description="\n".join(lines) or "No data", color=discord.Color.gold())
    await ctx.send(embed=embed)

@bot.command(name="tdm")
@commands.guild_only()
async def cmd_tdm(ctx: commands.Context):
    """!tdm - timetrack for members WITHOUT specific roles; shows longest avg daily"""
    data = load_data()
    users = data.get("users", {})
    leaderboard = []
    for uid, u in users.items():
        member = ctx.guild.get_member(int(uid))
        if not member:
            continue
        # only include those who do NOT have RCACHE_ROLES
        if any(r.id in RCACHE_ROLES for r in member.roles):
            continue
        avg = u.get("average_online", 0)
        leaderboard.append((member, avg))
    leaderboard.sort(key=lambda kv: kv[1], reverse=True)
    lines = []
    for member, avg in leaderboard[:10]:
        lines.append(f"{member.display_name} — {format_duration_seconds(int(avg))}")
    embed = discord.Embed(title="Timetrack (Non-role) Leaderboard", description="\n".join(lines) or "No data", color=discord.Color.dark_gold())
    await ctx.send(embed=embed)

# ------------------ RCACHE (deleted message cache) ------------------
def has_rcache_role(member: discord.Member) -> bool:
    return any(r.id in RCACHE_ROLES for r in member.roles)

@bot.command(name="rcache")
@commands.guild_only()
async def cmd_rcache(ctx: commands.Context, message_id: Optional[int] = None):
    """!rcache [message_id] - show deleted message / attachments info"""
    if not has_rcache_role(ctx.author):
        return await ctx.reply("You do not have permission to use rcache.")
    data = load_data()
    if message_id:
        key = f"deleted_{message_id}"
        entry = data.get("images", {}).get(key)
        if not entry:
            return await ctx.send("No deleted message found with that ID.")
        e = discord.Embed(title="Deleted Message", description=entry.get("content") or "No content", color=discord.Color.blue())
        e.add_field(name="Author", value=entry.get("author_name"), inline=True)
        e.add_field(name="Channel", value=f"<#{entry.get('channel_id')}>", inline=True)
        if entry.get("attachments"):
            e.add_field(name="Attachments", value="\n".join(entry.get("attachments")), inline=False)
        await ctx.send(embed=e)
    else:
        # show recent deleted messages from cache
        recent_keys = [k for k in data.get("images", {}).keys() if k.startswith("deleted_")]
        preview = []
        for k in sorted(recent_keys, reverse=True)[:10]:
            v = data["images"][k]
            preview.append(f"{v.get('author_name')}: {v.get('content')[:120] if v.get('content') else '(attachment)'}")
        embed = discord.Embed(title="Recent Deleted Messages", description="\n".join(preview) or "No data", color=discord.Color.blue())
        await ctx.send(embed=embed)

# ------------------ STAFF PING SYSTEM ------------------
@bot.command(name="rping")
@commands.guild_only()
async def cmd_rping(ctx: commands.Context):
    """Ping staff role (ROLE: STAFF_PING_ROLE). Deletes invoking message and logs context if replying."""
    if not is_mod(ctx):
        return await ctx.reply("You don't have permission.")
    # build message
    role_mention = f"<@&{STAFF_PING_ROLE}>"
    reply_context = None
    if ctx.message.reference:
        try:
            ref = ctx.message.reference.resolved
            if isinstance(ref, discord.Message):
                reply_context = (ref.author, ref.content[:1000])
        except Exception:
            pass
    # send to configured channels
    for cid in STAFF_NOTIFY_CHANNELS:
        ch = bot.get_channel(cid)
        if ch:
            text = role_mention
            if reply_context:
                text += f"\n\nReply Context: {reply_context[0]} — {reply_context[1]}"
            await ch.send(text)
    try:
        await ctx.message.delete()
    except Exception:
        pass
    await ctx.send("Staff ping sent.", delete_after=5)

@bot.command(name="hsping")
@commands.guild_only()
async def cmd_hsping(ctx: commands.Context):
    """Higher staff ping"""
    if not is_mod(ctx):
        return await ctx.reply("You don't have permission.")
    role_mention = f"<@&{HIGHER_STAFF_PING_ROLE}>"
    reply_context = None
    if ctx.message.reference:
        try:
            ref = ctx.message.reference.resolved
            if isinstance(ref, discord.Message):
                reply_context = (ref.author, ref.content[:1000])
        except Exception:
            pass
    for cid in STAFF_NOTIFY_CHANNELS:
        ch = bot.get_channel(cid)
        if ch:
            text = role_mention
            if reply_context:
                text += f"\n\nReply Context: {reply_context[0]} — {reply_context[1]}"
            await ch.send(text)
    try:
        await ctx.message.delete()
    except Exception:
        pass
    await ctx.send("Higher staff ping sent.", delete_after=5)

# ------------------ PURGE COMMAND (with logging) ------------------
@bot.command(name="purge")
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
async def cmd_purge(ctx: commands.Context, limit: int = 10):
    # fetch messages to delete
    if limit <= 0 or limit > 200:
        return await ctx.reply("Limit must be between 1 and 200.")
    messages = await ctx.channel.history(limit=limit+1).flatten()
    # prepare preview
    preview = []
    for m in messages:
        if m.author and not m.author.bot:
            preview.append(f"{m.author}: {m.content[:200] if m.content else '(attachment)'}")
    # delete
    await ctx.channel.purge(limit=limit)
    # log to tracking channel
    channel = bot.get_channel(TRACK_CHANNEL_ID)
    when = format_time(datetime.datetime.now(pytz.utc))
    embed = build_purge_embed(ctx.author, ctx.channel, limit, preview, when)
    if channel:
        await channel.send(embed=embed)
    await ctx.send(f"Purged {limit} messages and logged it.", delete_after=8)

# ------------------ HELP ------------------
@bot.command(name="rhelp")
async def cmd_rhelp(ctx: commands.Context):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.blurple())
    embed.add_field(name="!timetrack [user]", value="Show timetrack for a user", inline=False)
    embed.add_field(name="!rmute [users...] [duration] [reason]", value="Mute multiple users (mod only). Duration format: 30s 10m 2h 1d", inline=False)
    embed.add_field(name="!runmute [user] [duration] [reason]", value="Mute single user (mod only).", inline=False)
    embed.add_field(name="!rmlb", value="Top rmute users", inline=False)
    embed.add_field(name="!rcache [message_id]", value="Show deleted messages / attachments (role-restricted).", inline=False)
    embed.add_field(name="!tlb", value="Timetrack leaderboard (role-filtered)", inline=False)
    embed.add_field(name="!tdm", value="Timetrack leaderboard (non-role)", inline=False)
    embed.add_field(name="!rping / !hsping", value="Ping staff roles (mod only).", inline=False)
    embed.add_field(name="!rdm", value="Toggle opt-out of bot DMs.", inline=False)
    embed.add_field(name="!purge [n]", value="Purge messages and log to tracking channel (mod only).", inline=False)
    await ctx.send(embed=embed)

# ------------------ EVENT: on_member_update (role changes) ------------------
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # detect role changes and log them
    try:
        data = load_data()
        before_roles = set(r.id for r in before.roles)
        after_roles = set(r.id for r in after.roles)
        added = after_roles - before_roles
        removed = before_roles - after_roles
        if added or removed:
            ch = bot.get_channel(TRACK_CHANNEL_ID)
            lines = []
            if added:
                lines.append("Added roles: " + ", ".join(str(x) for x in added))
            if removed:
                lines.append("Removed roles: " + ", ".join(str(x) for x in removed))
            msg = f"{after} roles updated: " + " | ".join(lines)
            if ch:
                await ch.send(msg)
    except Exception:
        traceback.print_exc()

# ------------------ Graceful shutdown saving ------------------
async def _shutdown_save():
    try:
        save_data(DATA)
    except Exception:
        pass

# ------------------ Run the bot ------------------
if __name__ == "__main__":
    try:
        safe_print("Starting bot...")
        bot.loop.run_until_complete(bot.start(TOKEN))
    except KeyboardInterrupt:
        safe_print("Shutting down...")
        bot.loop.run_until_complete(_shutdown_save())
    except Exception:
        traceback.print_exc()
