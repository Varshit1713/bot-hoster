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
INACTIVITY_THRESHOLD = 60  # seconds
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}

MUTE_ROLE_ID = 1410423854563721287
MUTE_LOG_CHANNEL = 1403422664521023648

# ------------------ FLASK KEEP-ALIVE ------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

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
            activity_logs = {}
            for user_id, data in raw_logs.items():
                activity_logs[int(user_id)] = {
                    "total_seconds": data.get("total_seconds", 0),
                    "daily_seconds": data.get("daily_seconds", 0),
                    "weekly_seconds": data.get("weekly_seconds", 0),
                    "monthly_seconds": data.get("monthly_seconds", 0),
                    "last_activity": datetime.datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else None,
                    "online": data.get("online", False),
                    "last_message": datetime.datetime.fromisoformat(data["last_message"]) if data.get("last_message") else None
                }
    except Exception:
        print("⚠️ Corrupt activity_logs.json, resetting...")
        activity_logs = {}
else:
    activity_logs = {}

def save_logs():
    serializable_logs = {}
    for user_id, data in activity_logs.items():
        serializable_logs[str(user_id)] = {
            "total_seconds": data["total_seconds"],
            "daily_seconds": data.get("daily_seconds", 0),
            "weekly_seconds": data.get("weekly_seconds", 0),
            "monthly_seconds": data.get("monthly_seconds", 0),
            "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
            "online": data["online"],
            "last_message": data["last_message"].isoformat() if data.get("last_message") else None
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
    user = activity_logs.get(user_id)
    if not user or not user["online"] or not user["last_message"]:
        return
    elapsed = (now - user["last_message"]).total_seconds()
    if elapsed <= INACTIVITY_THRESHOLD:
        user["total_seconds"] += int(elapsed)
        user["daily_seconds"] += int(elapsed)
        user["weekly_seconds"] += int(elapsed)
        user["monthly_seconds"] += int(elapsed)
        user["last_activity"] = now

def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user_id, user in activity_logs.items():
        if user["online"] and user["last_message"]:
            if (now - user["last_message"]).total_seconds() > INACTIVITY_THRESHOLD:
                user["online"] = False

def reset_periods():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user in activity_logs.values():
        if user.get("last_daily_reset") is None or (now - user.get("last_daily_reset")).days >= 1:
            user["daily_seconds"] = 0
            user["last_daily_reset"] = now
        if user.get("last_weekly_reset") is None or (now - user.get("last_weekly_reset")).days >= 7:
            user["weekly_seconds"] = 0
            user["last_weekly_reset"] = now
        if user.get("last_monthly_reset") is None or now.month != user.get("last_monthly_reset").month:
            user["monthly_seconds"] = 0
            user["last_monthly_reset"] = now

# ------------------ BACKGROUND TASK ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    check_inactivity()
    reset_periods()
    for user_id, user in activity_logs.items():
        if user["online"]:
            update_user_time(user_id)
    save_logs()

# ------------------ MUTE SYSTEM ------------------
active_mutes = {}

def format_datetime(dt: datetime.datetime):
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

async def unmute_user(user_id):
    for guild in bot.guilds:
        member = guild.get_member(user_id)
        if member and MUTE_ROLE_ID in [role.id for role in member.roles]:
            role = guild.get_role(MUTE_ROLE_ID)
            await member.remove_roles(role, reason="Mute duration expired")
            try:
                await member.send(f"✅ You have been unmuted in **{guild.name}**.")
            except:
                pass
    if user_id in active_mutes:
        del active_mutes[user_id]

@tasks.loop(seconds=10)
async def check_mutes():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user_id, mute_data in list(active_mutes.items()):
        if now >= mute_data["end_time"]:
            await unmute_user(user_id)

check_mutes.start()

# ------------------ EVENT: ON_MESSAGE ------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # ---------- Time tracking ----------
    user_id = message.author.id
    now = datetime.datetime.now(datetime.timezone.utc)
    if user_id not in activity_logs:
        activity_logs[user_id] = {
            "total_seconds": 0,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_activity": None,
            "online": True,
            "last_message": now
        }
    else:
        activity_logs[user_id]["online"] = True
        activity_logs[user_id]["last_message"] = now
    save_logs()
    # ---------- End time tracking ----------

    # ---------- Trigger-based mute (!qmute) ----------
    if message.content.startswith("!qmute"):
        if not message.author.guild_permissions.mute_members:
            await message.channel.send("❌ You don't have permission to mute members.")
            return

        if not message.reference:
            await message.channel.send("❌ You must reply to a user's message to mute them.")
            return

        target_message = await message.channel.fetch_message(message.reference.message_id)
        target_member = target_message.author

        parts = message.content.split(" ", 2)
        if len(parts) < 2:
            await message.channel.send("❌ Usage: !qmute [duration in minutes] [reason]")
            return

        try:
            duration_minutes = int(parts[1])
        except ValueError:
            await message.channel.send("❌ Duration must be a number (minutes).")
            return

        reason = parts[2] if len(parts) > 2 else "No reason provided"
        end_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=duration_minutes)
        role = message.guild.get_role(MUTE_ROLE_ID)
        await target_member.add_roles(role, reason=reason)

        active_mutes[target_member.id] = {
            "end_time": end_time,
            "reason": reason,
            "proof": f"Reply to: {message.content}"
        }

        log_channel = message.guild.get_channel(MUTE_LOG_CHANNEL)
        if log_channel:
            await log_channel.send(
                f"🔇 **Mute Applied**\n"
                f"**User:** {target_member.mention}\n"
                f"**Duration:** {duration_minutes} minutes (until {format_datetime(end_time)})\n"
                f"**Reason:** {reason}\n"
                f"**Proof:** Reply to `{message.content}`\n"
                f"**Moderator:** {message.author.mention}"
            )

        await message.delete()
        try:
            await target_member.send(f"🔇 You have been muted in **{message.guild.name}** for {duration_minutes} minutes. Reason: {reason}")
        except:
            pass
        return

    await bot.process_commands(message)

