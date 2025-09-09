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
TOKEN = os.environ.get("DISCORD_TOKEN")
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
INACTIVITY_THRESHOLD = 60  # 1 minute inactivity

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
                    "last_activity": datetime.datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else None,
                    "online": data.get("online", False),
                    "offline_since": datetime.datetime.fromisoformat(data["offline_since"]) if data.get("offline_since") else None
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
            "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
            "offline_since": data["offline_since"].isoformat() if data.get("offline_since") else None,
            "online": data["online"]
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

def update_user_time(user_id: int):
    now = datetime.datetime.now(datetime.timezone.utc)
    user_data = activity_logs.get(user_id)
    if not user_data or not user_data["online"] or not user_data["last_activity"]:
        return
    elapsed = (now - user_data["last_activity"]).total_seconds()
    if elapsed > 0:
        user_data["total_seconds"] += int(elapsed)
        user_data["last_activity"] = now

def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user_id, data in activity_logs.items():
        if data["online"] and data["last_activity"]:
            elapsed = (now - data["last_activity"]).total_seconds()
            if elapsed > INACTIVITY_THRESHOLD:
                data["online"] = False
                data["offline_since"] = now
                data["last_activity"] = None

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    now = datetime.datetime.now(datetime.timezone.utc)

    # Fix users who were online while bot was offline
    for user_id, data in activity_logs.items():
        if data["online"] and data["last_activity"]:
            elapsed = (now - data["last_activity"]).total_seconds()
            if elapsed > 0:
                data["total_seconds"] += int(elapsed)
                data["last_activity"] = now

    # Track members currently online
    for guild in bot.guilds:
        for member in guild.members:
            if member.status != discord.Status.offline:
                if member.id not in activity_logs:
                    activity_logs[member.id] = {"total_seconds": 0, "last_activity": now, "online": True, "offline_since": None}
                else:
                    activity_logs[member.id]["online"] = True
                    activity_logs[member.id]["last_activity"] = now
                    activity_logs[member.id]["offline_since"] = None

    save_logs()

    if not update_all_users.is_running():
        update_all_users.start()

    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"⚠️ Slash sync failed: {e}")

    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_presence_update(before, after):
    now = datetime.datetime.now(datetime.timezone.utc)
    user_id = after.id
    if user_id not in activity_logs:
        activity_logs[user_id] = {"total_seconds": 0, "last_activity": None, "online": False, "offline_since": None}

    if before.status == discord.Status.offline and after.status != discord.Status.offline:
        activity_logs[user_id]["online"] = True
        activity_logs[user_id]["last_activity"] = now
        activity_logs[user_id]["offline_since"] = None
    elif before.status != discord.Status.offline and after.status == discord.Status.offline:
        update_user_time(user_id)
        activity_logs[user_id]["online"] = False
        activity_logs[user_id]["offline_since"] = now
        activity_logs[user_id]["last_activity"] = None

    save_logs()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    user_id = message.author.id
    if user_id not in activity_logs:
        activity_logs[user_id] = {"total_seconds": 0, "last_activity": None, "online": False, "offline_since": None}
    if not activity_logs[user_id]["online"]:
        activity_logs[user_id]["online"] = True
        activity_logs[user_id]["last_activity"] = now
        activity_logs[user_id]["offline_since"] = None
    else:
        update_user_time(user_id)

    last_messages[user_id] = {"content": message.content, "timestamp": now}
    save_logs()

# ------------------ BACKGROUND TASK ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    check_inactivity()
    for user_id, data in activity_logs.items():
        if data["online"]:
            update_user_time(user_id)
    save_logs()

# ------------------ SLASH COMMAND ------------------
@bot.tree.command(name="timetrack", description="Check a user's tracked online time")
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

    update_user_time(user_id)
    total_seconds = activity_logs[user_id]["total_seconds"]
    status = "🟢 Online" if activity_logs[user_id]["online"] else "⚫ Offline"

    msg = f"⏳ **{username.display_name}** has {format_time(total_seconds)} online.\n"
    msg += f"**Status:** {status}"

    # Show "Offline for" if user is offline
    if not activity_logs[user_id]["online"] and activity_logs[user_id].get("offline_since"):
        offline_elapsed = (datetime.datetime.now(datetime.timezone.utc) - activity_logs[user_id]["offline_since"]).total_seconds()
        msg += f"\n⚫ Offline for: {format_time(int(offline_elapsed))}"

    if show_last_message and user_id in last_messages:
        last_msg = last_messages[user_id]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        msg += f"\n💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"

    await interaction.response.send_message(msg)
    save_logs()

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
