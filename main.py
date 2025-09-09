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

INACTIVITY_THRESHOLD = 60  # seconds
DAY_SECONDS = 24 * 3600
WEEK_SECONDS = 7 * DAY_SECONDS
MONTH_SECONDS = 30 * DAY_SECONDS

MUTE_ROLE_ID = 1410423854563721287  # Change to your mute role
LOG_CHANNEL_ID = 1403422664521023648  # Channel to log mutes/unmutes

# ------------------ FLASK PORT ------------------
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
active_mutes = {}

# ------------------ HELPERS ------------------
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
        if data["online"] and data.get("last_activity"):
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

def parse_duration(duration: str):
    try:
        unit = duration[-1].lower()
        val = int(duration[:-1])
        if unit == "s": return val
        if unit == "m": return val * 60
        if unit == "h": return val * 3600
        if unit == "d": return val * 86400
    except:
        return 60
    return 60

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
    if not check_mutes.is_running():
        check_mutes.start()
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
        user_data = activity_logs[user_id]
        if not user_data["online"] and user_data.get("offline_start"):
            user_data["offline_seconds"] += int((now - user_data["offline_start"]).total_seconds())
        user_data["last_activity"] = now
        user_data["online"] = True
        user_data["offline_start"] = None
    last_messages[user_id] = {"content": message.content, "timestamp": now}
    save_logs()
    await bot.process_commands(message)

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)
    reset_periods()
    for user_id, data in activity_logs.items():
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

@tasks.loop(seconds=10)
async def check_mutes():
    now = datetime.datetime.utcnow()
    for user_id, data in list(active_mutes.items()):
        if now >= data["end_time"]:
            await remove_mute(user_id)

# ------------------ SEND TIME HELPER ------------------
async def send_time(interaction, username: discord.Member, user_data, show_last_message=False, timezone="UTC"):
    offline_time = 0
    if not user_data["online"] and user_data.get("offline_start"):
        offline_time = int((datetime.datetime.now(datetime.timezone.utc) - user_data["offline_start"]).total_seconds())
    
    msg = f"⏳ **{username.display_name}**\n"
    msg += f"🟢 Online time: `{format_time(user_data['total_seconds'])}`\n"
    msg += f"⚫ Offline for: `{format_time(user_data['offline_seconds'] + offline_time)}`\n\n"
    msg += "📆 **Periods**\n"
    msg += f"Daily: `{format_time(user_data['daily_seconds'])}`\n"
    msg += f"Weekly: `{format_time(user_data['weekly_seconds'])}`\n"
    msg += f"Monthly: `{format_time(user_data['monthly_seconds'])}`"

    if show_last_message and username.id in last_messages:
        last_msg = last_messages[username.id]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        msg += f"\n💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"

    await interaction.response.send_message(msg)

# ------------------ MUTE FUNCTIONS ------------------
async def apply_mute(member: discord.Member, duration_seconds: int, reason: str, responsible: discord.Member):
    end_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
    try:
        await member.edit(timeout=end_time)
    except discord.Forbidden:
        return False, "Missing permissions to timeout this user."
    role = member.guild.get_role(MUTE_ROLE_ID)
    if role and role not in member.roles:
        try:
            await member.add_roles(role)
        except:
            pass
    try:
        await member.send(f"You have been muted in **{member.guild.name}** until **{end_time.strftime('%Y-%m-%d %H:%M:%S UTC')}** for {duration_seconds//60} minutes. Reason: {reason}")
    except:
        pass
    log_channel = member.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(
            title="🔇 User Timed Out",
            description=f"**User:** {member.mention}\n**Muted by:** {responsible.mention}\n**Duration:** {duration_seconds//60} minutes\n**Reason:** {reason}",
            color=discord.Color.orange(),
            timestamp=datetime.datetime.utcnow()
        )
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        await log_channel.send(embed=embed)
    active_mutes[member.id] = {"end_time": end_time, "role": role}
    return True, None

async def remove_mute(user_id: int):
    data = active_mutes.pop(user_id, None)
    if not data:
        return
    guild = bot.guilds[0]
    member = guild.get_member(user_id)
    if not member:
        return
    try:
        await member.edit(timeout=None)
    except:
        pass
    if data.get("role") and data["role"] in member.roles:
        try:
            await member.remove_roles(data["role"])
        except:
            pass
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(
            title="✅ User Unmuted",
            description=f"**User:** {member.mention}",
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow()
        )
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        await log_channel.send(embed=embed)

# ------------------ SLASH COMMANDS ------------------
@bot.tree.command(name="rmute", description="Mute a user with duration and reason")
async def rmute(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str):
    if not interaction.user.guild_permissions.moderate_members:
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    dur_seconds = parse_duration(duration)
    success, error = await apply_mute(user, dur_seconds, reason, interaction.user)
    if success:
        await interaction.response.send_message(f"✅ {user.mention} muted for `{duration}`. Reason: {reason}")
    else:
        await interaction.response.send_message(f"❌ Could not mute: {error}", ephemeral=True)

@bot.tree.command(name="timetrack", description="Show current online/offline time")
async def timetrack(interaction: discord.Interaction, username: discord.Member, show_last_message: bool = False, timezone: str = "UTC"):
    user_data = activity_logs.get(username.id)
    await send_time(interaction, username, user_data, show_last_message, timezone)

@bot.tree.command(name="weekly", description="Show weekly online time")
async def weekly(interaction: discord.Interaction, username: discord.Member):
    user_data = activity_logs.get(username.id)
    await send_time(interaction, username, user_data)

@bot.tree.command(name="monthly", description="Show monthly online time")
async def monthly(interaction: discord.Interaction, username: discord.Member):
    user_data = activity_logs.get(username.id)
    await send_time(interaction, username, user_data)

@bot.tree.command(name="fulltime", description="Show total online and offline time")
async def fulltime(interaction: discord.Interaction, username: discord.Member):
    user_data = activity_logs.get(username.id)
    await send_time(interaction, username, user_data)

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
