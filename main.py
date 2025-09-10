# ------------------ IMPORTS ------------------
import os
import discord
from discord.ext import commands, tasks
import datetime
import json
import random
from zoneinfo import ZoneInfo
from flask import Flask
import threading
import re

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    exit()

GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD_MIN = 50
INACTIVITY_THRESHOLD_MAX = 60

TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo")
}

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ FLASK WEB SERVER ------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ------------------ DATA HANDLING ------------------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        activity_logs = json.load(f)
else:
    activity_logs = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(activity_logs, f, indent=4)

def get_user_log(user_id):
    uid = str(user_id)
    if uid not in activity_logs:
        activity_logs[uid] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "offline_start": None,
            "offline_delay": None,
            "last_message": None,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_daily_reset": None,
            "last_weekly_reset": None,
            "last_monthly_reset": None,
            "mute_count": 0
        }
    return activity_logs[uid]

def format_duration(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hrs: parts.append(f"{hrs}h")
    if mins: parts.append(f"{mins}m")
    if sec: parts.append(f"{sec}s")
    return " ".join(parts) if parts else "0s"

def parse_duration(duration_str):
    """Parse duration like 1m, 2h, 3d to seconds"""
    match = re.match(r"(\d+)([smhd])", duration_str.lower())
    if not match:
        return None
    val, unit = match.groups()
    val = int(val)
    if unit == "s": return val
    if unit == "m": return val * 60
    if unit == "h": return val * 3600
    if unit == "d": return val * 86400
    return None

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    timetrack_update.start()
    mute_check.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    log = get_user_log(message.author.id)
    log["offline_seconds"] = 0
    log["offline_start"] = None
    log["offline_delay"] = None
    log["last_message"] = datetime.datetime.utcnow().isoformat()
    save_data()
    await bot.process_commands(message)

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=5)
async def timetrack_update():
    now = datetime.datetime.utcnow()
    for uid, log in activity_logs.items():
        last_msg = log.get("last_message")
        if last_msg:
            last_msg_time = datetime.datetime.fromisoformat(last_msg)
            if not log.get("offline_delay"):
                log["offline_delay"] = random.randint(INACTIVITY_THRESHOLD_MIN, INACTIVITY_THRESHOLD_MAX)
            delta = (now - last_msg_time).total_seconds()
            if delta >= log["offline_delay"]:
                if not log.get("offline_start"):
                    log["offline_start"] = last_msg_time + datetime.timedelta(seconds=log["offline_delay"])
                log["offline_seconds"] = (now - log["offline_start"]).total_seconds()
            else:
                log["online_seconds"] += 5
                log["offline_start"] = None
                log["offline_seconds"] = 0
        # Daily / Weekly / Monthly resets
        today = datetime.datetime.utcnow().date()
        weekday = today.isocalendar()[1]
        month = today.month
        if not log.get("last_daily_reset") or log["last_daily_reset"] != str(today):
            log["daily_seconds"] = 0
            log["last_daily_reset"] = str(today)
        if not log.get("last_weekly_reset") or log["last_weekly_reset"] != str(weekday):
            log["weekly_seconds"] = 0
            log["last_weekly_reset"] = str(weekday)
        if not log.get("last_monthly_reset") or log["last_monthly_reset"] != str(month):
            log["monthly_seconds"] = 0
            log["last_monthly_reset"] = str(month)
        log["daily_seconds"] += 5
        log["weekly_seconds"] += 5
        log["monthly_seconds"] += 5
    save_data()

@tasks.loop(seconds=5)
async def mute_check():
    now = datetime.datetime.utcnow()
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    for uid, log in activity_logs.items():
        if log.get("mute_expires"):
            expires = datetime.datetime.fromisoformat(log["mute_expires"])
            if now >= expires:
                member = guild.get_member(int(uid))
                muted_role = guild.get_role(MUTED_ROLE_ID)
                if member and muted_role in member.roles:
                    try:
                        await member.remove_roles(muted_role)
                        await member.send(f"You have been unmuted in {guild.name}.")
                    except:
                        pass
                    await send_mute_log(member, unmuted=True, log=log)
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
    save_data()

# ------------------ HELP ------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="📖 R-Commands Help", color=0x00FF00)
    embed.add_field(name="!rmute", value="!rmute [user] [duration] [reason]  → Mutes the member.", inline=False)
    embed.add_field(name="!runmute", value="!runmute [user]  → Unmutes the member.", inline=False)
    embed.add_field(name="!timetrack", value="!timetrack [user]  → Shows online/offline time + daily/weekly/monthly.", inline=False)
    embed.add_field(name="!rmlb", value="!rmlb [true/false]  → Shows leaderboard of who has used !rmute most.", inline=False)
    await ctx.send(embed=embed)

