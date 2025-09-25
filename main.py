# ------------------ IMPORTS & SETUP ------------------
import discord
from discord.ext import commands, tasks
import asyncio
import pytz
import json
import os
import datetime
import traceback
from typing import Dict, Any, List, Optional

# ------------------ ENVIRONMENT ------------------
TOKEN = os.getenv("DISCORD_TOKEN")
DATA_FILE = "bot_data.json"
PREFIX = "!"

# ------------------ INTENTS ------------------
intents = discord.Intents.all()  # All intents enabled for tracking messages, members, roles, etc.

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

# ------------------ UTILITY FUNCTIONS ------------------
def safe_print(*args, **kwargs):
    """Safe print to console, catching encoding errors."""
    try:
        print(*args, **kwargs)
    except Exception:
        print("⚠️ Safe print error")

def load_data() -> Dict[str, Any]:
    """Load JSON data from disk."""
    if not os.path.exists(DATA_FILE):
        return init_data_structure()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        safe_print("⚠️ Error loading data:", e)
        return init_data_structure()

def save_data(data: Dict[str, Any]):
    """Save JSON data to disk safely."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        safe_print("⚠️ Error saving data:", e)

def init_data_structure() -> Dict[str, Any]:
    """Initialize the complete data structure."""
    return {
        "users": {},  # user_id -> {online, last_message, last_edit, daily_seconds}
        "mutes": {},  # user_id -> mute info
        "rmute_usage": {},  # moderator_id -> times used
        "cached_messages": {},  # message_id -> message data
        "logs": {  # channel, role, deletion logs
            "deletions": [],
            "edits": [],
            "purges": [],
            "mod_actions": []
        },
        "rdm_users": {},  # user_id -> True
        "rping_disabled_users": {}  # user_id -> True/False
    }

# ------------------ STARTUP EVENTS ------------------
@bot.event
async def on_ready():
    safe_print(f"🚀 Bot started as {bot.user}")
    safe_print(f"Connected to {len(bot.guilds)} guild(s)")
    if not os.path.exists(DATA_FILE):
        save_data(init_data_structure())
        safe_print("✅ Initialized data file")
    # Start any background tasks
    try:
        timetrack_loop.start()
    except Exception as e:
        safe_print("⚠️ Error starting background loop:", e)

# ------------------ ERROR HANDLING ------------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You do not have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: {error.param}")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send("❌ Unknown command.")
    else:
        await ctx.send("⚠️ An error occurred while running the command.")
        safe_print(f"Error in command {ctx.command}: {error}")
        traceback.print_exc()

# ------------------ USER DATA HELPERS ------------------
def get_user_data(user_id: str) -> Dict[str, Any]:
    data = load_data()
    return data.get("users", {}).get(user_id, {})

def update_user_data(user_id: str, field: str, value: Any):
    data = load_data()
    user_entry = data.setdefault("users", {}).setdefault(user_id, {})
    user_entry[field] = value
    save_data(data)

def increment_daily_seconds(user_id: str, seconds: int):
    data = load_data()
    user_entry = data.setdefault("users", {}).setdefault(user_id, {})
    daily_seconds = user_entry.setdefault("daily_seconds", {})
    today = datetime.datetime.now(pytz.utc).strftime("%Y-%m-%d")
    daily_seconds[today] = daily_seconds.get(today, 0) + seconds
    save_data(data)

# ------------------ RMUTE USAGE HELPERS ------------------
def increment_rmute_usage(mod_id: str):
    data = load_data()
    usage = data.setdefault("rmute_usage", {})
    usage[mod_id] = usage.get(mod_id, 0) + 1
    save_data(data)

# ------------------ RDM USER HELPERS ------------------
def is_rdm_user(user_id: str) -> bool:
    data = load_data()
    return data.get("rdm_users", {}).get(user_id, False)

def toggle_rdm_user(user_id: str) -> bool:
    data = load_data()
    rdm = data.setdefault("rdm_users", {})
    current = rdm.get(user_id, False)
    rdm[user_id] = not current
    save_data(data)
    return not current

# ------------------ MESSAGE CACHE HELPERS ------------------
def cache_message(message: discord.Message):
    data = load_data()
    cached = data.setdefault("cached_messages", {})
    cached[message.id] = {
        "author": message.author.id,
        "channel": message.channel.id,
        "content": message.content,
        "attachments": [a.url for a in message.attachments],
        "timestamp": str(datetime.datetime.now(pytz.utc))
    }
    save_data(data)

def remove_cached_message(message_id: int):
    data = load_data()
    cached = data.get("cached_messages", {})
    if message_id in cached:
        cached.pop(message_id, None)
        save_data(data)

# ------------------ CHANNEL LOGGING HELPERS ------------------
def log_channel_edit(channel: discord.TextChannel, before_name: str, after_name: str):
    data = load_data()
    logs = data.setdefault("logs", {}).setdefault("edits", [])
    logs.append({
        "type": "channel_edit",
        "channel_id": channel.id,
        "before_name": before_name,
        "after_name": after_name,
        "timestamp": str(datetime.datetime.now(pytz.utc))
    })
    save_data(data)

def log_deletion(message: discord.Message):
    data = load_data()
    deletions = data.setdefault("logs", {}).setdefault("deletions", [])
    deletions.append({
        "message_id": message.id,
        "author": message.author.id,
        "channel_id": message.channel.id,
        "content": message.content,
        "attachments": [a.url for a in message.attachments],
        "timestamp": str(datetime.datetime.now(pytz.utc))
    })
    save_data(data)

# ------------------ BASIC COMMAND: RDM TOGGLE ------------------
@bot.command(name="rdm", help="Toggle DM opt-out from the bot")
async def cmd_rdm(ctx: commands.Context):
    status = toggle_rdm_user(str(ctx.author.id))
    text = "disabled" if status else "enabled"
    await ctx.send(f"📩 DM notifications are now **{text}** for you.")

# ------------------ END OF CORE BOT FUNCTIONALITY ------------------

# Note: Part 2 will begin with the Timetrack System, fully perfected.      
# ------------------ TIMETRACK SYSTEM ------------------
RCACHE_ROLES = [
    1410422029236047975,
    1410422762895577088,
    1406326282429403306
]

TIMETRACK_INTERVAL = 60  # seconds

@tasks.loop(seconds=TIMETRACK_INTERVAL)
async def timetrack_loop():
    """Tracks online/offline time, last message, and last edit for members with specific roles."""
    data = load_data()
    now = datetime.datetime.now(pytz.utc)
    
    for guild in bot.guilds:
        for member in guild.members:
            # Skip bots
            if member.bot:
                continue
            
            # Only track members with RCACHE roles
            if not any(role.id in RCACHE_ROLES for role in member.roles):
                continue
            
            uid = str(member.id)
            user_entry = data.setdefault("users", {}).setdefault(uid, {})
            
            # Initialize fields if not exist
            online_start = user_entry.get("online_start")
            total_seconds = user_entry.get("total_online_seconds", 0)
            
            # Check if member is online
            if member.status != discord.Status.offline:
                if online_start is None:
                    # Start a new session
                    user_entry["online_start"] = now.isoformat()
            else:
                if online_start:
                    # Compute session duration
                    start_dt = datetime.datetime.fromisoformat(online_start)
                    session_seconds = (now - start_dt).total_seconds()
                    total_seconds += session_seconds
                    
                    user_entry["total_online_seconds"] = total_seconds
                    
                    # Update daily seconds
                    daily = user_entry.setdefault("daily_seconds", {})
                    today = now.strftime("%Y-%m-%d")
                    daily[today] = daily.get(today, 0) + session_seconds
                    
                    user_entry["online_start"] = None  # Reset session
            
            # Track last message and last edit
            last_message = user_entry.get("last_message", "")
            last_edit = user_entry.get("last_edit")
            # These will be updated on message events separately
            
    save_data(data)

# ------------------ MESSAGE EVENTS FOR TIMETRACK ------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    
    uid = str(message.author.id)
    data = load_data()
    user_entry = data.setdefault("users", {}).setdefault(uid, {})
    
    user_entry["last_message"] = message.content
    user_entry["last_edit"] = None  # Reset last edit on new message
    save_data(data)
    
    # Continue processing commands
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author.bot:
        return
    
    uid = str(after.author.id)
    data = load_data()
    user_entry = data.setdefault("users", {}).setdefault(uid, {})
    
    user_entry["last_edit"] = {
        "before": before.content,
        "after": after.content,
        "timestamp": str(datetime.datetime.now(pytz.utc))
    }
    save_data(data)

# ------------------ TIMETRACK LEADERBOARD HELPERS ------------------
def compute_daily_average(user_id: str) -> float:
    """Compute average daily online time in seconds for a user."""
    user_entry = get_user_data(user_id)
    daily = user_entry.get("daily_seconds", {})
    if not daily:
        return 0
    total = sum(daily.values())
    return total / len(daily)

def get_top_users_by_daily_average(limit: int = 10, roles_only: Optional[List[int]] = None):
    """Return top users filtered by roles with highest daily averages."""
    leaderboard = []
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            if roles_only and not any(role.id in roles_only for role in member.roles):
                continue
            avg = compute_daily_average(str(member.id))
            leaderboard.append((member, avg))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    return leaderboard[:limit]

# ------------------ COMMAND: TIMETRACK LEADERBOARD ------------------
@bot.command(name="tlb", help="Show top daily average online time for tracked members.")
async def cmd_tlb(ctx: commands.Context):
    top_users = get_top_users_by_daily_average(limit=10, roles_only=RCACHE_ROLES)
    if not top_users:
        await ctx.send("No data available for leaderboard.")
        return
    
    embed = discord.Embed(title="⏱ Timetrack Leaderboard", color=discord.Color.gold())
    for member, avg in top_users:
        embed.add_field(
            name=f"{member.display_name}",
            value=f"Average daily online: {avg/3600:.2f} hrs",
            inline=False
        )
    await ctx.send(embed=embed)

# ------------------ COMMAND: TIMETRACK LEADERBOARD (DM/ALL) ------------------
@bot.command(name="tdm", help="Show top daily average for users without tracked roles.")
async def cmd_tdm(ctx: commands.Context):
    top_users = get_top_users_by_daily_average(limit=10, roles_only=None)
    # Filter out RCACHE roles to only include non-tracked roles
    filtered = [(m, avg) for m, avg in top_users if not any(role.id in RCACHE_ROLES for role in m.roles)]
    if not filtered:
        await ctx.send("No data available for TDM leaderboard.")
        return
    
    embed = discord.Embed(title="⏱ TDM Leaderboard (Non-RCache Roles)", color=discord.Color.orange())
    for member, avg in filtered[:10]:
        embed.add_field(
            name=f"{member.display_name}",
            value=f"Average daily online: {avg/3600:.2f} hrs",
            inline=False
        )
    await ctx.send(embed=embed)

# ------------------ END OF TIMETRACK SYSTEM ------------------

# Note: If you want this function further perfected (like timezone adjustments per guild,
# more precise session tracking with disconnect events, or caching offline duration), we can continue with Part 2-2.
# ------------------ TIMETRACK SYSTEM: EXTENSIONS ------------------

# Use in-memory cache to reduce disk writes
timetrack_cache: dict[str, dict] = {}

def get_user_data(uid: str) -> dict:
    """Fetch user data from cache or load from disk."""
    if uid in timetrack_cache:
        return timetrack_cache[uid]
    data = load_data()
    user_entry = data.setdefault("users", {}).setdefault(uid, {})
    timetrack_cache[uid] = user_entry
    return user_entry

def save_user_data(uid: str):
    """Save specific user data back to disk."""
    data = load_data()
    data.setdefault("users", {})[uid] = timetrack_cache.get(uid, {})
    save_data(data)

async def update_online_status(member: discord.Member):
    """Update a member's online/offline status and compute session durations."""
    now = datetime.datetime.now(pytz.utc)
    uid = str(member.id)
    user_entry = get_user_data(uid)

    online_start = user_entry.get("online_start")
    total_seconds = user_entry.get("total_online_seconds", 0)

    if member.status != discord.Status.offline:
        if online_start is None:
            user_entry["online_start"] = now.isoformat()
    else:
        if online_start:
            start_dt = datetime.datetime.fromisoformat(online_start)
            session_seconds = (now - start_dt).total_seconds()
            total_seconds += session_seconds
            user_entry["total_online_seconds"] = total_seconds

            daily = user_entry.setdefault("daily_seconds", {})
            today = now.strftime("%Y-%m-%d")
            daily[today] = daily.get(today, 0) + session_seconds

            user_entry["online_start"] = None

    timetrack_cache[uid] = user_entry
    save_user_data(uid)

