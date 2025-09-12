# main.py
# Merged bot: timetrack + rmute + comprehensive logging + file/attachment caching + auto-unmute + activity ping
# Now detects mutes/unmutes done outside this bot too
# Requirements: discord.py 2.x, aiohttp, Python 3.9+ for zoneinfo

import os
import json
import random
import asyncio
import datetime
import io
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List

import aiohttp
import discord
from discord.ext import commands, tasks
from flask import Flask

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287

# Channels
MOD_ACTIVITY_LOG_CHANNEL = 1403422664521023648   # where mod online/offline messages go
LOGGING_CHANNEL_ID = 1410458084874260592        # server audit log channel upload/embeds

# Roles that trigger mod activity logs
ACTIVE_LOG_ROLE_IDS = {
    1410422029236047975,
    1410419924173848626,
    1410420126003630122,
    1410423594579918860,
    1410421466666631279,
    1410421647265108038,
    1410419345234067568
}

# timezone display map (stacked)
TIMEZONES = {
    "🌍 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🌴 PST": ZoneInfo("America/Los_Angeles"),
    "🇪🇺 CET": ZoneInfo("Europe/Berlin"),
}

DATA_FILE = "activity_logs.json"

INACTIVITY_MIN = 50
INACTIVITY_MAX = 60

# ------------------ BOT & INTENTS ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ------------------ STORAGE ------------------
activity_logs: Dict[str, Dict[str, Any]] = {}
# message cache to preserve attachments/content for deleted messages
message_cache: Dict[int, Dict[str, Any]] = {}
data_lock = asyncio.Lock()

def load_data():
    global activity_logs
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                activity_logs = json.load(f)
        else:
            activity_logs = {}
    except Exception:
        activity_logs = {}

async def save_data_async():
    async with data_lock:
        tmp = json.dumps(activity_logs, indent=4)
        with open(DATA_FILE, "w") as f:
            f.write(tmp)

def get_user_log(uid: int) -> Dict[str, Any]:
    key = str(uid)
    if key not in activity_logs:
        activity_logs[key] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "offline_start": None,
            "offline_delay": None,
            "last_message": None,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_daily_reset": None,
            "last_weekly_reset": None,
            "last_monthly_reset": None,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "inactive": False,
            "mute_count": 0,
            "rping_on": False,
            # counter for who used rmute (muter_count will be stored under the muter's user id)
            "muter_count": activity_logs.get(key, {}).get("muter_count", 0)
        }
    return activity_logs[key]

def fmt_duration(seconds: float) -> str:
    s = int(max(0, round(seconds)))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if secs or not parts: parts.append(f"{secs}s")
    return " ".join(parts)