# ------------------ RMUTE / RUNMUTE / TIMETRACK ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await ctx.send("Muted role not found.")
        return
    seconds = parse_duration(duration)
    if seconds is None:
        await ctx.send("Invalid duration. Use 1m, 2h, 1d, etc.")
        return
    try:
        await member.add_roles(muted_role)
        await member.timeout(datetime.timedelta(seconds=seconds))
        await member.send(f"You have been muted in {guild.name} for {duration}. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send(f"⚠️ Missing permission to mute {member}.")
        return
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count", 0) + 1
    save_data()
    # Log embed
    unmute_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)
    tz_lines = [f"{emoji} {unmute_time.astimezone(tz).strftime('%Y-%m-%d%H:%M:%S')}" for emoji, tz in TIMEZONES.items()]

embed = discord.Embed(
    title="🔒 User Muted",
    color=0xFF0000,
    timestamp=datetime.datetime.utcnow()
)
embed.set_thumbnail(url=member.display_avatar.url)
embed.add_field(name="👤 User", value=member.mention, inline=True)
embed.add_field(name="📝 Reason", value=reason, inline=False)
embed.add_field(name="⏳ Duration", value=duration, inline=True)
embed.add_field(name="🕒 Unmute Time", value="\n".join(tz_lines), inline=False)
embed.add_field(name="🔧 Muted by", value=ctx.author.mention, inline=True)

log_channel = guild.get_channel(LOG_CHANNEL_ID)
if log_channel:
    await log_channel.send(embed=embed)

await ctx.send(f"✅ {member.mention} has been muted for {duration}.")

@bot.command() async def runmute(ctx, member: discord.Member): guild = ctx.guild muted_role = guild.get_role(MUTED_ROLE_ID) log = get_user_log(member.id)

try:
    if muted_role in member.roles:
        await member.remove_roles(muted_role)
        await member.send(f"You have been unmuted in {guild.name}.")
        await send_mute_log(member, unmuted=True, log=log)
        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        save_data()
        await ctx.send(f"✅ {member.mention} has been unmuted by {ctx.author.mention}.")
    else:
        await ctx.send(f"ℹ️ {member.mention} is not muted.")
except discord.Forbidden:
    await ctx.send(f"⚠️ Missing permission to unmute {member}.")

async def send_mute_log(member, unmuted=False, log=None): guild = bot.get_guild(GUILD_ID) log_channel = guild.get_channel(LOG_CHANNEL_ID) if not log_channel: return embed = discord.Embed( title="✅ User Unmuted" if unmuted else "🔒 User Muted", color=0x00FF00 if unmuted else 0xFF0000, timestamp=datetime.datetime.utcnow() ) embed.set_thumbnail(url=member.display_avatar.url) embed.add_field(name="👤 User", value=member.mention) if not unmuted: embed.add_field(name="📝 Reason", value=log.get("mute_reason", "N/A")) embed.add_field(name="🔧 Muted by", value=f"<@{log.get('mute_responsible')}>") else: embed.add_field(name="📝 Original Reason", value=log.get("mute_reason", "N/A")) await log_channel.send(embed=embed)

@bot.command() async def timetrack(ctx, member: discord.Member = None): member = member or ctx.author log = get_user_log(member.id)

online_time = format_duration(log.get("online_seconds", 0))
offline_time = format_duration(log.get("offline_seconds", 0))
daily_time = format_duration(log.get("daily_seconds", 0))
weekly_time = format_duration(log.get("weekly_seconds", 0))
monthly_time = format_duration(log.get("monthly_seconds", 0))

tz_lines = [
    f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}"
    for emoji, tz in TIMEZONES.items()
]

embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
embed.add_field(name="🟢 Online Time", value=online_time, inline=True)
embed.add_field(name="🔴 Offline Time", value=offline_time, inline=True)
embed.add_field(name="Daily", value=daily_time, inline=True)
embed.add_field(name="Weekly", value=weekly_time, inline=True)
embed.add_field(name="Monthly", value=monthly_time, inline=True)
embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
await ctx.send(embed=embed)

@bot.command() async def rmlb(ctx, full: str = "false"): leaderboard = sorted(activity_logs.items(), key=lambda x: x[1].get("mute_count", 0), reverse=True) lines = [] for uid, log in leaderboard[:10]: member = ctx.guild.get_member(int(uid)) if member: lines.append(f"{member.display_name} → {log.get('mute_count',0)} mutes") embed = discord.Embed(title="🏆 Mute Leaderboard", color=0xFFD700) embed.add_field(name="Top 10 users who muted", value="\n".join(lines) if lines else "No data", inline=False) if full.lower() == "true": await ctx.send(embed=embed) else: await ctx.author.send(embed=embed)

------------------ RUN BOT ------------------

threading.Thread(target=run_web).start() bot.run(TOKEN)
