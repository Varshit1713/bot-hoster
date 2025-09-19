
# main.py
# Full-featured Discord moderation + timetrack bot
# Requirements: discord.py, pytz, Flask (optional keep-alive)
# Ensure DISCORD_TOKEN env var is set and bot has proper intents & permissions.

import os
import json
import asyncio
import datetime
import pytz
import io
import traceback
import re
from typing import Dict, Any, Optional, List

import discord
from discord.ext import commands, tasks
from flask import Flask
import threading

# ----------------------------
# CONFIG
# ----------------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Please set DISCORD_TOKEN environment variable")

PREFIX = "!"
INTENTS = discord.Intents.all()
INTENTS.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS, help_command=None)

# IDs copied from pastefy (preserved)
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE_ID = 1410423854563721287
TRACKING_CHANNEL_ID = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
STAFF_LOG_CHANNELS = [1410458084874260592]
DANGEROUS_LOG_USERS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

DATA_FILE = "bot_data.json"
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "users": {},
            "mutes": {},
            "rmute_usage": {},
            "cached_messages": {},
            "rdm_users": []
        }, f, indent=4)

file_lock = asyncio.Lock()

# ----------------------------
# UTILITIES
# ----------------------------
def utc_now() -> datetime.datetime:
    return datetime.datetime.now(pytz.utc)

def get_timestamp() -> int:
    return int(utc_now().timestamp())

def parse_duration(s: str) -> int:
    """
    Parse duration strings like '1h', '30m', '2d', '1d12h', '1h30m10s'
    Return seconds (int). If parsing fails, returns 0.
    """
    s = s.lower()
    pattern = r"(\d+)([smhd])"
    total = 0
    for amount, unit in re.findall(pattern, s):
        a = int(amount)
        if unit == "s":
            total += a
        elif unit == "m":
            total += a * 60
        elif unit == "h":
            total += a * 3600
        elif unit == "d":
            total += a * 86400
    return total

def human_duration(sec: int) -> str:
    sec = int(sec)
    if sec <= 0:
        return "0s"
    parts = []
    days, rem = divmod(sec, 86400)
    if days:
        parts.append(f"{days}d")
    hrs, rem = divmod(rem, 3600)
    if hrs:
        parts.append(f"{hrs}h")
    mins, s = divmod(rem, 60)
    if mins:
        parts.append(f"{mins}m")
    if s:
        parts.append(f"{s}s")
    return " ".join(parts)

def safe_load_data() -> Dict[str, Any]:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "mutes": {}, "rmute_usage": {}, "cached_messages": {}, "rdm_users": []}

async def safe_save_data(data: Dict[str, Any]):
    async with file_lock:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, DATA_FILE)

async def safe_send_channel(channel: Optional[discord.TextChannel], embed: discord.Embed = None, content: str = None, files: List[discord.File] = None):
    if channel is None:
        return False
    try:
        await channel.send(content=content, embed=embed, files=files)
        return True
    except Exception:
        return False

async def download_attachment_to_file(attachment: discord.Attachment) -> Optional[discord.File]:
    try:
        data = await attachment.read()
        bio = io.BytesIO(data)
        bio.seek(0)
        filename = attachment.filename or "file"
        return discord.File(bio, filename=filename)
    except Exception:
        return None

async def try_get_audit_deleter(guild: discord.Guild, time_window_seconds: int = 15) -> Optional[discord.User]:
    try:
        async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.message_delete):
            ts_age = (utc_now() - entry.created_at.replace(tzinfo=pytz.utc)).total_seconds()
            if ts_age <= time_window_seconds:
                return entry.user
    except Exception:
        pass
    return None

async def send_dm_safe(user: discord.User, embed: discord.Embed):
    data = safe_load_data()
    if str(user.id) in data.get("rdm_users", []):
        return
    try:
        await user.send(embed=embed)
    except Exception:
        pass

# ----------------------------
# FLASK KEEP-ALIVE (optional)
# ----------------------------
app = Flask("keepalive")
@app.route("/")
def home():
    return "Bot is running"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    try:
        app.run(host="0.0.0.0", port=port)
    except Exception:
        pass

threading.Thread(target=run_flask, daemon=True).start()

