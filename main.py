# mega_timetrack_rmoderation_logging_bot.py
# Monolithic all-in-one bot implementing:
# - Timetrack (53s offline delay, multi-timezone last-online, daily/weekly/monthly stats, average forever)
# - Moderation: !rmute (multi-target, duration parsing, auto-unmute), !runmute, !rmlb, !rhelp
# - Logging: message edits, deletions, image/file caching, role/channel/webhook create/edit/delete
# - Purge detection attempt (detects mass deletions & attempts to attribute)
# - Cache viewing: !rcache (role gated)
# - Leaderboards: !tlb (timetrack leaderboard)
# - JSON persistence, rotating backups, command cooldowns, verbose embeds, ASCII progress bars
#
# Requirements:
# - Python 3.9+
# - discord.py 2.x
# - pytz
#
# Configure environment variable DISCORD_TOKEN with your bot token.

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

# Roles to watch/send notifications for (IDs provided)
TRACK_ROLES = [
    1410419924173848626,
    1410420126003630122,
    1410423594579918860,
    1410421466666631279,
    1410421647265108038,
    1410419345234067568,
    1410422029236047975,
    1410458084874260592
]

# Mute role ID
RMUTE_ROLE_ID = 1410423854563721287

# Roles allowed to use !rcache
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]

# Timers and intervals
OFFLINE_DELAY = 53                     # seconds until we consider a user offline
PRESENCE_CHECK_INTERVAL = 5            # how often we check presence
AUTO_SAVE_INTERVAL = 120               # seconds between automatic saves
BACKUP_DIR = "mega_bot_backups"
DATA_FILE = "mega_bot_data.json"
MAX_BACKUPS = 20
COMMAND_COOLDOWN = 4                   # seconds per user command cooldown

# ------------------ INTENTS ------------------
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.reactions = True
intents.webhooks = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Locks and helper state
data_lock = threading.Lock()
console_lock = threading.Lock()
command_cooldowns: Dict[int, float] = {}

# ------------------ SAFE PRINT ------------------
def safe_print(*args, **kwargs):
    with console_lock:
        print(*args, **kwargs)

# ------------------ PERSISTENCE ------------------
def init_data_structure() -> Dict[str, Any]:
    """Return default data layout."""
    return {
        "users": {},         # keyed by str(user_id)
        "mutes": {},         # keyed by mute_id
        "images": {},        # keyed by message_id or generated id
        "logs": {},          # generic logs keyed by ids
        "rmute_usage": {}    # who used rmute how many times
    }

