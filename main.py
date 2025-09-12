# main.py
# Full bot with timetrack, rmute, logging, attachments, auto-unmute, DMs, and cross-bot mute detection

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

MOD_ACTIVITY_LOG_CHANNEL = 1403422664521023648
LOGGING_CHANNEL_ID = 1410458084874260592

ACTIVE_LOG_ROLE_IDS = {
    1410422029236047975,
    1410419924173848626,
    1410420126003630122,
    1410423594579918860,
    1410421466666631279,
    1410421647265108038,
    1410419345234067568
}

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
    if not s: return None
    s = s.strip().lower()
    if len(s) < 2: return None
    unit = s[-1]
    try: amount = int(s[:-1])
    except ValueError: return None
    mult = {"s":1,"m":60,"h":3600,"d":86400}
    if unit not in mult: return None
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
    lines = [f"{emoji} {dt.astimezone(tz).strftime('%b %d, %Y – %I:%M %p')}" for emoji, tz in TIMEZONES.items()]
    return "\n".join(lines)

async def fetch_audit_executor(guild: discord.Guild, action: discord.AuditLogAction, target_id: Optional[int] = None, limit: int = 10) -> Optional[discord.Member]:
    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            tid = getattr(entry.target, "id", None)
            if target_id is None or (tid and int(tid) == int(target_id)) or str(target_id) == str(entry.target):
                return entry.user
        return None
    except Exception:
        return None

async def send_server_log(embed: discord.Embed, files: Optional[List[discord.File]] = None):
    guild = bot.get_guild(GUILD_ID)
    if not guild: return
    ch = guild.get_channel(LOGGING_CHANNEL_ID)
    if ch:
        try:
            if files: await ch.send(embed=embed, files=files)
            else: await ch.send(embed=embed)
        except Exception: pass

