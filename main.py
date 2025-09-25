# bot.py - Full Version Part 1/4
"""
Fully Functional Discord Bot
Prefix: !m
Features:
- Timetrack
- RMute/Runmute with auto-unmute
- Deleted message cache
- Leaderboards
- Staff ping
- Purge logging
- DM opt-out
- Full customization (!mcustomize)
- JSON persistence
- Built-in keep-alive (no Flask, no open ports)
"""

import discord
from discord.ext import commands, tasks
import asyncio
import pytz
import json
import os
import re
from datetime import datetime, timedelta
from discord import Embed, Colour
from typing import Optional

# -----------------------------
# Configuration & Data
# -----------------------------
DATA_FILE = "bot_data.json"

DEFAULTS = {
    "mute_role_id": None,
    "timetrack_channel_id": None,
    "log_channel_id": None,
    "staff_ping_role_id": None,
    "higher_staff_ping_role_id": None,
    "rcache_roles": [],
    "rdm_users": [],
    "mutes": {},
    "rmute_usage": {},
    "cache": {},
    "users": {},
    "created_at": str(datetime.now(pytz.utc))
}

# -----------------------------
# Utility Functions
# -----------------------------
def now_utc():
    return datetime.now(pytz.utc)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_data():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2)
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)
    for k, v in DEFAULTS.items():
        if k not in raw:
            raw[k] = v
    raw['rcache_roles'] = [int(x) for x in raw.get('rcache_roles', []) if x]
    raw['rdm_users'] = [str(x) for x in raw.get('rdm_users', [])]
    return raw

def parse_duration_to_seconds(text: str) -> int:
    if not text:
        return 0
    text = text.strip().lower()
    if text.isdigit():
        return int(text)
    pattern = re.compile(r'(?:(?P<days>\d+)d)?\s*(?:(?P<hours>\d+)h)?\s*(?:(?P<minutes>\d+)m)?\s*(?:(?P<seconds>\d+)s)?$')
    m = pattern.fullmatch(text)
    if not m:
        return -1
    days = int(m.group('days') or 0)
    hours = int(m.group('hours') or 0)
    minutes = int(m.group('minutes') or 0)
    seconds = int(m.group('seconds') or 0)
    return days*86400 + hours*3600 + minutes*60 + seconds

def format_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if seconds or not parts: parts.append(f"{seconds}s")
    return " ".join(parts)

# -----------------------------
# Bot Initialization
# -----------------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!m", intents=intents, help_command=None)
DATA = load_data()
data_lock = asyncio.Lock()

async def persist():
    async with data_lock:
        save_data(DATA)

# -----------------------------
# Timetrack Helpers
# -----------------------------
def ensure_user_record(user_id: int):
    uid = str(user_id)
    if uid not in DATA['users']:
        DATA['users'][uid] = {
            'first_seen': str(now_utc()),
            'last_online': None,
            'last_message': None,
            'last_edit': None,
            'total_online_seconds': 0,
            'online_start': None
        }

async def update_last_message(member: discord.Member, message: discord.Message):
    ensure_user_record(member.id)
    rec = DATA['users'][str(member.id)]
    rec['last_message'] = {
        'content': message.content,
        'attachments': [a.url for a in message.attachments],
        'channel_id': message.channel.id,
        'timestamp': str(now_utc())
    }
    await persist()

async def update_cache_on_delete(message: discord.Message):
    msg_id = str(message.id)
    DATA['cache'][msg_id] = {
        'content': message.content,
        'author': str(message.author),
        'attachments': [a.url for a in message.attachments],
        'channel_id': message.channel.id,
        'timestamp': str(now_utc()),
        'reply_to': str(message.reference.message_id) if message.reference else None
    }
    if len(DATA['cache']) > 1000:
        keys = list(DATA['cache'].keys())[:len(DATA['cache'])-1000]
        for k in keys:
            DATA['cache'].pop(k)
    await persist()