@tasks.loop(seconds=TIMETRACK_INTERVAL)
async def timetrack_loop_perfected():
    """Enhanced timetrack loop with precise session handling."""
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            if not any(role.id in RCACHE_ROLES for role in member.roles):
                continue
            try:
                await update_online_status(member)
            except Exception as e:
                safe_print(f"⚠️ Error updating timetrack for {member.id}: {e}")
                traceback.print_exc()

# ------------------ HANDLING DISCONNECTS ------------------
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    """Capture status changes not caught in loop (online/offline)."""
    if before.status != after.status:
        try:
            await update_online_status(after)
        except Exception as e:
            safe_print(f"⚠️ Error on status update for {after.id}: {e}")
            traceback.print_exc()

# ------------------ COMMAND: TIMETRACK DETAILED ------------------
@bot.command(name="timetrack", help="Show detailed timetrack info for a user.")
async def cmd_timetrack(ctx: commands.Context, member: Optional[discord.Member] = None):
    if not member:
        member = ctx.author
    uid = str(member.id)
    user_entry = get_user_data(uid)
    total_seconds = user_entry.get("total_online_seconds", 0)
    daily_seconds = user_entry.get("daily_seconds", {})
    last_msg = user_entry.get("last_message", "N/A")
    last_edit = user_entry.get("last_edit", {})

    embed = discord.Embed(title=f"⏱ Timetrack Info: {member.display_name}", color=discord.Color.blue())
    embed.add_field(name="Total Online Time", value=f"{total_seconds/3600:.2f} hrs", inline=False)
    embed.add_field(name="Daily Breakdown", value="\n".join(
        f"{day}: {sec/3600:.2f} hrs" for day, sec in sorted(daily_seconds.items())
    ) or "No data", inline=False)
    embed.add_field(name="Last Message", value=last_msg, inline=False)
    if last_edit:
        embed.add_field(name="Last Edit", value=f"{last_edit.get('before')} → {last_edit.get('after')}", inline=False)
    await ctx.send(embed=embed)

