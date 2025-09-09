import discord
from discord.ext import commands
import datetime
import json
import os

TOKEN = os.environ.get("DISCORD_TOKEN")
DATA_FILE = "activity_logs.json"

intents = discord.Intents.default()
intents.members = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Load or initialize logs
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        activity_logs = json.load(f)
else:
    activity_logs = {}

# Helper to save
def save_logs():
    with open(DATA_FILE, "w") as f:
        json.dump(activity_logs, f, indent=4)

# Update time on presence change
@bot.event
async def on_presence_update(before, after):
    user_id = str(after.id)
    now = datetime.datetime.utcnow().timestamp()
    logs = activity_logs.setdefault(user_id, {"total_seconds": 0, "last_online": None, "status": "offline"})
    
    logs["status"] = str(after.status)
    
    # Went online
    if before.status == discord.Status.offline and after.status != discord.Status.offline:
        logs["last_online"] = now
    
    # Went offline
    elif before.status != discord.Status.offline and after.status == discord.Status.offline:
        if logs["last_online"]:
            logs["total_seconds"] += now - logs["last_online"]
            logs["last_online"] = None
    
    save_logs()

# Compute current online time
def get_total_time(user_id):
    logs = activity_logs.get(str(user_id))
    if not logs:
        return 0
    total = logs["total_seconds"]
    # Add ongoing session if user is currently online
    if logs["last_online"]:
        total += datetime.datetime.utcnow().timestamp() - logs["last_online"]
    return int(total)

# Slash command
@bot.tree.command(name="timetrack", description="Show total online time")
@discord.app_commands.describe(username="User to check")
async def timetrack(interaction: discord.Interaction, username: discord.Member):
    total_seconds = get_total_time(username.id)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    status = activity_logs.get(str(username.id), {}).get("status", "offline").capitalize()
    
    await interaction.response.send_message(
        f"⏳ **{username.display_name}** has {hours}h {minutes}m {seconds}s online.\nStatus: {status}"
    )

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await bot.tree.sync()

bot.run(TOKEN)
