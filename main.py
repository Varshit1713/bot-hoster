# main.py
# Full merged bot: timetrack + rmute + comprehensive logging + attachment caching + auto-unmute + activity ping
# Requirements: discord.py 2.x, aiohttp, Python 3.9+

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
# Replace with your guild / channel / role IDs (these are the ones you provided)
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
MOD_ACTIVITY_LOG_CHANNEL = 1403422664521023648   # where mod online/offline messages go (you provided)
LOGGING_CHANNEL_ID = 1410458084874260592        # main audit/log channel (you provided)

# Roles that should be tracked for mod online/offline messages
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

# inactivity random delay range (in seconds)
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
message_cache: Dict[int, Dict[str, Any]] = {}  # caches recent messages (content + attachment URLs)
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
            "muter_count": 0,   # counts who used !rmute
            "rping_on": False
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

def stacked_timezones(dt: datetime.datetime) -> str:
    lines = []
    for emoji, tz in TIMEZONES.items():
        # show as e.g. Sep 09, 2025 – 05:42 AM
        lines.append(f"{emoji} {dt.astimezone(tz).strftime('%b %d, %Y – %I:%M %p')}")
    return "\n".join(lines)

# ------------------ Keep-alive (Flask) ------------------
app = Flask("botkeepalive")
@app.route("/")
def home(): return "✅ Bot is running!"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
# run in executor so it won't block
asyncio.get_event_loop().run_in_executor(None, run_flask)

# ------------------ HTTP helper for attachments ------------------
async def download_and_prepare_files(urls: List[str]) -> List[discord.File]:
    files: List[discord.File] = []
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        filename = url.split("/")[-1].split("?")[0]
                        bio = io.BytesIO(data)
                        bio.seek(0)
                        files.append(discord.File(fp=bio, filename=filename))
            except Exception:
                continue
    return files

# ------------------ Audit log helper ------------------
async def fetch_audit_executor(guild: discord.Guild, action: discord.AuditLogAction, target_id: Optional[int] = None, limit: int = 20) -> Optional[discord.Member]:
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

def build_unmute_embed(member: discord.Member, by: Optional[discord.Member], original_reason: Optional[str], original_duration_seconds: Optional[int]) -> discord.Embed:
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
        description=f"Tracking activity for **{member.display_name}**",
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
            embed.add_field(name="🕒 Last message (timezones)", value="\n".join(lines), inline=False)
        except Exception:
            pass
    return embed

# ------------------ EVENTS & POLLERS ------------------
@bot.event
async def on_ready():
    load_data()
    if not inactivity_poller.is_running():
        inactivity_poller.start()
    if not auto_unmute_loop.is_running():
        auto_unmute_loop.start()
    print(f"✅ Bot ready: {bot.user} (guilds: {len(bot.guilds)})")

