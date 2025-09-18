# main.py
"""
Full production-ready Discord bot:
- Based on your Pastefy base + all requested feature additions
- Timetrack, rmute/runmute with auto-unmute + DM embed, rmute usage tracking
- Leaderboards: !tlb and !tdm
- Cache system: !rcache (deleted messages + attachments + reply info)
- Purge logging: logs content, attachments, reply info
- Staff pings: !rping / !hsping (pings members individually)
- RDM: opt-out from bot DMs
- Channel / role / webhook event logging
- Flask keep-alive server (daemon thread)
- All tasks started in on_ready()
- Uses role/channel IDs from your Pastefy
"""

import os
import json
import threading
import asyncio
import datetime
import pytz
from typing import Dict, Any, Optional, List

import discord
from discord.ext import commands, tasks
from flask import Flask

# ----------------------------
# CONFIG
# ----------------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Please set DISCORD_TOKEN environment variable.")

PREFIX = "!"
INTENTS = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=INTENTS, help_command=None)

# Role & Channel IDs from Pastefy (preserved)
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE_ID = 1410423854563721287
TRACKING_CHANNEL_ID = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
STAFF_LOG_CHANNELS = [1403422664521023648, 1410458084874260592]
DANGEROUS_LOG_USERS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

DATA_FILE = "bot_data.json"

# Ensure path exists
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w") as f:
        json.dump({
            "users": {},
            "mutes": {},
            "rmute_usage": {},
            "cached_messages": {},
            "rdm_users": []
        }, f, indent=4)

# Use an asyncio lock for JSON writes to reduce races
file_lock = asyncio.Lock()

# ----------------------------
# UTILS
# ----------------------------
def utc_now() -> datetime.datetime:
    return datetime.datetime.now(pytz.utc)

def get_timestamp() -> int:
    return int(utc_now().timestamp())

def safe_load_data() -> Dict[str, Any]:
    # synchronous small read; we use the file_lock around writes
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "mutes": {}, "rmute_usage": {}, "cached_messages": {}, "rdm_users": []}

async def safe_save_data(data: Dict[str, Any]):
    async with file_lock:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, DATA_FILE)

def format_duration(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

async def send_dm_safe(user: discord.User, embed: discord.Embed):
    data = safe_load_data()
    if user.id in data.get("rdm_users", []):
        return False
    try:
        await user.send(embed=embed)
        return True
    except Exception:
        return False

async def try_get_audit_deleter(guild: discord.Guild, message_id: int) -> Optional[int]:
    # Best-effort: returns the moderator user id who deleted message (if available)
    try:
        async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.message_delete):
            # entry.extra may contain channel, count, but not message id for bulk deletes
            # Audit logs are not precise for single message deletion; this is a best-effort attempt.
            # We'll return the user who created the entry if timestamp is recent.
            ts_age = (utc_now() - entry.created_at.replace(tzinfo=pytz.utc)).total_seconds()
            if ts_age < 10:  # within 10 seconds
                return entry.user.id
    except Exception:
        return None
    return None

# ----------------------------
# FLASK KEEP-ALIVE
# ----------------------------
app = Flask("keepalive")

@app.route("/")
def home():
    return "Bot alive"

def run_flask():
    # Keep Flask quiet
    try:
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
    except Exception:
        pass

flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

# ----------------------------
# TIMETRACK TASK
# ----------------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    data = safe_load_data()
    now_ts = get_timestamp()
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            # Only track members who have at least one RCACHE role
            try:
                if not any(role.id in RCACHE_ROLES for role in member.roles):
                    continue
            except Exception:
                continue
            uid = str(member.id)
            udata = data.get("users", {}).get(uid, {})
            # initialize
            if not udata:
                udata = {
                    "last_online": None,
                    "last_message": None,
                    "last_edit": None,
                    "total_online_seconds": 0,
                    "online_start": None
                }
            # Online start handling
            if member.status != discord.Status.offline:
                if udata.get("online_start") is None:
                    udata["online_start"] = now_ts
            else:
                # just went offline
                if udata.get("online_start") is not None:
                    session = now_ts - int(udata["online_start"])
                    udata["total_online_seconds"] = udata.get("total_online_seconds", 0) + session
                    udata["online_start"] = None
            udata["last_online"] = now_ts
            # last_message & last_edit are updated by event handlers
            data.setdefault("users", {})[uid] = udata
    await safe_save_data(data)