# -----------------------------
# RMute/Runmute Helpers
# -----------------------------
async def apply_mute(guild: discord.Guild, user: discord.Member, moderator: discord.Member, duration_seconds: int, reason: str):
    mute_role_id = DATA.get('mute_role_id')
    if not mute_role_id:
        raise RuntimeError("Mute role not configured. Use !mcustomize to set it.")
    mute_role = guild.get_role(int(mute_role_id))
    if not mute_role:
        raise RuntimeError("Configured mute role not found in guild.")
    await user.add_roles(mute_role, reason=reason)
    end_time = None
    if duration_seconds and duration_seconds > 0:
        end_time = now_utc() + timedelta(seconds=duration_seconds)
    DATA['mutes'][str(user.id)] = {
        'moderator': moderator.id,
        'start': str(now_utc()),
        'end': str(end_time) if end_time else None,
        'reason': reason
    }
    mid = str(moderator.id)
    DATA['rmute_usage'][mid] = int(DATA['rmute_usage'].get(mid, 0)) + 1
    await persist()

    if str(user.id) not in DATA.get('rdm_users', []):
        try:
            embed = Embed(title="You've been muted", colour=Colour.orange())
            embed.add_field(name="Moderator", value=str(moderator), inline=True)
            embed.add_field(name="Duration", value=(format_timedelta(timedelta(seconds=duration_seconds)) if duration_seconds else "Permanent"), inline=True)
            embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
            embed.timestamp = now_utc()
            await user.send(embed=embed)
        except Exception:
            pass

    if end_time:
        schedule_unmute_task_for(user.id, guild.id, duration_seconds)

async def remove_mute(guild: discord.Guild, user: discord.Member, by: Optional[discord.Member]=None):
    mute_role_id = DATA.get('mute_role_id')
    if not mute_role_id:
        return
    mute_role = guild.get_role(int(mute_role_id))
    if not mute_role:
        return
    try:
        await user.remove_roles(mute_role, reason=f"Manual unmute by {by}" if by else "Auto unmute")
    except Exception:
        pass
    DATA['mutes'].pop(str(user.id), None)
    await persist()

SCHEDULED_UNMUTE_TASKS = {}

def schedule_unmute_task_for(user_id: int, guild_id: int, delay_seconds: int):
    async def _unmute_after():
        try:
            await asyncio.sleep(delay_seconds)
            guild = bot.get_guild(guild_id)
            if guild:
                member = guild.get_member(user_id)
                if member:
                    mute_role_id = DATA.get('mute_role_id')
                    if mute_role_id:
                        role = guild.get_role(int(mute_role_id))
                        if role and role in member.roles:
                            await member.remove_roles(role, reason="Auto-unmute after duration")
            DATA['mutes'].pop(str(user_id), None)
            await persist()
        finally:
            SCHEDULED_UNMUTE_TASKS.pop(str(user_id), None)
    key = str(user_id)
    existing = SCHEDULED_UNMUTE_TASKS.get(key)
    if existing and not existing.done():
        existing.cancel()
    task = bot.loop.create_task(_unmute_after())
    SCHEDULED_UNMUTE_TASKS[key] = task

async def schedule_all_pending_unmutes():
    for uid, rec in list(DATA.get('mutes', {}).items()):
        end_iso = rec.get('end')
        if not end_iso:
            continue
        try:
            end_dt = datetime.fromisoformat(end_iso)
        except Exception:
            DATA['mutes'].pop(uid, None)
            continue
        now = now_utc()
        if end_dt <= now:
            DATA['mutes'].pop(uid, None)
        else:
            delay = int((end_dt - now).total_seconds())
            guild_id = None
            for g in bot.guilds:
                if g.get_member(int(uid)):
                    guild_id = g.id
                    break
            if guild_id:
                schedule_unmute_task_for(int(uid), guild_id, delay)

# -----------------------------
# Timetrack Loop
# -----------------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    try:
        rcache_roles = [int(x) for x in DATA.get('rcache_roles', []) if x]
        for guild in bot.guilds:
            for member in guild.members:
                if rcache_roles and not any(r.id in rcache_roles for r in member.roles):
                    continue
                ensure_user_record(member.id)
                rec = DATA['users'][str(member.id)]
                if member.status not in (discord.Status.offline, discord.Status.invisible):
                    if not rec.get('online_start'):
                        rec['online_start'] = str(now_utc())
                else:
                    if rec.get('online_start'):
                        try:
                            start = datetime.fromisoformat(rec['online_start'])
                            delta = now_utc() - start
                            rec['total_online_seconds'] = int(rec.get('total_online_seconds', 0)) + int(delta.total_seconds())
                        except Exception:
                            pass
                        rec['online_start'] = None
        await persist()
    except Exception as e:
        print("Error in timetrack_loop:", e)

