# ------------------ IMPORTS ------------------
import discord
from discord.ext import commands, tasks
import asyncio
import datetime
from zoneinfo import ZoneInfo
import os
import json
from flask import Flask
import threading

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739
LOG_CHANNEL_ID = 1403422664521023648   # Updated channel ID you gave
MUTED_ROLE_ID = 1410423854563721287
TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo"),
}

DATA_FILE = "activity_logs.json"
activity_logs = {}

# ------------------ INTENTS & BOT ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ HELPER FUNCTIONS ------------------
def load_data():
    global activity_logs
    try:
        with open(DATA_FILE, "r") as f:
            activity_logs = json.load(f)
    except:
        activity_logs = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(activity_logs, f, indent=4)

def get_user_log(user_id):
    uid = str(user_id)
    if uid not in activity_logs:
        activity_logs[uid] = {}
    return activity_logs[uid]

def format_duration(seconds):
    seconds = int(seconds)
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts) if parts else "0s"

# ------------------ RMUTE ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str):
    """Mute a member"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    # Convert duration
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        amount, unit = int(duration[:-1]), duration[-1]
        seconds = amount * multipliers.get(unit, 60)
    except:
        return await ctx.send("❌ Invalid duration format. Use 1m, 1h, 1d, etc.")

    # Add role + Discord timeout
    try:
        await member.add_roles(muted_role)
        await member.timeout(datetime.timedelta(seconds=seconds))
        try:
            await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
        except:
            pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to mute {member}.")

    # Save log
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count", 0) + 1
    save_data()

    # Log embed
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="🔇 User Muted",
        description=f"{member.mention} has been muted",
        color=0xFF0000,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Muted by", value=ctx.author.mention, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    tz_times = [
        f"{emoji} {(datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}"
        for emoji, tz in TIMEZONES.items()
    ]
    embed.add_field(name="Unmute Timezones", value="\n".join(tz_times), inline=False)
    if log_channel:
        await log_channel.send(embed=embed)
    await ctx.send(f"✅ {member.mention} has been muted.")
    # ------------------ RUNMUTE ------------------
@bot.command()
async def runmute(ctx, member: discord.Member):
    """Unmute a member manually"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    try:
        await member.remove_roles(muted_role)
        await member.timeout(None)  # remove timeout
        try:
            await member.send("🔊 You have been unmuted.")
        except:
            pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to unmute {member}.")

    log["mute_expires"] = None
    save_data()

    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="🔊 User Unmuted",
        description=f"{member.mention} has been unmuted",
        color=0x00FF00,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Unmuted by", value=ctx.author.mention, inline=True)
    if log_channel:
        await log_channel.send(embed=embed)
    await ctx.send(f"✅ {member.mention} has been unmuted.")

# ------------------ TIMETRACK ------------------
last_active = {}  # stores last activity timestamp
inactive_status = {}  # tracks if user is marked inactive

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    last_active[message.author.id] = datetime.datetime.utcnow()
    inactive_status[message.author.id] = False  # reset inactivity
    await bot.process_commands(message)

@tasks.loop(seconds=10)
async def check_inactivity():
    now = datetime.datetime.utcnow()
    for user_id, last_time in list(last_active.items()):
        if (now - last_time).total_seconds() > 53:  # 53s inactivity threshold
            if not inactive_status.get(user_id, False):
                inactive_status[user_id] = True
                user = bot.get_user(user_id)
                if user:
                    log = get_user_log(user_id)
                    log["last_seen"] = now.isoformat()
                    save_data()
                    log_channel = bot.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        await log_channel.send(f"⚫ {user.mention} has gone inactive (53s no activity).")

@bot.command()
async def timetrack(ctx, member: discord.Member = None):
    """Track how long someone has been online"""
    member = member or ctx.author
    log = get_user_log(member.id)
    last_seen = log.get("last_seen")
    if last_seen:
        last_seen_dt = datetime.datetime.fromisoformat(last_seen)
        duration = format_duration((datetime.datetime.utcnow() - last_seen_dt).total_seconds())
    else:
        duration = "No data"
    await ctx.send(f"🕒 {member.mention} last active {duration} ago.")

# ------------------ RMLB ------------------
@bot.command()
async def rmlb(ctx):
    """Show leaderboard of mutes"""
    leaderboard = []
    for uid, data in activity_logs.items():
        mute_count = data.get("mute_count", 0)
        if mute_count > 0:
            leaderboard.append((uid, mute_count))
    leaderboard.sort(key=lambda x: x[1], reverse=True)

    embed = discord.Embed(title="📊 Mute Leaderboard", color=0x3498db)
    for i, (uid, count) in enumerate(leaderboard[:10], start=1):
        user = bot.get_user(int(uid))
        name = user.name if user else f"User {uid}"
        embed.add_field(name=f"#{i} {name}", value=f"{count} mutes", inline=False)

    await ctx.send(embed=embed)
    # ------------------ AUTO UNMUTE LOOP ------------------
@tasks.loop(seconds=30)
async def auto_unmute():
    """Checks muted users and unmutes if time expired"""
    now = datetime.datetime.utcnow()
    guild = discord.utils.get(bot.guilds)  # get first guild
    if not guild:
        return
    muted_role = guild.get_role(MUTED_ROLE_ID)

    for uid, log in list(activity_logs.items()):
        mute_exp = log.get("mute_expires")
        if mute_exp:
            mute_time = datetime.datetime.fromisoformat(mute_exp)
            if now >= mute_time:
                member = guild.get_member(int(uid))
                if member and muted_role in member.roles:
                    try:
                        await member.remove_roles(muted_role)
                        await member.timeout(None)
                        try:
                            await member.send("🔊 You were auto-unmuted (mute expired).")
                        except:
                            pass
                    except discord.Forbidden:
                        continue
                    log["mute_expires"] = None
                    save_data()

                    log_channel = guild.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        await log_channel.send(f"🔊 {member.mention} was auto-unmuted (mute expired).")

# ------------------ WEB SERVER (Render Keep Alive) ------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "✅ Bot is running!"

async def start_web():
    port = int(os.environ.get("PORT", 8080))
    loop = asyncio.get_event_loop()
    server = loop.run_in_executor(None, app.run, "0.0.0.0", port)
    return server

# ------------------ STARTUP ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    auto_unmute.start()
    check_inactivity.start()

    # start web server for Render
    asyncio.create_task(start_web())
