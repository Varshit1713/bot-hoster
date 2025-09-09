# ------------------ IMPORTS ------------------
import os
import threading
from flask import Flask
import discord
from discord.ext import commands, tasks
import datetime
import json
import sys

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")  # Must be set in Render → Environment
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    sys.exit(1)

DATA_FILE = "activity_logs.json"
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}
INACTIVITY_THRESHOLD = 60  # 1 minute of inactivity to mark offline
WEEK_SECONDS = 7 * 24 * 3600
MONTH_SECONDS = 30 * 24 * 3600

# ------------------ FLASK PORT BINDING ------------------
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
                int(user_id): {
                    "total_seconds": data.get("total_seconds", 0),
                    "offline_seconds": data.get("offline_seconds", 0),
                    "weekly_seconds": data.get("weekly_seconds", 0),
                    "monthly_seconds": data.get("monthly_seconds", 0),
                    "last_activity": datetime.datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else None,
                    "online": data.get("online", False),
                    "first_seen": datetime.datetime.fromisoformat(data["first_seen"]) if data.get("first_seen") else datetime.datetime.now(datetime.timezone.utc)
                }
                for user_id, data in raw_logs.items()
            }
    except Exception:
        print("⚠️ Corrupt activity_logs.json, resetting...")
        activity_logs = {}
else:
    activity_logs = {}

last_messages = {}

def save_logs():
    serializable_logs = {
        str(user_id): {
            "total_seconds": data["total_seconds"],
            "offline_seconds": data["offline_seconds"],
            "weekly_seconds": data["weekly_seconds"],
            "monthly_seconds": data["monthly_seconds"],
            "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
            "online": data["online"],
            "first_seen": data["first_seen"].isoformat()
        }
        for user_id, data in activity_logs.items()
    }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_logs, f, indent=4)

# ------------------ HELPERS ------------------
def format_time(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

def convert_timezone(dt: datetime.datetime, tz_name: str):
    tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
    return dt.astimezone(tz)

def update_user_time(user_id: int, delta: int):
    user_data = activity_logs.get(user_id)
    if not user_data:
        return
    user_data["total_seconds"] += delta
    user_data["weekly_seconds"] += delta
    user_data["monthly_seconds"] += delta

def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user_id, data in activity_logs.items():
        if data["online"] and data["last_activity"]:
            elapsed = (now - data["last_activity"]).total_seconds()
            if elapsed > INACTIVITY_THRESHOLD:
                data["online"] = False
                data["offline_start"] = now
                data["last_activity"] = None

def reset_periods():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user_id, data in activity_logs.items():
        # Weekly reset
        if (now - data["first_seen"]).total_seconds() > WEEK_SECONDS:
            data["weekly_seconds"] = 0
            data["first_seen"] = now
        # Monthly reset
        if (now - data["first_seen"]).total_seconds() > MONTH_SECONDS:
            data["monthly_seconds"] = 0
            data["first_seen"] = now

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    now = datetime.datetime.now(datetime.timezone.utc)
    for guild in bot.guilds:
        for member in guild.members:
            if member.id not in activity_logs:
                activity_logs[member.id] = {
                    "total_seconds": 0,
                    "offline_seconds": 0,
                    "weekly_seconds": 0,
                    "monthly_seconds": 0,
                    "last_activity": now if member.status != discord.Status.offline else None,
                    "online": member.status != discord.Status.offline,
                    "first_seen": now
                }
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
    user_id = message.author.id
    if user_id not in activity_logs:
        activity_logs[user_id] = {
            "total_seconds": 0,
            "offline_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_activity": now,
            "online": True,
            "first_seen": now
        }
    else:
        activity_logs[user_id]["last_activity"] = now
        activity_logs[user_id]["online"] = True
    last_messages[user_id] = {"content": message.content, "timestamp": now}
    save_logs()

# ------------------ BACKGROUND TASK ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)
    reset_periods()
    for user_id, data in activity_logs.items():
        if data["online"] and data.get("last_activity"):
            elapsed = (now - data["last_activity"]).total_seconds()
            if elapsed > 0:
                delta = int(min(elapsed, 10))
                update_user_time(user_id, delta)
        else:
            # offline time counting
            if "offline_start" in data and data["offline_start"]:
                delta_off = (now - data["offline_start"]).total_seconds()
                data["offline_seconds"] += int(delta_off)
                data["offline_start"] = now
    check_inactivity()
    save_logs()

# ------------------ SLASH COMMAND ------------------
@bot.tree.command(name="timetrack", description="Check a user's tracked online/offline time")
async def timetrack(
    interaction: discord.Interaction,
    username: discord.Member,
    show_last_message: bool = False,
    timezone: str = "UTC"
):
    user_id = username.id
    if user_id not in activity_logs:
        await interaction.response.send_message("❌ No activity recorded for this user.", ephemeral=True)
        return

    data = activity_logs[user_id]
    status = "🟢 Online" if data["online"] else "⚫ Offline"
    offline_time = 0
    if not data["online"] and "offline_start" in data and data["offline_start"]:
        offline_time = int((datetime.datetime.now(datetime.timezone.utc) - data["offline_start"]).total_seconds())

    msg = f"⏳ **{username.display_name}** has {format_time(data['total_seconds'])} online.\n"
    msg += f"**Status:** {status}\n"
    msg += f"**Offline for:** {format_time(data['offline_seconds'] + offline_time)}"

    if show_last_message and user_id in last_messages:
        last_msg = last_messages[user_id]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        msg += f"\n💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"

    await interaction.response.send_message(msg)
    save_logs()

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
