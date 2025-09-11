# main.py
# Full bot: prefix commands only. Handles rmute/runmute/timetrack/rmlb/rhelp + inactivity + auto-unmute + rping + Render keep-alive.

import os
import json
import random
import asyncio
import datetime
from zoneinfo import ZoneInfo
from typing import Optional
from flask import Flask

import discord
from discord.ext import commands, tasks

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

ACTIVE_LOG_ROLE_IDS = {
    1410422029236047975,
    1410419345234067568,
    1410421647265108038,
    1410421466666631279,
    1410423594579918860,
    1410420126003630122,
    1410419924173848626
}

TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo"),
}

DATA_FILE = "activity_logs.json"

# ------------------ BOT & INTENTS ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ------------------ DATA HANDLING ------------------
activity_logs = {}
data_lock = asyncio.Lock()

def load_data():
    global activity_logs
    try:
        with open(DATA_FILE, "r") as f:
            activity_logs = json.load(f)
    except Exception:
        activity_logs = {}

async def save_data_async():
    async with data_lock:
        with open(DATA_FILE, "w") as f:
            json.dump(activity_logs, f, indent=4)

def get_user_log(user_id: int):
    uid = str(user_id)
    if uid not in activity_logs:
        activity_logs[uid] = {
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
            "last_mute_at": None,
            "user_ping_enabled": True  # ping preference
        }
    return activity_logs[uid]

def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, round(seconds)))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)

def parse_duration_abbrev(s: str) -> Optional[int]:
    s = s.strip().lower()
    if len(s) < 2:
        return None
    unit = s[-1]
    try:
        amount = int(s[:-1])
    except ValueError:
        return None
    multipliers = {"s":1, "m":60, "h":3600, "d":86400}
    if unit not in multipliers:
        return None
    return amount * multipliers[unit]