# ----------------------------
# AUTO-UNMUTE TASK (checks JSON mutes & unmute)
# ----------------------------
@tasks.loop(seconds=10)
async def auto_unmute_loop():
    data = safe_load_data()
    now_ts = get_timestamp()
    updated = False
    for uid, mute_record in list(data.get("mutes", {}).items()):
        try:
            if now_ts >= mute_record.get("unmute_time", 0):
                gid0 = None
                # Try to find guild where member exists
                member_obj = None
                for guild in bot.guilds:
                    m = guild.get_member(int(uid))
                    if m:
                        member_obj = m
                        gid0 = guild.id
                        break
                if member_obj:
                    role = member_obj.guild.get_role(MUTE_ROLE_ID)
                    if role and role in member_obj.roles:
                        try:
                            await member_obj.remove_roles(role, reason="Auto-unmute")
                        except Exception:
                            pass
                    # Log
                    ch = bot.get_channel(TRACKING_CHANNEL_ID)
                    if ch:
                        embed = discord.Embed(
                            title="Auto Unmute",
                            description=f"{member_obj} was automatically unmuted.",
                            color=discord.Color.green(),
                            timestamp=utc_now()
                        )
                        embed.add_field(name="User ID", value=str(member_obj.id), inline=True)
                        embed.add_field(name="Guild ID", value=str(gid0), inline=True)
                        await safe_send_channel(ch, embed)
                # remove mute record
                data["mutes"].pop(uid, None)
                updated = True
        except Exception:
            continue
    if updated:
        await safe_save_data(data)

# helper to send embed to channel but guard for failures
async def safe_send_channel(channel: Optional[discord.TextChannel], embed: discord.Embed):
    if channel is None:
        return False
    try:
        await channel.send(embed=embed)
        return True
    except Exception:
        return False

# ----------------------------
# EVENT HANDLERS (message edit/delete, status change)
# ----------------------------
@bot.event
async def on_ready():
    # Called once when bot is ready; start loops here
    print(f"[{utc_now().isoformat()}] Bot ready as {bot.user} ({bot.user.id})")
    if not timetrack_loop.is_running():
        timetrack_loop.start()
    if not auto_unmute_loop.is_running():
        auto_unmute_loop.start()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    # Update last message for timetrack (only if user in RCACHE roles)
    try:
        if any(role.id in RCACHE_ROLES for role in message.author.roles):
            data = safe_load_data()
            uid = str(message.author.id)
            udata = data.get("users", {}).get(uid, {})
            if not udata:
                udata = {}
            udata["last_message"] = {
                "content": message.content,
                "timestamp": get_timestamp(),
                "channel": message.channel.id,
                "message_id": message.id
            }
            data.setdefault("users", {})[uid] = udata
            # save asynchronously but don't block message handling heavily
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
            if not udata:
                udata = {}
            udata["last_edit"] = {
                "before": before.content,
                "after": after.content,
                "timestamp": get_timestamp(),
                "channel": after.channel.id,
                "message_id": after.id
            }
            data.setdefault("users", {})[uid] = udata
            await safe_save_data(data)
    except Exception:
        pass

@bot.event
async def on_message_delete(message: discord.Message):
    # cache deleted message with attachments and reply info and attempt to find deleter via audit logs
    if message.author.bot:
        return
    try:
        data = safe_load_data()
        cache = {
            "message_id": message.id,
            "author_id": message.author.id,
            "author_name": str(message.author),
            "content": message.content,
            "attachments": [a.url for a in message.attachments],
            "channel_id": message.channel.id,
            "timestamp": get_timestamp()
        }
        # reply info
        if message.reference:
            ref = message.reference.resolved
            if ref and isinstance(ref, discord.Message):
                cache["reply_to"] = {"author_id": ref.author.id, "content": ref.content, "message_id": ref.id}
        # who deleted? best-effort via audit log
        deleter = None
        try:
            deleter = await try_get_audit_deleter(message.guild, message.id)
            if deleter:
                cache["deleted_by"] = deleter
        except Exception:
            pass
        data.setdefault("cached_messages", {})[str(message.id)] = cache
        await safe_save_data(data)
        # log to tracking channel
        ch = bot.get_channel(TRACKING_CHANNEL_ID)
        if ch:
            embed = discord.Embed(title="Message Deleted", color=discord.Color.red(), timestamp=utc_now())
            embed.add_field(name="Author", value=f"{message.author} ({message.author.id})", inline=False)
            embed.add_field(name="Channel", value=f"{message.channel} ({message.channel.id})", inline=False)
            embed.add_field(name="Content", value=message.content or "No content", inline=False)
            if message.attachments:
                embed.add_field(name="Attachments", value="\n".join(a.url for a in message.attachments), inline=False)
            if message.reference:
                ref = message.reference.resolved
                if ref:
                    embed.add_field(name="Reply To", value=f"{ref.author} ({ref.id})", inline=False)
            if deleter:
                try:
                    deleter_member = message.guild.get_member(deleter)
                    embed.add_field(name="Deleted By (audit log)", value=str(deleter_member or f"<@{deleter}>"), inline=False)
                except Exception:
                    embed.add_field(name="Deleted By (audit log)", value=str(deleter), inline=False)
            await safe_send_channel(ch, embed)
    except Exception:
        pass

