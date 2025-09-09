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
LOG_CHANNEL_ID = 1403422664521023648  # Replace with your log channel
MUTE_ROLE_ID = 1410423854563721287    # Replace with your mute role
INACTIVITY_THRESHOLD = 60  # seconds
DAY_SECONDS = 24 * 3600
WEEK_SECONDS = 7 * DAY_SECONDS
MONTH_SECONDS = 30 * DAY_SECONDS

# ------------------ FLASK ------------------
app = Flask(__name__)
@app.route("/")
def index():
    return "Bot is running!"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
threading.Thread(target=run_flask).start()

# ------------------ BOT ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ LOAD LOGS ------------------
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            raw_logs = json.load(f)
            activity_logs = {
                int(uid): {
                    "total_seconds": data.get("total_seconds", 0),
                    "offline_seconds": data.get("offline_seconds", 0),
                    "daily_seconds": data.get("daily_seconds", 0),
                    "weekly_seconds": data.get("weekly_seconds", 0),
                    "monthly_seconds": data.get("monthly_seconds", 0),
                    "last_activity": datetime.datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else None,
                    "online": data.get("online", False),
                    "first_seen": datetime.datetime.fromisoformat(data.get("first_seen")) if data.get("first_seen") else datetime.datetime.now(datetime.timezone.utc),
                    "daily_start": datetime.datetime.fromisoformat(data.get("daily_start")) if data.get("daily_start") else datetime.datetime.now(datetime.timezone.utc),
                    "weekly_start": datetime.datetime.fromisoformat(data.get("weekly_start")) if data.get("weekly_start") else datetime.datetime.now(datetime.timezone.utc),
                    "monthly_start": datetime.datetime.fromisoformat(data.get("monthly_start")) if data.get("monthly_start") else datetime.datetime.now(datetime.timezone.utc),
                    "offline_start": datetime.datetime.fromisoformat(data.get("offline_start")) if data.get("offline_start") else None
                }
                for uid, data in raw_logs.items()
            }
    except Exception:
        print("⚠️ Corrupt activity_logs.json, resetting...")
        activity_logs = {}
else:
    activity_logs = {}

last_messages = {}

def save_logs():
    serializable = {}
    for uid, data in activity_logs.items():
        serializable[str(uid)] = {
            "total_seconds": data["total_seconds"],
            "offline_seconds": data["offline_seconds"],
            "daily_seconds": data["daily_seconds"],
            "weekly_seconds": data["weekly_seconds"],
            "monthly_seconds": data["monthly_seconds"],
            "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
            "online": data["online"],
            "first_seen": data["first_seen"].isoformat(),
            "daily_start": data["daily_start"].isoformat(),
            "weekly_start": data["weekly_start"].isoformat(),
            "monthly_start": data["monthly_start"].isoformat(),
            "offline_start": data["offline_start"].isoformat() if data["offline_start"] else None
        }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f, indent=4)

# ------------------ HELPERS ------------------
def format_time(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

def update_user_time(uid: int, delta: int):
    user = activity_logs.get(uid)
    if not user:
        return
    user["total_seconds"] += delta
    user["daily_seconds"] += delta
    user["weekly_seconds"] += delta
    user["monthly_seconds"] += delta

def reset_periods():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user in activity_logs.values():
        if (now - user["daily_start"]).total_seconds() > DAY_SECONDS:
            user["daily_seconds"] = 0
            user["daily_start"] = now
        if (now - user["weekly_start"]).total_seconds() > WEEK_SECONDS:
            user["weekly_seconds"] = 0
            user["weekly_start"] = now
        if (now - user["monthly_start"]).total_seconds() > MONTH_SECONDS:
            user["monthly_seconds"] = 0
            user["monthly_start"] = now

def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user in activity_logs.values():
        if user["online"] and user.get("last_activity"):
            if (now - user["last_activity"]).total_seconds() > INACTIVITY_THRESHOLD:
                user["online"] = False
                user["offline_start"] = now
                user["last_activity"] = None

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
                    "daily_seconds": 0,
                    "weekly_seconds": 0,
                    "monthly_seconds": 0,
                    "last_activity": now if member.status != discord.Status.offline else None,
                    "online": member.status != discord.Status.offline,
                    "first_seen": now,
                    "daily_start": now,
                    "weekly_start": now,
                    "monthly_start": now,
                    "offline_start": None
                }
    if not update_all_users.is_running():
        update_all_users.start()
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
            "first_seen": now,
            "daily_start": now,
            "weekly_start": now,
            "monthly_start": now,
            "offline_start": None
        }
    else:
        activity_logs[uid]["last_activity"] = now
        activity_logs[uid]["online"] = True
        activity_logs[uid]["offline_start"] = None
    last_messages[uid] = {"content": message.content, "timestamp": now}
    save_logs()

