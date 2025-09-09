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
LOG_CHANNEL_ID = 1403422664521023648  # change to your log channel
MUTE_ROLE_ID = 1410423854563721287    # change to your mute role

TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}

INACTIVITY_THRESHOLD = 60  # 1 minute inactivity timeout
DAY_SECONDS = 24 * 3600
WEEK_SECONDS = 7 * DAY_SECONDS
MONTH_SECONDS = 30 * DAY_SECONDS

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
    user_data["daily_seconds"] += delta
    user_data["weekly_seconds"] += delta
    user_data["monthly_seconds"] += delta

def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for data in activity_logs.values():
        if data["online"] and data["last_activity"]:
            elapsed = (now - data["last_activity"]).total_seconds()
            if elapsed > INACTIVITY_THRESHOLD:
                data["online"] = False
                data["offline_start"] = now
                data["last_activity"] = None

def reset_periods():
    now = datetime.datetime.now(datetime.timezone.utc)
    for data in activity_logs.values():
        if (now - data["daily_start"]).total_seconds() > DAY_SECONDS:
            data["daily_seconds"] = 0
            data["daily_start"] = now
        if (now - data["weekly_start"]).total_seconds() > WEEK_SECONDS:
            data["weekly_seconds"] = 0
            data["weekly_start"] = now
        if (now - data["monthly_start"]).total_seconds() > MONTH_SECONDS:
            data["monthly_seconds"] = 0
            data["monthly_start"] = now

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
        # Reset offline timer when user is online
        activity_logs[user_id]["last_activity"] = now
        activity_logs[user_id]["online"] = True
        activity_logs[user_id]["offline_start"] = None
    last_messages[user_id] = {"content": message.content, "timestamp": now}
    save_logs()

# ------------------ BACKGROUND TASK ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)
    reset_periods()
    for data in activity_logs.values():
        if data["online"] and data.get("last_activity"):
            delta = int(min((now - data["last_activity"]).total_seconds(), 10))
            if delta > 0:
                update_user_time(user_id, delta)
            data["offline_start"] = None
        else:
            if data.get("offline_start"):
                delta_off = (now - data["offline_start"]).total_seconds()
                data["offline_seconds"] += int(delta_off)
                data["offline_start"] = now
    check_inactivity()
    save_logs()

# ------------------ SEND TIME EMBED ------------------
async def send_time_embed(interaction, member: discord.Member):
    user_data = activity_logs.get(member.id)
    offline_time = 0
    if not user_data["online"] and user_data.get("offline_start"):
        offline_time = int((datetime.datetime.now(datetime.timezone.utc) - user_data["offline_start"]).total_seconds())
    embed = discord.Embed(title=f"⏱️ Time Track for {member.display_name}", color=0x00FF00)
    embed.add_field(name="🟢 Online Time", value=f"{format_time(user_data['total_seconds'])}", inline=True)
    embed.add_field(name="⚫ Offline Time", value=f"{format_time(user_data['offline_seconds'] + offline_time)}", inline=True)
    embed.add_field(name="📆 Daily", value=f"{format_time(user_data['daily_seconds'])}", inline=True)
    embed.add_field(name="📆 Weekly", value=f"{format_time(user_data['weekly_seconds'])}", inline=True)
    embed.add_field(name="📆 Monthly", value=f"{format_time(user_data['monthly_seconds'])}", inline=True)
    embed.set_footer(text="Tracked by TimeTracker Bot")
    await interaction.response.send_message(embed=embed)

# ------------------ /timetrack ------------------
@bot.tree.command(name="timetrack", description="Show online/offline time")
async def timetrack(interaction: discord.Interaction, member: discord.Member):
    await send_time_embed(interaction, member)

# ------------------ /rmute ------------------
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

@bot.tree.command(name="rmute", description="Mute a member with real timeout")
async def rmute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
    duration_seconds = parse_duration(duration)
    timeout_end = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)

    # Apply real timeout
    try:
        await member.edit(timeout=timeout_end)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions to timeout this user.", ephemeral=True)
        return

    # Add mute role
    role = member.guild.get_role(MUTE_ROLE_ID)
    if role and role not in member.roles:
        try:
            await member.add_roles(role)
        except:
            pass

    # DM user
    try:
        await member.send(f"You have been muted in {member.guild.name} until {timeout_end.strftime('%Y-%m-%d %H:%M:%S UTC')} for reason: {reason}")
    except:
        pass

    # Log embed
    log_channel = member.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(
            title="🔇 User Muted",
            description=f"{member.mention} has been muted by {interaction.user.mention}",
            color=0xFF0000
        )
        embed.add_field(name="Duration", value=f"{duration}", inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.set_footer(text=f"Muted by {interaction.user.display_name} • {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        await log_channel.send(embed=embed)

    await interaction.response.send_message(f"✅ {member.mention} has been muted for {duration}", ephemeral=True)

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