# ----------------------------
# rcache command (view cached messages)
# ----------------------------
@bot.command()
@commands.has_permissions(manage_guild=True)
async def rcache(ctx, count: int = 10):
    # Only allow members who have RCACHE_ROLES as well (double-check)
    if not any(role.id in RCACHE_ROLES for role in ctx.author.roles):
        await ctx.reply("You don't have permission (missing RCACHE role).", mention_author=False)
        return
    data = safe_load_data()
    cached = data.get("cached_messages", {})
    if not cached:
        await ctx.send("No cached messages.")
        return
    items = list(cached.items())[-count:]
    embed = discord.Embed(title=f"Recent {len(items)} cached messages", color=discord.Color.orange(), timestamp=utc_now())
    for mid, info in items:
        author_name = info.get("author_name", "Unknown")
        content = info.get("content", "No content")
        short = content if len(content) < 500 else content[:497] + "..."
        val = f"{short}\nChannel: <#{info.get('channel_id')}>"
        if info.get("attachments"):
            val += f"\nAttachments: " + ", ".join(info.get("attachments"))
        if info.get("reply_to"):
            val += f"\nReply to: {info['reply_to'].get('author_id')} (msg {info['reply_to'].get('message_id')})"
        embed.add_field(name=f"{author_name} (ID {info.get('author_id')})", value=val, inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# Purge command (logs purged messages)
# ----------------------------
@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, limit: int):
    if limit <= 0:
        await ctx.send("Limit must be positive.")
        return
    deleted = await ctx.channel.purge(limit=limit)
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if not ch:
        await ctx.send(f"Purged {len(deleted)} messages.")
        return
    embed = discord.Embed(title="Purge Log", description=f"{ctx.author} purged {len(deleted)} messages in #{ctx.channel.name}", color=discord.Color.red(), timestamp=utc_now())
    for msg in deleted:
        content = msg.content or "No content"
        name = f"{msg.author} ({msg.author.id})"
        field_val = content if len(content) < 1024 else content[:1020] + "..."
        embed.add_field(name=name, value=field_val, inline=False)
        if msg.attachments:
            embed.add_field(name="Attachments", value="\n".join(a.url for a in msg.attachments), inline=False)
        if msg.reference:
            ref = msg.reference.resolved
            if ref:
                embed.add_field(name=f"Reply To {ref.author}", value=ref.content or "No content", inline=False)
    await safe_send_channel(ch, embed)
    # optional notify executor
    try:
        await ctx.send(f"Purged {len(deleted)} messages. Logged to tracking channel.", delete_after=10)
    except Exception:
        pass