# ----------------------------
# TIMETRACK LOOP
# ----------------------------
@tasks.loop(seconds=60.0)
async def timetrack_loop():
    data = safe_load_data()
    now_ts = get_timestamp()
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            try:
                if not any(role.id in RCACHE_ROLES for role in member.roles):
                    continue
            except Exception:
                continue
            uid = str(member.id)
            udata = data.get("users", {}).get(uid)
            if not udata:
                udata = {
                    "first_seen": now_ts,
                    "last_online": now_ts,
                    "last_message": None,
                    "last_edit": None,
                    "total_online_seconds": 0,
                    "online_start": None
                }
            if member.status != discord.Status.offline:
                if not udata.get("online_start"):
                    udata["online_start"] = now_ts
            else:
                if udata.get("online_start"):
                    session = now_ts - int(udata["online_start"])
                    udata["total_online_seconds"] = udata.get("total_online_seconds", 0) + session
                    udata["online_start"] = None
            udata["last_online"] = now_ts
            data.setdefault("users", {})[uid] = udata
    await safe_save_data(data)

# ----------------------------
# AUTO-UNMUTE LOOP
# ----------------------------
@tasks.loop(seconds=10.0)
async def auto_unmute_loop():
    data = safe_load_data()
    now_ts = get_timestamp()
    changed = False
    for uid, mdata in list(data.get("mutes", {}).items()):
        try:
            if now_ts >= mdata.get("unmute_time", 0):
                member_obj = None
                for g in bot.guilds:
                    m = g.get_member(int(uid))
                    if m:
                        member_obj = m
                        break
                if member_obj:
                    role = member_obj.guild.get_role(MUTE_ROLE_ID)
                    if role and role in member_obj.roles:
                        try:
                            await member_obj.remove_roles(role, reason="Auto-unmute")
                        except Exception:
                            pass
                    ch = bot.get_channel(TRACKING_CHANNEL_ID)
                    if ch:
                        embed = discord.Embed(title="Auto Unmute", description=f"{member_obj} was automatically unmuted.", color=discord.Color.green(), timestamp=utc_now())
                        embed.add_field(name="User ID", value=str(member_obj.id), inline=True)
                        await safe_send_channel(ch, embed=embed)
                data.get("mutes", {}).pop(uid, None)
                changed = True
        except Exception:
            continue
    if changed:
        await safe_save_data(data)

# ----------------------------
# MESSAGE EVENTS
# ----------------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    try:
        if any(role.id in RCACHE_ROLES for role in message.author.roles):
            data = safe_load_data()
            uid = str(message.author.id)
            udata = data.get("users", {}).get(uid, {})
            if not udata:
                udata = {
                    "first_seen": get_timestamp(),
                    "last_online": get_timestamp(),
                    "last_message": None,
                    "last_edit": None,
                    "total_online_seconds": 0,
                    "online_start": None
                }
            udata["last_message"] = {
                "content": message.content,
                "timestamp": get_timestamp(),
                "channel_id": message.channel.id,
                "message_id": message.id
            }
            data.setdefault("users", {})[uid] = udata
            await safe_save_data(data)
    except Exception:
        pass
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author.bot:
        return
    try:
        if any(role.id in RCACHE_ROLES for role in after.author.roles):
            data = safe_load_data()
            uid = str(after.author.id)
            udata = data.get("users", {}).get(uid, {})
            udata["last_edit"] = {
                "before": before.content,
                "after": after.content,
                "timestamp": get_timestamp(),
                "channel_id": after.channel.id,
                "message_id": after.id
            }
            data.setdefault("users", {})[uid] = udata
            await safe_save_data(data)
    except Exception:
        pass

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author and message.author.bot:
        return
    try:
        data = safe_load_data()
        cache = {
            "message_id": message.id,
            "author_id": message.author.id if message.author else None,
            "author_name": str(message.author) if message.author else "Unknown",
            "content": message.content,
            "attachments": [],
            "channel_id": message.channel.id if message.channel else None,
            "timestamp": get_timestamp()
        }
        files_to_upload = []
        for att in message.attachments:
            cache["attachments"].append(att.url)
            try:
                dfile = await download_attachment_to_file(att)
                if dfile:
                    files_to_upload.append(dfile)
            except Exception:
                pass
        if message.reference:
            ref = message.reference.resolved
            if ref and isinstance(ref, discord.Message):
                cache["reply_to"] = {"author_id": ref.author.id, "author_name": str(ref.author), "message_id": ref.id, "content": ref.content}
        try:
            deleter = await try_get_audit_deleter(message.guild, time_window_seconds=15)
            if deleter:
                cache["deleted_by"] = {"id": getattr(deleter,"id",None), "name": str(deleter)}
        except Exception:
            pass
        data.setdefault("cached_messages", {})[str(message.id)] = cache
        await safe_save_data(data)
        ch = bot.get_channel(TRACKING_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="Message Deleted", color=discord.Color.red(), timestamp=utc_now())
            if message.author:
                embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
            if message.channel:
                embed.add_field(name="Channel", value=f"{message.channel} ({message.channel.id})", inline=False)
            embed.add_field(name="Content", value=message.content or "No content", inline=False)
            if cache.get("reply_to"):
                rt = cache["reply_to"]
                embed.add_field(name="Reply To", value=f"{rt.get('author_name')} (msg {rt.get('message_id')}) - {rt.get('content')}", inline=False)
            if cache.get("deleted_by"):
                embed.add_field(name="Deleted By (audit)", value=f"{cache['deleted_by'].get('name')} ({cache['deleted_by'].get('id')})", inline=False)
            await safe_send_channel(ch, embed=embed)
            for f in files_to_upload:
                try:
                    await ch.send(file=f)
                except Exception:
                    pass
    except Exception:
        traceback.print_exc()