# cache attachments & message content when messages are created so we can log deletions properly
@bot.event
async def on_message(message: discord.Message):
    # process commands first
    await bot.process_commands(message)

    if message.author.bot:
        return

    uid = message.author.id
    now = datetime.datetime.now(datetime.timezone.utc)
    log = get_user_log(uid)

    # cache message for deletion logs
    try:
        att_urls = [a.url for a in message.attachments]
    except Exception:
        att_urls = []
    message_cache[message.id] = {
        "author_id": uid,
        "author_name": getattr(message.author, "display_name", str(message.author)),
        "content": message.content,
        "attachments": att_urls,
        "channel_id": message.channel.id,
        "created_at": now.isoformat()
    }
    # bound cache size
    if len(message_cache) > 5000:
        keys = list(message_cache.keys())
        for k in keys[:100]:
            message_cache.pop(k, None)

    # reset offline state
    log["last_message"] = now.isoformat()
    log["offline_seconds"] = 0
    log["offline_start"] = None
    if not log.get("offline_delay"):
        log["offline_delay"] = random.randint(INACTIVITY_MIN, INACTIVITY_MAX)

    # daily/weekly/monthly rollovers
    today = now.date()
    weeknum = now.isocalendar()[1]
    monthnum = now.month
    if log.get("last_daily_reset") != str(today):
        log["daily_seconds"] = 0
        log["last_daily_reset"] = str(today)
    if log.get("last_weekly_reset") != str(weeknum):
        log["weekly_seconds"] = 0
        log["last_weekly_reset"] = str(weeknum)
    if log.get("last_monthly_reset") != str(monthnum):
        log["monthly_seconds"] = 0
        log["last_monthly_reset"] = str(monthnum)

    # small increment so active users get credit instantly (main increments by inactivity_poller)
    log["daily_seconds"] = log.get("daily_seconds", 0) + 1
    log["weekly_seconds"] = log.get("weekly_seconds", 0) + 1
    log["monthly_seconds"] = log.get("monthly_seconds", 0) + 1

    # if they were marked inactive and now back -> log to mod channel if user has one of the tracked roles
    if log.get("inactive", False):
        guild = message.guild
        member = message.author
        if guild and member:
            has_role = any((rid in [r.id for r in member.roles]) for rid in ACTIVE_LOG_ROLE_IDS)
            if has_role:
                lc = guild.get_channel(MOD_ACTIVITY_LOG_CHANNEL)
                ping = member.mention if log.get("rping_on", False) else member.display_name
                if lc:
                    try:
                        await lc.send(f"🟢 {ping} has come back online (sent a message).")
                    except Exception:
                        pass
    log["inactive"] = False
    await save_data_async()

# inactivity poller: runs every 1 second, increments active counters by 1 per sec
@tasks.loop(seconds=1)
async def inactivity_poller():
    now = datetime.datetime.now(datetime.timezone.utc)
    for uid_str, log in list(activity_logs.items()):
        # ensure delay assigned
        if not log.get("offline_delay"):
            log["offline_delay"] = random.randint(INACTIVITY_MIN, INACTIVITY_MAX)
        last_msg_iso = log.get("last_message")
        if last_msg_iso:
            try:
                last_msg_dt = datetime.datetime.fromisoformat(last_msg_iso)
            except Exception:
                last_msg_dt = now
                log["last_message"] = now.isoformat()
            delta = (now - last_msg_dt).total_seconds()
            delay = int(log.get("offline_delay", INACTIVITY_MIN))
            if delta >= delay:
                # mark offline if not already started
                if not log.get("offline_start"):
                    offline_start = last_msg_dt + datetime.timedelta(seconds=delay)
                    log["offline_start"] = offline_start.isoformat()
                    # send mod-only log if member has active role
                    guild = bot.get_guild(GUILD_ID)
                    if guild:
                        try:
                            member = guild.get_member(int(uid_str))
                            if member:
                                has_role = any((rid in [r.id for r in member.roles]) for rid in ACTIVE_LOG_ROLE_IDS)
                                if has_role:
                                    lc = guild.get_channel(MOD_ACTIVITY_LOG_CHANNEL)
                                    ping = member.mention if log.get("rping_on", False) else member.display_name
                                    if lc:
                                        try:
                                            await lc.send(f"⚫ {ping} has gone inactive ({delay}s without message).")
                                        except Exception:
                                            pass
                        except Exception:
                            pass
                # update offline_seconds
                try:
                    offline_start_dt = datetime.datetime.fromisoformat(log["offline_start"])
                    log["offline_seconds"] = (now - offline_start_dt).total_seconds()
                except Exception:
                    log["offline_seconds"] = delta
                log["inactive"] = True
            else:
                # still active - reset offline counters & increment online by 1s
                log["offline_seconds"] = 0
                log["offline_start"] = None
                log["inactive"] = False
                log["online_seconds"] = log.get("online_seconds", 0) + 1
                log["daily_seconds"] = log.get("daily_seconds", 0) + 1
                log["weekly_seconds"] = log.get("weekly_seconds", 0) + 1
                log["monthly_seconds"] = log.get("monthly_seconds", 0) + 1
    await save_data_async()