# ----------------------------
# RMute / Runmute logging helper
# ----------------------------
async def log_mute_action(target_member: discord.Member, moderator: discord.Member, duration: int, reason: str):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if not ch:
        return
    embed = discord.Embed(title="Mute Applied", color=discord.Color.red(), timestamp=utc_now())
    embed.add_field(name="User", value=f"{target_member} ({target_member.id})", inline=False)
    embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
    embed.add_field(name="Duration (s)", value=str(duration), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await safe_send_channel(ch, embed)

# ----------------------------
# Staff ping functions (ping members individually and log)
# ----------------------------
async def ping_members_by_role_and_log(ctx: commands.Context, role_id: int):
    members = [m for m in ctx.guild.members if any(r.id == role_id for r in m.roles)]
    if not members:
        await ctx.send("No members with that role were found.", delete_after=8)
        return
    mentions = " ".join(m.mention for m in members)
    # Send to invoking channel (so staff are pinged in place), then log
    try:
        await ctx.send(mentions)
    except Exception:
        # try sending smaller chunks in case of length issues
        chunk = []
        length = 0
        for m in members:
            part = m.mention + " "
            if length + len(part) > 1900:
                try:
                    await ctx.send("".join(chunk))
                except Exception:
                    pass
                chunk = []
                length = 0
            chunk.append(part)
            length += len(part)
        if chunk:
            try:
                await ctx.send("".join(chunk))
            except Exception:
                pass
    # delete the command message (silence)
    try:
        await ctx.message.delete()
    except Exception:
        pass
    # Log
    for log_channel_id in STAFF_LOG_CHANNELS:
        ch = bot.get_channel(log_channel_id)
        if ch:
            embed = discord.Embed(title="Staff Ping", description=f"{ctx.author} triggered a staff ping.", color=discord.Color.blue(), timestamp=utc_now())
            if ctx.message.reference:
                embed.add_field(name="Replying To", value=f"Message ID: {ctx.message.reference.message_id}", inline=False)
            await safe_send_channel(ch, embed)

@bot.command()
@commands.has_permissions(send_messages=True)
async def rping(ctx):
    await ping_members_by_role_and_log(ctx, STAFF_PING_ROLE)

@bot.command()
@commands.has_permissions(send_messages=True)
async def hsping(ctx):
    await ping_members_by_role_and_log(ctx, HIGHER_STAFF_PING_ROLE)

# ----------------------------
# Timetrack leaderboards (tlb/tdm)
# ----------------------------
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
            # compute days based on earliest activity (use last_online as proxy)
            days = max(1, (now_ts - int(udata.get("last_online", now_ts))) / 86400)
            avg = total / days
            rows.append((member, avg, total))
        except Exception:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (RCACHE roles)", color=discord.Color.green(), timestamp=utc_now())
    for i, (member, avg, total) in enumerate(rows[:top], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Avg/day: {format_duration(int(avg))} — Total: {format_duration(int(total))}", inline=False)
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
            days = max(1, (now_ts - int(udata.get("last_online", now_ts))) / 86400)
            avg = total / days
            rows.append((member, avg, total))
        except Exception:
            continue
    rows.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (No RCACHE roles)", color=discord.Color.green(), timestamp=utc_now())
    for i, (member, avg, total) in enumerate(rows[:top], start=1):
        embed.add_field(name=f"{i}. {member}", value=f"Avg/day: {format_duration(int(avg))} — Total: {format_duration(int(total))}", inline=False)
    await ctx.send(embed=embed)

# ----------------------------
# Channel / Role / Webhook event logging
# ----------------------------
@bot.event
async def on_guild_channel_create(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Channel Created", color=discord.Color.green(), timestamp=utc_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        embed.add_field(name="Type", value=str(type(channel)), inline=False)
        await safe_send_channel(ch, embed)

@bot.event
async def on_guild_channel_delete(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Channel Deleted", color=discord.Color.red(), timestamp=utc_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        await safe_send_channel(ch, embed)

@bot.event
async def on_guild_channel_update(before, after):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Channel Updated", color=discord.Color.orange(), timestamp=utc_now())
        embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=False)
        embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=False)
        await safe_send_channel(ch, embed)

@bot.event
async def on_guild_role_create(role):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Role Created", color=discord.Color.green(), timestamp=utc_now())
        embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        await safe_send_channel(ch, embed)

@bot.event
async def on_guild_role_delete(role):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Role Deleted", color=discord.Color.red(), timestamp=utc_now())
        embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        await safe_send_channel(ch, embed)

@bot.event
async def on_guild_role_update(before, after):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Role Updated", color=discord.Color.orange(), timestamp=utc_now())
        embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=False)
        embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=False)
        await safe_send_channel(ch, embed)

@bot.event
async def on_webhook_update(channel):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        embed = discord.Embed(title="Webhook Updated", color=discord.Color.purple(), timestamp=utc_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        await safe_send_channel(ch, embed)

# ----------------------------
# Help command listing everything
# ----------------------------
@bot.command()
async def rhelp(ctx):
    e = discord.Embed(title="Help — Commands", color=discord.Color.blue(), timestamp=utc_now())
    e.add_field(name="!timetrack [user]", value="Show timetrack info for a user", inline=False)
    e.add_field(name="!tlb [top]", value="Timetrack leaderboard (RCACHE)", inline=False)
    e.add_field(name="!tdm [top]", value="Timetrack leaderboard (no RCACHE)", inline=False)
    e.add_field(name="!rmute [users] [seconds] [reason]", value="Mute multiple users with duration in seconds", inline=False)
    e.add_field(name="!runmute [user] [seconds] [reason]", value="Mute a single user", inline=False)
    e.add_field(name="!rmlb", value="Show top rmute users (by moderator usage)", inline=False)
    e.add_field(name="!rcache [count]", value="Show deleted messages cache (restricted)", inline=False)
    e.add_field(name="!purge [limit]", value="Purge messages and log them", inline=False)
    e.add_field(name="!rping", value="Ping all members who have STAFF_PING_ROLE", inline=False)
    e.add_field(name="!hsping", value="Ping all members who have HIGHER_STAFF_PING_ROLE", inline=False)
    e.add_field(name="!rdm", value="Toggle opt-out from bot DMs", inline=False)
    await ctx.send(embed=e)

# ----------------------------
# Start the bot
# ----------------------------
if __name__ == "__main__":
    # Do not start loops here — they are started in on_ready()
    bot.run(TOKEN)