# ----------------------------
# COMMANDS
# ----------------------------
@bot.command()
@commands.has_permissions(manage_guild=True)
async def rcache(ctx, count: int = 10):
    if not any(role.id in RCACHE_ROLES for role in ctx.author.roles):
        await ctx.reply("You don't have the RCACHE role permission.", mention_author=False)
        return
    data = safe_load_data()
    cached = data.get("cached_messages", {})
    if not cached:
        await ctx.send("No cached messages.")
        return
    items = list(cached.items())[-count:]
    embed = discord.Embed(title=f"Last {len(items)} cached deletions", color=discord.Color.orange(), timestamp=utc_now())
    for mid, info in items:
        author = info.get("author_name", "Unknown")
        content = info.get("content", "No content")
        short = content if len(content) < 400 else content[:397] + "..."
        desc = f"{short}\\nChannel: <#{info.get('channel_id')}>"
        if info.get("attachments"):
            desc += f"\\nAttachments: {', '.join(info.get('attachments'))}"
        if info.get("reply_to"):
            rt = info.get("reply_to")
            desc += f"\\nReply to: {rt.get('author_name')} (msg {rt.get('message_id')})"
        if info.get("deleted_by"):
            desc += f"\\nDeleted by: {info['deleted_by'].get('name')} ({info['deleted_by'].get('id')})"
        embed.add_field(name=f"{author} (id {info.get('author_id')})", value=desc, inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_roles=True)
async def rmute(ctx, members: commands.Greedy[discord.Member], duration: str, *, reason: str = "No reason provided"):
    if not members:
        await ctx.send("No valid members provided.")
        return
    dur_seconds = parse_duration(duration)
    if dur_seconds <= 0:
        await ctx.send("Invalid duration. Use formats like '1h', '30m', '2d' or combinations like '1d12h'.")
        return
    data = safe_load_data()
    now_ts = get_timestamp()
    mute_role = ctx.guild.get_role(MUTE_ROLE_ID)
    if not mute_role:
        await ctx.send("Mute role not found on this guild.")
        return
    for m in members:
        try:
            await m.add_roles(mute_role, reason=f"RMute by {ctx.author}: {reason}")
            unmute_time = now_ts + dur_seconds
            data.setdefault("mutes", {})[str(m.id)] = {
                "moderator": ctx.author.id,
                "start_time": now_ts,
                "duration": dur_seconds,
                "reason": reason,
                "unmute_time": unmute_time
            }
            data.setdefault("rmute_usage", {})
            data["rmute_usage"][str(ctx.author.id)] = data.get("rmute_usage", {}).get(str(ctx.author.id), 0) + 1
            embed = discord.Embed(title="You have been muted", color=discord.Color.red(), timestamp=utc_now())
            embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
            embed.add_field(name="Duration", value=human_duration(dur_seconds), inline=True)
            embed.add_field(name="Reason", value=reason, inline=False)
            await send_dm_safe(m, embed)
            ch = bot.get_channel(TRACKING_CHANNEL_ID)
            if ch:
                log = discord.Embed(title="Mute Applied", color=discord.Color.red(), timestamp=utc_now())
                log.add_field(name="User", value=f"{m} ({m.id})", inline=False)
                log.add_field(name="Moderator", value=f"{ctx.author} ({ctx.author.id})", inline=False)
                log.add_field(name="Duration", value=human_duration(dur_seconds), inline=True)
                log.add_field(name="Reason", value=reason, inline=False)
                await safe_send_channel(ch, embed=log)
        except Exception:
            traceback.print_exc()
    await safe_save_data(data)
    try:
        await ctx.message.delete()
    except Exception:
        pass