# ------------------ EMBED BUILDERS ------------------
def build_mute_embed(member: discord.Member, by: discord.Member, reason: str, duration_seconds: int) -> discord.Embed:
    expire_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
    embed = discord.Embed(title="🔇 User Muted", description=f"{member.mention} was muted", color=0xFF5A5F, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_thumbnail(url=getattr(member, "display_avatar").url if getattr(member, "display_avatar", None) else None)
    embed.add_field(name="👤 Muted User", value=member.mention, inline=True)
    embed.add_field(name="🔒 Muted By", value=by.mention, inline=True)
    embed.add_field(name="⏳ Duration", value=fmt_duration(duration_seconds), inline=True)
    embed.add_field(name="📝 Reason", value=reason or "No reason provided", inline=False)
    embed.add_field(name="🕒 Unmute Time", value=stacked_timezones(expire_dt), inline=False)
    return embed

def build_unmute_embed(member: discord.Member, by: discord.Member, original_reason: Optional[str], original_duration_seconds: Optional[int]) -> discord.Embed:
    embed = discord.Embed(title="🔊 User Unmuted", description=f"{member.mention} was unmuted", color=0x2ECC71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_thumbnail(url=getattr(member, "display_avatar").url if getattr(member, "display_avatar", None) else None)
    embed.add_field(name="👤 Unmuted User", value=member.mention, inline=True)
    embed.add_field(name="🔓 Unmuted By", value=by.mention if by else "Unknown", inline=True)
    if original_reason: embed.add_field(name="📝 Original Reason", value=original_reason, inline=False)
    if original_duration_seconds: embed.add_field(name="⏳ Original Duration", value=fmt_duration(original_duration_seconds), inline=True)
    embed.add_field(name="🕒 Unmuted At", value=stacked_timezones(datetime.datetime.now(datetime.timezone.utc)), inline=False)
    return embed

def build_timetrack_embed(member: discord.Member, log: Dict[str, Any]) -> discord.Embed:
    online_secs = log.get("online_seconds", 0)
    offline_secs = log.get("offline_seconds", 0)
    offline_start_iso = log.get("offline_start")
    offline_delta = 0
    if offline_start_iso:
        try: offline_dt = datetime.datetime.fromisoformat(offline_start_iso); offline_delta = (datetime.datetime.now(datetime.timezone.utc) - offline_dt).total_seconds()
        except Exception: offline_delta = 0
    embed = discord.Embed(title="⏳ Time Tracker", description=f"Tracking activity for **{member.mention}**", color=0x2ecc71 if not log.get("inactive", False) else 0xe74c3c, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_thumbnail(url=getattr(member, "display_avatar").url if getattr(member, "display_avatar", None) else None)
    embed.add_field(name="🟢 Online time", value=f"`{fmt_duration(online_secs)}`", inline=True)
    embed.add_field(name="⚫ Offline time", value=f"`{fmt_duration(offline_secs + offline_delta)}`", inline=True)
    embed.add_field(name="📆 Daily", value=f"`{fmt_duration(log.get('daily_seconds',0))}`", inline=True)
    embed.add_field(name="📆 Weekly", value=f"`{fmt_duration(log.get('weekly_seconds',0))}`", inline=True)
    embed.add_field(name="📆 Monthly", value=f"`{fmt_duration(log.get('monthly_seconds',0))}`", inline=True)
    last_msg_iso = log.get("last_message")
    if last_msg_iso:
        try: last_dt = datetime.datetime.fromisoformat(last_msg_iso); embed.add_field(name="🕒 Last message (timezones)", value="\n".join([f"{emoji} {last_dt.astimezone(tz).strftime('%b %d, %Y – %I:%M %p')}" for emoji, tz in TIMEZONES.items()]), inline=False)
        except Exception: pass
    return embed

# ------------------ EVENTS & POLLERS ------------------

@bot.event
async def on_ready():
    load_data()
    if not inactivity_poller.is_running(): inactivity_poller.start()
    if not auto_unmute_loop.is_running(): auto_unmute_loop.start()
    print(f"✅ Bot is ready as {bot.user}")
    @tasks.loop(seconds=1)
async def inactivity_poller():
    guild = bot.get_guild(GUILD_ID)
    if not guild: return
    for member in guild.members:
        if member.bot: continue
        log = get_user_log(member.id)
        online = member.status != discord.Status.offline
        now = datetime.datetime.now(datetime.timezone.utc)
        if online:
            log["online_seconds"] = log.get("online_seconds", 0) + 1
            log["offline_start"] = None
        else:
            if not log.get("offline_start"):
                log["offline_start"] = now.isoformat()
            else:
                offline_start = datetime.datetime.fromisoformat(log["offline_start"])
                log["offline_seconds"] = log.get("offline_seconds", 0) + 1
        # Optional inactivity flag
        log["inactive"] = log.get("offline_seconds",0) >= INACTIVITY_MAX
    await save_data_async()

@tasks.loop(seconds=5)
async def auto_unmute_loop():
    guild = bot.get_guild(GUILD_ID)
    if not guild: return
    now = datetime.datetime.now(datetime.timezone.utc)
    for key, log in list(activity_logs.items()):
        mute_exp = log.get("mute_expires")
        if mute_exp:
            try:
                expire_dt = datetime.datetime.fromisoformat(mute_exp)
                if now >= expire_dt:
                    member = guild.get_member(int(key))
                    if member:
                        muted_role = guild.get_role(MUTED_ROLE_ID)
                        if muted_role in member.roles:
                            await member.remove_roles(muted_role, reason="Auto-unmute expired")
                            executor_id = log.get("mute_responsible")
                            executor = guild.get_member(executor_id) if executor_id else None
                            embed = build_unmute_embed(member, executor, log.get("mute_reason"), None)
                            await send_server_log(embed)
                    log["mute_expires"] = None
                    log["mute_reason"] = None
                    log["mute_responsible"] = None
                    await save_data_async()
            except Exception:
                continue

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # Detect if muted/unmuted manually
    muted_role = after.guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(after.id)
    if muted_role not in before.roles and muted_role in after.roles:
        # Was muted
        executor = await fetch_audit_executor(after.guild, discord.AuditLogAction.member_update, target_id=after.id)
        log["mute_responsible"] = executor.id if executor else None
        log["mute_expires"] = None
        await save_data_async()
    elif muted_role in before.roles and muted_role not in after.roles:
        # Was unmuted
        executor = await fetch_audit_executor(after.guild, discord.AuditLogAction.member_update, target_id=after.id)
        embed = build_unmute_embed(after, executor, log.get("mute_reason"), None)
        await send_server_log(embed)
        log["mute_responsible"] = None
        log["mute_expires"] = None
        log["mute_reason"] = None
        await save_data_async()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    log = get_user_log(message.author.id)
    log["last_message"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await save_data_async()
    await bot.process_commands(message)

# ------------------ COMMANDS ------------------

@bot.command()
@commands.has_permissions(manage_roles=True)
async def rmute(ctx: commands.Context, member: discord.Member, duration: str = "10m", *, reason: str = None):
    secs = parse_duration_abbrev(duration)
    if secs is None: return await ctx.send("Invalid duration format. Use 10s, 5m, 2h, etc.")
    muted_role = ctx.guild.get_role(MUTED_ROLE_ID)
    if muted_role not in member.roles:
        await member.add_roles(muted_role, reason=reason)
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=secs)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count",0)+1
    await save_data_async()
    embed = build_mute_embed(member, ctx.author, reason, secs)
    await send_server_log(embed)
    try: await member.send(f"You were muted in **{ctx.guild.name}** for {fmt_duration(secs)}. Reason: {reason or 'No reason'}")
    except Exception: pass
    await ctx.send(f"{member.mention} has been muted for {fmt_duration(secs)}.")

@bot.command()
async def timetrack(ctx: commands.Context, member: Optional[discord.Member] = None):
    member = member or ctx.author
    log = get_user_log(member.id)
    embed = build_timetrack_embed(member, log)
    await ctx.send(embed=embed)

# ------------------ RUN ------------------
bot.run(os.environ.get("DISCORD_TOKEN"))