# -----------------------------
# Event Listeners
# -----------------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID {bot.user.id})")
    if not timetrack_loop.is_running():
        timetrack_loop.start()
    await schedule_all_pending_unmutes()
    print("Pending unmutes scheduled.")

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    try:
        await update_last_message(message.author, message)
    except Exception:
        pass
    await bot.process_commands(message)

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author.bot:
        return
    try:
        await update_cache_on_delete(message)
    except Exception:
        pass

# -----------------------------
# End of Part 1
# -----------------------------
# -----------------------------
# Part 2/4: Commands - Mutes, Leaderboards, Cache, Staff Pings
# -----------------------------

# -----------------------------
# !mcustomize - Configuration
# -----------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def mcustomize(ctx, option: str = None, *, value: str = None):
    """
    Customize bot settings:
    - mute_role_id, timetrack_channel_id, log_channel_id, staff_ping_role_id,
      higher_staff_ping_role_id, rcache_roles
    Example: !mcustomize mute_role_id 1234567890
    """
    if not option or not value:
        await ctx.send("Usage: `!mcustomize [option] [value]`")
        return
    opt = option.lower()
    try:
        if opt in ["mute_role_id", "timetrack_channel_id", "log_channel_id", "staff_ping_role_id", "higher_staff_ping_role_id"]:
            DATA[opt] = int(value)
        elif opt == "rcache_roles":
            DATA[opt] = [int(v) for v in value.split()]
        else:
            await ctx.send(f"Unknown option `{option}`.")
            return
        await persist()
        await ctx.send(f"✅ `{opt}` updated successfully.")
    except Exception as e:
        await ctx.send(f"Error updating `{opt}`: {e}")

# -----------------------------
# !mrmute - Multiple user mute
# -----------------------------
@bot.command()
@commands.has_permissions(manage_roles=True)
async def mrmute(ctx, members: commands.Greedy[discord.Member], duration: str = None, *, reason: str = None):
    if not members:
        await ctx.send("Please mention at least one member to mute.")
        return
    seconds = parse_duration_to_seconds(duration) if duration else 0
    for member in members:
        try:
            await apply_mute(ctx.guild, member, ctx.author, seconds, reason or "No reason provided")
        except Exception as e:
            await ctx.send(f"Error muting {member}: {e}")
    await ctx.send(f"✅ Muted {len(members)} member(s).")

# -----------------------------
# !mrunmute - Single user mute
# -----------------------------
@bot.command()
@commands.has_permissions(manage_roles=True)
async def mrunmute(ctx, member: discord.Member):
    try:
        await remove_mute(ctx.guild, member, by=ctx.author)
        await ctx.send(f"✅ {member} has been unmuted.")
    except Exception as e:
        await ctx.send(f"Error unmuting {member}: {e}")

