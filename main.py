import discord
from discord.ext import commands
import datetime
import json
import os

# ---------- CONFIG ----------
TOKEN = os.environ.get("DISCORD_TOKEN")
DATA_FILE = "activity_logs.json"
MESSAGE_ACTIVITY_MINUTES = 5
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
                "total_seconds": s["total_seconds"],
                "last_online": datetime.datetime.fromisoformat(s["last_online"]) if s["last_online"] else None,
                "status": s.get("status", "offline")
            } for user_id, s in raw_logs.items()
        }
else:
    activity_logs = {}

last_messages = {}

def save_logs():
    serializable_logs = {
        str(user_id): {
            "total_seconds": s["total_seconds"],
            "last_online": s["last_online"].isoformat() if s["last_online"] else None,
            "status": s.get("status", "offline")
        } for user_id, s in activity_logs.items()
    }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_logs, f, indent=4)

# ---------- EVENTS ----------
@bot.event
async def on_ready():
    now = datetime.datetime.now(datetime.timezone.utc)
    for guild in bot.guilds:
        for member in guild.members:
            status = str(member.status)
            if member.id not in activity_logs:
                activity_logs[member.id] = {"total_seconds": 0, "last_online": now if status != "offline" else None, "status": status}
            else:
                activity_logs[member.id]["status"] = status
                if status != "offline" and activity_logs[member.id]["last_online"] is None:
                    activity_logs[member.id]["last_online"] = now
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")
    save_logs()

@bot.event
async def on_presence_update(before, after):
    now = datetime.datetime.now(datetime.timezone.utc)
    user_data = activity_logs.setdefault(after.id, {"total_seconds": 0, "last_online": None, "status": str(after.status)})
    
    # Update cumulative time
    if before.status == discord.Status.offline and after.status != discord.Status.offline:
        user_data["last_online"] = now
    elif before.status != discord.Status.offline and after.status == discord.Status.offline:
        if user_data["last_online"]:
            delta = (now - user_data["last_online"]).total_seconds()
            user_data["total_seconds"] += delta
            user_data["last_online"] = None

    user_data["status"] = str(after.status)
    save_logs()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    user_data = activity_logs.setdefault(message.author.id, {"total_seconds": 0, "last_online": now, "status": str(message.author.status)})
    
    # Add MESSAGE_ACTIVITY_MINUTES for sending messages
    user_data["total_seconds"] += MESSAGE_ACTIVITY_MINUTES * 60
    last_messages[message.author.id] = {"content": message.content, "timestamp": now}
    save_logs()

# ---------- HELPERS ----------
def get_total_time(user_id):
    user_data = activity_logs.get(user_id)
    if not user_data:
        return 0
    total = user_data["total_seconds"]
    # Add ongoing session
    if user_data["last_online"]:
        total += (datetime.datetime.now(datetime.timezone.utc) - user_data["last_online"]).total_seconds()
    return int(total)

def convert_timezone(dt: datetime.datetime, tz_name: str):
    tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
    return dt.astimezone(tz)

# ---------- SLASH COMMAND ----------
timezone_choices = [
    discord.app_commands.Choice(name=tz, value=tz) for tz in TIMEZONES.keys()
]

@bot.tree.command(name="timetrack", description="Check a user's tracked online time")
@discord.app_commands.describe(
    username="The user to check",
    show_last_message="Show last message?",
    timezone="Convert timestamp to this timezone"
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

    if show_last_message and username.id in last_messages:
        last_msg = last_messages[username.id]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        msg += f"\n💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"

    await interaction.response.send_message(msg)

# ---------- RUN BOT ----------
bot.run(TOKEN)
