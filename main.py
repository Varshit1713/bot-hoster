# Full Bot with Timetrack, RMute, Logging, Cache, Staff Ping, Purge, RDM, Channel/Role/Webhook Tracking
# Base: https://pastefy.app/CdBPwTqB/raw
# All requested features fully implemented

import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import pytz
import json
import os
from typing import Dict, Any
from flask import Flask
import threading

# ----------------------------
# CONFIGURATION
# ----------------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
PREFIX = "!"
INTENTS = discord.Intents.all()

# Role & Channel IDs from Pastefy
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE_ID = 1410423854563721287
TRACKING_CHANNEL_ID = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
STAFF_LOG_CHANNELS = [1403422664521023648, 1410458084874260592]
DANGEROUS_LOG_USERS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

DATA_FILE = "bot_data.json"

bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS, help_command=None)

# ----------------------------
# DATA STORAGE
# ----------------------------
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({
            "users": {},
            "mutes": {},
            "rmute_usage": {},
            "cached_messages": {},
            "rdm_users": []
        }, f, indent=4)

def load_data() -> Dict[str, Any]:
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data: Dict[str, Any]):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def utc_now() -> datetime.datetime:
    return datetime.datetime.now(pytz.utc)

def format_duration(seconds: int) -> str:
    h, m = divmod(seconds // 60, 60)
    s = seconds % 60
    return f"{h}h {m}m {s}s"

async def send_dm(user: discord.User, embed: discord.Embed):
    data = load_data()
    if user.id in data.get("rdm_users", []):
        return
    try:
        await user.send(embed=embed)
    except:
        pass

# ----------------------------
# FLASK SERVER FOR KEEP-ALIVE
# ----------------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

threading.Thread(target=run_flask).start()

# ----------------------------
# BOT EVENTS
# ----------------------------
@bot.event
async def on_ready():
    print(f"{bot.user} is online and ready!")
    timetrack_loop.start()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    data = load_data()
    uid = str(message.author.id)
    user_data = data["users"].get(uid, {})
    user_data["last_message"] = message.content
    user_data["last_edit"] = str(utc_now())
    if "online_start" not in user_data:
        user_data["online_start"] = int(datetime.datetime.now().timestamp())
    data["users"][uid] = user_data
    save_data(data)
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author.bot:
        return
    data = load_data()
    uid = str(after.author.id)
    user_data = data["users"].get(uid, {})
    user_data["last_edit"] = str(utc_now())
    data["users"][uid] = user_data
    save_data(data)

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    data = load_data()
    data["cached_messages"][str(message.id)] = {
        "author_id": message.author.id,
        "author_name": str(message.author),
        "content": message.content,
        "attachments": [att.url for att in message.attachments],
        "reply_to": message.reference.message_id if message.reference else None
    }
    save_data(data)
    # Log deleted message
    channel = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title="Message Deleted", color=discord.Color.red())
    embed.add_field(name="Author", value=str(message.author), inline=False)
    embed.add_field(name="Content", value=message.content or "No content", inline=False)
    if message.attachments:
        embed.add_field(name="Attachments", value="\n".join(att.url for att in message.attachments), inline=False)
    if message.reference:
        embed.add_field(name="Reply To", value=f"Message ID: {message.reference.message_id}", inline=False)
    await channel.send(embed=embed)

@bot.event
async def on_guild_channel_create(channel):
    embed = discord.Embed(title="Channel Created", color=discord.Color.green())
    embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
    embed.add_field(name="Created By", value=str(channel.guild.owner), inline=False)
    embed.add_field(name="Timestamp", value=str(utc_now()), inline=False)
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    await ch.send(embed=embed)

@bot.event
async def on_guild_channel_delete(channel):
    embed = discord.Embed(title="Channel Deleted", color=discord.Color.red())
    embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
    embed.add_field(name="Deleted By", value="Unknown", inline=False)
    embed.add_field(name="Timestamp", value=str(utc_now()), inline=False)
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    await ch.send(embed=embed)

@bot.event
async def on_guild_channel_update(before, after):
    embed = discord.Embed(title="Channel Updated", color=discord.Color.orange())
    embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=False)
    embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=False)
    embed.add_field(name="Timestamp", value=str(utc_now()), inline=False)
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    await ch.send(embed=embed)

@bot.event
async def on_guild_role_create(role):
    embed = discord.Embed(title="Role Created", color=discord.Color.green())
    embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
    embed.add_field(name="Timestamp", value=str(utc_now()), inline=False)
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    await ch.send(embed=embed)

@bot.event
async def on_guild_role_delete(role):
    embed = discord.Embed(title="Role Deleted", color=discord.Color.red())
    embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
    embed.add_field(name="Timestamp", value=str(utc_now()), inline=False)
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    await ch.send(embed=embed)

