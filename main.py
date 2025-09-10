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

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
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

def format_duration(seconds, abbreviated=True):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    if abbreviated:
        if days > 0:
            return f"{days}d"
        elif hrs > 0:
            return f"{hrs}h"
        elif mins > 0:
            return f"{mins}m"
        else:
            return f"{sec}s"
    else:
        return f"{days}D {hrs}H {mins}M {sec}S"

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
            delta_since_last_msg = (now - last_msg_time).total_seconds()
            if delta_since_last_msg >= log["offline_delay"]:
                if not log.get("offline_start"):
                    log["offline_start"] = last_msg_time + datetime.timedelta(seconds=log["offline_delay"])
                log["offline_seconds"] = (now - log["offline_start"]).total_seconds()
            else:
                log["online_seconds"] += 5
                log["offline_start"] = None
                log["offline_seconds"] = 0
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
                if member:
                    muted_role = guild.get_role(MUTED_ROLE_ID)
                    if muted_role in member.roles:
                        try:
                            await member.remove_roles(muted_role)
                            await member.send(f"You have been unmuted in {guild.name}.")
                        except discord.Forbidden:
                            print(f"⚠️ Missing permission to unmute {member}.")
                        await send_mute_log(member, unmuted=True, log=log)
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                save_data()

# ------------------ EMBED LOG ------------------
async def send_mute_log(member, reason=None, responsible=None, duration=None, unmuted=False, log=None):
    guild = bot.get_guild(GUILD_ID)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        print("⚠️ Log channel not found.")
        return
    embed = discord.Embed(
        title="🔒 Mute Log" if not unmuted else "✅ Unmute Log",
        color=0xFF0000 if not unmuted else 0x00FF00,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=member.mention, inline=True)
    if responsible:
        embed.add_field(name="Responsible", value=responsible.mention, inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    if duration and not unmuted:
        embed.add_field(name="Duration", value=duration, inline=True)
        unmute_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=int(duration.split('d')[0])*86400)
        tz_lines = [f"{emoji} {unmute_time.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
        embed.add_field(name="Unmute Time", value="\n".join(tz_lines), inline=False)
    if unmuted and log:
        embed.add_field(name="Original Reason", value=log.get("mute_reason", "N/A"), inline=False)
    await log_channel.send(embed=embed)

# ------------------ TRIGGERS ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await ctx.send("Muted role not found.")
        return
    try:
        await member.add_roles(muted_role)
        await member.send(f"You have been muted in {guild.name} for {duration}. Reason: {reason}")
    except discord.Forbidden:
        await ctx.send(f"⚠️ Cannot mute {member}.")
        return
    seconds = parse_duration(duration)
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count", 0) + 1  # increment mute leaderboard
    save_data()
    await send_mute_log(member, reason=reason, responsible=ctx.author, duration=duration)

    await ctx.send(f"✅ {member.mention} has been muted for {duration}.")

@bot.command()
async def runmute(ctx, member: discord.Member):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    if muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
            await member.send(f"You have been unmuted in {guild.name}.")
        except discord.Forbidden:
            await ctx.send(f"⚠️ Cannot unmute {member}.")
            return
        await send_mute_log(member, unmuted=True, log=log)
        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        save_data()
        await ctx.send(f"✅ {member.mention} has been unmuted by {ctx.author.mention}.")
    else:
        await ctx.send(f"ℹ️ {member.mention} is not muted.")

@bot.command()
async def timetrack(ctx, member: discord.Member = None):
    member = member or ctx.author
    log = get_user_log(member.id)
    online_time = format_duration(log.get("online_seconds", 0))
    offline_time = format_duration(log.get("offline_seconds", 0))
    daily_time = format_duration(log.get("daily_seconds", 0))
    weekly_time = format_duration(log.get("weekly_seconds", 0))
    monthly_time = format_duration(log.get("monthly_seconds", 0))
    tz_lines = [f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]

    embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🟢 Online Time", value=online_time, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline_time, inline=True)
    embed.add_field(name="Daily", value=daily_time, inline=True)
    embed.add_field(name="Weekly", value=weekly_time, inline=True)
    embed.add_field(name="Monthly", value=monthly_time, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def rmlb(ctx, show_full: bool = False):
    guild = ctx.guild
    leaderboard = [(int(uid), log.get("mute_count", 0)) for uid, log in activity_logs.items() if log.get("mute_count", 0) > 0]
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    lines = []
    for uid, count in leaderboard:
        member = guild.get_member(uid)
        if member:
            lines.append(f"{member.display_name}: {count}")
    msg = "\n".join(lines) if lines else "No mutes yet."
    if show_full:
        await ctx.send(f"📊 Mute Leaderboard:\n{msg}")
    else:
        await ctx.author.send(f"📊 Mute Leaderboard (Private):\n{msg}")
        await ctx.send(f"✅ Leaderboard sent to your DMs.")

# ------------------ HELP COMMAND ------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="📌 Bot Commands Help", color=0x3498DB)
    embed.add_field(name="!rmute [user] [duration] [reason]", value="Mute a user. Duration format: 1m, 1h, 1d, etc.", inline=False)
    embed.add_field(name="!runmute [user]", value="Unmute a user manually.", inline=False)
    embed.add_field(name="!timetrack [user]", value="Show online/offline time, daily, weekly, monthly, and timezones.", inline=False)
    embed.add_field(name="!rmlb [true/false]", value="Show mute leaderboard. True = channel message, False = DM only.", inline=False)
    await ctx.send(embed=embed)

# ------------------ UTILITY ------------------
def parse_duration(duration: str) -> int:
    # Converts 1m, 1h, 1d, 27d into seconds
    unit = duration[-1].lower()
    try:
        value = int(duration[:-1])
    except ValueError:
        return 60
    if unit == "s":
        return value
    elif unit == "m":
        return value * 60
    elif unit == "h":
        return value * 3600
    elif unit == "d":
        return value * 86400
    return value

# ------------------ RUN BOT ------------------
threading.Thread(target=run_web).start()
bot.run(TOKEN)