# ------------------ EMBED HELPERS ------------------
def build_mute_embed(member: discord.Member, by: discord.Member, reason: str, duration_seconds: int):
    expire_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
    embed = discord.Embed(
        title="🔇 User Muted",
        description=f"{member.mention} was muted",
        color=0xFF5A5F,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Muted User", value=member.mention, inline=True)
    embed.add_field(name="🔒 Muted By", value="Anonymous", inline=True)
    embed.add_field(name="⏳ Duration", value=fmt_duration(duration_seconds), inline=True)
    embed.add_field(name="📝 Reason", value=reason or "No reason provided", inline=False)
    tz_lines = [f"{emoji} {expire_dt.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="🕒 Unmute Time", value="\n".join(tz_lines), inline=False)
    return embed

def build_unmute_embed(member: discord.Member, by: discord.Member, original_reason: Optional[str], original_duration_seconds: Optional[int]):
    embed = discord.Embed(
        title="🔊 User Unmuted",
        description=f"{member.mention} was unmuted",
        color=0x2ECC71,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Unmuted User", value=member.mention, inline=True)
    embed.add_field(name="🔓 Unmuted By", value=by.mention, inline=True)
    if original_reason:
        embed.add_field(name="📝 Original Reason", value=original_reason, inline=False)
    if original_duration_seconds:
        embed.add_field(name="⏳ Original Duration", value=fmt_duration(original_duration_seconds), inline=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    tz_lines = [f"{emoji} {now.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="🕒 Unmuted At (timezones)", value="\n".join(tz_lines), inline=False)
    return embed

# ------------------ COMMANDS ------------------
@bot.command()
@commands.has_permissions(manage_roles=True)
async def rmute(ctx, member: discord.Member, duration: str, *, reason: Optional[str] = None):
    # Convert duration like "10s", "5m", "2h" to seconds
    duration_seconds = parse_duration_abbrev(duration)
    if duration_seconds is None:
        await ctx.send("❌ Invalid duration. Use 10s, 5m, 1h, 2d etc.", delete_after=5)
        return

    muted_role = ctx.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await ctx.send("❌ Muted role not found.", delete_after=5)
        return

    try:
        await member.add_roles(muted_role, reason=reason)
        expire_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
        log = get_user_log(member.id)
        log["mute_expires"] = expire_time.isoformat()
        log["mute_reason"] = reason
        log["mute_responsible"] = ctx.author.id
        await save_data_async()

        log_channel = ctx.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = build_mute_embed(member, ctx.author, reason, duration_seconds)
            await log_channel.send(embed=embed)

        # Delete the original command message
        await ctx.message.delete()
        await ctx.send(f"✅ {member.mention} has been muted.", delete_after=5)

    except Exception as e:
        await ctx.send(f"❌ Failed to mute: {e}", delete_after=5)

# ------------------ ON MESSAGE EVENT ------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    uid = message.author.id
    now = datetime.datetime.now(datetime.timezone.utc)
    user_log = get_user_log(uid)

    # reset offline tracking
    user_log["last_message"] = now.isoformat()
    user_log["offline_seconds"] = 0
    user_log["offline_start"] = None
    if not user_log.get("offline_delay"):
        user_log["offline_delay"] = random.randint(50, 60)
    await save_data_async()

    # increment online counters
    user_log["online_seconds"] = user_log.get("online_seconds", 0) + 1
    today = now.date()
    weeknum = now.isocalendar()[1]
    monthnum = now.month

    if user_log.get("last_daily_reset") != str(today):
        user_log["daily_seconds"] = 0
        user_log["last_daily_reset"] = str(today)
    if user_log.get("last_weekly_reset") != str(weeknum):
        user_log["weekly_seconds"] = 0
        user_log["last_weekly_reset"] = str(weeknum)
    if user_log.get("last_monthly_reset") != str(monthnum):
        user_log["monthly_seconds"] = 0
        user_log["last_monthly_reset"] = str(monthnum)

    user_log["daily_seconds"] += 1
    user_log["weekly_seconds"] += 1
    user_log["monthly_seconds"] += 1

    # back active notification
    was_inactive = user_log.get("inactive", False)
    if was_inactive:
        guild = message.guild
        member = message.author
        send_back_active = any(guild.get_role(rid) in member.roles for rid in ACTIVE_LOG_ROLE_IDS if guild.get_role(rid))
        if send_back_active:
            log_channel = guild.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                try:
                    if user_log.get("user_ping_enabled", True):
                        await log_channel.send(f"🟢 {member.mention} has come back online (sent a message).")
                    else:
                        await log_channel.send(f"🟢 {member.display_name} has come back online (sent a message).")
                except Exception:
                    pass

    # reset inactive flag
    user_log["inactive"] = False
    await save_data_async()
    await bot.process_commands(message)

# ------------------ AUTO UNMUTE TASK ------------------
@tasks.loop(seconds=10)
async def auto_unmute():
    now = datetime.datetime.now(datetime.timezone.utc)
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        return

    for uid, log in activity_logs.items():
        expires = log.get("mute_expires")
        if expires:
            try:
                expire_dt = datetime.datetime.fromisoformat(expires)
            except Exception:
                continue
            if expire_dt <= now:
                member = guild.get_member(int(uid))
                if member:
                    try:
                        if muted_role in member.roles:
                            await member.remove_roles(muted_role, reason="Auto-unmute expired")
                        try:
                            await member.timeout(None, reason="Auto-unmute expired")
                        except Exception:
                            pass
                        log["mute_expires"] = None
                        log["mute_reason"] = None
                        log["mute_responsible"] = None
                        await save_data_async()
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        if log_channel:
                            embed = build_unmute_embed(member, bot.user, None, None)
                            await log_channel.send(embed=embed)
                    except Exception:
                        pass

# ------------------ STARTUP ------------------
load_data()
asyncio.get_event_loop().create_task(auto_unmute())  # Start auto-unmute in event loop

# ------------------ RENDER KEEPALIVE ------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

import threading
threading.Thread(target=run_flask).start()

TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN not set")
else:
    bot.run(TOKEN)
