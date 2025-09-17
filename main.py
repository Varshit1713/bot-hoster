# mega_timetrack_bot.py
# Full-featured, massive Timetrack Bot
# Python 3.9+, discord.py 2.x, pytz

import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import pytz
import json
import os
import threading
import random
import math
import traceback

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = 140335996236909773
TRACK_CHANNEL_ID = 1410458084874260592
TRACK_ROLES = [
    1410419924173848626, 1410420126003630122, 1410423594579918860,
    1410421466666631279, 1410421647265108038, 1410419345234067568,
    1410422029236047975, 1410458084874260592
]
OFFLINE_DELAY = 53
STATUS_CHECK_INTERVAL = 5
AUTO_SAVE_INTERVAL = 120
DATA_FILE = "timetrack_data.json"
BACKUP_DIR = "timetrack_backups"
MAX_BACKUPS = 10
COMMAND_COOLDOWN = 5  # seconds

# ------------------ INTENTS ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
data_lock = threading.Lock()
console_lock = threading.Lock()
command_cooldowns = {}

# ------------------ HELPER FUNCTIONS ------------------
def safe_print(*args, **kwargs):
    with console_lock:
        print(*args, **kwargs)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_data(data):
    with data_lock:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"timetrack_backup_{timestamp}.json")
        try:
            # Save backup
            with open(backup_file, "w") as bf:
                json.dump(data, bf, indent=4)
            # Trim old backups
            backups = sorted(os.listdir(BACKUP_DIR))
            if len(backups) > MAX_BACKUPS:
                for old in backups[:len(backups)-MAX_BACKUPS]:
                    os.remove(os.path.join(BACKUP_DIR, old))
            # Save main data
            with open(DATA_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            safe_print("❌ Error saving data:", e)
            traceback.print_exc()

def ensure_user_data(uid, data):
    if uid not in data:
        data[uid] = {
            "status": "offline",
            "online_time": None,
            "offline_time": None,
            "last_message": None,
            "last_edit": None,
            "last_delete": None,
            "last_online_times": {},
            "offline_timer": 0,
            "total_online_seconds": 0,
            "daily_seconds": {},
            "weekly_seconds": {},
            "monthly_seconds": {},
            "notify": True
        }

def format_time(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def get_timezones():
    return {
        "UTC": pytz.utc,
        "EST": pytz.timezone("US/Eastern"),
        "PST": pytz.timezone("US/Pacific"),
        "CET": pytz.timezone("Europe/Paris")
    }

def ascii_progress_bar(current, total, length=20):
    try:
        ratio = min(max(float(current)/float(total),0),1)
        filled = int(length * ratio)
        empty = length - filled
        return "█"*filled + "░"*empty
    except:
        return "░"*length

async def update_last_online(user: discord.Member, data):
    tz_dict = {}
    for tz_name, tz in get_timezones().items():
        tz_dict[tz_name] = format_time(datetime.datetime.now(tz))
    data[str(user.id)]["last_online_times"] = tz_dict
    save_data(data)

def increment_total_online(user_id, seconds, data):
    uid = str(user_id)
    ensure_user_data(uid, data)
    data[uid]["total_online_seconds"] += seconds
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    week = datetime.datetime.now().strftime("%Y-W%U")
    month = datetime.datetime.now().strftime("%Y-%m")
    data[uid]["daily_seconds"][today] = data[uid]["daily_seconds"].get(today,0)+seconds
    data[uid]["weekly_seconds"][week] = data[uid]["weekly_seconds"].get(week,0)+seconds
    data[uid]["monthly_seconds"][month] = data[uid]["monthly_seconds"].get(month,0)+seconds

def can_execute_command(user_id):
    last = command_cooldowns.get(user_id, 0)
    now = datetime.datetime.now().timestamp()
    if now - last >= COMMAND_COOLDOWN:
        command_cooldowns[user_id] = now
        return True
    return False

def create_embed(user_data, member: discord.Member):
    embed = discord.Embed(title=f"Timetrack: {member.display_name}", color=discord.Color.blue())
    embed.add_field(name="Status", value=user_data.get("status","offline"), inline=True)
    embed.add_field(name="Online Since", value=user_data.get("online_time","N/A"), inline=True)
    embed.add_field(name="Offline Since", value=user_data.get("offline_time","N/A"), inline=True)
    embed.add_field(name="Last Message", value=user_data.get("last_message","N/A"), inline=False)
    embed.add_field(name="Last Edit", value=user_data.get("last_edit","N/A"), inline=False)
    embed.add_field(name="Last Delete", value=user_data.get("last_delete","N/A"), inline=False)
    
    tz_lines = ""
    for tz, t in user_data.get("last_online_times",{}).items():
        tz_lines += f"{tz}: {t}\n"
    embed.add_field(name="Last Online (Timezones)", value=tz_lines or "N/A", inline=False)
    
    total_sec = user_data.get("total_online_seconds",0)
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    embed.add_field(name="Total Online Time", value=f"{h}h {m}m {s}s", inline=False)
    
    # Daily progress bar example
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    daily_sec = user_data.get("daily_seconds",{}).get(today,0)
    embed.add_field(name="Today Activity", value=ascii_progress_bar(daily_sec, 3600)+f" ({daily_sec}s)", inline=False)
    
    embed.set_footer(text="Timetrack Bot | By Skibidisigma")
    return embed

# ------------------ TRACKING TASK ------------------
@tasks.loop(seconds=STATUS_CHECK_INTERVAL)
async def track_users():
    try:
        guild = bot.get_guild(GUILD_ID)
        if not guild: return
        channel = bot.get_channel(TRACK_CHANNEL_ID)
        if not channel: return
        data = load_data()
        now_utc = datetime.datetime.utcnow()
        for member in guild.members:
            if not any(r.id in TRACK_ROLES for r in member.roles): continue
            uid = str(member.id)
            ensure_user_data(uid,data)
            if member.status != discord.Status.offline:
                if data[uid]["status"]=="offline":
                    data[uid]["status"]="online"
                    data[uid]["online_time"]=format_time(now_utc)
                    data[uid]["offline_timer"]=0
                    await update_last_online(member,data)
                    if data[uid].get("notify",True):
                        await channel.send(f"✅ {member.display_name} is online")
                increment_total_online(member.id,STATUS_CHECK_INTERVAL,data)
            else:
                if data[uid]["status"]=="online":
                    data[uid]["offline_timer"]+=STATUS_CHECK_INTERVAL
                    if data[uid]["offline_timer"]>=OFFLINE_DELAY:
                        data[uid]["status"]="offline"
                        data[uid]["offline_time"]=format_time(now_utc)
                        data[uid]["offline_timer"]=0
                        await update_last_online(member,data)
                        if data[uid].get("notify",True):
                            await channel.send(f"⚠️ {member.display_name} is offline")
        save_data(data)
    except Exception as e:
        safe_print("❌ Error in track_users:", e)
        traceback.print_exc()

# ------------------ AUTO SAVE ------------------
@tasks.loop(seconds=AUTO_SAVE_INTERVAL)
async def auto_save():
    try:
        save_data(load_data())
        safe_print("💾 Auto-saved timetrack data")
    except Exception as e:
        safe_print("❌ Error in auto_save:", e)
        traceback.print_exc()

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    safe_print(f"Bot logged in as {bot.user}")
    track_users.start()
    auto_save.start()

@bot.event
async def on_message(message):
    if message.author.bot: return
    data = load_data()
    uid = str(message.author.id)
    ensure_user_data(uid,data)
    data[uid]["last_message"]=message.content
    save_data(data)
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before, after):
    if after.author.bot: return
    data = load_data()
    uid = str(after.author.id)
    ensure_user_data(uid,data)
    data[uid]["last_edit"]=after.content
    save_data(data)

@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    data = load_data()
    uid = str(message.author.id)
    ensure_user_data(uid,data)
    data[uid]["last_delete"]=message.content
    save_data(data)

# ------------------ COMMANDS ------------------
@bot.command()
async def timetrack(ctx, member: discord.Member = None):
    if not can_execute_command(ctx.author.id):
        await ctx.send("⏱ Please wait before using this command again.")
        return
    member = member or ctx.author
    data = load_data()
    uid = str(member.id)
    ensure_user_data(uid,data)
    embed = create_embed(data[uid], member)
    await ctx.send(embed=embed)

@bot.command()
async def tt(ctx, member: discord.Member = None):
    await timetrack(ctx, member)

@bot.command()
async def timetrackstats(ctx, top: int = 5):
    if not can_execute_command(ctx.author.id):
        await ctx.send("⏱ Please wait before using this command again.")
        return
    data = load_data()
    leaderboard = []
    for uid,info in data.items():
        leaderboard.append((uid, info.get("total_online_seconds",0)))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard", color=discord.Color.gold())
    for i,(uid,sec) in enumerate(leaderboard[:top], start=1):
        member = ctx.guild.get_member(int(uid))
        if not member: continue
        h = sec//3600
        m = (sec%3600)//60
        s = sec%60
        embed.add_field(name=f"{i}. {member.display_name}", value=f"{h}h {m}m {s}s", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def ttstats(ctx, top: int = 5):
    await timetrackstats(ctx, top)
