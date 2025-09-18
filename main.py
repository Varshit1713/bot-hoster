# ==============================================================
# Discord Bot: Ultimate Full Feature Version
# ==============================================================

import discord
from discord.ext import commands, tasks
import asyncio
import pytz
import datetime
import json
import os
from flask import Flask
import threading

# -------------------------
# Flask Keep-Alive Setup
# -------------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

threading.Thread(target=run_flask).start()

# -------------------------
# Bot Setup & Constants
# -------------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# Roles/Channels/User IDs
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE_ID = 1410423854563721287
TRACKING_CHANNEL_ID = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
DANGEROUS_NOTIFY_IDS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

DATA_FILE = "bot_data.json"
COOLDOWN_TIME = 5  # seconds default per command

# -------------------------
# Load / Save Data
# -------------------------
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({
            "users": {}, 
            "mutes": {}, 
            "rmute_usage": {}, 
            "cached_messages": {}, 
            "rdm_users": [],
            "logs": {},
            "cooldowns": {}
        }, f)

def load_data():
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

data = load_data()

# -------------------------
# Helper Functions
# -------------------------
def tz_now():
    return datetime.datetime.now(pytz.utc)

def format_seconds(seconds):
    h, m = divmod(seconds, 3600)
    m, s = divmod(m, 60)
    return f"{int(h)}h {int(m)}m {int(s)}s"

def is_rcache_role(member):
    return any(role.id in RCACHE_ROLES for role in member.roles)

async def send_dm(user: discord.User, embed: discord.Embed, force=False):
    if str(user.id) in data.get("rdm_users", []) and not force:
        return
    try:
        await user.send(embed=embed)
    except:
        pass

def parse_duration(duration_str):
    unit = duration_str[-1]
    num = int(duration_str[:-1])
    if unit == "s":
        return num
    elif unit == "m":
        return num*60
    elif unit == "h":
        return num*3600
    elif unit == "d":
        return num*86400
    else:
        return num

# -------------------------
# Cooldown System
# -------------------------
def check_cooldown(user_id, command):
    now_ts = tz_now().timestamp()
    user_cd = data.get("cooldowns", {}).get(str(user_id), {})
    last = user_cd.get(command, 0)
    if now_ts - last < COOLDOWN_TIME:
        return False
    data.setdefault("cooldowns", {}).setdefault(str(user_id), {})[command] = now_ts
    save_data(data)
    return True

# -------------------------
# Timetrack System
# -------------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    now = tz_now().timestamp()
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot or not is_rcache_role(member):
                continue
            uid = str(member.id)
            udata = data["users"].get(uid, {})
            online = member.status != discord.Status.offline
            if online:
                if not udata.get("online_start"):
                    udata["online_start"] = now
            else:
                if udata.get("online_start"):
                    session = now - udata["online_start"]
                    udata["total_online_seconds"] = udata.get("total_online_seconds", 0) + session
                    udata["online_start"] = None
            udata["last_online"] = now
            data["users"][uid] = udata
    save_data(data)

@bot.command()
async def tlb(ctx):
    if not check_cooldown(ctx.author.id, "tlb"):
        return await ctx.send("Cooldown active.")
    now_ts = tz_now().timestamp()
    users_list = []
    for uid, udata in data["users"].items():
        member = ctx.guild.get_member(int(uid))
        if not member or not is_rcache_role(member):
            continue
        total = udata.get("total_online_seconds", 0)
        if udata.get("online_start"):
            total += now_ts - udata["online_start"]
        users_list.append((member.display_name, total))
    users_list.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard", color=discord.Color.blurple())
    for name, total in users_list[:10]:
        embed.add_field(name=name, value=format_seconds(total), inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def tdm(ctx):
    if not check_cooldown(ctx.author.id, "tdm"):
        return await ctx.send("Cooldown active.")
    now_ts = tz_now().timestamp()
    users_list = []
    for uid, udata in data["users"].items():
        member = ctx.guild.get_member(int(uid))
        if not member or is_rcache_role(member):
            continue
        total = udata.get("total_online_seconds", 0)
        if udata.get("online_start"):
            total += now_ts - udata["online_start"]
        users_list.append((member.display_name, total))
    users_list.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (Non-RCache)", color=discord.Color.orange())
    for name, total in users_list[:10]:
        embed.add_field(name=name, value=format_seconds(total), inline=False)
    await ctx.send(embed=embed)

# -------------------------
# RMute / Runmute System
# -------------------------
async def rmute_user(ctx, member: discord.Member, duration_seconds: int, reason: str):
    mute_role = ctx.guild.get_role(MUTE_ROLE_ID)
    if mute_role in member.roles:
        return
    await member.add_roles(mute_role, reason=reason)
    uid = str(member.id)
    now_ts = tz_now().timestamp()
    data["mutes"][uid] = {"moderator": ctx.author.id, "start": now_ts, "duration": duration_seconds, "reason": reason}
    data["rmute_usage"][str(ctx.author.id)] = data.get("rmute_usage", {}).get(str(ctx.author.id), 0) + 1
    save_data(data)
    # DM User
    embed = discord.Embed(title="You have been muted", color=discord.Color.red())
    embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)
    embed.add_field(name="Duration", value=format_seconds(duration_seconds), inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    await send_dm(member, embed)
    # Log
    log_embed = discord.Embed(title="User Muted", color=discord.Color.red())
    log_embed.add_field(name="User", value=member.mention, inline=False)
    log_embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)
    log_embed.add_field(name="Duration", value=format_seconds(duration_seconds), inline=False)
    log_embed.add_field(name="Reason", value=reason, inline=False)
    log_embed.timestamp = tz_now()
    await send_dm_for_danger(log_embed)
    channel = ctx.guild.get_channel(TRACKING_CHANNEL_ID)
    if channel:
        await channel.send(embed=log_embed)
    # Auto-unmute
    asyncio.create_task(auto_unmute(member, duration_seconds))