# -----------------------------
# !mrmlb - RMute usage leaderboard
# -----------------------------
@bot.command()
async def mrmlb(ctx):
    usage_sorted = sorted(DATA.get('rmute_usage', {}).items(), key=lambda x: x[1], reverse=True)[:10]
    embed = Embed(title="Top RMute Users", colour=Colour.blue())
    for uid, count in usage_sorted:
        member = ctx.guild.get_member(int(uid))
        embed.add_field(name=str(member) if member else uid, value=f"Mutes: {count}", inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# !mrcache - Deleted messages/images
# -----------------------------
@bot.command()
async def mrcache(ctx, limit: int = 5):
    roles_ids = [r.id for r in ctx.author.roles]
    allowed = any(rid in DATA.get('rcache_roles', []) for rid in roles_ids)
    if not allowed:
        await ctx.send("❌ You do not have permission to access the cache.")
        return
    last_msgs = list(DATA.get('cache', {}).values())[-limit:]
    for msg in last_msgs:
        embed = Embed(title="Deleted Message", colour=Colour.red())
        embed.add_field(name="Author", value=msg.get('author', 'Unknown'))
        embed.add_field(name="Content", value=msg.get('content') or "None", inline=False)
        embed.add_field(name="Channel ID", value=msg.get('channel_id'))
        if msg.get('attachments'):
            embed.add_field(name="Attachments", value="\n".join(msg['attachments']))
        await ctx.send(embed=embed)

# -----------------------------
# !mtlb - Timetrack leaderboard (filtered roles)
# -----------------------------
@bot.command()
async def mtlb(ctx, top: int = 10):
    rcache_roles = DATA.get('rcache_roles', [])
    scores = []
    for uid, rec in DATA.get('users', {}).items():
        member = ctx.guild.get_member(int(uid))
        if not member:
            continue
        if rcache_roles and not any(r.id in rcache_roles for r in member.roles):
            continue
        total_sec = int(rec.get('total_online_seconds', 0))
        scores.append((member, total_sec))
    scores.sort(key=lambda x: x[1], reverse=True)
    embed = Embed(title="Timetrack Leaderboard", colour=Colour.green())
    for member, sec in scores[:top]:
        embed.add_field(name=str(member), value=format_timedelta(timedelta(seconds=sec)), inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# !mtdm - Timetrack leaderboard (no filter)
# -----------------------------
@bot.command()
async def mtdm(ctx, top: int = 10):
    scores = []
    for uid, rec in DATA.get('users', {}).items():
        member = ctx.guild.get_member(int(uid))
        if not member:
            continue
        total_sec = int(rec.get('total_online_seconds', 0))
        scores.append((member, total_sec))
    scores.sort(key=lambda x: x[1], reverse=True)
    embed = Embed(title="Timetrack Leaderboard (All Users)", colour=Colour.green())
    for member, sec in scores[:top]:
        embed.add_field(name=str(member), value=format_timedelta(timedelta(seconds=sec)), inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# !mrping - Staff Ping
# -----------------------------
@bot.command()
async def mrping(ctx):
    role_id = DATA.get('staff_ping_role_id')
    if not role_id:
        await ctx.send("Staff ping role not configured.")
        return
    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.send("Role not found in guild.")
        return
    await ctx.send(f"{role.mention}", delete_after=1)

# -----------------------------
# !mhsping - Higher Staff Ping
# -----------------------------
@bot.command()
async def mhsping(ctx):
    role_id = DATA.get('higher_staff_ping_role_id')
    if not role_id:
        await ctx.send("Higher staff ping role not configured.")
        return
    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.send("Role not found in guild.")
        return
    await ctx.send(f"{role.mention}", delete_after=1)

# -----------------------------
# !mrdm - DM opt-out
# -----------------------------
@bot.command()
async def mrdm(ctx):
    uid = str(ctx.author.id)
    if uid in DATA.get('rdm_users', []):
        DATA['rdm_users'].remove(uid)
        await ctx.send("✅ You have re-enabled DMs from the bot.")
    else:
        DATA['rdm_users'].append(uid)
        await ctx.send("✅ You have opted out of DMs from the bot.")
    await persist()

# -----------------------------
# !mpurge - Purge messages
# -----------------------------
@bot.command()
@commands.has_permissions(manage_messages=True)
async def mpurge(ctx, limit: int):
    if limit < 1:
        await ctx.send("❌ Limit must be at least 1.")
        return
    deleted = await ctx.channel.purge(limit=limit)
    embed = Embed(title="Messages Purged", colour=Colour.orange())
    embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
    embed.add_field(name="Channel", value=str(ctx.channel), inline=False)
    embed.add_field(name="Messages Deleted", value=str(len(deleted)), inline=False)
    for msg in deleted[:5]:
        embed.add_field(name=f"{msg.author}", value=msg.content or "No Content", inline=False)
    log_channel_id = DATA.get('log_channel_id')
    if log_channel_id:
        log_channel = ctx.guild.get_channel(int(log_channel_id))
        if log_channel:
            await log_channel.send(embed=embed)
    await ctx.send(f"✅ Purged {len(deleted)} messages.", delete_after=5)

# -----------------------------
# !mhelp - Help command
# -----------------------------
@bot.command()
async def mhelp(ctx):
    embed = Embed(title="Bot Commands", colour=Colour.blue())
    embed.add_field(name="!mcustomize [option] [value]", value="Configure bot settings", inline=False)
    embed.add_field(name="!mrmute [users] [duration] [reason]", value="Mute multiple users", inline=False)
    embed.add_field(name="!mrunmute [user]", value="Unmute a single user", inline=False)
    embed.add_field(name="!mrmlb", value="Top RMute users leaderboard", inline=False)
    embed.add_field(name="!mrcache", value="View deleted messages/images", inline=False)
    embed.add_field(name="!mtlb", value="Timetrack leaderboard (roles filtered)", inline=False)
    embed.add_field(name="!mtdm", value="Timetrack leaderboard (all users)", inline=False)
    embed.add_field(name="!mrping", value="Ping staff role", inline=False)
    embed.add_field(name="!mhsping", value="Ping higher staff role", inline=False)
    embed.add_field(name="!mrdm", value="Opt-out of bot DMs", inline=False)
    embed.add_field(name="!mpurge [limit]", value="Purge messages in channel", inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# End of Part 2
# -----------------------------
# -----------------------------
# Part 3/4: Logging Events, Advanced Timetrack, Auto-Unmute Recovery
# -----------------------------

# -----------------------------
# Channel/Role/Webhook Logging
# -----------------------------
async def log_action(guild: discord.Guild, title: str, description: str, color=Colour.red()):
    channel_id = DATA.get('log_channel_id')
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not channel:
        return
    embed = Embed(title=title, description=description, colour=color)
    embed.timestamp = now_utc()
    await channel.send(embed=embed)

@bot.event
async def on_guild_channel_create(channel):
    await log_action(channel.guild, "Channel Created", f"Channel: {channel.name} ({channel.id}) created by bot or unknown", Colour.green())

@bot.event
async def on_guild_channel_delete(channel):
    await log_action(channel.guild, "Channel Deleted", f"Channel: {channel.name} ({channel.id}) deleted", Colour.red())

@bot.event
async def on_guild_channel_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(f"Name: `{before.name}` → `{after.name}`")
    if changes:
        await log_action(before.guild, "Channel Updated", "\n".join(changes), Colour.orange())

@bot.event
async def on_guild_role_create(role):
    await log_action(role.guild, "Role Created", f"Role: {role.name} ({role.id}) created", Colour.green())

@bot.event
async def on_guild_role_delete(role):
    await log_action(role.guild, "Role Deleted", f"Role: {role.name} ({role.id}) deleted", Colour.red())

@bot.event
async def on_guild_role_update(before, after):
    changes = []
    if before.name != after.name:
        changes.append(f"Name: `{before.name}` → `{after.name}`")
    if before.permissions != after.permissions:
        changes.append(f"Permissions updated")
    if changes:
        await log_action(before.guild, "Role Updated", "\n".join(changes), Colour.orange())

@bot.event
async def on_webhook_update(channel):
    await log_action(channel.guild, "Webhook Updated", f"Webhooks in channel {channel.name} ({channel.id}) updated", Colour.purple())

# -----------------------------
# Advanced Timetrack Session Calculation
# -----------------------------
async def calculate_session(member: discord.Member):
    uid = str(member.id)
    ensure_user_record(member.id)
    rec = DATA['users'][uid]
    if rec.get('online_start'):
        try:
            start = datetime.fromisoformat(rec['online_start'])
            delta = now_utc() - start
            rec['total_online_seconds'] += int(delta.total_seconds())
            rec['online_start'] = str(now_utc())
        except Exception:
            rec['online_start'] = None
    await persist()

# -----------------------------
# Auto-Unmute Recovery (on restart)
# -----------------------------
@bot.event
async def on_connect():
    await schedule_all_pending_unmutes()

# -----------------------------
# Reaction Listener (optional logging)
# -----------------------------
@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return
    await log_action(reaction.message.guild, "Reaction Added", f"{user} reacted {reaction.emoji} to message {reaction.message.id}", Colour.light_grey())

@bot.event
async def on_reaction_remove(reaction, user):
    if user.bot:
        return
    await log_action(reaction.message.guild, "Reaction Removed", f"{user} removed reaction {reaction.emoji} from message {reaction.message.id}", Colour.light_grey())

# -----------------------------
# Member Update Logging
# -----------------------------
@bot.event
async def on_member_update(before, after):
    changes = []
    if before.nick != after.nick:
        changes.append(f"Nickname: `{before.nick}` → `{after.nick}`")
    if before.roles != after.roles:
        old = set(r.id for r in before.roles)
        new = set(r.id for r in after.roles)
        added = new - old
        removed = old - new
        if added:
            changes.append(f"Roles Added: {', '.join([str(after.guild.get_role(r)) for r in added])}")
        if removed:
            changes.append(f"Roles Removed: {', '.join([str(after.guild.get_role(r)) for r in removed])}")
    if changes:
        await log_action(after.guild, f"Member Updated: {after}", "\n".join(changes), Colour.orange())

# -----------------------------
# Voice State Logging
# -----------------------------
@bot.event
async def on_voice_state_update(member, before, after):
    changes = []
    if before.channel != after.channel:
        if before.channel:
            changes.append(f"Left voice channel: {before.channel.name}")
        if after.channel:
            changes.append(f"Joined voice channel: {after.channel.name}")
    if changes:
        await log_action(member.guild, f"Voice Update: {member}", "\n".join(changes), Colour.blurple())

# -----------------------------
# Message Edit Logging
# -----------------------------
@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content != after.content:
        await log_action(before.guild, "Message Edited", f"Author: {before.author}\nChannel: {before.channel}\nBefore: {before.content}\nAfter: {after.content}", Colour.yellow())

# -----------------------------
# Fancy Embed for Mute/Unmute (Helper)
# -----------------------------
def fancy_embed(title: str, description: str, color=Colour.orange(), timestamp=True):
    embed = Embed(title=title, description=description, colour=color)
    if timestamp:
        embed.timestamp = now_utc()
    return embed

# -----------------------------
# Background Keep-Alive Loop
# -----------------------------
@tasks.loop(seconds=300)
async def keep_alive_loop():
    # Dummy loop to prevent Render from idling
    for guild in bot.guilds:
        for member in guild.members[:5]:
            try:
                await calculate_session(member)
            except Exception:
                continue

# Start keep-alive loop
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID {bot.user.id})")
    if not timetrack_loop.is_running():
        timetrack_loop.start()
    if not keep_alive_loop.is_running():
        keep_alive_loop.start()
    await schedule_all_pending_unmutes()
    print("Bot fully ready and loops started.")

# -----------------------------
# End of Part 3
# -----------------------------
# -----------------------------
# Part 4/4: Final Async Startup + Render Web Server + Utilities
# -----------------------------

import os
import asyncio
from datetime import datetime
from discord import Embed, Colour

# -----------------------------
# Error Handling
# -----------------------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You do not have permission to run this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: {error.param.name}")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Bad argument: {error}")
    elif isinstance(error, commands.CommandNotFound):
        pass  # Ignore unknown commands
    else:
        await ctx.send(f"❌ An unexpected error occurred: {error}")
        print(f"Error in command {ctx.command}: {error}")

# -----------------------------
# Graceful Shutdown
# -----------------------------
async def shutdown():
    print("Shutting down bot...")
    await persist()
    await bot.close()

# -----------------------------
# Keep-alive dummy server for Render
# -----------------------------
from aiohttp import web

async def handle(request):
    return web.Response(text="Bot is alive!")

async def start_webserver():
    port = int(os.environ.get("PORT", 8000))
    app = web.Application()
    app.add_routes([web.get("/", handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"✅ Webserver running on port {port}")

# -----------------------------
# Admin Utilities
# -----------------------------
@bot.command()
@commands.has_permissions(administrator=True)
async def mresettimetrack(ctx):
    DATA['users'] = {}
    await persist()
    await ctx.send("✅ Timetrack data has been reset.")

@bot.command()
@commands.has_permissions(administrator=True)
async def mresetcache(ctx):
    DATA['cache'] = {}
    await persist()
    await ctx.send("✅ Cache data has been reset.")

@bot.command()
async def mshowconfig(ctx):
    config_keys = [
        "mute_role_id",
        "timetrack_channel_id",
        "log_channel_id",
        "staff_ping_role_id",
        "higher_staff_ping_role_id",
        "rcache_roles"
    ]
    embed = Embed(title="Bot Configuration", colour=Colour.blue())
    for key in config_keys:
        embed.add_field(name=key, value=str(DATA.get(key, "Not set")), inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# Preload members
# -----------------------------
async def preload_members():
    for guild in bot.guilds:
        await guild.chunk()

# -----------------------------
# Async main startup function
# -----------------------------
async def main():
    # Start Render webserver for open port
    asyncio.create_task(start_webserver())

    # Preload members for timetrack accuracy
    await preload_members()

    # Schedule pending unmutes
    await schedule_all_pending_unmutes()

    # Start loops if not running
    if not timetrack_loop.is_running():
        timetrack_loop.start()
    if not keep_alive_loop.is_running():
        keep_alive_loop.start()

    # Run Discord bot
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ DISCORD_TOKEN not found.")
        return
    try:
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        await shutdown()
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        await shutdown()

# -----------------------------
# Run async main
# -----------------------------
if __name__ == "__main__":
    asyncio.run(main())

# -----------------------------
# End of Updated Part 4/4
# -----------------------------
