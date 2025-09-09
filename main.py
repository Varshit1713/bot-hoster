# ------------------ IMPORTS ------------------
import os
import threading
from flask import Flask
import discord
from discord.ext import commands, tasks
import datetime
import json
import sys
from discord import app_commands
from discord.ui import View

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    sys.exit(1)

DATA_FILE = "activity_logs.json"
LOG_CHANNEL_ID = 123456789012345678  # <-- change this to your log channel ID
INACTIVITY_THRESHOLD = 60  # seconds

# ------------------ FLASK ------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask).start()

# ------------------ DISCORD BOT ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ LOAD/INIT LOGS ------------------
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            raw_logs = json.load(f)
            activity_logs = {
                int(uid): {
                    "total_seconds": d.get("total_seconds", 0),
                    "offline_seconds": d.get("offline_seconds", 0),
                    "daily_seconds": d.get("daily_seconds", 0),
                    "weekly_seconds": d.get("weekly_seconds", 0),
                    "monthly_seconds": d.get("monthly_seconds", 0),
                    "last_activity": datetime.datetime.fromisoformat(d["last_activity"]) if d.get("last_activity") else None,
                    "online": d.get("online", False),
                    "offline_start": datetime.datetime.fromisoformat(d["offline_start"]) if d.get("offline_start") else None
                }
                for uid, d in raw_logs.items()
            }
    except Exception:
        print("⚠️ Corrupt activity_logs.json, resetting...")
        activity_logs = {}
else:
    activity_logs = {}

def save_logs():
    serializable = {
        str(uid): {
            "total_seconds": d["total_seconds"],
            "offline_seconds": d["offline_seconds"],
            "daily_seconds": d["daily_seconds"],
            "weekly_seconds": d["weekly_seconds"],
            "monthly_seconds": d["monthly_seconds"],
            "last_activity": d["last_activity"].isoformat() if d["last_activity"] else None,
            "online": d["online"],
            "offline_start": d["offline_start"].isoformat() if d["offline_start"] else None
        }
        for uid, d in activity_logs.items()
    }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f, indent=4)

def format_time(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    if not update_all_users.is_running():
        update_all_users.start()
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"⚠️ Slash sync failed: {e}")
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    uid = message.author.id

    if uid not in activity_logs:
        activity_logs[uid] = {
            "total_seconds": 0,
            "offline_seconds": 0,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_activity": now,
            "online": True,
            "offline_start": None
        }
    else:
        activity_logs[uid]["last_activity"] = now
        activity_logs[uid]["online"] = True
        activity_logs[uid]["offline_start"] = None  # reset offline timer

    save_logs()

# ------------------ BACKGROUND TASK ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)

    for uid, data in activity_logs.items():
        if data["online"] and data.get("last_activity"):
            elapsed = (now - data["last_activity"]).total_seconds()
            if elapsed > 0:
                delta = int(min(elapsed, 10))
                data["total_seconds"] += delta
                data["daily_seconds"] += delta
                data["weekly_seconds"] += delta
                data["monthly_seconds"] += delta
            data["offline_start"] = None
        else:
            if data.get("offline_start"):
                delta_off = (now - data["offline_start"]).total_seconds()
                data["offline_seconds"] += int(delta_off)
                data["offline_start"] = now
            else:
                data["offline_start"] = now
    save_logs()

# ------------------ SLASH COMMANDS ------------------
@bot.tree.command(name="timetrack", description="Show online/offline time for a user")
async def timetrack(interaction: discord.Interaction, member: discord.Member):
    data = activity_logs.get(member.id)
    if not data:
        await interaction.response.send_message("❌ No activity data for this user yet.", ephemeral=True)
        return

    offline_time = 0
    if not data["online"] and data.get("offline_start"):
        offline_time = int((datetime.datetime.now(datetime.timezone.utc) - data["offline_start"]).total_seconds())

    embed = discord.Embed(
        title="⏳ Time Tracker",
        description=f"Tracking activity for **{member.mention}**",
        color=0x2ecc71 if data["online"] else 0xe74c3c
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    embed.add_field(name="🟢 Online time", value=f"`{format_time(data['total_seconds'])}`", inline=True)
    embed.add_field(name="⚫ Offline time", value=f"`{format_time(data['offline_seconds'] + offline_time)}`", inline=True)

    embed.add_field(name="📆 Daily", value=f"`{format_time(data['daily_seconds'])}`", inline=False)
    embed.add_field(name="📆 Weekly", value=f"`{format_time(data['weekly_seconds'])}`", inline=False)
    embed.add_field(name="📆 Monthly", value=f"`{format_time(data['monthly_seconds'])}`", inline=False)

    await interaction.response.send_message(embed=embed)

# ------------------ RMUTE ------------------
@bot.tree.command(name="rmute", description="Timeout a user (mute) with duration and reason")
@app_commands.describe(user="User to timeout", duration="Duration (e.g. 10m, 1h, 2d)", reason="Reason for mute")
async def rmute(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str):
    # parse duration
    unit = duration[-1]
    try:
        value = int(duration[:-1])
    except ValueError:
        await interaction.response.send_message("❌ Invalid duration format. Use like `10m`, `1h`, `2d`.", ephemeral=True)
        return

    seconds = 0
    if unit == "s":
        seconds = value
    elif unit == "m":
        seconds = value * 60
    elif unit == "h":
        seconds = value * 3600
    elif unit == "d":
        seconds = value * 86400
    else:
        await interaction.response.send_message("❌ Use s/m/h/d for duration.", ephemeral=True)
        return

    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)

    try:
        await user.timeout(datetime.timedelta(seconds=seconds), reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to mute that user.", ephemeral=True)
        return

    # DM user
    try:
        await user.send(
            f"You have been muted in **{interaction.guild.name}** until:\n"
            f"📅 **{until.strftime('%Y-%m-%d %I:%M %p')} UTC**\n"
            f"⏳ Duration: `{duration}`\n"
            f"Reason: ***{reason}***"
        )
    except:
        pass

    # Send log embed
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="🔇 User Timed Out",
        color=0xe67e22,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="👤 User", value=user.mention, inline=False)
    embed.add_field(name="📝 Reason", value=f"***{reason}***", inline=False)
    embed.add_field(name="⏳ Duration", value=f"`{duration}`", inline=True)
    embed.add_field(name="🛠 Responsible", value=interaction.user.mention, inline=True)

    if log_channel:
        await log_channel.send(embed=embed)

    await interaction.response.send_message(f"✅ {user.mention} has been muted for `{duration}`.", ephemeral=False)

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
