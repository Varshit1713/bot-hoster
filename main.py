# ------------------ IMPORTS ------------------
import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
from zoneinfo import ZoneInfo
import json
import random
import threading

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    exit()

GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD_MIN = 50  # seconds
INACTIVITY_THRESHOLD_MAX = 60  # seconds

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
from flask import Flask
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
            "rmute_count": 0
        }
    return activity_logs[uid]

# ------------------ TIME FORMATTING ------------------
def format_duration(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    parts = []
    if days > 0: parts.append(f"{days}d")
    if hrs > 0: parts.append(f"{hrs}h")
    if mins > 0: parts.append(f"{mins}m")
    if sec > 0 or not parts: parts.append(f"{sec}s")
    return " ".join(parts)
    # ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
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
@tasks.loop(seconds=1)
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
                log["online_seconds"] += 1
                log["offline_start"] = None
                log["offline_seconds"] = 0
        else:
            # If user never sent message, count offline
            log["offline_seconds"] += 1
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
        log["daily_seconds"] += 1
        log["weekly_seconds"] += 1
        log["monthly_seconds"] += 1
    save_data()

@tasks.loop(seconds=1)
async def mute_check():
    now = datetime.datetime.utcnow()
    for uid, log in activity_logs.items():
        if log.get("mute_expires"):
            expires = datetime.datetime.fromisoformat(log["mute_expires"])
            if now >= expires:
                guild = bot.get_guild(GUILD_ID)
                member = guild.get_member(int(uid))
                if member:
                    muted_role = guild.get_role(MUTED_ROLE_ID)
                    if muted_role in member.roles:
                        try:
                            await member.remove_roles(muted_role)
                        except discord.Forbidden:
                            print(f"⚠️ Missing permission to remove Muted role from {member}.")
                    try:
                        await member.remove_timeout()
                    except:
                        pass
                    await send_mute_log(member, unmuted=True, log=log)
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                save_data()
                # ------------------ TRIGGERS ------------------
@bot.command()
async def rhelp(ctx):
    """Shows all triggers and usage info."""
    embed = discord.Embed(title="📜 Bot Triggers & Usage", color=0x1ABC9C)
    embed.set_footer(text="Use the triggers as shown below!")
    embed.add_field(
        name="!rmute",
        value="!rmute [user] [duration] [reason] → Mutes a user, gives role, sends embed in log channel, DMs user, tracks duration",
        inline=False
    )
    embed.add_field(
        name="!runmute",
        value="!runmute [user] → Unmutes a user, removes role, sends embed in log channel, DMs user",
        inline=False
    )
    embed.add_field(
        name="!timetrack",
        value="!timetrack [user] → Shows online/offline, daily/weekly/monthly times with fancy emojis",
        inline=False
    )
    embed.add_field(
        name="!rmlb",
        value="!rmlb [true/false] → Shows top users who used !rmute, true = public, false = private reply",
        inline=False
    )
    await ctx.send(embed=embed)

# ------------------ RMUTE ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str):
    """Mutes a member with a duration (e.g., 1m, 2h, 3d)"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        return await ctx.send("Muted role not found.")
    
    # Parse duration
    multipliers = {"s":1, "m":60, "h":3600, "d":86400}
    try:
        unit = duration[-1].lower()
        time_value = int(duration[:-1])
        total_seconds = time_value * multipliers[unit]
    except:
        return await ctx.send("Invalid duration format! Use 1m, 2h, 3d, etc.")
    
    # Apply mute
    try:
        await member.add_roles(muted_role)
        await member.timeout(datetime.timedelta(seconds=total_seconds))
        try:
            await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
        except:
            pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to mute {member}.")
    
    # Log
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=total_seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count",0) + 1
    save_data()
    
    # Send embed in log channel
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="🔇 User Muted",
        description=f"{member.mention} has been muted",
        color=0xFF0000,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Muted by", value=ctx.author.mention, inline=True)
    unmute_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=total_seconds)
    tz_lines = [f"{emoji} {unmute_time.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="Unmute Time", value="\n".join(tz_lines), inline=False)
    if log_channel:
        await log_channel.send(embed=embed)
    await ctx.send(f"✅ {member.mention} has been muted for {duration}.")
    # ------------------ RMUTE ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """Mute a member with a role, Discord timeout, send embed and DM"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    
    # Convert duration string (e.g., 1m, 1h, 1d) to seconds
    time_multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    try:
        unit = duration[-1]
        number = int(duration[:-1])
        if unit not in time_multipliers:
            raise ValueError
        seconds = number * time_multipliers[unit]
    except:
        return await ctx.send("❌ Invalid duration format. Use 1s, 1m, 1h, 1d, etc.")

    # Apply role and Discord timeout
    try:
        await member.add_roles(muted_role)
        await member.timeout(datetime.timedelta(seconds=seconds))
        try:
            await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
        except:
            pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to mute {member}.")

    # Update user log
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count", 0) + 1
    save_data()

    # Send embed in log channel
    if log_channel:
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
        # Show current time in multiple timezones
        tz_lines = [
            f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}"
            for emoji, tz in TIMEZONES.items()
        ]
        embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
        await log_channel.send(embed=embed)

    await ctx.send(f"✅ {member.mention} has been muted for {duration}.")

# ------------------ RHELP ------------------
@bot.command()
async def rhelp(ctx):
    """Shows all triggers and how to use them"""
    embed = discord.Embed(title="ℹ️ Bot Commands & Triggers", color=0x00FFFF)
    embed.add_field(name="!rmute", value="!rmute [user] [duration] [reason] → Mutes a user, adds role, sends embed to log, DMs user", inline=False)
    embed.add_field(name="!runmute", value="!runmute [user] [reason] → Unmutes a user, removes role, sends embed to log, DMs user", inline=False)
    embed.add_field(name="!timetrack", value="!timetrack [user] → Shows online/offline, daily/weekly/monthly times with fancy emojis", inline=False)
    embed.add_field(name="!rmlb", value="!rmlb [true/false] → Shows top users who used !rmute (true = public, false = reply privately)", inline=False)
    await ctx.send(embed=embed)

# ------------------ RUN BOT ------------------
if __name__ == "__main__":
    import threading
    import asyncio

    # Optional Flask web server for Render
    def run_web():
        from flask import Flask
        app = Flask(__name__)
        @app.route("/")
        def home():
            return "Bot is running!"
        app.run(host="0.0.0.0", port=10000)

    threading.Thread(target=run_web).start()

    bot.run(TOKEN)