async def auto_unmute(member, duration_seconds):
    await asyncio.sleep(duration_seconds)
    mute_role = member.guild.get_role(MUTE_ROLE_ID)
    if mute_role in member.roles:
        await member.remove_roles(mute_role, reason="Auto-unmute")
        uid = str(member.id)
        if uid in data["mutes"]:
            del data["mutes"][uid]
        save_data(data)
        embed = discord.Embed(title="User Automatically Unmuted", color=discord.Color.green())
        embed.add_field(name="User", value=member.mention, inline=False)
        channel = member.guild.get_channel(TRACKING_CHANNEL_ID)
        if channel:
            await channel.send(embed=embed)

@bot.command()
async def rmute(ctx, members: commands.Greedy[discord.Member], duration: str, *, reason: str):
    if not check_cooldown(ctx.author.id, "rmute"):
        return await ctx.send("Cooldown active.")
    seconds = parse_duration(duration)
    for member in members:
        await rmute_user(ctx, member, seconds, reason)
    await ctx.message.delete()

@bot.command()
async def runmute(ctx, member: discord.Member, duration: str, *, reason: str):
    if not check_cooldown(ctx.author.id, "runmute"):
        return await ctx.send("Cooldown active.")
    seconds = parse_duration(duration)
    await rmute_user(ctx, member, seconds, reason)
    await ctx.message.delete()

@bot.command()
async def rmlb(ctx):
    usage = data.get("rmute_usage", {})
    sorted_usage = sorted(usage.items(), key=lambda x:x[1], reverse=True)
    embed = discord.Embed(title="RMute Leaderboard", color=discord.Color.purple())
    for uid, count in sorted_usage[:10]:
        member = ctx.guild.get_member(int(uid))
        embed.add_field(name=member.display_name if member else uid, value=f"Mutes: {count}", inline=False)
    await ctx.send(embed=embed)

# -------------------------
# Cache System
# -------------------------
@bot.command()
async def rcache(ctx):
    if not is_rcache_role(ctx.author):
        return await ctx.send("You do not have permission.")
    embed = discord.Embed(title="Deleted Messages Cache", color=discord.Color.blurple())
    for mid, cdata in data.get("cached_messages", {}).items():
        info = f"Author: {cdata.get('author_name')}\nContent: {cdata.get('content')}\nAttachments: {', '.join(cdata.get('attachments',[]))}\nDeleted By: {cdata.get('deleted_by')}"
        embed.add_field(name=f"Message ID: {mid}", value=info, inline=False)
    await ctx.send(embed=embed)

# -------------------------
# Logging & Dangerous DM Notifications
# -------------------------
async def log_action(embed: discord.Embed):
    channel = bot.get_channel(TRACKING_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)

async def send_dm_for_danger(embed: discord.Embed):
    for uid in DANGEROUS_NOTIFY_IDS:
        user = bot.get_user(uid)
        if user:
            await send_dm(user, embed, force=True)

# -------------------------
# Staff Ping System
# -------------------------
@bot.command()
async def rping(ctx):
    if not check_cooldown(ctx.author.id, "rping"):
        return await ctx.send("Cooldown active.")
    role = ctx.guild.get_role(STAFF_PING_ROLE)
    if role:
        await ctx.send(f"{role.mention}")
    await ctx.message.delete()

@bot.command()
async def hsping(ctx):
    if not check_cooldown(ctx.author.id, "hsping"):
        return await ctx.send("Cooldown active.")
    role = ctx.guild.get_role(HIGHER_STAFF_PING_ROLE)
    if role:
        await ctx.send(f"{role.mention}")
    await ctx.message.delete()