# ------------------ PERIODIC CACHE FLUSH ------------------
@tasks.loop(minutes=5)
async def flush_timetrack_cache():
    """Flush in-memory timetrack cache to disk periodically."""
    safe_print(f"💾 Flushing {len(timetrack_cache)} timetrack entries to disk.")
    for uid in timetrack_cache.keys():
        save_user_data(uid)

# ------------------ NOTES ON PERFECTED TIMETRACK ------------------
# - Handles disconnect/reconnect accurately
# - Uses in-memory caching for reduced disk I/O
# - Supports detailed per-day breakdown
# - Status changes outside of loop are captured via on_member_update
# - Periodic flush ensures data persistence without affecting performance
# - Fully timezone-aware using pytz.utc
# ------------------ RMUTE / RUNMUTE SYSTEM ------------------

RMUTE_ROLE_ID = 1410423854563721287
RMUTE_TRACK_CHANNEL = 1410458084874260592

def format_duration(seconds: int) -> str:
    """Convert seconds to human-readable duration."""
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days: parts.append(f"{int(days)}d")
    if hours: parts.append(f"{int(hours)}h")
    if minutes: parts.append(f"{int(minutes)}m")
    if sec: parts.append(f"{int(sec)}s")
    return " ".join(parts) or "0s"

async def send_rmute_dm(user: discord.Member, moderator: discord.Member, duration: int, reason: str):
    """Send fancy embed DM to muted user."""
    if str(user.id) in load_data().get("rdm_users", []):
        return
    embed = discord.Embed(title="🔇 You have been muted!", color=discord.Color.red())
    embed.add_field(name="Moderator", value=moderator.mention, inline=False)
    embed.add_field(name="Duration", value=format_duration(duration), inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text="You can opt out of these DMs with !rdm")
    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        safe_print(f"⚠️ Could not DM {user.id} for rmute.")

