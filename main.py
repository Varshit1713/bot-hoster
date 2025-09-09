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

GUILD_ID = 1403359962369097739
MUTE_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648
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

# ------------------ FLASK ------------------
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

# ------------------ LOAD LOGS ------------------
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            raw_logs = json.load(f)
            activity_logs = {
                int(uid): {
                    "total_seconds": d.get("total_seconds", 0),
                    "offline_seconds": d.get("offline_seconds", 0),
                    "daily_seconds": d.get("daily_seconds", 0),
                    "weekly_seconds": d.get("weekly_seconds", 0),
                    "monthly_seconds": d.get("monthly_seconds", 0),
                    "last_activity": datetime.datetime.fromisoformat(d["last_activity"]) if d.get("last_activity") else None,
                    "online": d.get("online", False),
                    "first_seen": datetime.datetime.fromisoformat(d.get("first_seen")) if d.get("first_seen") else datetime.datetime.now(datetime.timezone.utc),
                    "daily_start": datetime.datetime.fromisoformat(d.get("daily_start")) if d.get("daily_start") else datetime.datetime.now(datetime.timezone.utc),
                    "weekly_start": datetime.datetime.fromisoformat(d.get("weekly_start")) if d.get("weekly_start") else datetime.datetime.now(datetime.timezone.utc),
                    "monthly_start": datetime.datetime.fromisoformat(d.get("monthly_start")) if d.get("monthly_start") else datetime.datetime.now(datetime.timezone.utc),
                    "offline_start": datetime.datetime.fromisoformat(d.get("offline_start")) if d.get("offline_start") else None
                } for uid, d in raw_logs.items()
            }
    except:
        print("⚠️ Corrupt activity_logs.json, resetting...")
        activity_logs = {}
else:
    activity_logs = {}

last_messages = {}

def save_logs():
    serializable_logs = {
        str(uid): {
            "total_seconds": d["total_seconds"],
            "offline_seconds": d["offline_seconds"],
            "daily_seconds": d["daily_seconds"],
            "weekly_seconds": d["weekly_seconds"],
            "monthly_seconds": d["monthly_seconds"],
            "last_activity": d["last_activity"].isoformat() if d["last_activity"] else None,
            "online": d["online"],
            "first_seen": d["first_seen"].isoformat(),
            "daily_start": d["daily_start"].isoformat(),
            "weekly_start": d["weekly_start"].isoformat(),
            "monthly_start": d["monthly_start"].isoformat(),
            "offline_start": d["offline_start"].isoformat() if d["offline_start"] else None
        } for uid, d in activity_logs.items()
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

def update_user_time(uid: int, delta: int):
    user_data = activity_logs.get(uid)
    if user_data:
        user_data["total_seconds"] += delta
        user_data["daily_seconds"] += delta
        user_data["weekly_seconds"] += delta
        user_data["monthly_seconds"] += delta

def reset_periods():
    now = datetime.datetime.now(datetime.timezone.utc)
    for d in activity_logs.values():
        if (now - d["daily_start"]).total_seconds() > DAY_SECONDS:
            d["daily_seconds"] = 0
            d["daily_start"] = now
        if (now - d["weekly_start"]).total_seconds() > WEEK_SECONDS:
            d["weekly_seconds"] = 0
            d["weekly_start"] = now
        if (now - d["monthly_start"]).total_seconds() > MONTH_SECONDS:
            d["monthly_seconds"] = 0
            d["monthly_start"] = now

# ------------------ ONLINE/OFFLINE TRACK ------------------
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
        data = activity_logs[uid]
        if not data["online"] and data.get("offline_start"):
            # reset offline time when back online
            data["offline_seconds"] += int((now - data["offline_start"]).total_seconds())
        data["last_activity"] = now
        data["online"] = True
        data["offline_start"] = None
    last_messages[uid] = {"content": message.content, "timestamp": now}
    save_logs()

@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)
    reset_periods()
    for uid, d in activity_logs.items():
        if d["online"] and d.get("last_activity"):
            delta = min(int((now - d["last_activity"]).total_seconds()), 10)
            update_user_time(uid, delta)
            d["offline_start"] = None
        else:
            if d.get("offline_start"):
                d["offline_seconds"] += int((now - d["offline_start"]).total_seconds())
                d["offline_start"] = now
    save_logs()

