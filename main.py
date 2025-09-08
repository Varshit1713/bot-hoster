import discord
from discord.ext import commands, tasks
import datetime
import json
import os

# ---------- CONFIG ----------
# Load bot token from environment variable for safety
TOKEN = os.environ.get("DISCORD_TOKEN")
DATA_FILE = "activity_logs.json"  # File to persist logs
# -----------------------------

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- LOAD/SAVE LOGS ----------

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        raw_logs = json.load(f)
        # Convert timestamps back to datetime objects
        activity_logs = {
            int(user_id): [
                {"start": datetime.datetime.fromisoformat(s["start"]),
                 "end": datetime.datetime.fromisoformat(s["end"]) if s["end"] else None}
                for s in sessions
            ]
            for user_id, sessions in raw_logs.items()
        }
else:
    activity_logs = {}

def save_logs():
    serializable_logs = {
        str(user_id): [
            {"start": s["start"].isoformat(), "end": s["end"].isoformat() if s["end"] else None}
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
    # Track members currently online at startup
    for guild in bot.guilds:
        for member in guild.members:
            if member.status != discord.Status.offline:
                activity_logs.setdefault(member.id, []).append({"start": now, "end": None})
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_presence_update(before, after):
    now = datetime.datetime.now(datetime.timezone.utc)

    # User comes online
    if before.status == discord.Status.offline and after.status != discord.Status.offline:
        activity_logs.setdefault(after.id, []).append({"start": now, "end": None})
        save_logs()

    # User goes offline
    elif before.status != discord.Status.offline and after.status == discord.Status.offline:
        if after.id in activity_logs and activity_logs[after.id]:
            for session in reversed(activity_logs[after.id]):
                if session["end"] is None:
                    session["end"] = now
                    save_logs()
                    break

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

# ---------- SLASH COMMAND ----------

period_choices = [
    discord.app_commands.Choice(name="This week", value="this week"),
    discord.app_commands.Choice(name="This hour", value="this hour"),
    discord.app_commands.Choice(name="This month", value="this month"),
    discord.app_commands.Choice(name="Last week", value="last week"),
    discord.app_commands.Choice(name="Last hour", value="last hour"),
    discord.app_commands.Choice(name="Last month", value="last month"),
]

@bot.tree.command(name="timetrack", description="Check a user's tracked online time")
@discord.app_commands.describe(
    username="The user to check",
    period="Timeframe"
)
@discord.app_commands.choices(period=period_choices)
async def timetrack(
    interaction: discord.Interaction,
    username: discord.Member,
    period: str = "this week"
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
    display_period = period.title()

    await interaction.response.send_message(
        f"⏳ **{username.display_name}** has {int(hours)}h {int(minutes)}m online in **{display_period}**."
    )

# ---------- RUN BOT ----------
bot.run(TOKEN)
