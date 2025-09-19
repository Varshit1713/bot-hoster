# mega_merged_bot.py
# Merged bot: pastefy base (user-provided) + requested features:
# - Timetrack loop (60s)
# - RMute (multi), Runmute (unmute), auto-unmute loop
# - rmlb, rcache, tlb, tdm leaderboards
# - rping/hsping (ping members, log reply content)
# - rdm opt-out, fancy embeds, audit reconciliation on start
# - Flask keep-alive, safe persistence
#
# Requirements: discord.py 2.x, pytz, Flask
# Set DISCORD_TOKEN environment variable.

import os, io, json, threading, traceback, re
import datetime, pytz, tempfile
from typing import Optional, List, Dict, Any
import discord
from discord.ext import commands, tasks
from flask import Flask

# ---------------- CONFIG ----------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Please set DISCORD_TOKEN environment variable")

GUILD_ID = 140335996236909773
TRACK_CHANNEL_ID = 1410458084874260592

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

RMUTE_ROLE_ID = 1410423854563721287
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]

OFFLINE_DELAY = 53
PRESENCE_CHECK_INTERVAL = 60
AUTO_SAVE_INTERVAL = 120

DATA_FILE = "mega_bot_data.json"
BACKUP_DIR = "mega_bot_backups"
MAX_BACKUPS = 20
COMMAND_COOLDOWN = 4
AUDIT_LOOKBACK_SECONDS = 3600

STAFF_LOG_CHANNELS = [TRACK_CHANNEL_ID]
DANGEROUS_LOG_USERS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

# ---------------- BOT ----------------
intents = discord.Intents.all()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

file_lock = threading.Lock()
console_lock = threading.Lock()

def safe_print(*args, **kwargs):
    with console_lock:
        print(*args, **kwargs)

# ---------------- PERSISTENCE ----------------
def init_data_structure() -> Dict[str, Any]:
    return {
        "users": {},
        "mutes": {},
        "images": {},
        "logs": {},
        "rmute_usage": {},
        "last_audit_check": None,
        "rping_disabled_users": {},
        "rdm_users": []
    }

def load_data() -> Dict[str, Any]:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            safe_print("âš ï¸ Failed to load data file:", e)
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
        safe_print("âš ï¸ backup rotation error:", e)

def save_data(data: Dict[str, Any]):
    with file_lock:
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            backup = os.path.join(BACKUP_DIR, f"backup_{ts}.json")
            with open(backup, "w", encoding="utf-8") as bf:
                json.dump(data, bf, indent=2, default=str)
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
            rotate_backups()
        except Exception as e:
            safe_print("âŒ Error saving data:", e)
            traceback.print_exc()

# ---------------- USER HELPERS ----------------
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
            "notify": True
        }

def add_seconds_to_user(uid: str, seconds: int, data: Dict[str, Any]) -> None:
    ensure_user_data(uid, data)
    u = data["users"][uid]
    u["total_online_seconds"] = u.get("total_online_seconds", 0) + seconds
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    week = datetime.datetime.utcnow().strftime("%Y-W%U")
    month = datetime.datetime.utcnow().strftime("%Y-%m")
    u["daily_seconds"][today] = u["daily_seconds"].get(today, 0) + seconds
    u["weekly_seconds"][week] = u["weekly_seconds"].get(week, 0) + seconds
    u["monthly_seconds"][month] = u["monthly_seconds"].get(month, 0) + seconds
    total_time = u["total_online_seconds"]
    total_days = max(len(u["daily_seconds"]), 1)
    u["average_online"] = total_time / total_days

# ---------------- TIME HELPERS ----------------
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
    return dt.strftime("%Y-%m-%d %H:%M:%S")

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
    if s is None:
        return None
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    total = 0
    parts = re.findall(r"(\d+)([smhd])", s)
    if not parts:
        return None
    for amount, unit in parts:
        a = int(amount)
        if unit == "s": total += a
        elif unit == "m": total += a * 60
        elif unit == "h": total += a * 3600
        elif unit == "d": total += a * 86400
    return total

def ascii_progress_bar(current: int, total: int, length: int = 20) -> str:
    try:
        ratio = min(max(float(current) / float(total), 0.0), 1.0)
        filled = int(length * ratio)
        return "â–ˆ" * filled + "â–‘" * (length - filled)
    except:
        return "â–‘" * length