# auto-unmute background - checks stored mute_expires and removes role + timeout, logs unmute
@tasks.loop(seconds=15)
async def auto_unmute_loop():
    now = datetime.datetime.now(datetime.timezone.utc)
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    muted_role = guild.get_role(MUTED_ROLE_ID)
    for uid_str, log in list(activity_logs.items()):
        expire_iso = log.get("mute_expires")
        if not expire_iso:
            continue
        try:
            expire_dt = datetime.datetime.fromisoformat(expire_iso)
        except Exception:
            log["mute_expires"] = None
            await save_data_async()
            continue
        if now >= expire_dt:
            member = guild.get_member(int(uid_str))
            if member and muted_role and muted_role in member.roles:
                try:
                    await member.remove_roles(muted_role, reason="Auto-unmute (mute expired)")
                except Exception:
                    pass
                try:
                    await member.timeout(None, reason="Auto-unmute (mute expired)")
                except Exception:
                    pass
                try:
                    await member.send(f"🔊 Your mute in **{guild.name}** has expired and you were unmuted.")
                except Exception:
                    pass
                embed = build_unmute_embed(member, bot.user, log.get("mute_reason"), None)
                await send_server_log(embed)
            log["mute_expires"] = None
            log["mute_reason"] = None
            log["mute_responsible"] = None
            await save_data_async()

