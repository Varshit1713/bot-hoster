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

# ------------------ BOT SETUP ------------------
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
        now = datetime.datetime.utcnow().isoformat()
        activity_logs[uid] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "offline_start": None,
            "offline_delay": None,
            "last_message": now,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_daily_reset": now,
            "last_weekly_reset": now,
            "last_monthly_reset": now
        }
    return activity_logs[uid]

def format_duration(seconds):
    days, rem = divmod(int(seconds), 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    return f"{days}D {hrs}H {mins}M {sec}S"

# ------------------ GLOBAL FLAG ------------------
synced = False  # Ensure slash commands are only synced once

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    global synced
    if not synced:
        try:
            guild = discord.Object(id=GUILD_ID)
            await bot.tree.sync(guild=guild)  # Guild sync = instant
            synced = True
            print("✅ Commands synced successfully")
        except Exception as e:
            print(f"⚠️ Error syncing commands: {e}")

    print(f"✅ Logged in as {bot.user}")
    timetrack_update.start()
    mute_check.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    now = datetime.datetime.utcnow()
    log = get_user_log(message.author.id)
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
        # Handle daily/weekly/monthly resets
        last_daily = datetime.datetime.fromisoformat(log["last_daily_reset"])
        if (now - last_daily).days >= 1:
            log["daily_seconds"] = 0
            log["last_daily_reset"] = now.isoformat()
        last_weekly = datetime.datetime.fromisoformat(log["last_weekly_reset"])
        if (now - last_weekly).days >= 7:
            log["weekly_seconds"] = 0
            log["last_weekly_reset"] = now.isoformat()
        last_monthly = datetime.datetime.fromisoformat(log["last_monthly_reset"])
        if (now - last_monthly).days >= 30:
            log["monthly_seconds"] = 0
            log["last_monthly_reset"] = now.isoformat()

        # Offline timer
        if log.get("offline_start"):
            delta = (now - datetime.datetime.fromisoformat(log["offline_start"])).total_seconds()
            log["offline_seconds"] = delta
        else:
            last_msg = datetime.datetime.fromisoformat(log["last_message"])
            if not log.get("offline_delay"):
                log["offline_delay"] = random.randint(INACTIVITY_THRESHOLD_MIN, INACTIVITY_THRESHOLD_MAX)
            if (now - last_msg).total_seconds() >= log["offline_delay"]:
                log["offline_start"] = last_msg + datetime.timedelta(seconds=log["offline_delay"])
            else:
                # Online accumulation
                log["online_seconds"] += 5
                log["daily_seconds"] += 5
                log["weekly_seconds"] += 5
                log["monthly_seconds"] += 5
    save_data()

@tasks.loop(seconds=5)
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
                        await send_mute_log(member, unmuted=True, log=log)
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                save_data()
                # ------------------ EMBED HELPERS ------------------
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
    if reason and not unmuted:
        embed.add_field(name="📝 Reason", value=reason, inline=False)
    if duration and not unmuted:
        embed.add_field(name="⏳ Duration", value=duration, inline=True)
        unmute_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=int(duration.split('D')[0])*86400 + int(duration.split('D')[1].split('H')[0])*3600)
        unmute_time = unmute_time.replace(tzinfo=ZoneInfo("UTC"))
        tz_lines = [f"{emoji} {unmute_time.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
        embed.add_field(name="🕒 Unmute Time", value="\n".join(tz_lines), inline=False)

    if unmuted and log:
        embed.add_field(name="📝 Original Reason", value=log.get("mute_reason", "N/A"), inline=False)
        if log.get("mute_expires") and log.get("last_message"):
            start_time = datetime.datetime.fromisoformat(log.get("last_message"))
            end_time = datetime.datetime.fromisoformat(log.get("mute_expires"))
            orig_duration = end_time - start_time
            embed.add_field(name="⏳ Original Duration", value=format_duration(orig_duration.total_seconds()), inline=True)
        if log.get("mute_responsible"):
            responsible_member = bot.get_guild(GUILD_ID).get_member(log.get("mute_responsible"))
            if responsible_member:
                embed.add_field(name="👤 Muted By", value=responsible_member.mention, inline=True)

    try:
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        print(f"⚠️ Cannot send embed in log channel for {member}.")

# ------------------ SLASH COMMANDS ------------------
@bot.tree.command(name="timetrack", description="Shows online/offline time and timezones")
@app_commands.describe(member="Member to check timetrack for (optional)")
async def timetrack(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    log = get_user_log(member.id)

    # Total times
    online_time = format_duration(log.get("online_seconds", 0))
    offline_time = format_duration(log.get("offline_seconds", 0))
    daily_time = format_duration(log.get("daily_seconds", log.get("online_seconds", 0)))
    weekly_time = format_duration(log.get("weekly_seconds", log.get("online_seconds", 0)))
    monthly_time = format_duration(log.get("monthly_seconds", log.get("online_seconds", 0)))

    tz_lines = [f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]

    embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
    embed.add_field(name="🟢 Online Time", value=online_time, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline_time, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
    embed.add_field(name="📆 Daily", value=daily_time, inline=True)
    embed.add_field(name="📅 Weekly", value=weekly_time, inline=True)
    embed.add_field(name="🗓️ Monthly", value=monthly_time, inline=True)
    await interaction.response.send_message(embed=embed)

# ------------------ MUTE / UNMUTE COMMANDS ------------------
@bot.tree.command(name="rmute", description="Mute a member with duration and reason")
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

        await send_mute_log(member, unmuted=True, log=log)
        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        save_data()
        await interaction.response.send_message(f"✅ {member.mention} has been unmuted by {interaction.user.mention}.")
    else:
        await interaction.response.send_message(f"ℹ️ {member.mention} is not muted.", ephemeral=True)

# ------------------ RUN BOT ------------------
threading.Thread(target=run_web).start()
bot.run(TOKEN)
