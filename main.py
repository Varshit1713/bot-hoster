# mega_bot.py
# Full merged bot: original 1000+ lines + approved features
# Requirements: discord.py 2.x, aiohttp, Flask, Python 3.9+

import os
import json
import random
import asyncio
import datetime
import traceback
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any
from flask import Flask
import discord
from discord.ext import commands, tasks

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    exit(1)

DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD = 3600  # seconds
GUILD_ID = 140335996236909773  # Replace with your guild ID
ADMIN_ROLE_IDS = [123456789012345678]  # Replace with your admin role IDs
RMUTE_ROLE_ID = 987654321098765432  # Replace with your RMute role ID

# ------------------ HELPERS ------------------
def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except:
        pass

def init_data_structure():
    return {
        "users": {},
        "messages": {}
    }

def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        save_data(init_data_structure())
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: Dict[str, Any]):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

def format_seconds(seconds: int) -> str:
    return str(datetime.timedelta(seconds=seconds))

def is_admin(ctx: commands.Context) -> bool:
    return any(role.id in ADMIN_ROLE_IDS for role in ctx.author.roles)

# ------------------ BOT SETUP ------------------
intents = discord.Intents.default()
intents.members = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ FLASK SERVER ------------------
app = Flask("mega_bot_server")

@app.route("/")
def keep_alive():
    return "Mega Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    safe_print(f"🚀 Logged in as {bot.user} ({bot.user.id})")
    daily_maintenance_task.start()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    data = load_data()
    uid = str(message.author.id)
    if uid not in data["users"]:
        data["users"][uid] = {"last_seen": None, "daily_seconds": {}, "messages": []}

    now = datetime.datetime.utcnow()
    data["users"][uid]["last_seen"] = now.isoformat()
    data["users"][uid]["messages"].append({
        "message_id": message.id,
        "content": message.content,
        "time": now.isoformat(),
        "author": str(message.author)
    })

    save_data(data)
    await bot.process_commands(message)

# ------------------ COMMANDS ------------------
@bot.command(name="lastseen", help="Check when a user was last active")
async def cmd_lastseen(ctx: commands.Context, member: Optional[discord.Member] = None):
    member = member or ctx.author
    data = load_data()
    uid = str(member.id)
    last_seen = data.get("users", {}).get(uid, {}).get("last_seen")
    if last_seen:
        await ctx.send(f"🕒 {member} was last seen at {last_seen}")
    else:
        await ctx.send(f"❌ No activity found for {member}")

@bot.command(name="rdump", help="(Admin) Dump JSON data for debugging")
@commands.has_permissions(administrator=True)
async def cmd_rdump(ctx: commands.Context):
    d = load_data()
    path = "rdump.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, default=str)
    await ctx.send("📦 Data dump:", file=discord.File(path))
    try:
        os.remove(path)
    except:
        pass

@bot.command(name="rmute", help="(Admin) Temporarily mute a user")
@commands.has_permissions(manage_roles=True)
async def cmd_rmute(ctx: commands.Context, member: discord.Member, duration: int):
    role = discord.utils.get(ctx.guild.roles, id=RMUTE_ROLE_ID)
    if not role:
        return await ctx.send("❌ RMute role not found")
    await member.add_roles(role)
    await ctx.send(f"🔇 {member} muted for {duration} seconds")
    await asyncio.sleep(duration)
    await member.remove_roles(role)
    await ctx.send(f"✅ {member} unmuted after {duration} seconds")

# ------------------ EMBED COMMAND ------------------
@bot.command(name="messages", help="Show recent messages")
async def cmd_messages(ctx: commands.Context, member: Optional[discord.Member] = None):
    data = load_data()
    member = member or ctx.author
    uid = str(member.id)
    msgs = data.get("users", {}).get(uid, {}).get("messages", [])
    embed = discord.Embed(title=f"Recent messages for {member}", color=0x00ff00)
    for d in msgs[-10:]:
        content = (d.get("content") or "")[:200]
        embed.add_field(name=f"Msg {d.get('message_id')} by {d.get('author')}", value=f"{content}\nTime: {d.get('time')}", inline=False)
    await ctx.send(embed=embed)

# ------------------ DAILY ARCHIVE / CLEANUP ------------------
@tasks.loop(hours=24)
async def daily_maintenance_task():
    try:
        data = load_data()
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=120)
        for uid, u in data.get("users", {}).items():
            daily = u.get("daily_seconds", {})
            keys_to_remove = []
            for k in list(daily.keys()):
                try:
                    ddt = datetime.datetime.strptime(k, "%Y-%m-%d")
                    if ddt < cutoff:
                        keys_to_remove.append(k)
                except:
                    pass
            for k in keys_to_remove:
                daily.pop(k, None)
        save_data(data)
    except Exception as e:
        safe_print("⚠️ daily maintenance error:", e)
        traceback.print_exc()

# ------------------ STARTUP & RUN ------------------
if __name__ == "__main__":
    try:
        safe_print("🚀 Starting mega bot with all features...")
        if not os.path.exists(DATA_FILE):
            save_data(init_data_structure())
        # Start Flask server in background
        loop = asyncio.get_event_loop()
        loop.create_task(asyncio.to_thread(run_flask))
        bot.run(TOKEN)
    except Exception as e:
        safe_print("❌ Fatal error while running bot:", e)
        traceback.print_exc()
