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

# ------------------ ACTIVITY LOGS ------------------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        raw_logs = json.load(f)
        activity_logs = {
            int(uid): {
                "total_seconds": d.get("total_seconds", 0),
                "offline_seconds": d.get("offline_seconds", 0),
                "last_activity": datetime.datetime.fromisoformat(d["last_activity"]) if d.get("last_activity") else None,
                "online": d.get("online", False),
                "offline_start": datetime.datetime.fromisoformat(d.get("offline_start")) if d.get("offline_start") else None
            } for uid, d in raw_logs.items()
        }
else:
    activity_logs = {}

last_messages = {}

def save_logs():
    serializable_logs = {
        str(uid): {
            "total_seconds": d["total_seconds"],
            "offline_seconds": d["offline_seconds"],
            "last_activity": d["last_activity"].isoformat() if d["last_activity"] else None,
            "online": d["online"],
            "offline_start": d["offline_start"].isoformat() if d["offline_start"] else None
        } for uid, d in activity_logs.items()
    }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_logs, f, indent=4)

def format_time(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

# ------------------ EVENTS ------------------
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
            "last_activity": now,
            "online": True,
            "offline_start": None
        }
    else:
        data = activity_logs[uid]
        if not data["online"] and data.get("offline_start"):
            data["offline_seconds"] += int((now - data["offline_start"]).total_seconds())
        data["last_activity"] = now
        data["online"] = True
        data["offline_start"] = None
    last_messages[uid] = {"content": message.content, "timestamp": now}
    save_logs()

@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)
    for uid, d in activity_logs.items():
        if d["online"] and d.get("last_activity"):
            delta = min(int((now - d["last_activity"]).total_seconds()), 10)
            d["total_seconds"] += delta
            d["offline_start"] = None
        else:
            if d.get("offline_start"):
                d["offline_seconds"] += int((now - d["offline_start"]).total_seconds())
                d["offline_start"] = now
    save_logs()

# ------------------ TIMETRACK EMBED ------------------
async def send_time_embed(interaction, member: discord.Member):
    data = activity_logs.get(member.id)
    if not data:
        await interaction.response.send_message(f"{member.mention} has no activity recorded.", ephemeral=True)
        return

    offline_time = 0
    now = datetime.datetime.now(datetime.timezone.utc)
    if not data["online"] and data.get("offline_start"):
        offline_time = int((now - data["offline_start"]).total_seconds())

    embed = discord.Embed(
        title="⏳ Time Tracking",
        description=f"{member.mention}",
        color=discord.Color.blurple(),
        timestamp=now
    )
    embed.add_field(name="Status", value="🟢 Online" if data["online"] else "⚫ Offline")
    embed.add_field(name="Online Time", value=format_time(data["total_seconds"]))
    embed.add_field(name="Offline Time", value=format_time(data["offline_seconds"] + offline_time))
    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.set_footer(text=f"User ID: {member.id}")
    await interaction.response.send_message(embed=embed)

# ------------------ RMUTE EMBED ------------------
async def rmute_command(interaction, member: discord.Member, duration_minutes: int, reason: str):
    try:
        timeout_end = datetime.datetime.utcnow() + datetime.timedelta(minutes=duration_minutes)
        await member.edit(timeout=timeout_end)
        role = member.guild.get_role(MUTE_ROLE_ID)
        if role and role not in member.roles:
            await member.add_roles(role)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Missing permissions.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🔇 User Timed Out",
        description=f"{member.mention} has been muted by {interaction.user.mention}",
        color=discord.Color.red(),
        timestamp=datetime.datetime.utcnow()
    )
    embed.add_field(name="Duration", value=f"{duration_minutes} minutes")
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_author(name=str(member), icon_url=member.display_avatar.url)
    embed.set_footer(text=f"User ID: {member.id}")

    log_channel = member.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(embed=embed)
    await interaction.response.send_message(embed=embed)

# ------------------ SLASH COMMANDS ------------------
@bot.tree.command(name="timetrack", description="Show current online/offline time")
async def timetrack(interaction: discord.Interaction, member: discord.Member):
    await send_time_embed(interaction, member)

@bot.tree.command(name="rmute", description="Timeout a member")
async def rmute(interaction: discord.Interaction, member: discord.Member, duration: int, reason: str):
    await rmute_command(interaction, member, duration, reason)

# ------------------ RUN BOT ------------------
@bot.event
async def on_ready():
    if not update_all_users.is_running():
        update_all_users.start()
    try:
        await bot.tree.sync()
        print("✅ Commands synced")
    except Exception as e:
        print(f"⚠️ Slash sync failed: {e}")
    print(f"✅ Logged in as {bot.user}")

bot.run(TOKEN)
