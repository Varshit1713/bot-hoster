# ultimate_m_bot.py
"""
Fully-Functional Discord Bot
Prefix: !m
Features:
- Timetrack system
- RMute/Runmute with auto-unmute
- Deleted message cache
- Leaderboards
- Staff ping
- Purge logging
- DM opt-out
- Full customization via !mcustomize
- JSON persistence
"""

import discord
from discord.ext import commands, tasks
import asyncio
import pytz
import json
import os
import re
from datetime import datetime, timedelta
from typing import Optional
from discord import Embed, Colour

# keep_alive_bot.py
"""
Headless keep-alive wrapper for Discord bot.
Runs your bot anywhere with no open ports needed.
"""

import asyncio
import os
import subprocess
import sys

BOT_FILE = "bot.py"  # Your Discord bot file

async def keep_alive():
    print("Keep-alive loop started. Bot will auto-restart on crash.")
    while True:
        try:
            # Start bot as subprocess
            process = subprocess.Popen([sys.executable, BOT_FILE])
            # Wait for it to finish
            process.wait()
            print(f"{BOT_FILE} exited. Restarting in 5 seconds...")
            await asyncio.sleep(5)
        except Exception as e:
            print(f"Error starting {BOT_FILE}: {e}")
            await asyncio.sleep(5)

# Run the keep-alive loop
asyncio.run(keep_alive())

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
# Commands
# -----------------------------

# mcustomize
@bot.command()
@commands.has_permissions(administrator=True)
async def mcustomize(ctx, setting: str = None, value: str = None):
    if not setting:
        embed = Embed(title="!mcustomize Help", colour=Colour.blue())
        embed.add_field(name="Usage", value="`!mcustomize [setting] [value]`", inline=False)
        embed.add_field(name="Settings", value="`mute_role`, `timetrack_channel`, `log_channel`, `staff_ping_role`, `higher_staff_ping_role`, `rcache_roles`", inline=False)
        await ctx.send(embed=embed)
        return
    setting = setting.lower()
    if setting not in ["mute_role", "timetrack_channel", "log_channel", "staff_ping_role", "higher_staff_ping_role", "rcache_roles"]:
        await ctx.send("Invalid setting name.")
        return
    if not value:
        await ctx.send("Please provide a value for this setting.")
        return
    if setting == "rcache_roles":
        DATA['rcache_roles'] = [int(x.strip()) for x in value.split(",")]
    else:
        DATA[f"{setting}_id"] = int(value)
    await persist()
    await ctx.send(f"Setting `{setting}` updated successfully.")

# mrmute
@bot.command()
@commands.has_permissions(manage_roles=True)
async def mrmute(ctx, members: commands.Greedy[discord.Member], duration: str = None, *, reason: str = None):
    if not members:
        await ctx.send("Please mention at least one user to mute.")
        return
    seconds = parse_duration_to_seconds(duration) if duration else 0
    for member in members:
        try:
            await apply_mute(ctx.guild, member, ctx.author, seconds, reason or "No reason provided")
        except Exception as e:
            await ctx.send(f"Failed to mute {member}: {e}")
    await ctx.send(f"Muted {len(members)} members successfully.")

# mrunmute
@bot.command()
@commands.has_permissions(manage_roles=True)
async def mrunmute(ctx, member: discord.Member):
    await remove_mute(ctx.guild, member, ctx.author)
    await ctx.send(f"{member} has been unmuted.")

# mrmlb
@bot.command()
async def mrmlb(ctx):
    usage = DATA.get('rmute_usage', {})
    sorted_usage = sorted(usage.items(), key=lambda x: x[1], reverse=True)[:10]
    embed = Embed(title="RMute Leaderboard", colour=Colour.gold())
    for uid, count in sorted_usage:
        member = ctx.guild.get_member(int(uid))
        name = member.name if member else f"User {uid}"
        embed.add_field(name=name, value=f"{count} rmutes", inline=False)
    await ctx.send(embed=embed)

# mrcache
@bot.command()
async def mrcache(ctx):
    if not DATA['cache']:
        await ctx.send("Cache is empty.")
        return
    embed = Embed(title="Deleted Messages Cache", colour=Colour.red())
    count = 0
    for msg_id, rec in list(DATA['cache'].items())[-10:]:
        author = rec.get('author', 'Unknown')
        content = rec.get('content', '')
        attachments = "\n".join(rec.get('attachments', [])) or "None"
        reply_to = rec.get('reply_to', 'None')
        embed.add_field(name=f"{author} (Deleted)", value=f"Content: {content}\nAttachments: {attachments}\nReply to: {reply_to}", inline=False)
        count += 1
    embed.set_footer(text=f"Showing last {count} deleted messages")
    await ctx.send(embed=embed)