def load_data() -> Dict[str, Any]:
    """Load JSON data from DATA_FILE, or return default structure."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            safe_print("⚠️ Failed to load data file:", e)
            # try rename corrupted file
            try:
                os.rename(DATA_FILE, DATA_FILE + ".corrupt")
            except:
                pass
            return init_data_structure()
    else:
        return init_data_structure()

def rotate_backups():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        files = sorted(os.listdir(BACKUP_DIR))
        while len(files) > MAX_BACKUPS:
            os.remove(os.path.join(BACKUP_DIR, files.pop(0)))
    except Exception as e:
        safe_print("⚠️ backup rotation failed:", e)

def save_data(data: Dict[str, Any]):
    """Save data to file and write a rotating backup copy."""
    with data_lock:
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(BACKUP_DIR, f"backup_{timestamp}.json")
            with open(backup_path, "w", encoding="utf-8") as bf:
                json.dump(data, bf, indent=2, default=str)
            # also write main file
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            rotate_backups()
        except Exception as e:
            safe_print("❌ Error saving data:", e)
            traceback.print_exc()

# ------------------ USER DATA HELPERS ------------------
def ensure_user_data(uid: str, data: Dict[str, Any]) -> None:
    """Ensure the user entry exists with full fields."""
    if uid not in data["users"]:
        data["users"][uid] = {
            "status": "offline",
            "online_time": None,
            "offline_time": None,
            "last_message": None,
            "last_edit": None,
            "last_delete": None,
            "last_online_times": {},        # dict tz_name -> timestamp string
            "offline_timer": 0,
            "total_online_seconds": 0,
            "daily_seconds": {},            # date -> seconds
            "weekly_seconds": {},           # weekkey -> seconds
            "monthly_seconds": {},          # monthkey -> seconds
            "average_online": 0.0,
            "notify": True
        }

def add_seconds_to_user(uid: str, seconds: int, data: Dict[str, Any]) -> None:
    ensure_user_data(uid, data)
    user = data["users"][uid]
    user["total_online_seconds"] = user.get("total_online_seconds", 0) + seconds
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    week = datetime.datetime.utcnow().strftime("%Y-W%U")
    month = datetime.datetime.utcnow().strftime("%Y-%m")
    user["daily_seconds"][today] = user["daily_seconds"].get(today, 0) + seconds
    user["weekly_seconds"][week] = user["weekly_seconds"].get(week, 0) + seconds
    user["monthly_seconds"][month] = user["monthly_seconds"].get(month, 0) + seconds
    total_time = user["total_online_seconds"]
    total_days = max(len(user["daily_seconds"]), 1)
    user["average_online"] = total_time / total_days

def tz_now_strings() -> Dict[str, str]:
    tzs = {
        "UTC": pytz.utc,
        "EST": pytz.timezone("US/Eastern"),
        "PST": pytz.timezone("US/Pacific"),
        "CET": pytz.timezone("Europe/Paris")
    }
    out = {}
    for name, tz in tzs.items():
        out[name] = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    return out

# ------------------ FORMAT HELPERS ------------------
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

def parse_duration_string_to_seconds(s: str) -> Optional[int]:
    return parse_duration(s)  # reuse below

def parse_duration(s: str) -> Optional[int]:
    """Accept strings like 10s, 5m, 2h, 1d. Return seconds or None."""
    if not s:
        return None
    pattern = re.compile(r"^(\d+)([smhd])$", re.I)
    m = pattern.match(s.strip())
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2).lower()
    if unit == "s":
        return value
    if unit == "m":
        return value * 60
    if unit == "h":
        return value * 3600
    if unit == "d":
        return value * 86400
    return None

# ------------------ EMBED BUILDERS ------------------
def build_timetrack_embed(member: discord.Member, user_data: Dict[str, Any]) -> discord.Embed:
    """Return a fancy embed showing all timetrack info for a user."""
    embed = discord.Embed(title=f"📊 Timetrack • {member.display_name}", color=discord.Color.blue())
    status = user_data.get("status", "offline")
    embed.add_field(name="Status", value=f"**{status}**", inline=True)
    embed.add_field(name="Online Since", value=user_data.get("online_time") or "N/A", inline=True)
    embed.add_field(name="Offline Since", value=user_data.get("offline_time") or "N/A", inline=True)
    embed.add_field(name="Last Message", value=user_data.get("last_message") or "N/A", inline=False)
    embed.add_field(name="Last Edit", value=user_data.get("last_edit") or "N/A", inline=False)
    embed.add_field(name="Last Delete", value=user_data.get("last_delete") or "N/A", inline=False)

    # last online times
    tz_map = user_data.get("last_online_times", {})
    tz_lines = []
    for tz in ("UTC", "EST", "PST", "CET"):
        tz_lines.append(f"{tz}: {tz_map.get(tz, 'N/A')}")
    embed.add_field(name="Last Online (4 timezones)", value="\n".join(tz_lines), inline=False)

    total = user_data.get("total_online_seconds", 0)
    avg = user_data.get("average_online", 0)
    embed.add_field(name="Total Online (forever)", value=format_duration_seconds(total), inline=True)
    embed.add_field(name="Average Daily Online", value=format_duration_seconds(int(avg)), inline=True)

    # today's activity
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    today_seconds = user_data.get("daily_seconds", {}).get(today, 0)
    progress = ascii_progress_bar(today_seconds, 3600)
    embed.add_field(name="Today", value=f"{progress}  ({today_seconds}s)", inline=False)

    embed.set_footer(text="Timetrack • offline timer: 53s • fancy embed")
    return embed

def build_mute_embed(target: discord.Member, moderator: discord.Member, duration_str: str, reason: str, unmute_time_str: str) -> discord.Embed:
    embed = discord.Embed(title="🔇 User Muted", color=discord.Color.orange())
    embed.add_field(name="User", value=f"{target} ({target.id})", inline=False)
    embed.add_field(name="Muted by", value=f"{moderator} ({moderator.id})", inline=False)
    embed.add_field(name="Duration", value=duration_str, inline=True)
    embed.add_field(name="Unmute at", value=unmute_time_str, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="Mute logged")
    return embed

def build_unmute_embed(target: discord.Member, moderator: Optional[discord.Member], reason: Optional[str], auto: bool=False) -> discord.Embed:
    title = "✅ Auto Unmute" if auto else "🔈 User Unmuted"
    embed = discord.Embed(title=title, color=discord.Color.green() if auto else discord.Color.blue())
    embed.add_field(name="User", value=f"{target} ({target.id})", inline=False)
    if moderator:
        embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="Unmute event")
    return embed

# ------------------ UTIL: CHECK TRACK ROLE ------------------
def has_any_track_role(member: discord.Member) -> bool:
    for r in member.roles:
        if r.id in TRACK_ROLES:
            return True
    return False

# ------------------ PRESENCE & TRACKING TASK ------------------
@tasks.loop(seconds=PRESENCE_CHECK_INTERVAL)
async def presence_tracker_task():
    """Periodically scan guild and update online/offline timers for tracked roles."""
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        channel = bot.get_channel(TRACK_CHANNEL_ID)
        if not channel:
            return
        data = load_data()
        now_utc = datetime.datetime.utcnow()
        # iterate members — only those in guild cache
        for member in guild.members:
            # only track members who have any TRACK_ROLES
            if not any(r.id in TRACK_ROLES for r in member.roles):
                continue
            uid = str(member.id)
            ensure_user_data(uid, data)
            user_entry = data["users"][uid]
            # if online (not offline)
            if member.status != discord.Status.offline:
                # user came online
                if user_entry.get("status") == "offline":
                    user_entry["status"] = "online"
                    user_entry["online_time"] = format_time(now_utc)
                    user_entry["offline_timer"] = 0
                    # update last online times
                    tzs = tz_now_strings()
                    user_entry["last_online_times"] = tzs
                    # notification
                    if user_entry.get("notify", True):
                        try:
                            await channel.send(f"✅ **{member.display_name}** is online")
                        except Exception:
                            pass
                # credit them PRESENCE_CHECK_INTERVAL seconds for being online
                add_seconds_to_user(uid, PRESENCE_CHECK_INTERVAL, data)
            else:
                # member is offline in presence
                if user_entry.get("status") == "online":
                    # increment a small offline timer — only after OFFLINE_DELAY do we mark offline
                    user_entry["offline_timer"] = user_entry.get("offline_timer", 0) + PRESENCE_CHECK_INTERVAL
                    if user_entry["offline_timer"] >= OFFLINE_DELAY:
                        user_entry["status"] = "offline"
                        user_entry["offline_time"] = format_time(now_utc)
                        user_entry["offline_timer"] = 0
                        # update last online timezone times
                        tzs = tz_now_strings()
                        user_entry["last_online_times"] = tzs
                        if user_entry.get("notify", True):
                            try:
                                await channel.send(f"❌ **{member.display_name}** is offline")
                            except Exception:
                                pass
        save_data(data)
    except Exception as e:
        safe_print("❌ Error in presence_tracker_task:", e)
        traceback.print_exc()

# ------------------ AUTO-SAVE TASK ------------------
@tasks.loop(seconds=AUTO_SAVE_INTERVAL)
async def auto_save_task():
    try:
        save_data(load_data())
        safe_print("💾 Auto-save complete.")
    except Exception as e:
        safe_print("❌ Auto-save failed:", e)
        traceback.print_exc()

# ------------------ BOT EVENTS: messages, edits, deletions ------------------
@bot.event
async def on_ready():
    safe_print(f"✅ Logged in as: {bot.user} • id: {bot.user.id}")
    presence_tracker_task.start()
    auto_save_task.start()
    safe_print("📡 Presence tracker & auto-save started.")

@bot.event
async def on_message(message: discord.Message):
    # ignore bots
    if message.author.bot:
        return
    data = load_data()
    uid = str(message.author.id)
    ensure_user_data(uid, data)
    # store last message content and timestamp
    data["users"][uid]["last_message"] = (message.content or "")[:1900]
    data["users"][uid]["last_message_time"] = format_time(datetime.datetime.utcnow())
    # cache attachments
    if message.attachments:
        attachments = []
        for a in message.attachments:
            attachments.append(a.url)
        # store images under message.id
        data["images"][str(message.id)] = {
            "author": message.author.id,
            "time": format_time(datetime.datetime.utcnow()),
            "attachments": attachments,
            "content": (message.content or "")[:1900]
        }
    save_data(data)
    # process commands
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author.bot:
        return
    data = load_data()
    uid = str(after.author.id)
    ensure_user_data(uid, data)
    data["users"][uid]["last_edit"] = (after.content or "")[:1900]
    data["users"][uid]["last_edit_time"] = format_time(datetime.datetime.utcnow())
    # log it
    # create a small log entry
    data["logs"].setdefault("edits", []).append({
        "message_id": after.id,
        "author": after.author.id,
        "before": (before.content or "")[:1900],
        "after": (after.content or "")[:1900],
        "time": format_time(datetime.datetime.utcnow())
    })
    save_data(data)

@bot.event
async def on_message_delete(message: discord.Message):
    # called when a single message is deleted
    if message.author and message.author.bot:
        return
    data = load_data()
    try:
        attachments = [a.url for a in message.attachments] if message.attachments else []
        data["images"][str(message.id)] = {
            "author": message.author.id if message.author else None,
            "time": format_time(datetime.datetime.utcnow()),
            "attachments": attachments,
            "content": (message.content or "")[:1900],
            "deleted_by": None  # will be filled if we detect who deleted it
        }
        data["logs"].setdefault("deletions", []).append({
            "message_id": message.id,
            "author": message.author.id if message.author else None,
            "content": (message.content or "")[:1900],
            "attachments": attachments,
            "time": format_time(datetime.datetime.utcnow())
        })
    except Exception as e:
        safe_print("⚠️ on_message_delete error:", e)
    save_data(data)

# ------------------ PURGE DETECTION (BOT PURGES / MASS DELETIONS) ------------------
# We cannot always detect who purged via normal events. But we can hook BulkMessageDeleteEvent
@bot.event
async def on_bulk_message_delete(messages: List[discord.Message]):
    # messages is a list of Message objects that were bulk-deleted
    data = load_data()
    try:
        # message.guild can be None; use first message to get guild
        guild = None
        if messages:
            guild = messages[0].guild
        channel = bot.get_channel(TRACK_CHANNEL_ID)
        # Build a summary embed listing messages that were removed
        embed = discord.Embed(title="🗑️ Bulk Delete Detected", color=discord.Color.dark_red())
        embed.add_field(name="Count", value=str(len(messages)), inline=False)
        # We'll show up to 10 items to avoid huge embeds
        preview = []
        for m in messages[:10]:
            author = m.author
            content = (m.content or "")[:200]
            preview.append(f"• {author.display_name if author else 'Unknown'}: {content}")
            # Cache attachments if present
            if m.attachments:
                attachments = [a.url for a in m.attachments]
                data["images"][str(m.id)] = {
                    "author": author.id if author else None,
                    "time": format_time(datetime.datetime.utcnow()),
                    "attachments": attachments,
                    "content": content,
                    "deleted_by": None,
                    "bulk_deleted": True
                }
            else:
                data["images"][str(m.id)] = {
                    "author": author.id if author else None,
                    "time": format_time(datetime.datetime.utcnow()),
                    "attachments": [],
                    "content": content,
                    "deleted_by": None,
                    "bulk_deleted": True
                }
        if preview:
            embed.add_field(name="Preview", value="\n".join(preview), inline=False)
        embed.set_footer(text="Bulk delete detected — message authors and small preview")
        if channel:
            await channel.send(embed=embed)
    except Exception as e:
        safe_print("⚠️ on_bulk_message_delete error:", e)
        traceback.print_exc()
    save_data(data)

# ------------------ MODERATION COMMANDS ------------------
# NOTE: rmute supports multiple targets; rmute usage logged per moderator in rmute_usage
@bot.command(name="rmute", help="Mute one or more users. Usage: !rmute @user1 @user2 10m [reason]")
async def cmd_rmute(ctx: commands.Context, targets: commands.Greedy[discord.Member], duration: str, *, reason: str = "No reason provided"):
    if not can_execute_command(ctx.author.id):
        await ctx.send("⌛ Command cooldown active. Wait a moment and try again.")
        return
    seconds = parse_duration(duration)
    if seconds is None:
        await ctx.send("❌ Invalid duration format. Use numbers + s/m/h/d (e.g. 10m, 2h).")
        return
    data = load_data()
    channel = bot.get_channel(TRACK_CHANNEL_ID)
    if not targets:
        await ctx.send("❌ Please mention at least one user to mute.")
        return
    # delete invoking message (instant)
    try:
        await ctx.message.delete()
    except Exception:
        pass
    for target in targets:
        try:
            # apply mute role
            mute_role = ctx.guild.get_role(RMUTE_ROLE_ID)
            if mute_role is None:
                await ctx.send("⚠️ Mute role not found on server.")
                return
            await target.add_roles(mute_role, reason=f"rmute by {ctx.author} reason: {reason}")
            # create mute record
            mute_id = f"{target.id}_{int(datetime.datetime.utcnow().timestamp())}"
            unmute_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)
            data["mutes"][mute_id] = {
                "user": target.id,
                "moderator": ctx.author.id,
                "reason": reason,
                "duration_seconds": seconds,
                "start_utc": format_time(datetime.datetime.utcnow()),
                "unmute_utc": format_time(unmute_at),
                "auto": True
            }
            # increment usage for leaderboard
            data["rmute_usage"][str(ctx.author.id)] = data.get("rmute_usage", {}).get(str(ctx.author.id), 0) + 1
            save_data(data)
            # send embed notification to TRACK_CHANNEL_ID
            if channel:
                embed = build_mute_embed(target, ctx.author, duration, reason, format_time(unmute_at))
                await channel.send(embed=embed)
            # schedule auto unmute
            async def schedule_unmute(mute_record_id: str, user_id: int, seconds_left: int):
                try:
                    await asyncio.sleep(seconds_left)
                    g = bot.get_guild(GUILD_ID)
                    if not g:
                        return
                    member = g.get_member(user_id)
                    if member:
                        mute_role_inner = g.get_role(RMUTE_ROLE_ID)
                        if mute_role_inner in member.roles:
                            await member.remove_roles(mute_role_inner, reason="Auto unmute after rmute duration")
                            # log auto-unmute
                            data_local = load_data()
                            # remove mute record
                            if mute_record_id in data_local.get("mutes", {}):
                                data_local["mutes"].pop(mute_record_id, None)
                            save_data(data_local)
                            c = bot.get_channel(TRACK_CHANNEL_ID)
                            if c:
                                embed_un = build_unmute_embed(member, None, reason, auto=True)
                                await c.send(embed=embed_un)
                except Exception as e:
                    safe_print("❌ schedule_unmute error:", e)
                    traceback.print_exc()
            bot.loop.create_task(schedule_unmute(mute_id, target.id, seconds))
        except Exception as e:
            safe_print("❌ rmute per-target error:", e)
            traceback.print_exc()
    # final save
    save_data(data)

@bot.command(name="runmute", help="Runmute: similar to rmute but logs differently. Usage: !runmute @user 10m [reason]")
async def cmd_runmute(ctx: commands.Context, target: discord.Member, duration: str, *, reason: str = "No reason provided"):
    if not can_execute_command(ctx.author.id):
        await ctx.send("⌛ Command cooldown active.")
        return
    seconds = parse_duration(duration)
    if seconds is None:
        await ctx.send("❌ Invalid duration.")
        return
    data = load_data()
    try:
        mute_role = ctx.guild.get_role(RMUTE_ROLE_ID)
        if mute_role is None:
            await ctx.send("⚠️ Mute role not configured.")
            return
        await target.add_roles(mute_role, reason=f"runmute by {ctx.author}")
        mute_id = f"runmute_{target.id}_{int(datetime.datetime.utcnow().timestamp())}"
        unmute_at = datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)
        data["mutes"][mute_id] = {
            "user": target.id,
            "moderator": ctx.author.id,
            "duration_seconds": seconds,
            "start_utc": format_time(datetime.datetime.utcnow()),
            "unmute_utc": format_time(unmute_at),
            "reason": reason,
            "auto": True
        }
        save_data(data)
        c = bot.get_channel(TRACK_CHANNEL_ID)
        if c:
            embed = build_mute_embed(target, ctx.author, duration, reason, format_time(unmute_at))
            await c.send(embed=embed)
        # schedule unmute same as rmute
        async def runmute_unmute(mute_record_id: str, user_id: int, seconds_left: int):
            await asyncio.sleep(seconds_left)
            g = bot.get_guild(GUILD_ID)
            if not g:
                return
            member = g.get_member(user_id)
            if member:
                role = g.get_role(RMUTE_ROLE_ID)
                if role in member.roles:
                    await member.remove_roles(role, reason="Auto-unmute runmute")
                    data_local = load_data()
                    if mute_record_id in data_local.get("mutes", {}):
                        data_local["mutes"].pop(mute_record_id, None)
                    save_data(data_local)
                    ch = bot.get_channel(TRACK_CHANNEL_ID)
                    if ch:
                        await ch.send(embed=build_unmute_embed(member, None, reason, auto=True))
        bot.loop.create_task(runmute_unmute(mute_id, target.id, seconds))
    except Exception as e:
        safe_print("❌ runmute error:", e)
        traceback.print_exc()

@bot.command(name="rmlb", help="Show top 10 users by rmute usage")
async def cmd_rmlb(ctx: commands.Context):
    data = load_data()
    usage = data.get("rmute_usage", {})
    sorted_usage = sorted(usage.items(), key=lambda kv: kv[1], reverse=True)[:10]
    embed = discord.Embed(title="🏆 RMute Leaderboard", color=discord.Color.gold())
    for uid, count in sorted_usage:
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else f"User ID {uid}"
        embed.add_field(name=name, value=f"Mutes used: {count}", inline=False)
    await ctx.send(embed=embed)

# ------------------ CACHE VIEW (rcache) ------------------
@bot.command(name="rcache", help="Show cached deleted images/files (restricted roles only)")
async def cmd_rcache(ctx: commands.Context):
    # role gated
    if not any(r.id in RCACHE_ROLES for r in ctx.author.roles):
        await ctx.send("❌ You do not have permission to use this command.")
        return
    data = load_data()
    images = data.get("images", {})
    embed = discord.Embed(title="🗄️ Deleted Images/Files Cache", color=discord.Color.purple())
    count = 0
    for mid, info in list(images.items()):
        if count >= 25:
            break
        author = ctx.guild.get_member(info.get("author")) if info.get("author") else None
        author_name = author.display_name if author else str(info.get("author"))
        attachments = info.get("attachments", [])
        attachments_str = "\n".join(attachments) if attachments else "None"
        content = (info.get("content") or "")[:500]
        deleted_by = info.get("deleted_by", None)
        embed.add_field(
            name=f"Message {mid} — by {author_name}",
            value=f"Time: {info.get('time')}\nDeleted by: {deleted_by}\nAttachments:\n{attachments_str}\nContent: {content}",
            inline=False
        )
        count += 1
    if count == 0:
        embed.description = "No cached deleted images/files."
    await ctx.send(embed=embed)

# ------------------ TIMETRACK LEADERBOARD (tlb) ------------------
@bot.command(name="tlb", help="Show timetrack leaderboard")
async def cmd_tlb(ctx: commands.Context):
    data = load_data()
    users = data.get("users", {})
    ranking = sorted(users.items(), key=lambda kv: kv[1].get("total_online_seconds", 0), reverse=True)[:15]
    embed = discord.Embed(title="📊 Timetrack Leaderboard", color=discord.Color.green())
    for uid, ud in ranking:
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else f"User ID {uid}"
        total = ud.get("total_online_seconds", 0)
        embed.add_field(name=name, value=f"Total Online: {format_duration_seconds(total)}", inline=False)
    await ctx.send(embed=embed)

# ------------------ RHELP ------------------
@bot.command(name="rhelp", help="Show commands and usage")
async def cmd_rhelp(ctx: commands.Context):
    embed = discord.Embed(title="🤖 RHelp — Commands", color=discord.Color.blue())
    embed.add_field(name="!timetrack [user]", value="Show timetrack info for a user (or yourself).", inline=False)
    embed.add_field(name="!rmute @u1 @u2 <duration> [reason]", value="Mute one or multiple users. Duration format: 10s, 5m, 2h, 1d", inline=False)
    embed.add_field(name="!runmute @u <duration> [reason]", value="Runmute (alternative) with logging", inline=False)
    embed.add_field(name="!rmlb", value="Show top 10 mute-invokers", inline=False)
    embed.add_field(name="!rcache", value="Show cached deleted images/files (restricted)", inline=False)
    embed.add_field(name="!tlb", value="Show timetrack leaderboard", inline=False)
    await ctx.send(embed=embed)

# ------------------ SIMPLE TIMETRACK COMMAND ------------------
@bot.command(name="timetrack", help="Show timetrack details for a user.")
async def cmd_timetrack(ctx: commands.Context, member: Optional[discord.Member] = None):
    target = member or ctx.author
    data = load_data()
    uid = str(target.id)
    ensure_user_data(uid, data)
    embed = build_timetrack_embed(target, data["users"][uid])
    await ctx.send(embed=embed)

# alias
@bot.command(name="tt")
async def cmd_tt(ctx: commands.Context, member: Optional[discord.Member] = None):
    await cmd_timetrack(ctx, member)

# ------------------ ROLE/CHANNEL/WEBHOOK LOGGING EVENTS ------------------
@bot.event
async def on_guild_role_create(role: discord.Role):
    try:
        data = load_data()
        data["logs"].setdefault("role_create", []).append({
            "role_id": role.id,
            "role_name": role.name,
            "permissions": str(role.permissions),
            "time": format_time(datetime.datetime.utcnow())
        })
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_guild_role_create error:", e)

@bot.event
async def on_guild_role_delete(role: discord.Role):
    try:
        data = load_data()
        data["logs"].setdefault("role_delete", []).append({
            "role_id": role.id,
            "role_name": role.name,
            "time": format_time(datetime.datetime.utcnow())
        })
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_guild_role_delete error:", e)

@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    try:
        data = load_data()
        data["logs"].setdefault("role_update", []).append({
            "role_id": after.id,
            "before": before.name,
            "after": after.name,
            "before_perms": str(before.permissions),
            "after_perms": str(after.permissions),
            "time": format_time(datetime.datetime.utcnow())
        })
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_guild_role_update error:", e)

@bot.event
async def on_guild_channel_create(channel: discord.abc.GuildChannel):
    try:
        data = load_data()
        data["logs"].setdefault("channel_create", []).append({
            "channel_id": channel.id,
            "name": channel.name,
            "type": str(channel.type),
            "time": format_time(datetime.datetime.utcnow())
        })
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_guild_channel_create error:", e)

@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    try:
        data = load_data()
        data["logs"].setdefault("channel_delete", []).append({
            "channel_id": channel.id,
            "name": channel.name,
            "type": str(channel.type),
            "time": format_time(datetime.datetime.utcnow())
        })
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_guild_channel_delete error:", e)

@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    try:
        data = load_data()
        data["logs"].setdefault("channel_update", []).append({
            "channel_id": after.id,
            "before_name": before.name,
            "after_name": after.name,
            "time": format_time(datetime.datetime.utcnow())
        })
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_guild_channel_update error:", e)

@bot.event
async def on_webhooks_update(channel: discord.abc.GuildChannel):
    try:
        data = load_data()
        data["logs"].setdefault("webhook_update", []).append({
            "channel_id": channel.id,
            "channel_name": getattr(channel, "name", str(channel)),
            "time": format_time(datetime.datetime.utcnow())
        })
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_webhooks_update error:", e)

# ------------------ MESSAGE PURGE LOGGING (DETECTION ATTEMPT) ------------------
# When mass deletions happen, bots often receive bulk delete events. This tries to log them.
# It is not always possible to know who initiated the purge (Discord audit logs are the best source).
@bot.command(name="rpurge", help="(Admin) Check recent purge logs. Try to attribute a recent bulk delete.")
@commands.has_permissions(manage_messages=True)
async def cmd_rpurge(ctx: commands.Context):
    # This command scans recent logs for bulk deletes and prints cached info
    data = load_data()
    deletions = data.get("logs", {}).get("deletions", [])[-50:]
    embed = discord.Embed(title="🧾 Recent Deletions (cached)", color=discord.Color.dark_red())
    if not deletions:
        embed.description = "No cached deletions found."
        await ctx.send(embed=embed)
        return
    for d in deletions[-10:]:
        content = (d.get("content") or "")[:200]
        embed.add_field(name=f"Msg {d.get('message_id')} by {d.get('author')}", value=f"{content}\nTime: {d.get('time')}", inline=False)
    await ctx.send(embed=embed)

# ------------------ MISC HELPERS: DEBUG & DUMP ------------------
@bot.command(name="rdump", help="(Admin) Dump the saved JSON data (for debugging)")
@commands.has_permissions(administrator=True)
async def cmd_rdump(ctx: commands.Context):
    data = load_data()
    # write to a temp file and attach
    path = "rdump.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    await ctx.send("📦 Data dump:", file=discord.File(path))
    try:
        os.remove(path)
    except:
        pass

# ------------------ AUTOMATIC CLEANUP TASKS ------------------
@tasks.loop(hours=24)
async def daily_reset_tasks():
    # optional: do weekly/monthly resets or archive old days
    try:
        data = load_data()
        # Example: prune daily_seconds older than 90 days
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=90)
        cutoff_key = cutoff.strftime("%Y-%m-%d")
        for uid, u in data.get("users", {}).items():
            daily = u.get("daily_seconds", {})
            keys_to_remove = []
            for day in daily.keys():
                try:
                    dt = datetime.datetime.strptime(day, "%Y-%m-%d")
                    if dt < cutoff:
                        keys_to_remove.append(day)
                except:
                    pass
            for k in keys_to_remove:
                daily.pop(k, None)
        save_data(data)
    except Exception as e:
        safe_print("⚠️ daily_reset_tasks error:", e)

# ------------------ START & RUN BOT ------------------
if __name__ == "__main__":
    try:
        safe_print("🚀 Starting mega bot...")
        # create example data file if missing
        if not os.path.exists(DATA_FILE):
            save_data(init_data_structure())
        bot.run(TOKEN)
    except Exception as e:
        safe_print("❌ Fatal error running bot:", e)
        traceback.print_exc()