# ------------------ TIMETRACK EMBED ------------------
async def send_time_embed(interaction, member: discord.Member, show_last_message=False, timezone="UTC"):
    user_data = activity_logs.get(member.id)
    if not user_data:
        await interaction.response.send_message(f"{member.mention} has no activity recorded yet.", ephemeral=True)
        return

    offline_time = 0
    now = datetime.datetime.now(datetime.timezone.utc)
    if not user_data["online"] and user_data.get("offline_start"):
        offline_time = int((now - user_data["offline_start"]).total_seconds())

    embed = discord.Embed(
        title=f"⏳ Time Tracking for {member.display_name}",
        color=discord.Color.blurple(),
        timestamp=now
    )
    embed.add_field(name="Status", value=f"{'🟢 Online' if user_data['online'] else '⚫ Offline'}", inline=True)
    embed.add_field(
        name="Total Time",
        value=f"🟢 Online: `{format_time(user_data['total_seconds'])}`\n⚫ Offline: `{format_time(user_data['offline_seconds'] + offline_time)}`",
        inline=False
    )
    embed.add_field(
        name="📆 Periods",
        value=f"Daily: `{format_time(user_data['daily_seconds'])}`\nWeekly: `{format_time(user_data['weekly_seconds'])}`\nMonthly: `{format_time(user_data['monthly_seconds'])}`",
        inline=False
    )
    if show_last_message and member.id in last_messages:
        last_msg = last_messages[member.id]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        embed.add_field(
            name=f"💬 Last message ({timezone})",
            value=f"[{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content'][:1024]}",
            inline=False
        )
    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.set_footer(text=f"User ID: {member.id}")
    await interaction.response.send_message(content=member.mention, embed=embed)

# ------------------ RMUTE EMBED ------------------
async def rmute_command(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
    duration_seconds = parse_duration(duration)
    timeout_end = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
    try:
        await member.edit(timeout=timeout_end)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions to timeout this user.", ephemeral=True)
        return

    role = member.guild.get_role(MUTE_ROLE_ID)
    if role and role not in member.roles:
        try:
            await member.add_roles(role)
        except:
            pass

    # DM user
    try:
        await member.send(f"You have been muted until {timeout_end.strftime('%Y-%m-%d %H:%M:%S UTC')} for reason: {reason}")
    except:
        pass

    # Log embed
    log_channel = member.guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="🔇 User Timed Out",
        description=f"{member.mention} has been muted by {interaction.user.mention}",
        color=discord.Color.red(),
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(name="Duration", value=f"{duration_seconds//60} minutes")
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.set_footer(text=f"User ID: {member.id}")
    await log_channel.send(embed=embed)
    await interaction.response.send_message(f"{member.mention} has been muted for {duration_seconds//60} minutes.", embed=embed)

# ------------------ SLASH COMMANDS ------------------
@bot.tree.command(name="timetrack", description="Show current online/offline time")
async def timetrack(interaction: discord.Interaction, member: discord.Member, show_last_message: bool = False, timezone: str = "UTC"):
    await send_time_embed(interaction, member, show_last_message, timezone)

@bot.tree.command(name="rmute", description="Timeout a member with embed log")
async def rmute(interaction: discord.Interaction, member: discord.Member, duration: str, reason: str):
    await rmute_command(interaction, member, duration, reason)

# ------------------ RUN BOT ------------------
@bot.event
async def on_ready():
    if not update_all_users.is_running():
        update_all_users.start()
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced")
    except Exception as e:
        print(f"⚠️ Slash sync failed: {e}")
    print(f"✅ Logged in as {bot.user}")

bot.run(TOKEN)