# ------------------ BACKGROUND TASK ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)
    reset_periods()
    for uid, user in activity_logs.items():
        if user["online"] and user.get("last_activity"):
            delta = int(min((now - user["last_activity"]).total_seconds(), 10))
            update_user_time(uid, delta)
            user["offline_start"] = None
        else:
            if user.get("offline_start"):
                delta_off = int((now - user["offline_start"]).total_seconds())
                user["offline_seconds"] += delta_off
                user["offline_start"] = now
    check_inactivity()
    save_logs()

# ------------------ TIMETRACK ------------------
async def send_time(interaction, member: discord.Member, user_data):
    offline_time = 0
    if not user_data["online"] and user_data.get("offline_start"):
        offline_time = int((datetime.datetime.now(datetime.timezone.utc) - user_data["offline_start"]).total_seconds())

    embed = discord.Embed(title=f"⏳ Time Tracking: {member.display_name}", color=discord.Color.blue())
    embed.add_field(name="Online time", value=f"`{format_time(user_data['total_seconds'])}`", inline=True)
    embed.add_field(name="Offline time", value=f"`{format_time(user_data['offline_seconds'] + offline_time)}`", inline=True)
    embed.add_field(name="Daily", value=f"`{format_time(user_data['daily_seconds'])}`", inline=True)
    embed.add_field(name="Weekly", value=f"`{format_time(user_data['weekly_seconds'])}`", inline=True)
    embed.add_field(name="Monthly", value=f"`{format_time(user_data['monthly_seconds'])}`", inline=True)

    # Last message
    if member.id in last_messages:
        last_msg = last_messages[member.id]
        ts = last_msg["timestamp"].strftime("%Y-%m-%d %H:%M:%S UTC")
        embed.add_field(name="Last message", value=f"[{ts}] {last_msg['content']}", inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="timetrack", description="Show current online/offline time")
async def timetrack(interaction: discord.Interaction, member: discord.Member):
    user_data = activity_logs.get(member.id)
    if not user_data:
        await interaction.response.send_message("No data for this user.")
        return
    await send_time(interaction, member, user_data)

# ------------------ RMUTE ------------------
def parse_duration(duration: str):
    try:
        unit = duration[-1]
        val = int(duration[:-1])
        if unit == "s": return val
        if unit == "m": return val*60
        if unit == "h": return val*3600
        if unit == "d": return val*86400
    except:
        return 60
    return 60

@bot.tree.command(name="rmute", description="Mute a member for a specified duration")
async def rmute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
    dur_seconds = parse_duration(duration)
    timeout_end = datetime.datetime.utcnow() + datetime.timedelta(seconds=dur_seconds)

    try:
        await member.edit(timeout=timeout_end)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions to mute this user.")
        return

    role = interaction.guild.get_role(MUTE_ROLE_ID)
    if role and role not in member.roles:
        await member.add_roles(role)

    try:
        await member.send(f"You have been muted in **{interaction.guild.name}** for {duration}. Reason: {reason}")
    except:
        pass

    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(title="🔇 User Muted", color=discord.Color.red(), timestamp=datetime.datetime.utcnow())
    embed.add_field(name="Member", value=member.mention, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Muted by", value=interaction.user.mention, inline=True)
    if log_channel:
        await log_channel.send(embed=embed)
    await interaction.response.send_message(f"✅ {member.mention} has been muted for {duration}.")

# ------------------ RUN ------------------
bot.run(TOKEN)