# mtlb
@bot.command()
async def mtlb(ctx):
    rcache_roles = DATA.get('rcache_roles', [])
    leaderboard = []
    for uid, rec in DATA.get('users', {}).items():
        member = ctx.guild.get_member(int(uid))
        if not member:
            continue
        if rcache_roles and not any(r.id in rcache_roles for r in member.roles):
            continue
        total = rec.get('total_online_seconds', 0)
        leaderboard.append((member.name, total))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    embed = Embed(title="Timetrack Leaderboard", colour=Colour.green())
    for name, total in leaderboard[:10]:
        embed.add_field(name=name, value=format_timedelta(timedelta(seconds=total)), inline=False)
    await ctx.send(embed=embed)

# mtdm
@bot.command()
async def mtdm(ctx):
    rcache_roles = DATA.get('rcache_roles', [])
    leaderboard = []
    for uid, rec in DATA.get('users', {}).items():
        member = ctx.guild.get_member(int(uid))
        if not member:
            continue
        if rcache_roles and any(r.id in rcache_roles for r in member.roles):
            continue
        total = rec.get('total_online_seconds', 0)
        leaderboard.append((member.name, total))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    embed = Embed(title="Non-RCACHE Users Leaderboard", colour=Colour.green())
    for name, total in leaderboard[:10]:
        embed.add_field(name=name, value=format_timedelta(timedelta(seconds=total)), inline=False)
    await ctx.send(embed=embed)

# mrping
@bot.command()
async def mrping(ctx):
    role_id = DATA.get('staff_ping_role_id')
    if not role_id:
        await ctx.send("Staff ping role not configured.")
        return
    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.send("Staff ping role not found.")
        return
    await ctx.send(role.mention)

# mhsping
@bot.command()
async def mhsping(ctx):
    role_id = DATA.get('higher_staff_ping_role_id')
    if not role_id:
        await ctx.send("Higher staff ping role not configured.")
        return
    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.send("Higher staff ping role not found.")
        return
    await ctx.send(role.mention)

# mrdm
@bot.command()
async def mrdm(ctx):
    uid = str(ctx.author.id)
    if uid in DATA.get('rdm_users', []):
        DATA['rdm_users'].remove(uid)
        await ctx.send("You have opted back in to receive DMs from the bot.")
    else:
        DATA['rdm_users'].append(uid)
        await ctx.send("You have opted out from receiving DMs from the bot.")
    await persist()

# mpurge
@bot.command()
@commands.has_permissions(manage_messages=True)
async def mpurge(ctx, limit: int):
    messages = await ctx.channel.purge(limit=limit)
    log_channel_id = DATA.get('log_channel_id')
    if log_channel_id:
        log_channel = ctx.guild.get_channel(int(log_channel_id))
        if log_channel:
            embed = Embed(title=f"{ctx.author} purged messages", colour=Colour.dark_red())
            for msg in messages[-10:]:
                content = msg.content or "No text"
                attachments = "\n".join([a.url for a in msg.attachments]) or "None"
                embed.add_field(name=str(msg.author), value=f"{content}\nAttachments: {attachments}", inline=False)
            await log_channel.send(embed=embed)
    await ctx.send(f"Purged {len(messages)} messages.", delete_after=5)

# mhelp
@bot.command()
async def mhelp(ctx):
    embed = Embed(title="Bot Commands", colour=Colour.blue())
    embed.add_field(name="!mcustomize", value="Configure bot settings.", inline=False)
    embed.add_field(name="!mrmute [users] [duration] [reason]", value="Mute users.", inline=False)
    embed.add_field(name="!mrunmute [user]", value="Unmute a user.", inline=False)
    embed.add_field(name="!mrmlb", value="Show RMute leaderboard.", inline=False)
    embed.add_field(name="!mrcache", value="Show last deleted messages.", inline=False)
    embed.add_field(name="!mtlb", value="Timetrack leaderboard (RCACHE_ROLES).", inline=False)
    embed.add_field(name="!mtdm", value="Timetrack leaderboard (non-RCACHE).", inline=False)
    embed.add_field(name="!mrping", value="Ping staff.", inline=False)
    embed.add_field(name="!mhsping", value="Ping higher staff.", inline=False)
    embed.add_field(name="!mrdm", value="Opt-in/out of bot DMs.", inline=False)
    embed.add_field(name="!mpurge [number]", value="Purge messages.", inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# Run Bot
# -----------------------------
bot.run(os.environ.get("DISCORD_TOKEN"))