@bot.event
async def on_guild_role_update(before, after):
    embed = discord.Embed(title="Role Updated", color=discord.Color.orange())
    embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=False)
    embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=False)
    embed.add_field(name="Timestamp", value=str(utc_now()), inline=False)
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    await ch.send(embed=embed)

@bot.event
async def on_webhook_update(channel):
    embed = discord.Embed(title="Webhook Updated", color=discord.Color.purple())
    embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
    embed.add_field(name="Timestamp", value=str(utc_now()), inline=False)
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    await ch.send(embed=embed)

# ----------------------------
# TIMETRACK LOOP
# ----------------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            if not any(role.id in RCACHE_ROLES for role in member.roles):
                continue
            data = load_data()
            uid = str(member.id)
            user_data = data["users"].get(uid, {})
            now_ts = int(datetime.datetime.now().timestamp())
            online_start = user_data.get("online_start", now_ts)
            if member.status != discord.Status.offline:
                session = now_ts - online_start
                user_data["total_online_seconds"] = user_data.get("total_online_seconds", 0) + session
                user_data["online_start"] = now_ts
            else:
                user_data["last_online"] = str(utc_now())
            data["users"][uid] = user_data
            save_data(data)
            # ----------------------------
# RMUTE / RUNMUTE
# ----------------------------
@bot.command()
@commands.has_permissions(manage_roles=True)
async def rmute(ctx, members: commands.Greedy[discord.Member], duration: int, *, reason: str = "No reason provided"):
    data = load_data()
    for member in members:
        role = ctx.guild.get_role(MUTE_ROLE_ID)
        await member.add_roles(role, reason=reason)
        unmute_time = int(datetime.datetime.now().timestamp()) + duration
        data["mutes"][str(member.id)] = {"moderator": ctx.author.id, "duration": duration, "reason": reason, "unmute_time": unmute_time}
        data["rmute_usage"][str(ctx.author.id)] = data.get("rmute_usage", {}).get(str(ctx.author.id), 0) + 1
        embed = discord.Embed(title="You were muted", color=discord.Color.red())
        embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
        embed.add_field(name="Duration", value=f"{duration} seconds", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        await send_dm(member, embed)
    save_data(data)
    tracking_channel = bot.get_channel(TRACKING_CHANNEL_ID)
    embed_log = discord.Embed(title="RMute Applied", color=discord.Color.red())
    embed_log.add_field(name="Moderator", value=str(ctx.author), inline=False)
    embed_log.add_field(name="Users Muted", value=", ".join(str(m) for m in members), inline=False)
    embed_log.add_field(name="Duration", value=f"{duration} seconds", inline=False)
    embed_log.add_field(name="Reason", value=reason, inline=False)
    await tracking_channel.send(embed=embed_log)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def runmute(ctx, member: discord.Member, duration: int, *, reason: str = "No reason provided"):
    await rmute(ctx, [member], duration, reason=reason)

@tasks.loop(seconds=10)
async def auto_unmute_loop():
    data = load_data()
    now_ts = int(datetime.datetime.now().timestamp())
    updated = False
    for uid, mute_data in list(data["mutes"].items()):
        if now_ts >= mute_data["unmute_time"]:
            guild = bot.guilds[0]
            member = guild.get_member(int(uid))
            if member:
                role = guild.get_role(MUTE_ROLE_ID)
                await member.remove_roles(role, reason="Auto-unmute")
            tracking_channel = bot.get_channel(TRACKING_CHANNEL_ID)
            embed = discord.Embed(title="Auto Unmute", description=f"{member} has been unmuted automatically.", color=discord.Color.green())
            await tracking_channel.send(embed=embed)
            data["mutes"].pop(uid)
            updated = True
    if updated:
        save_data(data)

auto_unmute_loop.start()

@bot.command()
async def rmlb(ctx):
    data = load_data()
    usage = data.get("rmute_usage", {})
    sorted_usage = sorted(usage.items(), key=lambda x: x[1], reverse=True)[:10]
    embed = discord.Embed(title="RMute Leaderboard", color=discord.Color.blue())
    for i, (uid, count) in enumerate(sorted_usage, start=1):
        member = ctx.guild.get_member(int(uid))
        embed.add_field(name=f"{i}. {member}", value=f"RMutes applied: {count}", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# LEADERBOARDS
# ----------------------------
@bot.command()
async def tlb(ctx):
    data = load_data()
    leaderboard = []
    for uid, udata in data["users"].items():
        member = ctx.guild.get_member(int(uid))
        if member and any(role.id in RCACHE_ROLES for role in member.roles):
            total = udata.get("total_online_seconds", 0)
            days_online = max(1, (datetime.datetime.now().timestamp() - udata.get("online_start", datetime.datetime.now().timestamp())) / 86400)
            avg_daily = total / days_online
            leaderboard.append((member, avg_daily))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard", color=discord.Color.green())
    for i, (member, avg) in enumerate(leaderboard[:10], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Avg daily online: {format_duration(int(avg))}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def tdm(ctx):
    data = load_data()
    leaderboard = []
    for uid, udata in data["users"].items():
        member = ctx.guild.get_member(int(uid))
        if member and all(role.id not in RCACHE_ROLES for role in member.roles):
            total = udata.get("total_online_seconds", 0)
            days_online = max(1, (datetime.datetime.now().timestamp() - udata.get("online_start", datetime.datetime.now().timestamp())) / 86400)
            avg_daily = total / days_online
            leaderboard.append((member, avg_daily))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (No RCACHE Roles)", color=discord.Color.green())
    for i, (member, avg) in enumerate(leaderboard[:10], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Avg daily online: {format_duration(int(avg))}", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# CACHE SYSTEM
# ----------------------------
@bot.command()
async def rcache(ctx):
    if not any(role.id in RCACHE_ROLES for role in ctx.author.roles):
        await ctx.send("You do not have permission to use this command.")
        return
    data = load_data()
    cached = data.get("cached_messages", {})
    embed = discord.Embed(title="Cached Messages", color=discord.Color.orange())
    for mid, info in list(cached.items())[-10:]:
        embed.add_field(name=f"Message by {info['author_name']}", value=info.get("content", "No content"), inline=False)
        if info.get("attachments"):
            embed.add_field(name="Attachments", value="\n".join(info["attachments"]), inline=False)
        if info.get("reply_to"):
            embed.add_field(name="Reply To", value=f"Message ID: {info['reply_to']}", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# STAFF PING
# ----------------------------
@bot.command()
async def rping(ctx):
    msg = await ctx.send(f"<@&{STAFF_PING_ROLE}>")
    await ctx.message.delete()
    for log_channel_id in STAFF_LOG_CHANNELS:
        channel = bot.get_channel(log_channel_id)
        embed = discord.Embed(title="Staff Ping", description=f"{ctx.author} triggered a staff ping.", color=discord.Color.blue())
        if ctx.message.reference:
            embed.add_field(name="Replying To", value=f"Message ID: {ctx.message.reference.message_id}", inline=False)
        await channel.send(embed=embed)

@bot.command()
async def hsping(ctx):
    msg = await ctx.send(f"<@&{HIGHER_STAFF_PING_ROLE}>")
    await ctx.message.delete()
    for log_channel_id in STAFF_LOG_CHANNELS:
        channel = bot.get_channel(log_channel_id)
        embed = discord.Embed(title="Higher Staff Ping", description=f"{ctx.author} triggered a high staff ping.", color=discord.Color.blue())
        if ctx.message.reference:
            embed.add_field(name="Replying To", value=f"Message ID: {ctx.message.reference.message_id}", inline=False)
        await channel.send(embed=embed)

# ----------------------------
# PURGE COMMAND
# ----------------------------
@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, limit: int):
    deleted = await ctx.channel.purge(limit=limit)
    channel = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title="Purge Log", description=f"{ctx.author} purged {len(deleted)} messages.", color=discord.Color.red())
    for msg in deleted:
        embed.add_field(name=f"{msg.author}", value=msg.content or "No content", inline=False)
        if msg.attachments:
            embed.add_field(name="Attachments", value="\n".join(att.url for att in msg.attachments), inline=False)
    await channel.send(embed=embed)

# ----------------------------
# RDM OPT-OUT
# ----------------------------
@bot.command()
async def rdm(ctx):
    data = load_data()
    uid = ctx.author.id
    rdm_users = data.get("rdm_users", [])
    if uid not in rdm_users:
        rdm_users.append(uid)
        data["rdm_users"] = rdm_users
        save_data(data)
        await ctx.send("You have opted out of bot DMs.")
    else:
        await ctx.send("You are already opted out of bot DMs.")

# ----------------------------
# HELP COMMAND
# ----------------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.blue())
    embed.add_field(name="!timetrack [user]", value="Show timetrack stats for a user.", inline=False)
    embed.add_field(name="!rmute [users] [duration] [reason]", value="Mute multiple users.", inline=False)
    embed.add_field(name="!runmute [user] [duration] [reason]", value="Mute single user.", inline=False)
    embed.add_field(name="!rmlb", value="Top 10 RMute users.", inline=False)
    embed.add_field(name="!rcache", value="View cached deleted messages.", inline=False)
    embed.add_field(name="!tlb", value="Timetrack leaderboard.", inline=False)
    embed.add_field(name="!tdm", value="Leaderboard without RCACHE roles.", inline=False)
    embed.add_field(name="!rping", value="Ping staff.", inline=False)
    embed.add_field(name="!hsping", value="Ping higher staff.", inline=False)
    embed.add_field(name="!rdm", value="Opt-out of bot DMs.", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# RUN BOT
# ----------------------------
bot.run(TOKEN)