# ------------------ SLASH COMMAND: TIMETRACK ------------------
@bot.tree.command(name="timetrack", description="Check a user's tracked online/offline time")
async def timetrack(interaction: discord.Interaction, username: discord.Member, show_last_message: bool = False, timezone: str = "UTC"):
    user_id = username.id
    if user_id not in activity_logs:
        await interaction.response.send_message("❌ No activity recorded for this user.", ephemeral=True)
        return

    user = activity_logs[user_id]
    update_user_time(user_id)
    online_time = user["total_seconds"]
    daily_time = user["daily_seconds"]
    weekly_time = user["weekly_seconds"]
    monthly_time = user["monthly_seconds"]

    now = datetime.datetime.now(datetime.timezone.utc)
    offline_seconds = 0
    if not user["online"] and user.get("last_message"):
        offline_seconds = int((now - user["last_message"]).total_seconds())

    msg = f"⏳ **{username.display_name}**\n"
    msg += f"🟢 Online time: {format_time(online_time)}\n"
    msg += f"⚫ Offline for: {format_time(offline_seconds)}\n"
    msg += f"📆 Daily: {format_time(daily_time)}, Weekly: {format_time(weekly_time)}, Monthly: {format_time(monthly_time)}\n"

    if show_last_message and user.get("last_message"):
        ts = convert_timezone(user["last_message"], timezone)
        msg += f"💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}]"

    await interaction.response.send_message(msg)

# ------------------ SLASH COMMAND: RMUTE ------------------
@bot.tree.command(name="rmute", description="Mute a user")
@discord.app_commands.describe(user="The user to mute", duration="Duration in minutes", reason="Reason for mute")
async def rmute(interaction: discord.Interaction, user: discord.Member, duration: int, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.mute_members:
        await interaction.response.send_message("❌ You don't have permission to mute members.", ephemeral=True)
        return

    end_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=duration)
    role = interaction.guild.get_role(MUTE_ROLE_ID)
    await user.add_roles(role, reason=reason)

    active_mutes[user.id] = {"end_time": end_time, "reason": reason, "proof": None}

    log_channel = interaction.guild.get_channel(MUTE_LOG_CHANNEL)
    if log_channel:
        await log_channel.send(
            f"🔇 **Mute Applied**\n"
            f"**User:** {user.mention}\n"
            f"**Duration:** {duration} minutes (until {format_datetime(end_time)})\n"
            f"**Reason:** {reason}\n"
            f"**Moderator:** {interaction.user.mention}"
        )

    try:
        await user.send(f"🔇 You have been muted in **{interaction.guild.name}** for {duration} minutes. Reason: {reason}")
    except:
        pass

    await interaction.response.send_message(f"✅ {user.mention} has been muted for {duration} minutes.", ephemeral=True)

# ------------------ EVENT: ON_READY ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    if not update_all_users.is_running():
        update_all_users.start()
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"⚠️ Slash sync failed: {e}")

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