# -------------------------
# DM Opt-out System
# -------------------------
@bot.command()
async def rdm(ctx):
    uid = str(ctx.author.id)
    if uid in data.get("rdm_users", []):
        data["rdm_users"].remove(uid)
        await ctx.send("You will now receive bot DMs.")
    else:
        data.setdefault("rdm_users", []).append(uid)
        await ctx.send("You have opted out from bot DMs.")
    save_data(data)

# -------------------------
# Help Command
# -------------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.blurple())
    cmds = {
        "!timetrack [user]":"Show online stats",
        "!rmute [users] [duration] [reason]":"Mute multiple users",
        "!runmute [user] [duration] [reason]":"Mute single user",
        "!rmlb":"Top RMute users",
        "!rcache":"Deleted messages cache",
        "!tlb":"Top online users",
        "!tdm":"Top users without roles",
        "!rping":"Ping staff",
        "!hsping":"Ping higher staff",
        "!rdm":"Opt-out/opt-in bot DMs",
    }
    for c,d in cmds.items():
        embed.add_field(name=c, value=d, inline=False)
    await ctx.send(embed=embed)

# -------------------------
# Logging Events
# -------------------------
@bot.event
async def on_guild_channel_create(channel):
    embed = discord.Embed(title="Channel Created", color=discord.Color.green())
    embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
    embed.add_field(name="Guild", value=channel.guild.name, inline=False)
    embed.timestamp = tz_now()
    await log_action(embed)

@bot.event
async def on_guild_channel_delete(channel):
    embed = discord.Embed(title="Channel Deleted", color=discord.Color.red())
    embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
    embed.add_field(name="Guild", value=channel.guild.name, inline=False)
    embed.timestamp = tz_now()
    await log_action(embed)

@bot.event
async def on_guild_channel_update(before, after):
    embed = discord.Embed(title="Channel Updated", color=discord.Color.orange())
    embed.add_field(name="Before", value=before.name, inline=False)
    embed.add_field(name="After", value=after.name, inline=False)
    embed.timestamp = tz_now()
    await log_action(embed)

@bot.event
async def on_guild_role_create(role):
    embed = discord.Embed(title="Role Created", color=discord.Color.green())
    embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
    embed.timestamp = tz_now()
    await log_action(embed)

@bot.event
async def on_guild_role_delete(role):
    embed = discord.Embed(title="Role Deleted", color=discord.Color.red())
    embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
    embed.timestamp = tz_now()
    await log_action(embed)

@bot.event
async def on_guild_role_update(before, after):
    embed = discord.Embed(title="Role Updated", color=discord.Color.orange())
    embed.add_field(name="Before", value=before.name, inline=False)
    embed.add_field(name="After", value=after.name, inline=False)
    embed.timestamp = tz_now()
    await log_action(embed)

@bot.event
async def on_webhook_update(channel):
    embed = discord.Embed(title="Webhook Updated", color=discord.Color.gold())
    embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
    embed.timestamp = tz_now()
    await log_action(embed)

@bot.event
async def on_message_delete(message):
    uid = str(message.author.id)
    data["cached_messages"][str(message.id)] = {
        "author_id": message.author.id,
        "author_name": message.author.display_name,
        "content": message.content,
        "attachments": [a.url for a in message.attachments],
        "channel": message.channel.id,
        "timestamp": message.created_at.timestamp(),
        "deleted_by": None  # optional: track if moderator deleted
    }
    save_data(data)

@bot.event
async def on_bulk_message_delete(messages):
    embed = discord.Embed(title="Messages Purged", color=discord.Color.red())
    for message in messages:
        info = f"Author: {message.author.display_name}\nContent: {message.content}\nAttachments: {', '.join([a.url for a in message.attachments])}"
        embed.add_field(name=f"Message ID: {message.id}", value=info, inline=False)
    embed.timestamp = tz_now()
    await log_action(embed)

# -------------------------
# Bot Ready
# -------------------------
@bot.event
async def on_ready():
    print(f"{bot.user} has connected and is ready!")
    timetrack_loop.start()
    # Restart auto-unmute for existing mutes
    now_ts = tz_now().timestamp()
    for uid, mute_info in data.get("mutes", {}).items():
        member = None
        for guild in bot.guilds:
            member = guild.get_member(int(uid))
            if member:
                break
        if member:
            elapsed = now_ts - mute_info["start"]
            remaining = mute_info["duration"] - elapsed
            if remaining > 0:
                asyncio.create_task(auto_unmute(member, remaining))
            else:
                mute_role = member.guild.get_role(MUTE_ROLE_ID)
                if mute_role in member.roles:
                    asyncio.create_task(member.remove_roles(mute_role, reason="Auto-unmute after restart"))
                    del data["mutes"][uid]
    save_data(data)

# -------------------------
# Run Bot
# -------------------------
bot.run(TOKEN)