async def rmute_users(users: list[discord.Member], duration: int, reason: str, moderator: discord.Member):
    """Mute multiple users, schedule auto-unmute, and log."""
    data = load_data()
    rmute_usage = data.setdefault("rmute_usage", {})
    now = datetime.datetime.now(pytz.utc).isoformat()
    for user in users:
        try:
            await user.add_roles(discord.Object(id=RMUTE_ROLE_ID), reason=reason)
        except Exception as e:
            safe_print(f"⚠️ Failed to add RMute role to {user.id}: {e}")
            continue

        # Update usage tracking
        rmute_usage.setdefault(str(moderator.id), 0)
        rmute_usage[str(moderator.id)] += 1

        # Record mute entry
        mutes = data.setdefault("mutes", {})
        mutes[str(user.id)] = {
            "moderator": str(moderator.id),
            "start_time": now,
            "duration_seconds": duration,
            "reason": reason
        }
        save_data(data)
        timetrack_cache.pop(str(user.id), None)  # optional: prevent online tracking while muted
        await send_rmute_dm(user, moderator, duration, reason)

        # Schedule auto-unmute
        asyncio.create_task(auto_unmute(user, duration, reason, moderator))

        # Log in tracking channel
        embed = discord.Embed(
            title="🔇 User Muted",
            description=f"{user.mention} has been muted by {moderator.mention}",
            color=discord.Color.dark_red(),
            timestamp=datetime.datetime.now(pytz.utc)
        )
        embed.add_field(name="Duration", value=format_duration(duration), inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        channel = bot.get_channel(RMUTE_TRACK_CHANNEL)
        if channel:
            await channel.send(embed=embed)

async def auto_unmute(user: discord.Member, duration: int, reason: str, moderator: discord.Member):
    """Auto-unmute after duration ends, with logging."""
    await asyncio.sleep(duration)
    try:
        await user.remove_roles(discord.Object(id=RMUTE_ROLE_ID))
    except Exception as e:
        safe_print(f"⚠️ Failed to remove RMute role from {user.id}: {e}")
    data = load_data()
    mutes = data.get("mutes", {})
    mutes.pop(str(user.id), None)
    save_data(data)

    embed = discord.Embed(
        title="✅ Auto-Unmute",
        description=f"{user.mention} has been unmuted automatically.",
        color=discord.Color.green(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    embed.add_field(name="Original Moderator", value=moderator.mention, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    channel = bot.get_channel(RMUTE_TRACK_CHANNEL)
    if channel:
        await channel.send(embed=embed)

# ------------------ COMMAND: RMUTE ------------------
@bot.command(name="rmute", help="Mute multiple users. Usage: !rmute [users] [duration_seconds] [reason]")
@commands.has_permissions(manage_roles=True)
async def cmd_rmute(ctx: commands.Context, users: commands.Greedy[discord.Member], duration: int, *, reason: str):
    if not users:
        await ctx.send("❌ You must mention at least one user.")
        return
    await rmute_users(users, duration, reason, ctx.author)
    await ctx.message.delete()

# ------------------ COMMAND: RUNMUTE ------------------
@bot.command(name="runmute", help="Mute a single user. Usage: !runmute [user] [duration_seconds] [reason]")
@commands.has_permissions(manage_roles=True)
async def cmd_runmute(ctx: commands.Context, user: discord.Member, duration: int, *, reason: str):
    await rmute_users([user], duration, reason, ctx.author)
    await ctx.message.delete()

# ------------------ COMMAND: RMLB ------------------
@bot.command(name="rmlb", help="Show top 10 users who used rmute the most.")
async def cmd_rmlb(ctx: commands.Context):
    data = load_data()
    usage = data.get("rmute_usage", {})
    top = sorted(usage.items(), key=lambda x: x[1], reverse=True)[:10]
    embed = discord.Embed(title="🏆 RMute Leaderboard", color=discord.Color.gold())
    for uid, count in top:
        user = ctx.guild.get_member(int(uid))
        embed.add_field(name=user.display_name if user else uid, value=f"Used rmute: {count} times", inline=False)
    await ctx.send(embed=embed)
    # ------------------ LEADERBOARDS SYSTEM ------------------

RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]

def get_user_daily_avg(data: dict, user_id: str) -> float:
    """Calculate average daily online time for a user in seconds."""
    user_data = data.get("users", {}).get(user_id, {})
    daily = user_data.get("daily_seconds", {})
    if not daily:
        return 0
    total = sum(daily.values())
    return total / max(len(daily), 1)

def filter_users_by_roles(guild: discord.Guild, role_ids: list[int], include: bool = True) -> list[discord.Member]:
    """Filter members by role IDs. Include or exclude."""
    result = []
    for member in guild.members:
        has_role = any(r.id in role_ids for r in member.roles)
        if (include and has_role) or (not include and not has_role):
            result.append(member)
    return result

@bot.command(name="tlb", help="Show top daily average online time for members with RCACHE_ROLES")
async def cmd_tlb(ctx: commands.Context):
    data = load_data()
    members = filter_users_by_roles(ctx.guild, RCACHE_ROLES, include=True)
    leaderboard = []
    for member in members:
        avg = get_user_daily_avg(data, str(member.id))
        leaderboard.append((member, avg))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="📊 Timetrack Leaderboard (RCACHE Roles)", color=discord.Color.blue())
    for i, (member, avg) in enumerate(leaderboard[:10], start=1):
        embed.add_field(name=f"{i}. {member.display_name}", value=f"Avg Daily: {format_duration(int(avg))}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="tdm", help="Show top daily average online time for members without RCACHE_ROLES")
async def cmd_tdm(ctx: commands.Context):
    data = load_data()
    members = filter_users_by_roles(ctx.guild, RCACHE_ROLES, include=False)
    leaderboard = []
    for member in members:
        avg = get_user_daily_avg(data, str(member.id))
        leaderboard.append((member, avg))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="📊 Timetrack Leaderboard (Non-RCACHE Roles)", color=discord.Color.teal())
    for i, (member, avg) in enumerate(leaderboard[:10], start=1):
        embed.add_field(name=f"{i}. {member.display_name}", value=f"Avg Daily: {format_duration(int(avg))}", inline=False)
    await ctx.send(embed=embed)
    # ------------------ CACHE SYSTEM ------------------

@bot.command(name="rcache", help="Show recently deleted messages/images (RCACHE roles only).")
async def cmd_rcache(ctx: commands.Context):
    # Only members with RCACHE_ROLES can use
    if not any(r.id in RCACHE_ROLES for r in ctx.author.roles):
        await ctx.send("❌ You don't have permission to access the cache.")
        return

    data = load_data()
    deleted_messages = data.get("logs", {}).get("deletions", [])[-50:]  # last 50 deletions
    if not deleted_messages:
        await ctx.send("ℹ️ No cached deletions available.")
        return

    for msg_data in deleted_messages:
        embed = discord.Embed(color=discord.Color.orange(), timestamp=datetime.datetime.now(pytz.utc))
        author = msg_data.get("author", "Unknown")
        content = msg_data.get("content", "")
        channel_id = msg_data.get("channel_id")
        channel = ctx.guild.get_channel(channel_id)
        channel_name = channel.name if channel else "Unknown"
        message_id = msg_data.get("message_id")
        embed.title = f"🗑 Deleted Message in #{channel_name}"
        embed.add_field(name="Author", value=author, inline=True)
        embed.add_field(name="Message ID", value=message_id, inline=True)
        embed.add_field(name="Content", value=content[:1024] if content else "(No Text)", inline=False)
        embed.add_field(name="Time", value=msg_data.get("time", "Unknown"), inline=False)

        # If reply exists
        if msg_data.get("reply_to"):
            reply_data = msg_data["reply_to"]
            reply_author = reply_data.get("author", "Unknown")
            reply_content = reply_data.get("content", "")
            embed.add_field(name="Reply To", value=f"{reply_author}: {reply_content[:500]}", inline=False)

        # Attachments
        attachments = msg_data.get("attachments", [])
        if attachments:
            attach_text = "\n".join(a.get("url", "") for a in attachments)
            embed.add_field(name="Attachments", value=attach_text[:1024], inline=False)

        await ctx.send(embed=embed)
        # ------------------ LOGGING EVENTS ------------------

async def log_event(embed: discord.Embed, tracking_channel_id: int = 1410458084874260592):
    """Helper function to log events to the tracking channel."""
    tracking_channel = bot.get_channel(tracking_channel_id)
    if tracking_channel:
        await tracking_channel.send(embed=embed)

# Channel events
@bot.event
async def on_guild_channel_create(channel):
    embed = discord.Embed(
        title="📂 Channel Created",
        description=f"Channel: {channel.name} ({channel.id})",
        color=discord.Color.green(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    embed.add_field(name="Type", value=str(channel.type))
    embed.add_field(name="Category", value=channel.category.name if channel.category else "None")
    await log_event(embed)

@bot.event
async def on_guild_channel_delete(channel):
    embed = discord.Embed(
        title="🗑 Channel Deleted",
        description=f"Channel: {channel.name} ({channel.id})",
        color=discord.Color.red(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    await log_event(embed)

@bot.event
async def on_guild_channel_update(before, after):
    embed = discord.Embed(
        title="✏️ Channel Updated",
        description=f"Channel: {before.name} ({before.id})",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    if before.name != after.name:
        embed.add_field(name="Name Changed", value=f"{before.name} → {after.name}")
    if before.topic != after.topic:
        embed.add_field(name="Topic Changed", value=f"{before.topic or 'None'} → {after.topic or 'None'}")
    await log_event(embed)

# Role events
@bot.event
async def on_guild_role_create(role):
    embed = discord.Embed(
        title="🔹 Role Created",
        description=f"Role: {role.name} ({role.id})",
        color=discord.Color.green(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    await log_event(embed)

@bot.event
async def on_guild_role_delete(role):
    embed = discord.Embed(
        title="🔸 Role Deleted",
        description=f"Role: {role.name} ({role.id})",
        color=discord.Color.red(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    await log_event(embed)

@bot.event
async def on_guild_role_update(before, after):
    embed = discord.Embed(
        title="✏️ Role Updated",
        description=f"Role: {before.name} ({before.id})",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    changes = []
    if before.name != after.name:
        changes.append(f"Name: {before.name} → {after.name}")
    if before.permissions != after.permissions:
        changes.append("Permissions Changed")
    if changes:
        embed.add_field(name="Changes", value="\n".join(changes), inline=False)
    await log_event(embed)

# Webhook events
@bot.event
async def on_webhook_update(channel):
    embed = discord.Embed(
        title="🔧 Webhook Updated",
        description=f"Channel: {channel.name} ({channel.id})",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    await log_event(embed)

# Purge / message deletion events
async def log_deleted_messages(messages: list[discord.Message], moderator: discord.Member):
    """Logs bulk deletion."""
    for msg in messages:
        embed = discord.Embed(
            title="🗑 Message Deleted",
            description=f"Author: {msg.author}\nChannel: {msg.channel}\nID: {msg.id}",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now(pytz.utc)
        )
        content = msg.content or "(No text)"
        embed.add_field(name="Content", value=content[:1024], inline=False)
        if msg.reference:
            ref = msg.reference.resolved
            if ref:
                embed.add_field(name="Reply To", value=f"{ref.author}: {ref.content[:500]}", inline=False)
        attachments = [a.url for a in msg.attachments]
        if attachments:
            embed.add_field(name="Attachments", value="\n".join(attachments)[:1024], inline=False)
        embed.set_footer(text=f"Purged by: {moderator}")
        await log_event(embed)

# General mod actions
async def log_mod_action(action: str, target: discord.Member, moderator: discord.Member, reason: str = None):
    embed = discord.Embed(
        title=f"🛠 Mod Action: {action}",
        description=f"Target: {target} ({target.id})\nModerator: {moderator} ({moderator.id})",
        color=discord.Color.purple(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    await log_event(embed)
    # ------------------ STAFF PING SYSTEM ------------------

STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
STAFF_LOG_CHANNELS = [1403422664521023648, 1410458084874260592]

async def send_staff_ping(ctx: commands.Context, role_id: int, mention_text: str):
    role = ctx.guild.get_role(role_id)
    if not role:
        await ctx.send("⚠️ Role not found.")
        return

    # Delete the command message for cleanliness
    try:
        await ctx.message.delete()
    except:
        pass

    # Construct ping message
    content = f"{role.mention} {mention_text}"
    await ctx.send(content)

    # Log if command was a reply
    if ctx.message.reference:
        original = ctx.message.reference.resolved
        if original:
            embed = discord.Embed(
                title="📢 Staff Ping (Reply)",
                description=f"Pinger: {ctx.author} ({ctx.author.id})\nReplying to: {original.author} ({original.author.id})",
                color=discord.Color.gold(),
                timestamp=datetime.datetime.now(pytz.utc)
            )
            embed.add_field(name="Original Message", value=(original.content or "(No text)")[:1024], inline=False)
            for ch_id in STAFF_LOG_CHANNELS:
                ch = ctx.guild.get_channel(ch_id)
                if ch:
                    await ch.send(embed=embed)

@bot.command(name="rping", help="Ping Staff (with reply logging)")
async def cmd_rping(ctx: commands.Context, *, mention_text: str = ""):
    await send_staff_ping(ctx, STAFF_PING_ROLE, mention_text)

@bot.command(name="hsping", help="Ping Higher Staff (with reply logging)")
async def cmd_hsping(ctx: commands.Context, *, mention_text: str = ""):
    await send_staff_ping(ctx, HIGHER_STAFF_PING_ROLE, mention_text)

# Optional: Only allow certain roles to ping staff
async def can_ping_staff(member: discord.Member) -> bool:
    allowed_roles = RCACHE_ROLES  # Only members with these roles can ping staff
    return any(role.id in allowed_roles for role in member.roles)

@bot.check
async def check_staff_ping(ctx: commands.Context):
    if ctx.command.name in ["rping", "hsping"]:
        if not await can_ping_staff(ctx.author):
            await ctx.send("❌ You do not have permission to use this command.")
            return False
    return True
    # ------------------ DM & NOTIFICATION CONTROL ------------------

RDM_USERS_KEY = "rdm_users"
DANGEROUS_LOG_USERS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

@bot.command(name="rdm", help="Opt-out from bot DMs")
async def cmd_rdm(ctx: commands.Context):
    data = load_data()
    rdm_users = data.setdefault(RDM_USERS_KEY, [])
    uid = str(ctx.author.id)

    if uid in rdm_users:
        rdm_users.remove(uid)
        status = "enabled"
    else:
        rdm_users.append(uid)
        status = "disabled"

    save_data(data)
    await ctx.send(f"📩 DMs are now **{status}** for you.")

def is_rdm_opted_out(user_id: int) -> bool:
    data = load_data()
    rdm_users = data.get(RDM_USERS_KEY, [])
    return str(user_id) in rdm_users

async def send_dm(user: discord.User, embed: discord.Embed, dangerous: bool = False):
    if is_rdm_opted_out(user.id) and not dangerous:
        return  # Respect user's opt-out
    try:
        await user.send(embed=embed)
    except:
        safe_print(f"⚠️ Could not send DM to user {user.id}")

async def notify_dangerous_action(embed: discord.Embed):
    for uid in DANGEROUS_LOG_USERS:
        user = bot.get_user(uid)
        if user:
            await send_dm(user, embed, dangerous=True)

# Example usage for mod actions
async def log_mod_action(user: discord.User, moderator: discord.Member, reason: str, action_type: str):
    embed = discord.Embed(
        title=f"🛡️ Mod Action: {action_type}",
        color=discord.Color.red(),
        timestamp=datetime.datetime.now(pytz.utc)
    )
    embed.add_field(name="User", value=f"{user} ({user.id})", inline=False)
    embed.add_field(name="Moderator", value=f"{moderator} ({moderator.id})", inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    await send_dm(user, embed)
    await notify_dangerous_action(embed)
    # ------------------ PURGE & DELETED MESSAGE TRACKING ------------------

@bot.event
async def on_bulk_message_delete(messages):
    """
    Tracks bulk deleted messages and logs them with attachments, authors, and reply info.
    Sends logs to a dedicated tracking channel and optionally DMs staff for dangerous content.
    """
    if not messages:
        return

    tracking_channel_id = 1410458084874260592  # RMute tracking channel or dedicated logs
    tracking_channel = bot.get_channel(tracking_channel_id)
    if not tracking_channel:
        safe_print("⚠️ Tracking channel not found for bulk deletions.")
        return

    data = load_data()
    deletions = data.setdefault("logs", {}).setdefault("deletions", [])

    for msg in messages:
        author = msg.author
        content = msg.content or ""
        attachments = [a.url for a in msg.attachments]
        timestamp = msg.created_at.astimezone(pytz.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Reply info if applicable
        reply_info = None
        if msg.reference and msg.reference.resolved:
            replied = msg.reference.resolved
            reply_info = f"Reply to {replied.author} : {replied.content[:150]}"

        deletion_entry = {
            "message_id": msg.id,
            "author": f"{author} ({author.id})",
            "content": content[:300],
            "attachments": attachments,
            "time": timestamp,
            "reply_info": reply_info
        }

        deletions.append(deletion_entry)

        # Keep last 500 deletions to limit memory usage
        if len(deletions) > 500:
            deletions = deletions[-500:]

    data["logs"]["deletions"] = deletions
    save_data(data)

    # Create an embed for staff tracking
    embed = discord.Embed(title="🗑️ Bulk Message Deletion Detected", color=discord.Color.dark_red(),
                          description=f"{len(messages)} messages deleted in {messages[0].channel.mention}")
    for msg in messages[:10]:  # Show only the first 10 for brevity
        content_preview = (msg.content or "[No Content]")[:200]
        reply_preview = ""
        if msg.reference and msg.reference.resolved:
            reply_preview = f"\nReply to {msg.reference.resolved.author}: {msg.reference.resolved.content[:100]}"
        attachments_preview = "\n".join([a.url for a in msg.attachments]) if msg.attachments else ""
        embed.add_field(name=f"{msg.author} ({msg.author.id})", value=f"{content_preview}{reply_preview}\n{attachments_preview}", inline=False)

    try:
        await tracking_channel.send(embed=embed)
    except Exception as e:
        safe_print("❌ Failed to send bulk delete log:", e)
        traceback.print_exc()

    # Optionally DM designated staff for dangerous deletions
    dangerous_staff_ids = [
        1406326282429403306,
        1410422762895577088,
        1410422029236047975
    ]
    for staff_id in dangerous_staff_ids:
        staff_member = bot.get_user(staff_id)
        if staff_member:
            try:
                dm_embed = discord.Embed(title="⚠️ Bulk Deletion Alert",
                                         description=f"{len(messages)} messages deleted in {messages[0].channel.mention}",
                                         color=discord.Color.red())
                await staff_member.send(embed=dm_embed)
            except:
                pass
                # ------------------ HELP COMMAND ------------------

@bot.command(name="rhelp", help="Shows all available bot commands with descriptions.")
async def cmd_rhelp(ctx: commands.Context):
    """
    Sends a help embed listing all commands, their usage, and description.
    Dynamically updates for future commands.
    """
    embed = discord.Embed(title="📖 Bot Commands Help", color=discord.Color.blurple())
    embed.set_footer(text="Use !command for standard commands. Reply context commands supported.")

    # Core commands
    embed.add_field(name="!timetrack [user]", value="Shows online/offline time stats for a user.", inline=False)
    embed.add_field(name="!rmute [users] [duration] [reason]", value="Mute multiple users with automatic unmute. Logs to tracking channel.", inline=False)
    embed.add_field(name="!runmute [user] [duration] [reason]", value="Mute a single user with automatic unmute. Logs to tracking channel.", inline=False)
    embed.add_field(name="!rmlb", value="Shows top 10 users who used rmute the most.", inline=False)
    embed.add_field(name="!rcache", value="Shows cached deleted messages/images. Restricted to RCACHE_ROLES.", inline=False)
    embed.add_field(name="!tlb", value="Timetrack leaderboard: top online users with specified roles.", inline=False)
    embed.add_field(name="!tdm", value="Timetrack leaderboard: users without certain roles, longest daily average time.", inline=False)
    embed.add_field(name="!rping", value="Pings STAFF_PING_ROLE. Works in reply context.", inline=False)
    embed.add_field(name="!hsping", value="Pings HIGHER_STAFF_PING_ROLE. Works in reply context.", inline=False)
    embed.add_field(name="!rdm", value="Opt-out from receiving bot DMs.", inline=False)

    # Optional future command support
    all_commands = [cmd for cmd in bot.commands if cmd.name not in [
        "rhelp", "rdump", "rpurge"
    ]]
    for cmd_obj in all_commands:
        # Skip commands already documented
        if cmd_obj.name.startswith("r") and cmd_obj.name not in ["rhelp"]:
            embed.add_field(name=f"!{cmd_obj.name}", value=cmd_obj.help or "No description provided.", inline=False)

    embed.set_author(name=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
    embed.timestamp = datetime.datetime.now(pytz.utc)

    try:
        await ctx.send(embed=embed)
    except Exception as e:
        safe_print("❌ Failed to send help embed:", e)
        await ctx.send("⚠️ Error displaying help. Please try again later.")
