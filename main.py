# ----------------------------
# IMPORTS & CONFIGURATION
# ----------------------------
import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import pytz
import json
import os

TOKEN = os.environ.get("DISCORD_TOKEN")
INTENTS = discord.Intents.all()

bot = commands.Bot(command_prefix="!", intents=INTENTS)

# Role & Channel IDs from your pastefy/base script
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE_ID = 1410423854563721287
TRACKING_CHANNEL_ID = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
STAFF_LOG_CHANNELS = [1403422664521023648, 1410458084874260592]

DATA_FILE = "bot_data.json"

# ----------------------------
# DATA HANDLING
# ----------------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "mutes": {}, "rmute_usage": {}, "cached_messages": {}, "rdm_users": []}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# ----------------------------
# HELPER FUNCTIONS
# ----------------------------
def format_duration(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours}h {minutes}m {sec}s"

async def send_dm(member, embed):
    data = load_data()
    if member.id in data.get("rdm_users", []):
        return
    try:
        await member.send(embed=embed)
    except:
        pass

def get_timestamp():
    return int(datetime.datetime.now(pytz.utc).timestamp())
    # ----------------------------
# TIMETRACK SYSTEM
# ----------------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    data = load_data()
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            if not any(role.id in RCACHE_ROLES for role in member.roles):
                continue
            uid = str(member.id)
            user_data = data["users"].get(uid, {})
            now_ts = get_timestamp()
            # Online tracking
            if member.status != discord.Status.offline:
                if "online_start" not in user_data:
                    user_data["online_start"] = now_ts
            else:
                if "online_start" in user_data:
                    session_time = now_ts - user_data["online_start"]
                    user_data["total_online_seconds"] = user_data.get("total_online_seconds", 0) + session_time
                    user_data.pop("online_start", None)
            # Last activity
            user_data["last_online"] = now_ts
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
        unmute_time = get_timestamp() + duration
        data["mutes"][str(member.id)] = {
            "moderator": ctx.author.id,
            "duration": duration,
            "reason": reason,
            "unmute_time": unmute_time
        }
        data.setdefault("rmute_usage", {})
        data["rmute_usage"][str(ctx.author.id)] = data["rmute_usage"].get(str(ctx.author.id), 0) + 1
        # DM Embed
        embed = discord.Embed(title="You were muted", color=discord.Color.red())
        embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
        embed.add_field(name="Duration", value=f"{duration} seconds", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        await send_dm(member, embed)
    save_data(data)
    # Tracking Embed
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

# ----------------------------
# AUTO UNMUTE LOOP
# ----------------------------
@tasks.loop(seconds=10)
async def auto_unmute_loop():
    data = load_data()
    now_ts = get_timestamp()
    updated = False
    for uid, mute_data in list(data.get("mutes", {}).items()):
        if now_ts >= mute_data["unmute_time"]:
            guild = bot.guilds[0]
            member = guild.get_member(int(uid))
            if member:
                role = guild.get_role(MUTE_ROLE_ID)
                await member.remove_roles(role, reason="Auto-unmute")
            tracking_channel = bot.get_channel(TRACKING_CHANNEL_ID)
            embed = discord.Embed(
                title="Auto Unmute",
                description=f"{member} has been unmuted automatically.",
                color=discord.Color.green()
            )
            await tracking_channel.send(embed=embed)
            data["mutes"].pop(uid)
            updated = True
    if updated:
        save_data(data)

auto_unmute_loop.start()

# ----------------------------
# RMUTE LEADERBOARD
# ----------------------------
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
    for uid, udata in data.get("users", {}).items():
        member = ctx.guild.get_member(int(uid))
        if member and any(role.id in RCACHE_ROLES for role in member.roles):
            total = udata.get("total_online_seconds", 0)
            days_online = max(1, (get_timestamp() - udata.get("online_start", get_timestamp())) / 86400)
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
    for uid, udata in data.get("users", {}).items():
        member = ctx.guild.get_member(int(uid))
        if member and all(role.id not in RCACHE_ROLES for role in member.roles):
            total = udata.get("total_online_seconds", 0)
            days_online = max(1, (get_timestamp() - udata.get("online_start", get_timestamp())) / 86400)
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
        await ctx.send("You do not have permission to access the cache.")
        return
    data = load_data()
    cached = data.get("cached_messages", {})
    if not cached:
        await ctx.send("No cached messages.")
        return
    embed = discord.Embed(title="Cached Messages", color=discord.Color.orange())
    for mid, msgdata in list(cached.items())[-10:]:
        author = bot.get_user(msgdata["author_id"])
        content = msgdata.get("content", "No content")
        embed.add_field(name=f"{author}", value=content, inline=False)
        if "attachments" in msgdata:
            embed.add_field(name="Attachments", value="\n".join(msgdata["attachments"]), inline=False)
        if "reply_to" in msgdata:
            reply_author = bot.get_user(msgdata["reply_to"]["author_id"])
            reply_content = msgdata["reply_to"].get("content", "No content")
            embed.add_field(name=f"Reply to {reply_author}", value=reply_content, inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_message_delete(message):
    data = load_data()
    cached = {
        "author_id": message.author.id,
        "content": message.content,
        "attachments": [a.url for a in message.attachments]
    }
    if message.reference:
        ref = message.reference.resolved
        if ref:
            cached["reply_to"] = {
                "author_id": ref.author.id,
                "content": ref.content
            }
    data.setdefault("cached_messages", {})[str(message.id)] = cached
    save_data(data)

# ----------------------------
# PURGE LOGGING
# ----------------------------
@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, limit: int):
    deleted = await ctx.channel.purge(limit=limit)
    channel = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title=f"{len(deleted)} messages purged in #{ctx.channel.name}", color=discord.Color.red())
    for msg in deleted:
        author = msg.author
        content = msg.content if msg.content else "No text"
        embed.add_field(name=f"{author}", value=content, inline=False)
        if msg.attachments:
            embed.add_field(name="Attachments", value="\n".join(a.url for a in msg.attachments), inline=False)
        if msg.reference:
            ref = msg.reference.resolved
            if ref:
                embed.add_field(name=f"Reply to {ref.author}", value=ref.content, inline=False)
    await channel.send(embed=embed)

