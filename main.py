import discord
from discord.ext import commands, tasks
import datetime
import json
import os

# ---------- CONFIG ----------
TOKEN = os.environ.get("DISCORD_TOKEN")  # Token from Render environment variable
DATA_FILE = "activity_logs.json"         # File to save activity logs
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}
# -----------------------------

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- LOAD/INIT LOGS ----------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        raw_logs = json.load(f)
        activity_logs = {
            int(user_id): {
                "total_seconds": data.get("total_seconds", 0),
                "last_activity": datetime.datetime.fromisoformat(data["last_activity"])
                if data.get("last_activity") else None,
                "online": data.get("online", False)
            }
            for user_id, data in raw_logs.items()
        }
else:
    activity_logs = {}

last_messages = {}  # Stores last message per user

def save_logs():
    serializable_logs = {
        str(user_id): {
            "total_seconds": data["total_seconds"],
            "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
            "online": data["online"]
        }
        for user_id, data in activity_logs.items()
    }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_logs, f, indent=4)

# ---------- HELPERS ----------
def format_time(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

def convert_timezone(dt: datetime.datetime, tz_name: str):
    tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
    return dt.astimezone(tz)

def update_user_time(user_id: int):
    """Update cumulative time for an online user."""
    now = datetime.datetime.now(datetime.timezone.utc)
    user_data = activity_logs.get(user_id)

    if not user_data or not user_data["online"] or not user_data["last_activity"]:
        return

    # Add elapsed time since last check
    elapsed = (now - user_data["last_activity"]).total_seconds()
    if elapsed > 0:
        user_data["total_seconds"] += int(elapsed)
        user_data["last_activity"] = now

# ---------- EVENTS ----------
@bot.event
async def on_ready():
    now = datetime.datetime.now(datetime.timezone.utc)
    for guild in bot.guilds:
        for member in guild.members:
            if member.status != discord.Status.offline:
                activity_logs[member.id] = {
                    "total_seconds": activity_logs.get(member.id, {}).get("total_seconds", 0),
                    "last_activity": now,
                    "online": True
                }
    save_logs()
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_presence_update(before, after):
    now = datetime.datetime.now(datetime.timezone.utc)
    user_id = after.id

    if user_id not in activity_logs:
        activity_logs[user_id] = {"total_seconds": 0, "last_activity": None, "online": False}

    # User comes online
    if before.status == discord.Status.offline and after.status != discord.Status.offline:
        activity_logs[user_id]["online"] = True
        activity_logs[user_id]["last_activity"] = now

    # User goes offline
    elif before.status != discord.Status.offline and after.status == discord.Status.offline:
        update_user_time(user_id)
        activity_logs[user_id]["online"] = False
        activity_logs[user_id]["last_activity"] = None

    save_logs()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    user_id = message.author.id

    if user_id not in activity_logs:
        activity_logs[user_id] = {"total_seconds": 0, "last_activity": None, "online": False}

    # Treat sending a message as being "active"
    if not activity_logs[user_id]["online"]:
        activity_logs[user_id]["online"] = True
        activity_logs[user_id]["last_activity"] = now
    else:
        update_user_time(user_id)

    last_messages[user_id] = {"content": message.content, "timestamp": now}
    save_logs()

# ---------- BACKGROUND TASK ----------
@tasks.loop(seconds=60)
async def update_all_users():
    for user_id, data in activity_logs.items():
        if data["online"]:
            update_user_time(user_id)
    save_logs()

update_all_users.start()

# ---------- SLASH COMMAND ----------
period_choices = [
    discord.app_commands.Choice(name="This week", value="this week"),
    discord.app_commands.Choice(name="This hour", value="this hour"),
    discord.app_commands.Choice(name="This month", value="this month"),
    discord.app_commands.Choice(name="All time", value="all time"),
]

timezone_choices = [discord.app_commands.Choice(name=tz, value=tz) for tz in TIMEZONES.keys()]

@bot.tree.command(name="timetrack", description="Check a user's tracked online time")
@discord.app_commands.describe(
    username="The user to check",
    period="Timeframe",
    show_last_message="Show last message?",
    timezone="Convert timestamp to this timezone"
)
@discord.app_commands.choices(period=period_choices, timezone=timezone_choices)
async def timetrack(
    interaction: discord.Interaction,
    username: discord.Member,
    period: str = "all time",
    show_last_message: bool = False,
    timezone: str = "UTC"
):
    user_id = username.id

    if user_id not in activity_logs:
        await interaction.response.send_message("❌ No activity recorded for this user.", ephemeral=True)
        return

    total_seconds = activity_logs[user_id]["total_seconds"]
    status = "🟢 Online" if activity_logs[user_id]["online"] else "⚫ Offline"

    msg = f"⏳ **{username.display_name}** has {format_time(total_seconds)} online in **{period.title()}**.\n"
    msg += f"**Status:** {status}"

    if show_last_message and user_id in last_messages:
        last_msg = last_messages[user_id]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        msg += f"\n💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"

    await interaction.response.send_message(msg)
    save_logs()

# ---------- RUN BOT ----------
bot.run(TOKEN)
