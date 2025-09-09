import discord
from discord.ext import commands
import asyncio
import json
import os
from datetime import datetime

intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "active_time.json"

# Load saved data
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        active_time = json.load(f)
else:
    active_time = {}

# Track current sessions {user_id: session_start}
sessions = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(active_time, f)

def format_time(seconds: int) -> str:
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_presence_update(before, after):
    user_id = str(after.id)

    # User goes ONLINE
    if after.status == discord.Status.online and user_id not in sessions:
        sessions[user_id] = datetime.utcnow()

    # User goes OFFLINE
    elif after.status == discord.Status.offline and user_id in sessions:
        start_time = sessions.pop(user_id)
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        active_time[user_id] = active_time.get(user_id, 0) + int(elapsed)
        save_data()

@bot.command()
async def active(ctx, member: discord.Member = None):
    member = member or ctx.author
    user_id = str(member.id)

    total = active_time.get(user_id, 0)

    # If user is in a current session, add live time
    if user_id in sessions:
        elapsed = (datetime.utcnow() - sessions[user_id]).total_seconds()
        total += int(elapsed)

    status = member.status.name.capitalize()
    await ctx.send(
        f"⏳ **{member.display_name}**\n"
        f"Status: {status}\n"
        f"Active Time: {format_time(total)}"
    )

bot.run(os.getenv("DISCORD_TOKEN"))