# ----------------------------
# STAFF PING
# ----------------------------
async def ping_members_by_role(ctx, role_id):
    members = [m for m in ctx.guild.members if any(r.id == role_id for r in m.roles)]
    mentions = " ".join(m.mention for m in members)
    for ch_id in STAFF_LOG_CHANNELS:
        ch = bot.get_channel(ch_id)
        await ch.send(mentions)
    await ctx.message.delete()

@bot.command()
async def rping(ctx):
    await ping_members_by_role(ctx, STAFF_PING_ROLE)

@bot.command()
async def hsping(ctx):
    await ping_members_by_role(ctx, HIGHER_STAFF_PING_ROLE)

# ----------------------------
# DM & NOTIFICATION CONTROL
# ----------------------------
# Already implemented in previous chunk with !rdm

# ----------------------------
# HELP COMMAND
# ----------------------------
# Already implemented in previous chunk with !rhelp

# ----------------------------
# EVENT LOGGING
# ----------------------------
@bot.event
async def on_guild_channel_create(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title="Channel Created", description=f"{channel.name}", color=discord.Color.green())
    embed.add_field(name="Channel ID", value=channel.id)
    await ch.send(embed=embed)

@bot.event
async def on_guild_channel_delete(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title="Channel Deleted", description=f"{channel.name}", color=discord.Color.red())
    embed.add_field(name="Channel ID", value=channel.id)
    await ch.send(embed=embed)

@bot.event
async def on_guild_role_create(role):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title="Role Created", description=f"{role.name}", color=discord.Color.green())
    embed.add_field(name="Role ID", value=role.id)
    await ch.send(embed=embed)

@bot.event
async def on_guild_role_delete(role):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title="Role Deleted", description=f"{role.name}", color=discord.Color.red())
    embed.add_field(name="Role ID", value=role.id)
    await ch.send(embed=embed)

@bot.event
async def on_guild_role_update(before, after):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title="Role Updated", description=f"{before.name} → {after.name}", color=discord.Color.orange())
    await ch.send(embed=embed)

@bot.event
async def on_webhook_update(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    embed = discord.Embed(title="Webhook Updated", description=f"Channel: {channel.name}", color=discord.Color.orange())
    await ch.send(embed=embed)
    # ----------------------------
# BOT STARTUP & FINALIZATION
# ----------------------------
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    timetrack_loop.start()
    auto_unmute_loop.start()

# ----------------------------
# MESSAGE EVENTS
# ----------------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    # Track last message for timetrack
    data = load_data()
    uid = str(message.author.id)
    user_data = data["users"].get(uid, {})
    user_data["last_message"] = message.content
    user_data["last_edit"] = get_timestamp()
    data["users"][uid] = user_data
    save_data(data)
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before, after):
    if after.author.bot:
        return
    data = load_data()
    uid = str(after.author.id)
    user_data = data["users"].get(uid, {})
    user_data["last_edit"] = get_timestamp()
    data["users"][uid] = user_data
    save_data(data)

# ----------------------------
# RDM COMMAND
# ----------------------------
@bot.command()
async def rdm(ctx):
    data = load_data()
    uid = ctx.author.id
    if uid not in data.get("rdm_users", []):
        data.setdefault("rdm_users", []).append(uid)
        await ctx.send("You have opted out of bot DMs.")
    else:
        data["rdm_users"].remove(uid)
        await ctx.send("You have opted back into bot DMs.")
    save_data(data)

# ----------------------------
# HELP COMMAND
# ----------------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.blue())
    embed.add_field(name="!timetrack [user]", value="Shows timetrack info", inline=False)
    embed.add_field(name="!rmute [users] [duration] [reason]", value="Mute multiple users", inline=False)
    embed.add_field(name="!runmute [user] [duration] [reason]", value="Mute a single user", inline=False)
    embed.add_field(name="!rmlb", value="Top 10 users who used rmute most", inline=False)
    embed.add_field(name="!rcache", value="Shows deleted messages", inline=False)
    embed.add_field(name="!tlb", value="Timetrack leaderboard (RCACHE roles)", inline=False)
    embed.add_field(name="!tdm", value="Timetrack leaderboard (non-RCACHE roles)", inline=False)
    embed.add_field(name="!rping", value="Ping staff members individually", inline=False)
    embed.add_field(name="!hsping", value="Ping higher staff members individually", inline=False)
    embed.add_field(name="!rdm", value="Opt-out/in of bot DMs", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# RUN BOT
# ----------------------------
bot.run(TOKEN)
