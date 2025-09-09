import discord
from discord.ext import commands, tasks
import datetime
import json
import os

# ---------- CONFIG ----------
TOKEN = os.environ.get("DISCORD_TOKEN")  # Your bot token in Render env
DATA_FILE = "activity_logs.json"
ACTIVITY_WINDOW = 300  # 5 minutes in seconds
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}
UPDATE_INTERVAL = 60  # seconds, background update interval
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
                "total_seconds": s["total_seconds"],
                "last_activity": datetime.datetime.fromisoformat(s["last_activity"]) if s.get("last_activity") else None,
                "status": s.get("status", "offline"),
                "last_message": s.get("last_message")
            } for user_id, s in raw_logs.items()
        }
else:
    activity_logs = {}

def save_logs():
    serializable = {}
    for user_id, data in activity_logs.items():
        serializable[str(user_id)] = {
            "total_seconds": data["total_seconds"],
            "last_activity": data["last_activity"].isoformat() if data.get("last_activity") else None,
            "status": data.get("status", "offline"),
            "last_message": data.get("last_message")
        }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f, indent=4)

# ---------- HELPERS ----------
def convert_timezone(dt: datetime.datetime, tz_name: str):
    tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
    return dt.astimezone(tz)

def update_cumulative_time():
    now = datetime.datetime.utcnow()
    for user_id, data in activity_logs.items():
        last = data.get("last_activity")
        status = data.get("status", "offline")
        # Only count if user is active within window and not offline
        if last and status != "offline":
            elapsed = (now - last).total_seconds()
            if elapsed <= ACTIVITY_WINDOW:
                data["total_seconds"] += UPDATE_INTERVAL
    save_logs()

def get_total_time(user_id):
    data = activity_logs.get(user_id)
    if not data:
        return 0
    total = data["total_seconds"]
    last = data.get("last_activity")
    status = data.get("status", "offline")
    if last and status != "offline":
        elapsed = (datetime.datetime.utcnow() - last).total_seconds()
        if elapsed <= ACTIVITY_WINDOW:
            total += elapsed
    return int(total)

# ---------- EVENTS ----------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await bot.tree.sync()
    background_updater.start()

@bot.event
async def on_presence_update(before, after):
    now = datetime.datetime.utcnow()
    user_id = after.id
    data = activity_logs.setdefault(user_id, {
        "total_seconds": 0,
        "last_activity": None,
        "status": str(after.status),
        "last_message": None
    })
    data["status"] = str(after.status)
    if after.status != discord.Status.offline:
        # Only refresh last_activity if outside activity window
        if not data["last_activity"] or (now - data["last_activity"]).total_seconds() > ACTIVITY_WINDOW:
            data["last_activity"] = now
    save_logs()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    now = datetime.datetime.utcnow()
    user_id = message.author.id
    data = activity_logs.setdefault(user_id, {
        "total_seconds": 0,
        "last_activity": now,
        "status": str(message.author.status),
        "last_message": {"content": message.content, "timestamp": now.isoformat()}
    })
    # Only refresh last_activity if outside activity window
    if not data.get("last_activity") or (now - data["last_activity"]).total_seconds() > ACTIVITY_WINDOW:
        data["last_activity"] = now
    data["status"] = str(message.author.status)
    data["last_message"] = {"content": message.content, "timestamp": now.isoformat()}
    save_logs()

# ---------- BACKGROUND TASK ----------
@tasks.loop(seconds=UPDATE_INTERVAL)
async def background_updater():
    update_cumulative_time()

# ---------- SLASH COMMAND ----------
timezone_choices = [discord.app_commands.Choice(name=tz, value=tz) for tz in TIMEZONES.keys()]

@bot.tree.command(name="timetrack", description="Show total online time based on activity")
@discord.app_commands.describe(
    username="User to check",
    show_last_message="Include last message?",
    timezone="Display times in this timezone"
)
@discord.app_commands.choices(timezone=timezone_choices)
async def timetrack(
    interaction: discord.Interaction,
    username: discord.Member,
    show_last_message: bool = False,
    timezone: str = "UTC"
):
    total_seconds = get_total_time(username.id)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    status = activity_logs.get(username.id, {}).get("status", "offline").capitalize()

    msg = f"⏳ **{username.display_name}** has {hours}h {minutes}m {seconds}s online.\nStatus: {status}"

    if show_last_message and username.id in activity_logs:
        last_msg = activity_logs[username.id].get("last_message")
        if last_msg:
            ts = convert_timezone(datetime.datetime.fromisoformat(last_msg["timestamp"]), timezone)
            msg += f"\n💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"

    await interaction.response.send_message(msg)

# ---------- RUN BOT ----------
bot.run(TOKEN)