@bot.command()
@commands.has_permissions(manage_roles=True)
async def runmute(ctx, member: discord.Member):
    mute_role = ctx.guild.get_role(MUTE_ROLE_ID)
    if not mute_role:
        await ctx.send("Mute role not found.")
        return
    try:
        if mute_role in member.roles:
            await member.remove_roles(mute_role, reason=f"Runmute by {ctx.author}")
        data = safe_load_data()
        data.get("mutes", {}).pop(str(member.id), None)
        await safe_save_data(data)
        ch = bot.get_channel(TRACKING_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="Manual Unmute", description=f"{member} was unmuted by {ctx.author}.", color=discord.Color.green(), timestamp=utc_now())
            embed.add_field(name="User ID", value=str(member.id), inline=True)
            embed.add_field(name="Moderator", value=f"{ctx.author} ({ctx.author.id})", inline=True)
            await safe_send_channel(ch, embed=embed)
        try:
            await ctx.message.delete()
        except Exception:
            pass
    except Exception:
        traceback.print_exc()

@bot.command()
async def rmlb(ctx, top: int = 10):
    data = safe_load_data()
    usage = data.get("rmute_usage", {})
    items = sorted(usage.items(), key=lambda x: x[1], reverse=True)[:top]
    embed = discord.Embed(title="RMute Usage Leaderboard", color=discord.Color.blue(), timestamp=utc_now())
    for i, (modid, count) in enumerate(items, start=1):
        member = ctx.guild.get_member(int(modid))
        name = str(member) if member else f"<@{modid}>"
        embed.add_field(name=f"{i}. {name}", value=f"RMute actions: {count}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def timetrack(ctx, member: discord.Member = None):
    data = safe_load_data()
    now_ts = get_timestamp()
    member = member or ctx.author
    uid = str(member.id)
    udata = data.get("users", {}).get(uid)
    if not udata:
        await ctx.send(f"No timetrack data for {member}.")
        return
    total = udata.get("total_online_seconds", 0)
    if udata.get("online_start"):
        total += now_ts - int(udata.get("online_start"))
    first = int(udata.get("first_seen", now_ts))
    days = max(1, (now_ts - first) / 86400)
    avg_daily = int(total / days)
    embed = discord.Embed(title=f"Timetrack for {member}", color=discord.Color.green(), timestamp=utc_now())
    embed.add_field(name="Total Online", value=human_duration(total), inline=True)
    embed.add_field(name="Average / day", value=human_duration(avg_daily), inline=True)
    last_msg = udata.get("last_message")
    if last_msg:
        embed.add_field(name="Last Message", value=f"{(last_msg.get('content') or '')[:200]} (in <#{last_msg.get('channel_id')}>)", inline=False)
    last_edit = udata.get("last_edit")
    if last_edit:
        embed.add_field(name="Last Edit", value=f"Before: {(last_edit.get('before') or '')[:200]} | After: {(last_edit.get('after') or '')[:200]}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def tlb(ctx, top: int = 10):
    data = safe_load_data()
    now_ts = get_timestamp()
    rows = []
    for uid, udata in data.get("users", {}).items():
        try:
            member = ctx.guild.get_member(int(uid))
            if not member:
                continue
            if not any(r.id in RCACHE_ROLES for r in member.roles):
                continue
            total = udata.get("total_online_seconds", 0)
            if udata.get("online_start"):
                total += now_ts - int(udata.get("online_start"))
            first = int(udata.get("first_seen", now_ts))
            days = max(1, (now_ts - first) / 86400)
            avg_daily = total / days
            rows.append((member, avg_daily, total))
        except Exception:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (RCACHE Roles)", color=discord.Color.green(), timestamp=utc_now())
    for i, (member, avg, total) in enumerate(rows[:top], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Avg/day: {human_duration(int(avg))} â€” Total: {human_duration(int(total))}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def tdm(ctx, top: int = 10):
    data = safe_load_data()
    now_ts = get_timestamp()
    rows = []
    for uid, udata in data.get("users", {}).items():
        try:
            member = ctx.guild.get_member(int(uid))
            if not member:
                continue
            if any(r.id in RCACHE_ROLES for r in member.roles):
                continue
            total = udata.get("total_online_seconds", 0)
            if udata.get("online_start"):
                total += now_ts - int(udata.get("online_start"))
            first = int(udata.get("first_seen", now_ts))
            days = max(1, (now_ts - first) / 86400)
            avg_daily = total / days
            rows.append((member, avg_daily, total))
        except Exception:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (No RCACHE Roles)", color=discord.Color.green(), timestamp=utc_now())
    for i, (member, avg, total) in enumerate(rows[:top], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Avg/day: {human_duration(int(avg))} â€” Total: {human_duration(int(total))}", inline=False)
    await ctx.send(embed=embed)

async def ping_members_by_role_and_log(ctx: commands.Context, role_id: int, title: str):
    members = [m for m in ctx.guild.members if any(r.id == role_id for r in m.roles)]
    if not members:
        await ctx.send("No members with that role found.", delete_after=8)
        return
    chunk = ""
    for m in members:
        mention = m.mention + " "
        if len(chunk) + len(mention) > 1900:
            try:
                await ctx.send(chunk)
            except Exception:
                pass
            chunk = mention
        else:
            chunk += mention
    if chunk:
        try:
            await ctx.send(chunk)
        except Exception:
            pass
    try:
        await ctx.message.delete()
    except Exception:
        pass
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title=title, description=f"{ctx.author} triggered {title}", color=discord.Color.blue(), timestamp=utc_now())
        if ctx.message.reference:
            try:
                ref_msg = ctx.message.reference.resolved
                if isinstance(ref_msg, discord.Message):
                    embed.add_field(name="Replying To", value=f"{ref_msg.author}: {ref_msg.content[:500]}", inline=False)
            except Exception:
                pass
        await safe_send_channel(ch, embed=embed)

@bot.command()
@commands.has_permissions(send_messages=True)
async def rping(ctx):
    await ping_members_by_role_and_log(ctx, STAFF_PING_ROLE, "Staff Ping")

@bot.command()
@commands.has_permissions(send_messages=True)
async def hsping(ctx):
    await ping_members_by_role_and_log(ctx, HIGHER_STAFF_PING_ROLE, "Higher Staff Ping")

@bot.command()
async def rdm(ctx):
    data = safe_load_data()
    lst = data.get("rdm_users", [])
    uid = str(ctx.author.id)
    if uid in lst:
        lst.remove(uid)
        data["rdm_users"] = lst
        await safe_save_data(data)
        await ctx.send("You will now receive DMs from the bot.")
    else:
        lst.append(uid)
        data["rdm_users"] = lst
        await safe_save_data(data)
        await ctx.send("You have opted out of DMs from the bot.")

@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.blue(), timestamp=utc_now())
    embed.add_field(name="!timetrack [user]", value="Show timetrack stats for a user (if tracked).", inline=False)
    embed.add_field(name="!tlb [top]", value="Timetrack leaderboard (RCACHE roles)", inline=False)
    embed.add_field(name="!tdm [top]", value="Timetrack leaderboard (no RCACHE roles)", inline=False)
    embed.add_field(name="!rmute [users] [duration] [reason]", value="Mute one or more users (duration like 1h, 30m, 2d).", inline=False)
    embed.add_field(name="!runmute [user]", value="Unmute single user (remove mute role).", inline=False)
    embed.add_field(name="!rmlb", value="Top moderators who used rmute the most", inline=False)
    embed.add_field(name="!rcache [count]", value="View cached deleted messages (RCACHE roles only).", inline=False)
    embed.add_field(name="!rping", value="Ping staff by member (sends mentions to matched members).", inline=False)
    embed.add_field(name="!hsping", value="Ping higher staff by member.", inline=False)
    embed.add_field(name="!rdm", value="Toggle opt-out from bot DMs", inline=False)
    await ctx.send(embed=embed)

@bot.event
async def on_guild_channel_create(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Channel Created", description=f"{channel.name}", color=discord.Color.green(), timestamp=utc_now())
        embed.add_field(name="Channel ID", value=str(channel.id), inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_channel_delete(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Channel Deleted", description=f"{channel.name}", color=discord.Color.red(), timestamp=utc_now())
        embed.add_field(name="Channel ID", value=str(channel.id), inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_channel_update(before, after):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Channel Updated", color=discord.Color.orange(), timestamp=utc_now())
        embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=False)
        embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_role_create(role):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Role Created", color=discord.Color.green(), timestamp=utc_now())
        embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_role_delete(role):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Role Deleted", color=discord.Color.red(), timestamp=utc_now())
        embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_guild_role_update(before, after):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Role Updated", color=discord.Color.orange(), timestamp=utc_now())
        embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=False)
        embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_webhook_update(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Webhook Updated", color=discord.Color.purple(), timestamp=utc_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        await safe_send_channel(ch, embed=embed)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    try:
        timetrack_loop.start()
    except RuntimeError:
        pass
    try:
        auto_unmute_loop.start()
    except RuntimeError:
        pass

if __name__ == "__main__":
    bot.run(TOKEN)