def parse_duration_abbrev(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.strip().lower()
    if len(s) < 2:
        return None
    unit = s[-1]
    try:
        amount = int(s[:-1])
    except ValueError:
        return None
    mult = {"s":1,"m":60,"h":3600,"d":86400}
    if unit not in mult:
        return None
    return amount * mult[unit]

# ------------------ Flask keep-alive ------------------
app = Flask("botkeepalive")
@app.route("/")
def home(): return "✅ Bot is running!"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
asyncio.get_event_loop().run_in_executor(None, run_flask)

# ------------------ UTILS ------------------
def stacked_timezones(dt: datetime.datetime) -> str:
    lines = []
    for emoji, tz in TIMEZONES.items():
        lines.append(f"{emoji} {dt.astimezone(tz).strftime('%b %d, %Y – %I:%M %p')}")
    return "\n".join(lines)

async def fetch_audit_executor(guild: discord.Guild, action: discord.AuditLogAction, target_id: Optional[int] = None, limit: int = 10) -> Optional[discord.Member]:
    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            try:
                if target_id is None:
                    return entry.user
                tid = getattr(entry.target, "id", None)
                if tid is None:
                    if str(target_id) == str(entry.target):
                        return entry.user
                else:
                    if int(tid) == int(target_id):
                        return entry.user
            except Exception:
                continue
        return None
    except Exception:
        return None

async def send_server_log(embed: discord.Embed, files: Optional[List[discord.File]] = None):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    ch = guild.get_channel(LOGGING_CHANNEL_ID)
    if ch:
        try:
            if files:
                await ch.send(embed=embed, files=files)
            else:
                await ch.send(embed=embed)
        except Exception:
            pass

# ------------------ EMBED BUILDERS ------------------
def build_mute_embed(member: discord.Member, by: discord.Member, reason: str, duration_seconds: int) -> discord.Embed:
    expire_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
    embed = discord.Embed(
        title="🔇 User Muted",
        description=f"{member.mention} was muted",
        color=0xFF5A5F,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=getattr(member, "display_avatar").url if getattr(member, "display_avatar", None) else None)
    embed.add_field(name="👤 Muted User", value=member.mention, inline=True)
    embed.add_field(name="🔒 Muted By", value=by.mention, inline=True)
    embed.add_field(name="⏳ Duration", value=fmt_duration(duration_seconds), inline=True)
    embed.add_field(name="📝 Reason", value=reason or "No reason provided", inline=False)
    embed.add_field(name="🕒 Unmute Time", value=stacked_timezones(expire_dt), inline=False)
    return embed

def build_unmute_embed(member: discord.Member, by: discord.Member, original_reason: Optional[str], original_duration_seconds: Optional[int]) -> discord.Embed:
    embed = discord.Embed(
        title="🔊 User Unmuted",
        description=f"{member.mention} was unmuted",
        color=0x2ECC71,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=getattr(member, "display_avatar").url if getattr(member, "display_avatar", None) else None)
    embed.add_field(name="👤 Unmuted User", value=member.mention, inline=True)
    embed.add_field(name="🔓 Unmuted By", value=by.mention if by else "Unknown", inline=True)
    if original_reason:
        embed.add_field(name="📝 Original Reason", value=original_reason, inline=False)
    if original_duration_seconds:
        embed.add_field(name="⏳ Original Duration", value=fmt_duration(original_duration_seconds), inline=True)
    embed.add_field(name="🕒 Unmuted At", value=stacked_timezones(datetime.datetime.now(datetime.timezone.utc)), inline=False)
    return embed

def build_timetrack_embed(member: discord.Member, log: Dict[str, Any]) -> discord.Embed:
    online_secs = log.get("online_seconds", 0)
    offline_secs = log.get("offline_seconds", 0)
    offline_start_iso = log.get("offline_start")
    offline_delta = 0
    if offline_start_iso:
        try:
            offline_dt = datetime.datetime.fromisoformat(offline_start_iso)
            offline_delta = (datetime.datetime.now(datetime.timezone.utc) - offline_dt).total_seconds()
        except Exception:
            offline_delta = 0
    embed = discord.Embed(
        title="⏳ Time Tracker",
        description=f"Tracking activity for **{member.mention}**",
        color=0x2ecc71 if not log.get("inactive", False) else 0xe74c3c,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=getattr(member, "display_avatar").url if getattr(member, "display_avatar", None) else None)
    embed.add_field(name="🟢 Online time", value=f"`{fmt_duration(online_secs)}`", inline=True)
    embed.add_field(name="⚫ Offline time", value=f"`{fmt_duration(offline_secs + offline_delta)}`", inline=True)
    embed.add_field(name="📆 Daily", value=f"`{fmt_duration(log.get('daily_seconds',0))}`", inline=True)
    embed.add_field(name="📆 Weekly", value=f"`{fmt_duration(log.get('weekly_seconds',0))}`", inline=True)
    embed.add_field(name="📆 Monthly", value=f"`{fmt_duration(log.get('monthly_seconds',0))}`", inline=True)
    last_msg_iso = log.get("last_message")
    if last_msg_iso:
        try:
            last_dt = datetime.datetime.fromisoformat(last_msg_iso)
            lines = [f"{emoji} {last_dt.astimezone(tz).strftime('%b %d, %Y – %I:%M %p')}" for emoji, tz in TIMEZONES.items()]
            embed.add_field(name="💬 Last Message", value="\n".join(lines), inline=False)
        except Exception:
            pass
    return embed

# ------------------ TASKS ------------------
@tasks.loop(seconds=1.0)
async def timetrack_loop():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    for member in guild.members:
        log = get_user_log(member.id)
        # skip bots
        if member.bot:
            continue
        # increment online/offline
        if member.status != discord.Status.offline:
            log["online_seconds"] = log.get("online_seconds", 0) + 1
            log["daily_seconds"] = log.get("daily_seconds", 0) + 1
            log["weekly_seconds"] = log.get("weekly_seconds", 0) + 1
            log["monthly_seconds"] = log.get("monthly_seconds", 0) + 1
            log["offline_start"] = None
        else:
            log["offline_seconds"] = log.get("offline_seconds", 0) + 1
            if not log.get("offline_start"):
                log["offline_start"] = now.isoformat()
    await save_data_async()

@tasks.loop(seconds=5.0)
async def auto_unmute_loop():
    now = datetime.datetime.now(datetime.timezone.utc)
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    role = guild.get_role(MUTED_ROLE_ID)
    if not role:
        return
    for key, log in activity_logs.items():
        mute_expires_iso = log.get("mute_expires")
        if not mute_expires_iso:
            continue
        try:
            mute_expires_dt = datetime.datetime.fromisoformat(mute_expires_iso)
        except Exception:
            continue
        if now >= mute_expires_dt:
            user = guild.get_member(int(key))
            if user and role in user.roles:
                try:
                    await user.remove_roles(role, reason="Auto-unmute (time expired)")
                    executor = bot.user
                    embed = build_unmute_embed(user, executor, log.get("mute_reason"), log.get("mute_duration"))
                    await send_server_log(embed)
                    # reset mute info
                    log["mute_expires"] = None
                    log["mute_reason"] = None
                    log["mute_duration"] = None
                    log["mute_responsible"] = None
                except Exception:
                    continue
    await save_data_async()

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    load_data()
    timetrack_loop.start()
    auto_unmute_loop.start()

@bot.event
async def on_member_update(before, after):
    # Detect mutes/unmutes done outside the bot
    muted_role = after.guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(after.id)
    if muted_role in before.roles and muted_role not in after.roles:
        # User was unmuted
        executor = await fetch_audit_executor(after.guild, discord.AuditLogAction.member_role_update, target_id=after.id)
        embed = build_unmute_embed(after, executor, log.get("mute_reason"), log.get("mute_duration"))
        await send_server_log(embed)
        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_duration"] = None
        log["mute_responsible"] = None
    elif muted_role not in before.roles and muted_role in after.roles:
        # User was muted
        executor = await fetch_audit_executor(after.guild, discord.AuditLogAction.member_role_update, target_id=after.id)
        # default 1 hour if unknown
        duration = 3600
        embed = build_mute_embed(after, executor, "Unknown (manual)", duration)
        await send_server_log(embed)
        log["mute_expires"] = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration)).isoformat()
        log["mute_reason"] = "Unknown (manual)"
        log["mute_duration"] = duration
        log["mute_responsible"] = executor.id if executor else None
    await save_data_async()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    log = get_user_log(message.author.id)
    log["last_message"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await save_data_async()
    # Cache messages for attachments
    if message.attachments or message.content:
        message_cache[message.id] = {
            "author": message.author.id,
            "content": message.content,
            "attachments": [att.url for att in message.attachments],
            "created_at": message.created_at.isoformat()
        }
    await bot.process_commands(message)

# ------------------ COMMANDS ------------------
@bot.command()
async def timetrack(ctx, member: Optional[discord.Member] = None):
    member = member or ctx.author
    log = get_user_log(member.id)
    embed = build_timetrack_embed(member, log)
    await ctx.send(embed=embed)

@bot.command()
async def rmute(ctx, member: discord.Member, duration: str = "1h", *, reason: str = None):
    role = ctx.guild.get_role(MUTED_ROLE_ID)
    dur_secs = parse_duration_abbrev(duration)
    if dur_secs is None:
        await ctx.send("❌ Invalid duration format. Use 10s, 5m, 2h, 1d")
        return
    await member.add_roles(role, reason=reason or "No reason provided")
    now = datetime.datetime.now(datetime.timezone.utc)
    expire_dt = now + datetime.timedelta(seconds=dur_secs)
    log = get_user_log(member.id)
    log["mute_expires"] = expire_dt.isoformat()
    log["mute_reason"] = reason or "No reason provided"
    log["mute_duration"] = dur_secs
    log["mute_responsible"] = ctx.author.id
    # increment muter_count
    muter_log = get_user_log(ctx.author.id)
    muter_log["muter_count"] = muter_log.get("muter_count", 0) + 1
    embed = build_mute_embed(member, ctx.author, reason, dur_secs)
    await send_server_log(embed)
    await ctx.send(f"✅ Muted {member.mention} for {duration}.")
    await save_data_async()

@bot.command()
async def runmute(ctx, member: discord.Member):
    role = ctx.guild.get_role(MUTED_ROLE_ID)
    if role in member.roles:
        await member.remove_roles(role, reason=f"Unmuted by {ctx.author}")
        log = get_user_log(member.id)
        embed = build_unmute_embed(member, ctx.author, log.get("mute_reason"), log.get("mute_duration"))
        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_duration"] = None
        log["mute_responsible"] = None
        await send_server_log(embed)
        await ctx.send(f"✅ Unmuted {member.mention}.")
        await save_data_async()
    else:
        await ctx.send(f"⚠️ {member.mention} is not muted.")

@bot.command()
async def rmlb(ctx):
    # leaderboard of who used rmute
    lb = [(int(uid), log.get("muter_count", 0)) for uid, log in activity_logs.items() if log.get("muter_count",0) > 0]
    lb.sort(key=lambda x: x[1], reverse=True)
    lines = []
    for i, (uid, count) in enumerate(lb[:10], 1):
        member = ctx.guild.get_member(uid)
        name = member.display_name if member else str(uid)
        lines.append(f"**{i}. {name}** – `{count}` mutes")
    if not lines:
        lines.append("No data yet.")
    await ctx.send("\n".join(lines))

# ------------------ START BOT ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ DISCORD_TOKEN not set!")
else:
    bot.run(TOKEN)
