# ------------------ IMPORTS ------------------
import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import datetime
import json
import random
from zoneinfo import ZoneInfo
from flask import Flask
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
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_reset": {
                "daily": datetime.datetime.utcnow().isoformat(),
                "weekly": datetime.datetime.utcnow().isoformat(),
                "monthly": datetime.datetime.utcnow().isoformat()
            },
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None
        }
    return activity_logs[uid]

def format_duration(seconds):
    days, rem = divmod(int(seconds), 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    return f"{days}D {hrs}H {mins}M {sec}S"

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    guild = discord.Object(id=GUILD_ID)
    # Clear old guild commands to prevent duplication
    await bot.tree.clear_commands(guild=guild)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    timetrack_update.start()
    mute_check.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    log = get_user_log(message.author.id)
    now = datetime.datetime.utcnow()
    # Reset offline timer when user sends a message
    log["offline_seconds"] = 0
    log["offline_start"] = None
    log["offline_delay"] = None
    log["last_message"] = now.isoformat()
    save_data()
    await bot.process_commands(message)

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=5)
async def timetrack_update():
    now = datetime.datetime.utcnow()
    for uid, log in activity_logs.items():
        # Handle online/offline timer
        last_msg = log.get("last_message")
        if last_msg:
            last_msg_time = datetime.datetime.fromisoformat(last_msg)
            # Random delay 50–60s
            if not log.get("offline_delay"):
                log["offline_delay"] = random.randint(INACTIVITY_THRESHOLD_MIN, INACTIVITY_THRESHOLD_MAX)
            delta_since_last_msg = (now - last_msg_time).total_seconds()
            if delta_since_last_msg >= log["offline_delay"]:
                if not log.get("offline_start"):
                    log["offline_start"] = last_msg_time + datetime.timedelta(seconds=log["offline_delay"])
            else:
                log["online_seconds"] += 5
        # Update offline_seconds
        if log.get("offline_start"):
            log["offline_seconds"] = (now - datetime.datetime.fromisoformat(log["offline_start"])).total_seconds()

        # DAILY/WEEKLY/MONTHLY resets
        for period, key in [("daily", "daily_seconds"), ("weekly", "weekly_seconds"), ("monthly", "monthly_seconds")]:
            last_reset = datetime.datetime.fromisoformat(log["last_reset"][period])
            reset_needed = False
            if period == "daily" and (now - last_reset).total_seconds() >= 86400:
                reset_needed = True
            elif period == "weekly" and (now - last_reset).total_seconds() >= 604800:
                reset_needed = True
            elif period == "monthly" and (now - last_reset).days >= 30:
                reset_needed = True
            if reset_needed:
                log[key] = 0
                log["last_reset"][period] = now.isoformat()
            else:
                log[key] += 5

    save_data()

@tasks.loop(seconds=5)
async def mute_check():
    now = datetime.datetime.utcnow()
    for uid, log in activity_logs.items():
        if log.get("mute_expires"):
            expires = datetime.datetime.fromisoformat(log["mute_expires"])
            if now >= expires:
                guild = bot.get_guild(GUILD_ID)
                member = guild.get_member(int(uid)) if guild else None
                if member:
                    muted_role = guild.get_role(MUTED_ROLE_ID)
                    if muted_role in member.roles:
                        try:
                            await member.remove_roles(muted_role)
                        except discord.Forbidden:
                            print(f"⚠️ Missing permission to remove Muted role from {member}.")
                        await send_mute_log(member, unmuted=True, log=log)
                # Clear mute info
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                save_data()

# ------------------ MUTE LOG EMBED ------------------
async def send_mute_log(member, reason=None, responsible=None, duration=None, unmuted=False, log=None):
    guild = bot.get_guild(GUILD_ID)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        print("⚠️ Log channel not found or bot lacks access.")
        return

    embed = discord.Embed(
        title="🔒 Mute Log" if not unmuted else "✅ Unmute Log",
        color=0xFF0000 if not unmuted else 0x00FF00,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🔒 User", value=member.mention, inline=True)
    if responsible:
        embed.add_field(name="👤 Responsible", value=responsible.mention, inline=True)
    if reason:
        embed.add_field(name="📝 Reason", value=reason, inline=False)
    if duration and not unmuted:
        embed.add_field(name="⏳ Duration", value=duration, inline=True)
        unmute_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=int(duration.split('D')[0])*86400 + int(duration.split('D')[1].split('H')[0])*3600)
        unmute_time = unmute_time.replace(tzinfo=ZoneInfo("UTC"))
        tz_lines = [f"{emoji} {unmute_time.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
        embed.add_field(name="🕒 Unmute Time", value="\n".join(tz_lines), inline=False)
    if unmuted and log:
        embed.add_field(name="👤 Unmuted by", value=member.mention, inline=True)
    try:
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        print(f"⚠️ Cannot send embed in log channel for {member}.")

# ------------------ SLASH COMMANDS ------------------
@bot.tree.command(name="timetrack", description="Shows online/offline time and timezones")
@app_commands.describe(member="Member to check timetrack for")
async def timetrack(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    log = get_user_log(member.id)

    online_time = format_duration(log.get("online_seconds", 0))
    offline_time = format_duration(log.get("offline_seconds", 0))
    daily_time = format_duration(log.get("daily_seconds", 0))
    weekly_time = format_duration(log.get("weekly_seconds", 0))
    monthly_time = format_duration(log.get("monthly_seconds", 0))

    tz_lines = [f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]

    embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
    embed.add_field(name="🟢 Online Time", value=online_time, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline_time, inline=True)
    embed.add_field(name="📅 Daily", value=daily_time, inline=True)
    embed.add_field(name="🗓️ Weekly", value=weekly_time, inline=True)
    embed.add_field(name="🕒 Monthly", value=monthly_time, inline=True)
    embed.add_field(name="🌐 Timezones", value="\n".join(tz_lines), inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="rmute", description="Mute a member with duration (minutes) and reason")
@app_commands.describe(member="Member to mute", duration="Duration in minutes", reason="Reason for mute")
async def rmute(interaction: discord.Interaction, member: discord.Member, duration: int, reason: str):
    guild = interaction.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("Muted role not found.", ephemeral=True)
        return
    try:
        await member.add_roles(muted_role)
    except discord.Forbidden:
        await interaction.response.send_message(f"⚠️ Missing permission to add Muted role to {member}.", ephemeral=True)
        return

    delta = datetime.timedelta(minutes=duration)
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + delta).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = interaction.user.id
    save_data()

    await send_mute_log(member, reason=reason, responsible=interaction.user, duration=format_duration(delta.total_seconds()))
    await interaction.response.send_message(f"✅ {member.mention} has been muted for {duration} minutes.")

@bot.tree.command(name="runmute", description="Unmute a member manually")
@app_commands.describe(member="Member to unmute")
async def runmute(interaction: discord.Interaction, member: discord.Member):
    guild = interaction.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)
    if muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
        except discord.Forbidden:
            await interaction.response.send_message(f"⚠️ Missing permission to remove Muted role from {member}.", ephemeral=True)
            return

        # Set unmuted info
        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        save_data()

        await send_mute_log(member, unmuted=True, log=log)
        await interaction.response.send_message(f"✅ {member.mention} has been unmuted.", ephemeral=False)

------------------ RUN BOT ------------------

Start Flask web server in background

threading.Thread(target=run_web, daemon=True).start()

bot.run(TOKEN)