# ------------------ Commands ------------------
@bot.command(name="rmute")
@commands.has_permissions(moderate_members=True)
async def cmd_rmute(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """Mute + timeout a member. Deletes your command message for anonymity."""
    seconds = parse_duration_abbrev(duration)
    if seconds is None:
        return await ctx.reply("❌ Invalid duration. Use `1m`, `1h`, `1d`, etc.", mention_author=False)
    guild = ctx.guild
    if not guild:
        return await ctx.reply("❌ Use this command inside a server.", mention_author=False)

    # delete the command message to preserve anonymity
    try:
        await ctx.message.delete()
    except Exception:
        pass

    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        return await ctx.send("❌ Muted role not found in this guild.", delete_after=10)

    # attempt to add muted role
    try:
        await member.add_roles(muted_role, reason=f"Muted by {ctx.author}")
    except discord.Forbidden:
        return await ctx.send("❌ Permission error when adding muted role.", delete_after=10)
    except Exception:
        pass

    # Apply Discord timeout
    try:
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        try:
            await member.timeout(until, reason=f"Muted by {ctx.author}: {reason}")
        except TypeError:
            await member.timeout(datetime.timedelta(seconds=seconds), reason=f"Muted by {ctx.author}: {reason}")
        except Exception:
            pass
    except discord.Forbidden:
        # undo role if unable to timeout
        try:
            await member.remove_roles(muted_role, reason="Failed to timeout after adding role")
        except Exception:
            pass
        return await ctx.send("❌ Missing permission to timeout this user.", delete_after=10)

    # DM member (EST formatting for time)
    try:
        expire_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        # EST time string
        est = expire_dt.astimezone(ZoneInfo("America/New_York"))
        dm_text = (
            f"You have been muted in **{guild.name}** until\n"
            f"__{est.strftime('%Y-%m-%d')}__\n"
            f"**{est.strftime('%I:%M:%S %p')} EST**\n"
            f"duration: {duration}\n"
            f"Reason: `{reason}`"
        )
        await member.send(dm_text)
    except Exception:
        pass

    # update persisted data
    now = datetime.datetime.now(datetime.timezone.utc)
    log = get_user_log(member.id)
    log["mute_expires"] = (now + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = str(ctx.author.id)
    log["mute_count"] = log.get("mute_count", 0) + 1

    # increment muter_count for the moderator who used rmute
    muter_log = get_user_log(ctx.author.id)
    muter_log["muter_count"] = muter_log.get("muter_count", 0) + 1

    await save_data_async()

    # send embed to server log with stacked timezones
    embed = build_mute_embed(member, ctx.author, reason, seconds)
    await send_server_log(embed)

    # ephemeral-like channel feedback: send and delete quickly
    try:
        await ctx.send(f"✅ {member.mention} muted for `{duration}`.", delete_after=8)
    except Exception:
        pass

@bot.command(name="runmute")
@commands.has_permissions(moderate_members=True)
async def cmd_runmute(ctx: commands.Context, member: discord.Member):
    guild = ctx.guild
    if not guild:
        return await ctx.reply("❌ Use inside a guild.", mention_author=False)
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role and muted_role in member.roles:
        try:
            await member.remove_roles(muted_role, reason=f"Unmuted by {ctx.author}")
            try:
                await member.timeout(None, reason=f"Unmuted by {ctx.author}")
            except Exception:
                pass
            try:
                await member.send(f"You have been unmuted in **{guild.name}** by {ctx.author.display_name}.")
            except Exception:
                pass
        except discord.Forbidden:
            return await ctx.reply("❌ Missing permission to remove role.", mention_author=False)
    # clear stored mute
    log = get_user_log(member.id)
    orig_reason = log.get("mute_reason")
    orig_expires_iso = log.get("mute_expires")
    orig_duration_seconds = None
    if orig_expires_iso:
        try:
            dt = datetime.datetime.fromisoformat(orig_expires_iso)
            orig_duration_seconds = int((dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
            if orig_duration_seconds < 0:
                orig_duration_seconds = None
        except Exception:
            orig_duration_seconds = None
    log["mute_expires"] = None
    log["mute_reason"] = None
    log["mute_responsible"] = None
    await save_data_async()

    embed = build_unmute_embed(member, ctx.author, orig_reason, orig_duration_seconds)
    await send_server_log(embed)
    await ctx.reply(f"✅ {member.mention} has been unmuted.", mention_author=False)

@bot.command(name="rhelp")
async def cmd_rhelp(ctx: commands.Context):
    embed = discord.Embed(title="🤖 Moderation Commands", color=0x3498db)
    embed.add_field(name="!rmute", value="`!rmute [user] [duration] [reason]` — Mute a user (deletes your command message).", inline=False)
    embed.add_field(name="!runmute", value="`!runmute [user]` — Unmute a user.", inline=False)
    embed.add_field(name="!timetrack", value="`!timetrack [user]` — Shows online/offline and counters.", inline=False)
    embed.add_field(name="!rmlb", value="`!rmlb` — Leaderboard of who used !rmute most.", inline=False)
    embed.add_field(name="!rping", value="`!rping [on/off] [user]` — Toggle ping for online/offline notices.", inline=False)
    embed.set_footer(text="Use prefix commands only.")
    await ctx.send(embed=embed)

@bot.command(name="rping")
async def cmd_rping(ctx: commands.Context, toggle: str, member: discord.Member = None):
    allowed = ctx.author.guild_permissions.administrator or any(r.id in ACTIVE_LOG_ROLE_IDS for r in ctx.author.roles)
    if not allowed:
        return await ctx.reply("❌ You do not have permission to use !rping.", mention_author=False)
    toggle = toggle.lower()
    if toggle not in {"on", "off"}:
        return await ctx.reply("❌ Usage: `!rping [on/off] [user]`", mention_author=False)
    member = member or ctx.author
    log = get_user_log(member.id)
    log["rping_on"] = (toggle == "on")
    await save_data_async()
    await ctx.reply(f"✅ Ping for {member.display_name} set to `{toggle}`.", mention_author=False)

@bot.command(name="rmlb")
async def cmd_rmlb(ctx: commands.Context):
    # leaderboard showing who USED !rmute the most
    scores = []
    for uid_str, data in activity_logs.items():
        count = data.get("muter_count", 0)
        if count > 0:
            try:
                uid_int = int(uid_str)
            except Exception:
                continue
            member = ctx.guild.get_member(uid_int)
            name = member.display_name if member else f"User {uid_str}"
            scores.append((name, count))
    scores.sort(key=lambda x: x[1], reverse=True)
    if not scores:
        embed = discord.Embed(title="📊 !rmlb", description="No data yet.", color=0xFFD700)
    else:
        desc = "\n".join([f"🏆 {i+1}. {name} — {count} rmutes" for i, (name, count) in enumerate(scores[:10])])
        embed = discord.Embed(title="📊 !rmlb — Top Muters (who used !rmute)", description=desc, color=0xFFD700)
    await ctx.reply(embed=embed, mention_author=False)

@bot.command(name="timetrack")
async def cmd_timetrack(ctx: commands.Context, member: discord.Member = None):
    member = member or ctx.author
    log = get_user_log(member.id)
    embed = build_timetrack_embed(member, log)
    await ctx.send(embed=embed)

# ------------------ Message delete & attachments handling ------------------
@bot.event
async def on_message_delete(message: discord.Message):
    guild = getattr(message, "guild", None)
    cached = message_cache.get(getattr(message, "id", None))
    author = getattr(message, "author", None)
    channel = getattr(message, "channel", None)
    content = getattr(message, "content", None)
    attachments = []
    if cached:
        content = cached.get("content") or content
        attachments = cached.get("attachments", []) or []
    else:
        try:
            attachments = [a.url for a in message.attachments]
        except Exception:
            attachments = []
    embed = discord.Embed(title="🗑️ Message Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Author", value=(author.mention if author else (cached.get("author_name") if cached else "Unknown")), inline=True)
    embed.add_field(name="Channel", value=(channel.mention if channel else (f"<#{cached['channel_id']}>" if cached else "Unknown")), inline=True)
    embed.add_field(name="Content", value=(content[:1024] if content else "⚠️ (empty or embed/attachment)"), inline=False)

    # attempt to find deleter via audit log
    if guild:
        try:
            executor = await fetch_audit_executor(guild, discord.AuditLogAction.message_delete)
            if executor:
                embed.add_field(name="Deleted by", value=executor.mention, inline=True)
        except Exception:
            pass

    files = None
    if attachments:
        try:
            files = await download_and_prepare_files(attachments)
            if files:
                embed.add_field(name="Attachment(s) (reuploaded)", value="\n".join(attachments), inline=False)
        except Exception:
            embed.add_field(name="Attachment(s)", value="\n".join(attachments), inline=False)

    await send_server_log(embed, files=files)
    if cached:
        message_cache.pop(message.id, None)

@bot.event
async def on_bulk_message_delete(messages: List[discord.Message]):
    lines = []
    for m in messages:
        author = getattr(m, "author", None)
        author_name = getattr(author, "display_name", str(author)) if author else "Unknown"
        content = getattr(m, "content", "")
        created = getattr(m, "created_at", datetime.datetime.now(datetime.timezone.utc))
        lines.append(f"[{created.isoformat()}] {author_name}: {content}")
    if not lines:
        return
    dump = "\n".join(lines)
    buf = io.BytesIO(dump.encode("utf-8"))
    buf.seek(0)
    filename = f"purge_{int(datetime.datetime.now().timestamp())}.txt"
    file = discord.File(fp=buf, filename=filename)
    embed = discord.Embed(title="🧹 Messages Purged (bulk delete)", color=0xf39c12, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Count", value=str(len(lines)), inline=True)
    embed.add_field(name="Time", value=datetime.datetime.now(datetime.timezone.utc).isoformat(), inline=True)
    await send_server_log(embed, files=[file])

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.author and before.author.bot:
        return
    embed = discord.Embed(title="✏️ Message Edited", color=0xf39c12, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Author", value=before.author.mention if before.author else "Unknown", inline=True)
    embed.add_field(name="Channel", value=before.channel.mention if before.channel else "Unknown", inline=True)
    embed.add_field(name="Before", value=(before.content[:1024] or "(embed/attachment)"), inline=False)
    embed.add_field(name="After", value=(after.content[:1024] or "(embed/attachment)"), inline=False)
    await send_server_log(embed)

# ------------------ Guild / role / channel / webhook / emoji events ------------------
@bot.event
async def on_guild_role_create(role: discord.Role):
    guild = role.guild
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.role_create, target_id=role.id)
    embed = discord.Embed(title="🆕 Role Created", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Role", value=role.name, inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_role_delete(role: discord.Role):
    guild = role.guild
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.role_delete, target_id=role.id)
    embed = discord.Embed(title="❌ Role Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Role name", value=role.name, inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    guild = after.guild
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.role_update, target_id=after.id)
    changed = []
    if before.name != after.name:
        changed.append(f"Name: `{before.name}` → `{after.name}`")
    if before.permissions != after.permissions:
        changed.append("Permissions changed")
    embed = discord.Embed(title="⚙️ Role Updated", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Role", value=after.name, inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    if changed:
        embed.add_field(name="Changes", value="\n".join(changed), inline=False)
    await send_server_log(embed)

@bot.event
async def on_guild_channel_create(channel):
    guild = channel.guild
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.channel_create)
    embed = discord.Embed(title="🆕 Channel Created", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Channel", value=channel.mention if hasattr(channel, "mention") else str(channel), inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.channel_delete)
    embed = discord.Embed(title="❌ Channel Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Channel name", value=getattr(channel, "name", str(channel)), inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_channel_update(before, after):
    guild = after.guild
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.channel_update, target_id=after.id)
    changed = []
    if getattr(before, "name", None) != getattr(after, "name", None):
        changed.append(f"Name: `{getattr(before,'name',None)}` → `{getattr(after,'name',None)}`")
    # permissions changed detection can be more involved; we note existence
    embed = discord.Embed(title="⚙️ Channel Updated", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Channel", value=after.mention if hasattr(after, "mention") else str(after), inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    if changed:
        embed.add_field(name="Changes", value="\n".join(changed), inline=False)
    await send_server_log(embed)

@bot.event
async def on_webhooks_update(channel):
    guild = channel.guild
    # best-effort executor
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.webhook_create) or await fetch_audit_executor(guild, discord.AuditLogAction.webhook_update)
    embed = discord.Embed(title="🔗 Webhooks Updated", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    embed.add_field(name="Recent audit (may be None)", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_emojis_update(guild: discord.Guild, before, after):
    before_ids = {e.id for e in before}
    after_ids = {e.id for e in after}
    created = after_ids - before_ids
    deleted = before_ids - after_ids
    for cid in created:
        emoji = discord.utils.get(after, id=cid)
        executor = await fetch_audit_executor(guild, discord.AuditLogAction.emoji_create)
        embed = discord.Embed(title="🎉 Emoji Created", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="Emoji", value=str(emoji), inline=True)
        embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
        await send_server_log(embed)
    for cid in deleted:
        emoji = discord.utils.get(before, id=cid)
        executor = await fetch_audit_executor(guild, discord.AuditLogAction.emoji_delete)
        embed = discord.Embed(title="❌ Emoji Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="Emoji", value=str(emoji) if emoji else str(cid), inline=True)
        embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
        await send_server_log(embed)

@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.ban, target_id=getattr(user, "id", None))
    embed = discord.Embed(title="🔨 User Banned", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="User", value=getattr(user, "mention", str(user)), inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.unban, target_id=getattr(user, "id", None))
    embed = discord.Embed(title="✅ User Unbanned", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="User", value=str(user), inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_member_remove(member: discord.Member):
    embed = discord.Embed(title="👋 Member Left", color=0x95a5a6, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="User", value=member.mention, inline=True)
    await send_server_log(embed)

@bot.event
async def on_member_join(member: discord.Member):
    acct_age = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days if member.created_at else "Unknown"
    embed = discord.Embed(title="🟢 Member Joined", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Account age (days)", value=str(acct_age), inline=True)
    await send_server_log(embed)

@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    guild = after.guild
    # nickname change
    if before.nick != after.nick:
        embed = discord.Embed(title="🔤 Nickname Changed", color=0x9b59b6, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="User", value=after.mention, inline=True)
        embed.add_field(name="Before", value=(before.nick or "(none)"), inline=True)
        embed.add_field(name="After", value=(after.nick or "(none)"), inline=True)
        await send_server_log(embed)

    # detect untimeout/manual unmute by others: if timed_out_until changed from present -> None
    try:
        before_to = getattr(before, "timed_out_until", None)
        after_to = getattr(after, "timed_out_until", None)
    except Exception:
        before_to = None
        after_to = None

    if before_to and not after_to:
        executor = await fetch_audit_executor(guild, discord.AuditLogAction.member_update, target_id=after.id)
        muted_role = guild.get_role(MUTED_ROLE_ID)
        if muted_role and muted_role in after.roles:
            try:
                await after.remove_roles(muted_role, reason="Detected untimeout/manual unmute")
            except Exception:
                pass
        embed = build_unmute_embed(after, executor or bot.user, None, None)
        await send_server_log(embed)
        # clear stored mute info
        log = get_user_log(after.id)
        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        await save_data_async()

@bot.event
async def on_guild_update(before: discord.Guild, after: discord.Guild):
    embed = discord.Embed(title="🏷️ Guild Updated", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Before Name", value=before.name, inline=True)
    embed.add_field(name="After Name", value=after.name, inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_integrations_update(guild: discord.Guild):
    embed = discord.Embed(title="🔗 Integrations Updated", color=0xf39c12, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Guild", value=guild.name, inline=True)
    await send_server_log(embed)

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    # join / leave / move / mute / deafen
    guild = member.guild
    if before.channel != after.channel:
        # join or leave or move
        embed = discord.Embed(title="🔊 Voice State Change", color=0x8e44ad, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="From", value=(before.channel.mention if before.channel else "None"), inline=True)
        embed.add_field(name="To", value=(after.channel.mention if after.channel else "None"), inline=True)
        await send_server_log(embed)
    if before.self_mute != after.self_mute:
        embed = discord.Embed(title="🎙️ Self Mute Toggle", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Muted", value=str(after.self_mute), inline=True)
        await send_server_log(embed)
    if before.self_deaf != after.self_deaf:
        embed = discord.Embed(title="🔇 Self Deaf Toggle", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Deafened", value=str(after.self_deaf), inline=True)
        await send_server_log(embed)

@bot.event
async def on_thread_create(thread: discord.Thread):
    embed = discord.Embed(title="🧵 Thread Created", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Thread", value=thread.mention if hasattr(thread, "mention") else thread.name, inline=True)
    await send_server_log(embed)

@bot.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    if before.archived != after.archived:
        title = "📦 Thread Archived" if after.archived else "📤 Thread Unarchived"
        embed = discord.Embed(title=title, color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="Thread", value=after.mention if hasattr(after, "mention") else after.name, inline=True)
        await send_server_log(embed)

@bot.event
async def on_invite_create(invite: discord.Invite):
    executor = await fetch_audit_executor(invite.guild, discord.AuditLogAction.invite_create)
    embed = discord.Embed(title="✉️ Invite Created", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Code", value=invite.code, inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_invite_delete(invite: discord.Invite):
    executor = await fetch_audit_executor(invite.guild, discord.AuditLogAction.invite_delete)
    embed = discord.Embed(title="❌ Invite Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Code", value=invite.code if invite.code else "Unknown", inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

# ------------------ START & BOOT ------------------
if __name__ == "__main__":
    load_data()
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ DISCORD_TOKEN environment variable not set. Exiting.")
        raise SystemExit(1)
    bot.run(TOKEN)
