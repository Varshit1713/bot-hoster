import discord
from discord.ext import commands
import datetime
import json
import os

# ---------- CONFIG ----------
# Load bot token from environment variable
TOKEN = os.environ.get("DISCORD_TOKEN")
DATA_FILE = "activity_logs.json"
# Amount of time (in minutes) added per message activity
MESSAGE_ACTIVITY_MINUTES = 5
# Default timezone UTC offset mapping
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "EDT": datetime.timezone(datetime.timedelta(hours=-4)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}
# -----------------------------

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True  # required to read messages

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- LOAD/SAVE LOGS ----------

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        raw_logs = json.load(f)
        activity_logs = {
            int(user_id): [
                {
                    "start": datetime.datetime.fromisoformat(s["start"]),
                    "end": datetime.datetime.fromisoformat(s["end"]) if s["end"] else None,
                    "source": s.get("source", "presence"),
                }
                for s in sessions
            ]
            for user_id, sessions in raw_logs.items()
        }
else:
    activity_logs = {}

# Store last messages per user
last_messages = {}

def save_logs():
    serializable_logs = {
        str(user_id): [
            {
                "start": s["start"].isoformat(),
                "end": s["end"].isoformat() if s["end"] else None,
                "source": s.get("source", "presence"),
            }
            for s in sessions
        ]
        for user_id, sessions in activity_logs.items()
    }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_logs, f, indent=4)

# ---------- EVENTS ----------

@bot.event
async def on_ready():
    now = datetime.datetime.now(datetime.timezone.utc)
    for guild in bot.guilds:
        for member in guild.members:
            if member.status != discord.Status.offline:
                activity_logs.setdefault(member.id, []).append({"start": now, "end": None, "source": "presence"})
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_presence_update(before, after):
    now = datetime.datetime.now(datetime.timezone.utc)

    # User comes online
    if before.status == discord.Status.offline and after.status != discord.Status.offline:
        activity_logs.setdefault(after.id, []).append({"start": now, "end": None, "source": "presence"})
        save_logs()

    # User goes offline
    elif before.status != discord.Status.offline and after.status == discord.Status.offline:
        if after.id in activity_logs and activity_logs[after.id]:
            for session in reversed(activity_logs[after.id]):
                if session["end"] is None:
                    session["end"] = now
                    save_logs()
                    break

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    # Log a short "active session" for message activity
    activity_logs.setdefault(message.author.id, []).append({
        "start": now,
        "end": now + datetime.timedelta(minutes=MESSAGE_ACTIVITY_MINUTES),
        "source": "message"
    })
    last_messages[message.author.id] = {"content": message.content, "timestamp": now}
    save_logs()

# ---------- HELPERS ----------

def calculate_activity(user_id, since_time, until_time=None):
    total = datetime.timedelta()
    if user_id not in activity_logs:
        return total
    until_time = until_time or datetime.datetime.now(datetime.timezone.utc)
    for session in activity_logs[user_id]:
        start = session["start"]
        end = session["end"] or datetime.datetime.now(datetime.timezone.utc)
        if end >= since_time and start <= until_time:
            total += min(end, until_time) - max(start, since_time)
    return total

def parse_period(period: str, now: datetime.datetime):
    period = period.lower()
    if period in ["this hour"]:
        return now - datetime.timedelta(hours=1), now
    elif period in ["this week"]:
        return now - datetime.timedelta(weeks=1), now
    elif period in ["this month"]:
        return now - datetime.timedelta(days=30), now
    elif period in ["last week"]:
        return now - datetime.timedelta(weeks=2), now - datetime.timedelta(weeks=1)
    elif period in ["last hour"]:
        return now - datetime.timedelta(hours=2), now - datetime.timedelta(hours=1)
    elif period in ["last month"]:
        return now - datetime.timedelta(days=60), now - datetime.timedelta(days=30)
    else:
        return None, None

def convert_timezone(dt: datetime.datetime, tz_name: str):
    tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
    return dt.astimezone(tz)

# ---------- SLASH COMMAND ----------

period_choices = [
    discord.app_commands.Choice(name="This week", value="this week"),
    discord.app_commands.Choice(name="This hour", value="this hour"),
    discord.app_commands.Choice(name="This month", value="this month"),
    discord.app_commands.Choice(name="Last week", value="last week"),
    discord.app_commands.Choice(name="Last hour", value="last hour"),
    discord.app_commands.Choice(name="Last month", value="last month"),
]

timezone_choices = [
    discord.app_commands.Choice(name=tz, value=tz) for tz in TIMEZONES.keys()
]

@bot.tree.command(name="timetrack", description="Check a user's tracked online time")
@discord.app_commands.describe(
    username="The user to check",
    period="Timeframe",
    show_last_message="Show last message?",
    timezone="Convert displayed time to this timezone"
)
@discord.app_commands.choices(period=period_choices, timezone=timezone_choices)
async def timetrack(
    interaction: discord.Interaction,
    username: discord.Member,
    period: str = "this week",
    show_last_message: bool = False,
    timezone: str = "UTC"
):
    now = datetime.datetime.now(datetime.timezone.utc)
    since, until = parse_period(period, now)

    if since is None:
        await interaction.response.send_message(
            "❌ Invalid period selection.",
            ephemeral=True
        )
        return

    total = calculate_activity(username.id, since, until)
    hours, remainder = divmod(total.total_seconds(), 3600)
    minutes = remainder // 60

    display_msg = f"⏳ **{username.display_name}** has {int(hours)}h {int(minutes)}m online in **{period.title()}**."

    if show_last_message and username.id in last_messages:
        last_msg = last_messages[username.id]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        display_msg += f"\n💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"

    await interaction.response.send_message(display_msg)