# ---------------- EMBEDS ----------------
def build_timetrack_embed(member: discord.Member, user_data: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(title=f"ðŸ“Š Timetrack â€” {member.display_name}", color=discord.Color.blue())
    embed.add_field(name="Status", value=f\"**{user_data.get('status','offline')}**\", inline=True)
    embed.add_field(name="Online Since", value=user_data.get("online_time") or "N/A", inline=True)
    embed.add_field(name="Offline Since", value=user_data.get("offline_time") or "N/A", inline=True)
    embed.add_field(name="Last Message", value=user_data.get("last_message") or "N/A", inline=False)
    embed.add_field(name="Last Edit", value=user_data.get("last_edit") or "N/A", inline=False)
    embed.add_field(name="Last Delete", value=user_data.get("last_delete") or "N/A", inline=False)
    tz_map = user_data.get("last_online_times", {})
    tz_lines = [f"{tz}: {tz_map.get(tz,'N/A')}" for tz in ("UTC", "EST", "PST", "CET")]
    embed.add_field(name="Last Online (4 TZ)", value="\\n".join(tz_lines), inline=False)
    total = user_data.get("total_online_seconds", 0)
    avg = int(user_data.get("average_online", 0))
    embed.add_field(name="Total Online (forever)", value=format_duration_seconds(total), inline=True)
    embed.add_field(name="Average Daily Online", value=format_duration_seconds(avg), inline=True)
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    todays = user_data.get("daily_seconds", {}).get(today, 0)
    embed.add_field(name="Today's Activity", value=f"{ascii_progress_bar(todays,3600)} ({todays}s)", inline=False)
    embed.set_footer(text="Timetrack â€¢ offline-delay 53s")
    return embed

def build_mute_dm_embed(target: discord.Member, moderator: discord.Member, duration_str: Optional[str], reason: str, auto: bool = False) -> discord.Embed:
    title = "ðŸ”‡ You've been muted" if not auto else "ðŸ”‡ You were auto-muted"
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
    embed = discord.Embed(title="ðŸ”‡ Mute Log", color=discord.Color.orange())
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
    title = "âœ… Auto Unmute" if auto else "ðŸ”ˆ Unmute Log"
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
    embed = discord.Embed(title="ðŸ—‘ï¸ Purge Detected", color=discord.Color.dark_red())
    embed.add_field(name="Channel", value=f"{channel.mention} ({channel.id})", inline=False)
    if actor:
        embed.add_field(name="Purged by", value=f"{actor} ({actor.id})", inline=True)
    else:
        embed.add_field(name="Purged by", value="Unknown / could be bot", inline=True)
    embed.add_field(name="Message count", value=str(count), inline=True)
    if preview:
        embed.add_field(name="Preview", value="\\n".join(preview[:10]), inline=False)
    embed.set_footer(text=f"Purge at {when}")
    return embed

# ---------------- COOLDOWN ----------------
command_cooldowns: Dict[int, float] = {}
def can_execute_command(user_id: int) -> bool:
    last = command_cooldowns.get(user_id, 0.0)
    now = datetime.datetime.utcnow().timestamp()
    if now - last >= COMMAND_COOLDOWN:
        command_cooldowns[user_id] = now
        return True
    return False

# ---------------- ATTACHMENT DOWNLOAD ----------------
async def download_attachment_to_file(attachment: discord.Attachment) -> Optional[discord.File]:
    try:
        data = await attachment.read()
        bio = io.BytesIO(data)
        bio.seek(0)
        filename = attachment.filename or "file"
        return discord.File(bio, filename=filename)
    except Exception:
        return None

# ---------------- PRESENCE TRACKER ----------------
@tasks.loop(seconds=PRESENCE_CHECK_INTERVAL)
async def presence_tracker_task():
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        channel = bot.get_channel(TRACK_CHANNEL_ID)
        if not channel:
            return
        data = load_data()
        now_utc = datetime.datetime.utcnow()
        for member in guild.members:
            try:
                if not any(r.id in TRACK_ROLES for r in member.roles):
                    continue
            except Exception:
                continue
            uid = str(member.id)
            ensure_user_data(uid, data)
            u = data["users"][uid]
            if member.status != discord.Status.offline:
                if u.get("status") == "offline":
                    u["status"] = "online"
                    u["online_time"] = format_time(now_utc)
                    u["offline_timer"] = 0
                    u["last_online_times"] = tz_now_strings()
                    if u.get("notify", True):
                        recipient_id = member.id
                        rping_disabled = data.get("rping_disabled_users", {}).get(str(recipient_id), False)
                        mention = member.mention if not rping_disabled else member.display_name
                        try:
                            await channel.send(f"âœ… {mention} is online")
                        except:
                            pass
                add_seconds_to_user(uid, PRESENCE_CHECK_INTERVAL, data)
            else:
                if u.get("status") == "online":
                    u["offline_timer"] = u.get("offline_timer", 0) + PRESENCE_CHECK_INTERVAL
                    if u["offline_timer"] >= OFFLINE_DELAY:
                        u["status"] = "offline"
                        u["offline_time"] = format_time(now_utc)
                        u["offline_timer"] = 0
                        u["last_online_times"] = tz_now_strings()
                        if u.get("notify", True):
                            recipient_id = member.id
                            rping_disabled = data.get("rping_disabled_users", {}).get(str(recipient_id), False)
                            mention = member.mention if not rping_disabled else member.display_name
                            try:
                                await channel.send(f"âŒ {mention} is offline")
                            except:
                                pass
        save_data(data)
    except Exception as e:
        safe_print("âŒ presence_tracker_task error:", e)
        traceback.print_exc()

# ---------------- AUTO SAVE ----------------
@tasks.loop(seconds=AUTO_SAVE_INTERVAL)
async def auto_save_task():
    try:
        save_data(load_data())
        safe_print("ðŸ’¾ Auto-saved data.")
    except Exception as e:
        safe_print("âŒ auto_save_task error:", e)
        traceback.print_exc()

# ---------------- AUDIT RECONCILE ON START ----------------
async def reconcile_audit_logs_on_start():
    try:
        data = load_data()
        guild = bot.get_guild(GUILD_ID)
        channel = bot.get_channel(TRACK_CHANNEL_ID)
        if not guild or not channel:
            return
        last_check_iso = data.get("last_audit_check")
        now = datetime.datetime.utcnow()
        lookback_since = now - datetime.timedelta(seconds=AUDIT_LOOKBACK_SECONDS)
        if last_check_iso:
            try:
                last_check_dt = datetime.datetime.fromisoformat(last_check_iso)
            except:
                last_check_dt = lookback_since
        else:
            last_check_dt = lookback_since
        actions_to_check = [
            discord.AuditLogAction.role_create,
            discord.AuditLogAction.role_delete,
            discord.AuditLogAction.role_update,
            discord.AuditLogAction.channel_create,
            discord.AuditLogAction.channel_delete,
            discord.AuditLogAction.channel_update,
            discord.AuditLogAction.message_bulk_delete,
            discord.AuditLogAction.member_role_update
        ]
        for action in actions_to_check:
            try:
                async for entry in guild.audit_logs(limit=50, action=action):
                    if entry.created_at.replace(tzinfo=None) < last_check_dt:
                        break
                    if entry.action == discord.AuditLogAction.message_bulk_delete:
                        actor = entry.user
                        when = entry.created_at.strftime("%Y-%m-%d %H:%M:%S")
                        emb = discord.Embed(title="ðŸ—‘ï¸ Missed Bulk Delete (while offline)", color=discord.Color.dark_red())
                        emb.add_field(name="Possible actor", value=f"{actor} ({actor.id})", inline=False)
                        emb.add_field(name="When", value=when, inline=False)
                        emb.set_footer(text="Audit logs suggest a bulk delete occurred while bot was offline.")
                        try:
                            await channel.send(embed=emb)
                        except:
                            pass
                    elif entry.action == discord.AuditLogAction.member_role_update:
                        target = entry.target
                        actor = entry.user
                        changes = entry.changes
                        emb = discord.Embed(title="ðŸ›¡ï¸ Missed Member Role Update", color=discord.Color.orange())
                        emb.add_field(name="Member", value=f"{target} ({getattr(target,'id',str(target))})", inline=False)
                        emb.add_field(name="Actor", value=f"{actor} ({actor.id})", inline=False)
                        emb.add_field(name="Changes", value=str(changes), inline=False)
                        emb.set_footer(text=f"At {entry.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
                        try:
                            await channel.send(embed=emb)
                        except:
                            pass
                    elif entry.action in (discord.AuditLogAction.role_update, discord.AuditLogAction.role_create, discord.AuditLogAction.role_delete):
                        actor = entry.user
                        target = entry.target
                        emb = discord.Embed(title="âš™ï¸ Missed Role Audit", color=discord.Color.orange())
                        emb.add_field(name="Action", value=str(entry.action), inline=True)
                        emb.add_field(name="Role", value=f"{target} ({getattr(target,'id',str(target))})", inline=True)
                        emb.add_field(name="Actor", value=f"{actor} ({actor.id})", inline=False)
                        emb.add_field(name="Changes", value=str(entry.changes), inline=False)
                        emb.set_footer(text=f"At {entry.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
                        try:
                            await channel.send(embed=emb)
                        except:
                            pass
                    elif entry.action in (discord.AuditLogAction.channel_update, discord.AuditLogAction.channel_create, discord.AuditLogAction.channel_delete):
                        actor = entry.user
                        target = entry.target
                        emb = discord.Embed(title="ðŸ“¢ Missed Channel Audit", color=discord.Color.blurple())
                        emb.add_field(name="Action", value=str(entry.action), inline=True)
                        emb.add_field(name="Channel", value=f"{target} ({getattr(target,'id',str(target))})", inline=True)
                        emb.add_field(name="Actor", value=f"{actor} ({actor.id})", inline=False)
                        emb.add_field(name="Changes", value=str(entry.changes), inline=False)
                        emb.set_footer(text=f"At {entry.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
                        try:
                            await channel.send(embed=emb)
                        except:
                            pass
            except Exception as e:
                safe_print("âš ï¸ audit log scanning error for action", action, e)
        data["last_audit_check"] = datetime.datetime.utcnow().isoformat()
        save_data(data)
    except Exception as e:
        safe_print("âŒ reconcile_audit_logs_on_start error:", e)
        traceback.print_exc()

# ---------------- EVENTS READY/MSG/EDIT/DELETE/BULK ----------------
@bot.event
async def on_ready():
    safe_print(f"âœ… Logged in as: {bot.user} ({bot.user.id})")
    try: presence_tracker_task.start()
    except: pass
    try: auto_save_task.start()
    except: pass
    try: auto_unmute_loop.start()
    except: pass
    try:
        await reconcile_audit_logs_on_start()
    except Exception as e:
        safe_print("âš ï¸ reconcile audit on start failed:", e)
    safe_print("ðŸ“¡ Presence tracker, auto-save & auto-unmute started.")

@bot.event
async def on_message(message: discord.Message):
    if message.author and message.author.bot:
        return
    data = load_data()
    if message.author:
        uid = str(message.author.id)
        ensure_user_data(uid, data)
        data["users"][uid]["last_message"] = (message.content or "")[:1900]
        data["users"][uid]["last_message_time"] = format_time(datetime.datetime.utcnow())
    if message.attachments:
        attachments = [a.url for a in message.attachments]
        data["images"][str(message.id)] = {
            "author": message.author.id if message.author else None,
            "time": format_time(datetime.datetime.utcnow()),
            "attachments": attachments,
            "content": (message.content or "")[:1900],
            "deleted_by": None
        }
    save_data(data)
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author and after.author.bot:
        return
    data = load_data()
    uid = str(after.author.id)
    ensure_user_data(uid, data)
    data["users"][uid]["last_edit"] = (after.content or "")[:1900]
    data["users"][uid]["last_edit_time"] = format_time(datetime.datetime.utcnow())
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
    if message.author and message.author.bot:
        return
    data = load_data()
    try:
        attachments = [a.url for a in message.attachments] if message.attachments else []
        files_to_upload = []
        for att in message.attachments:
            try:
                dfile = await download_attachment_to_file(att)
                if dfile:
                    files_to_upload.append(dfile)
            except:
                pass
        data["images"][str(message.id)] = {
            "author": message.author.id if message.author else None,
            "time": format_time(datetime.datetime.utcnow()),
            "attachments": attachments,
            "content": (message.content or "")[:1900],
            "deleted_by": None
        }
        data["logs"].setdefault("deletions", []).append({
            "message_id": message.id,
            "author": message.author.id if message.author else None,
            "content": (message.content or "")[:1900],
            "attachments": attachments,
            "time": format_time(datetime.datetime.utcnow())
        })
    except Exception as e:
        safe_print("âš ï¸ on_message_delete error:", e)
    save_data(data)
    ch = bot.get_channel(TRACK_CHANNEL_ID)
    if ch:
        try:
            embed = discord.Embed(title="Message Deleted", color=discord.Color.red(), timestamp=datetime.datetime.now(pytz.utc))
            if message.author:
                embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
            if message.channel:
                embed.add_field(name="Channel", value=f"{message.channel} ({message.channel.id})", inline=False)
            embed.add_field(name="Content", value=message.content or "No content", inline=False)
            if message.reference:
                ref = message.reference.resolved
                if ref and isinstance(ref, discord.Message):
                    embed.add_field(name="Reply To", value=f"{ref.author} (msg {ref.id}) - {ref.content[:400]}", inline=False)
            try:
                deleter = None
                async for entry in message.guild.audit_logs(limit=6, action=discord.AuditLogAction.message_delete):
                    ts_age = (datetime.datetime.utcnow().replace(tzinfo=None) - entry.created_at.replace(tzinfo=None)).total_seconds()
                    if ts_age < 15:
                        deleter = entry.user
                        break
                if deleter:
                    embed.add_field(name="Deleted By (audit)", value=f"{deleter} ({deleter.id})", inline=False)
            except:
                pass
            await ch.send(embed=embed)
            for f in files_to_upload:
                try:
                    await ch.send(file=f)
                except:
                    pass
        except:
            traceback.print_exc()

@bot.event
async def on_bulk_message_delete(messages: List[discord.Message]):
    data = load_data()
    guild = None
    try:
        if messages:
            guild = messages[0].guild
    except:
        guild = None
    preview = []
    files_to_upload = []
    for m in messages[:15]:
        author_name = (m.author.display_name if m.author else "Unknown")
        preview.append(f"{author_name}: {(m.content or '')[:120]}")
        data["images"][str(m.id)] = {
            "author": m.author.id if m.author else None,
            "time": format_time(datetime.datetime.utcnow()),
            "attachments": [a.url for a in m.attachments] if m.attachments else [],
            "content": (m.content or "")[:1900],
            "deleted_by": None,
            "bulk_deleted": True
        }
        data["logs"].setdefault("deletions", []).append({
            "message_id": m.id,
            "author": m.author.id if m.author else None,
            "content": (m.content or "")[:1900],
            "attachments": [a.url for a in m.attachments] if a.att else [],
            "bulk": True,
            "time": format_time(datetime.datetime.utcnow())
        })
        for a in m.attachments:
            try:
                dfile = await download_attachment_to_file(a)
                if dfile:
                    files_to_upload.append(dfile)
            except:
                pass
    probable_actor = None
    try:
        if guild:
            async for entry in guild.audit_logs(limit=12, action=discord.AuditLogAction.message_bulk_delete):
                probable_actor = entry.user
                break
    except Exception as e:
        safe_print("âš ï¸ audit log check bulk delete failed:", e)
    channel = bot.get_channel(TRACK_CHANNEL_ID)
    when = format_time(datetime.datetime.utcnow())
    emb = build_purge_embed(probable_actor, messages[0].channel if messages else channel, len(messages), preview, when)
    if channel:
        try:
            await channel.send(embed=emb)
            for f in files_to_upload:
                try:
                    await channel.send(file=f)
                except:
                    pass
        except:
            pass
    save_data(data)

# MEMBER UPDATE handled earlier for role attribution in original pastefy content
# (kept as-is further up if necessary; pastefy content had a long handler - already merged)

# ---------------- COMMANDS ----------------
@bot.command(name="rmute")
@commands.has_permissions(manage_roles=True)
async def cmd_rmute(ctx: commands.Context, targets: commands.Greedy[discord.Member], duration: str, *, reason: str = "No reason provided"):
    if not can_execute_command(ctx.author.id):
        await ctx.send("âŒ› Command cooldown active.")
        return
    seconds = parse_duration(duration)
    if seconds is None:
        await ctx.send("âŒ Invalid duration format (10s,5m,2h,1d or combos).")
        return
    if not targets:
        await ctx.send("âŒ Mention at least one user.")
        return
    data = load_data()
    ch = bot.get_channel(TRACK_CHANNEL_ID)
    try:
        await ctx.message.delete()
    except:
        pass
    for target in targets:
        try:
            role = ctx.guild.get_role(RMUTE_ROLE_ID)
            if role is None:
                await ctx.send("âš ï¸ RMUTE role not configured on this server.")
                return
            await target.add_roles(role, reason=f"rmute by {ctx.author} reason: {reason}")
            mute_id = f"rmute_{target.id}_{int(datetime.datetime.utcnow().timestamp())}"
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
            data["rmute_usage"][str(ctx.author.id)] = data.get("rmute_usage", {}).get(str(ctx.author.id), 0) + 1
            save_data(data)
            try:
                dm = build_mute_dm_embed(target, ctx.author, format_duration_seconds(seconds), reason, auto=False)
                if str(target.id) not in data.get("rdm_users", []):
                    try:
                        await target.send(embed=dm)
                    except:
                        pass
            except:
                pass
            if ch:
                await ch.send(embed=build_mute_log_embed(target, ctx.author, format_duration_seconds(seconds), reason, format_time(unmute_at), source=f"{ctx.author}"))
        except Exception as e:
            safe_print("âŒ Error applying rmute:", e)
            traceback.print_exc()
    save_data(data)

@bot.command(name="runmute")
@commands.has_permissions(manage_roles=True)
async def cmd_runmute(ctx: commands.Context, target: discord.Member):
    if not can_execute_command(ctx.author.id):
        await ctx.send("âŒ› Command cooldown active.")
        return
    role = ctx.guild.get_role(RMUTE_ROLE_ID)
    if role is None:
        await ctx.send("âš ï¸ RMUTE role not configured.")
        return
    try:
        if role in target.roles:
            await target.remove_roles(role, reason=f"runmute by {ctx.author}")
        data = load_data()
        removed_any = False
        for mid in list(data.get("mutes", {}).keys()):
            if data["mutes"][mid].get("user") == target.id:
                data["mutes"].pop(mid, None)
                removed_any = True
        save_data(data)
        ch = bot.get_channel(TRACK_CHANNEL_ID)
        if ch:
            await ch.send(embed=build_unmute_log_embed(target, ctx.author, reason=None, auto=False))
        try:
            await ctx.message.delete()
        except:
            pass
    except Exception as e:
        safe_print("âŒ runmute error:", e)
        traceback.print_exc()

@bot.command(name="rmlb")
async def cmd_rmlb(ctx: commands.Context, top: int = 10):
    data = load_data()
    usage = data.get("rmute_usage", {})
    sorted_usage = sorted(usage.items(), key=lambda kv: kv[1], reverse=True)[:top]
    embed = discord.Embed(title="ðŸ† RMute Leaderboard", color=discord.Color.gold())
    for uid, cnt in sorted_usage:
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else f"User ID {uid}"
        embed.add_field(name=name, value=f"Mutes used: {cnt}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="rcache")
@commands.has_permissions(manage_guild=True)
async def cmd_rcache(ctx: commands.Context, count: int = 20):
    if not any(r.id in RCACHE_ROLES for r in ctx.author.roles):
        await ctx.send("âŒ You do not have permission to view cache.")
        return
    data = load_data()
    images = data.get("images", {})
    items = list(images.items())[-count:]
    embed = discord.Embed(title=f"ðŸ—‚ï¸ Deleted Images/Files Cache (last {len(items)})", color=discord.Color.purple())
    for mid, info in items:
        author = ctx.guild.get_member(info.get("author")) if info.get("author") else None
        author_str = author.display_name if author else str(info.get("author"))
        attachments = info.get("attachments", [])
        attachments_txt = "\\n".join(attachments) if attachments else "None"
        content = (info.get("content") or "")[:500]
        deleted_by = info.get("deleted_by")
        val = f"Time: {info.get('time')}\\nDeleted by: {deleted_by}\\nAttachments:\\n{attachments_txt}\\nContent: {content}"
        embed.add_field(name=f"Msg {mid} by {author_str}", value=val, inline=False)
    if not items:
        embed.description = "No cached deleted images/files."
    await ctx.send(embed=embed)

@bot.command(name="tlb")
async def cmd_tlb(ctx: commands.Context, top: int = 10):
    data = load_data()
    rows = []
    for uid, udata in data.get("users", {}).items():
        try:
            member = ctx.guild.get_member(int(uid))
            if not member:
                continue
            if not any(r.id in RCACHE_ROLES for r in member.roles):
                continue
            total = udata.get("total_online_seconds", 0)
            rows.append((member, total))
        except:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="ðŸ“Š Timetrack Leaderboard (RCACHE Roles)", color=discord.Color.green())
    for i, (member, total) in enumerate(rows[:top], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Total: {format_duration_seconds(int(total))}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="tdm")
async def cmd_tdm(ctx: commands.Context, top: int = 10):
    data = load_data()
    rows = []
    for uid, udata in data.get("users", {}).items():
        try:
            member = ctx.guild.get_member(int(uid))
            if not member:
                continue
            if any(r.id in RCACHE_ROLES for r in member.roles):
                continue
            total = udata.get("total_online_seconds", 0)
            rows.append((member, total))
        except:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="ðŸ“Š Timetrack Leaderboard (No RCACHE Roles)", color=discord.Color.green())
    for i, (member, total) in enumerate(rows[:top], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Total: {format_duration_seconds(int(total))}", inline=False)
    await ctx.send(embed=embed)

async def ping_members_by_role_and_log(ctx: commands.Context, role_id: int, title: str):
    members = [m for m in ctx.guild.members if any(r.id == role_id for r in m.roles)]
    if not members:
        await ctx.send("No members with that role found.", delete_after=8)
        return
    chunk = ""
    for m in members:
        mention = m.mention + " "
        if len(chunk) + len(mention) > 1900:
            try:
                await ctx.send(chunk)
            except:
                pass
            chunk = mention
        else:
            chunk += mention
    if chunk:
        try:
            await ctx.send(chunk)
        except:
            pass
    try:
        await ctx.message.delete()
    except:
        pass
    ch = bot.get_channel(TRACK_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title=title, description=f"{ctx.author} triggered {title}", color=discord.Color.blue(), timestamp=datetime.datetime.now(pytz.utc))
        if ctx.message.reference:
            try:
                ref_msg = ctx.message.reference.resolved
                if isinstance(ref_msg, discord.Message):
                    embed.add_field(name="Replying To", value=f"{ref_msg.author}: {ref_msg.content[:500]}", inline=False)
            except:
                pass
        await ch.send(embed=embed)

@bot.command()
async def rping(ctx: commands.Context):
    await ping_members_by_role_and_log(ctx, 1410422475942264842, "Staff Ping")

@bot.command()
async def hsping(ctx: commands.Context):
    await ping_members_by_role_and_log(ctx, 1410422656112791592, "Higher Staff Ping")

@bot.command(name="rdm")
async def cmd_rdm(ctx: commands.Context):
    data = load_data()
    lst = data.get("rdm_users", [])
    uid = str(ctx.author.id)
    if uid in lst:
        lst.remove(uid)
        data["rdm_users"] = lst
        save_data(data)
        await ctx.send("You will now receive DMs from the bot.")
    else:
        lst.append(uid)
        data["rdm_users"] = lst
        save_data(data)
        await ctx.send("You have opted out of DMs from the bot.")

@bot.command(name="rhelp")
async def cmd_rhelp(ctx: commands.Context):
    embed = discord.Embed(title="ðŸ¤– RHelp", color=discord.Color.blue())
    embed.add_field(name="!timetrack [user]", value="Show timetrack info.", inline=False)
    embed.add_field(name="!rmute @u1 @u2 <duration> [reason]", value="Mute user(s).", inline=False)
    embed.add_field(name="!runmute @u", value="Unmute single user (remove mute role).", inline=False)
    embed.add_field(name="!rmlb", value="Top mute-invokers.", inline=False)
    embed.add_field(name="!rcache [count]", value="Show deleted images/files (roles only).", inline=False)
    embed.add_field(name="!tlb [top]", value="Timetrack leaderboard (RCACHE roles).", inline=False)
    embed.add_field(name="!tdm [top]", value="Timetrack leaderboard (no RCACHE roles).", inline=False)
    embed.add_field(name="!rping", value="Ping staff by member (sends mentions to matched members).", inline=False)
    embed.add_field(name="!hsping", value="Ping higher staff by member.", inline=False)
    embed.add_field(name="!rdm", value="Toggle opt-out from bot DMs", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="timetrack")
async def cmd_timetrack(ctx: commands.Context, member: Optional[discord.Member] = None):
    member = member or ctx.author
    data = load_data()
    uid = str(member.id)
    ensure_user_data(uid, data)
    embed = build_timetrack_embed(member, data["users"][uid])
    await ctx.send(embed=embed)

@bot.command(name="tt")
async def cmd_tt(ctx: commands.Context, member: Optional[discord.Member] = None):
    await cmd_timetrack(ctx, member)

@bot.command(name="rpurge")
@commands.has_permissions(manage_messages=True)
async def cmd_rpurge(ctx: commands.Context):
    data = load_data()
    deletions = data.get("logs", {}).get("deletions", [])[-70:]
    embed = discord.Embed(title="ðŸ§¾ Recent Cached Deletions", color=discord.Color.dark_red())
    if not deletions:
        embed.description = "No cached deletions stored."
        await ctx.send(embed=embed)
        return
    for d in deletions[-15:]:
        content = (d.get("content") or "")[:200]
        embed.add_field(name=f"Msg {d.get('message_id')} by {d.get('author')}", value=f"{content}\\nTime: {d.get('time')}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="rdump")
@commands.has_permissions(administrator=True)
async def cmd_rdump(ctx: commands.Context):
    d = load_data()
    path = "rdump.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, default=str)
    await ctx.send("ðŸ“¦ Data dump:", file=discord.File(path))
    try:
        os.remove(path)
    except:
        pass

# AUTO-UNMUTE
@tasks.loop(seconds=10.0)
async def auto_unmute_loop():
    try:
        data = load_data()
        now_ts = int(datetime.datetime.utcnow().timestamp())
        changed = False
        for mid, m in list(data.get("mutes", {}).items()):
            try:
                unmute_dt = None
                if m.get("unmute_utc"):
                    try:
                        unmute_dt = datetime.datetime.strptime(m["unmute_utc"], "%Y-%m-%d %H:%M:%S")
                    except:
                        unmute_dt = None
                if not unmute_dt and m.get("start_utc") and m.get("duration_seconds"):
                    try:
                        start_dt = datetime.datetime.strptime(m["start_utc"], "%Y-%m-%d %H:%M:%S")
                        unmute_dt = start_dt + datetime.timedelta(seconds=int(m.get("duration_seconds",0)))
                    except:
                        unmute_dt = None
                if unmute_dt:
                    if datetime.datetime.utcnow() >= unmute_dt:
                        g = bot.get_guild(GUILD_ID)
                        if g:
                            member = g.get_member(int(m.get("user")))
                            if member:
                                role = g.get_role(RMUTE_ROLE_ID)
                                if role and role in member.roles:
                                    try:
                                        await member.remove_roles(role, reason="Auto-unmute")
                                    except:
                                        pass
                                ch = bot.get_channel(TRACK_CHANNEL_ID)
                                if ch:
                                    await ch.send(embed=build_unmute_log_embed(member, None, reason=None, auto=True))
                        data.get("mutes", {}).pop(mid, None)
                        changed = True
            except:
                continue
        if changed:
            save_data(data)
    except Exception as e:
        safe_print("âš ï¸ auto_unmute_loop error:", e)
        traceback.print_exc()

# STARTUP (Flask)
if __name__ == "__main__":
    app = Flask("keepalive")
    @app.route("/")
    def home():
        return "Bot is running"
    def run_flask():
        port = int(os.environ.get("PORT", 8080))
        try:
            app.run(host="0.0.0.0", port=port)
        except:
            pass
    threading.Thread(target=run_flask, daemon=True).start()
    safe_print("ðŸš€ Starting mega merged bot...")
    if not os.path.exists(DATA_FILE):
        save_data(init_data_structure())
    try:
        bot.run(TOKEN)
    except Exception as e:
        safe_print("âŒ Fatal error starting bot:", e)
        traceback.print_exc()
