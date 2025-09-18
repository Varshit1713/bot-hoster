# ultimate_fancy_bot.py
# Full single-file bot. No trimming.
# Requirements: discord.py (2.x), pytz, Flask
# Make sure DISCORD_TOKEN env var is set and privileged intents enabled.

import discord
from discord.ext import commands, tasks
import asyncio
import pytz
import datetime
import json
import os
import threading
from flask import Flask

# -------------------------
# Flask keep-alive (optional)
# -------------------------
app = Flask("keep_alive")
@app.route("/")
def home():
    return "Bot is running."

def run_flask():
    app.run(host="0.0.0.0", port=8080)

# Start Flask in background thread (harmless if you don't use it)
threading.Thread(target=run_flask, daemon=True).start()

# -------------------------
# Bot setup & constants
# -------------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN environment variable not set.")

# Intents - needs to be enabled in Developer Portal for presence and members
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True
intents.guilds = True
intents.messages = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# IDs (kept as constants per your request)
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE_ID = 1410423854563721287
TRACKING_CHANNEL_ID = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
# Channels to post staff ping logs to (as requested)
STAFF_PING_LOG_CHANNELS = [1403422664521023648, TRACKING_CHANNEL_ID]
# Users to force DM for dangerous actions
DANGEROUS_NOTIFY_IDS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

DATA_FILE = "bot_data.json"
COOLDOWN_TIME = 5  # seconds default

# -------------------------
# Persistent data helpers
# -------------------------
DEFAULT_DATA = {
    "users": {},            # keyed by user id str
    "mutes": {},            # active mutes keyed by user id str
    "rmute_usage": {},      # moderator id -> count
    "cached_messages": {},  # message id -> details
    "rdm_users": [],        # list of user id strings who opted out
    "logs": {},             # misc logs like edits
    "cooldowns": {}         # per-user cooldown timestamps
}

def ensure_data_exists():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_DATA, f, indent=4)
    else:
        # if file exists but missing keys, fill them
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                d = json.load(f)
            except Exception:
                d = {}
        changed = False
        for k, v in DEFAULT_DATA.items():
            if k not in d:
                d[k] = v
                changed = True
        if changed:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=4)

ensure_data_exists()

def load_data():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(d=None):
    if d is None:
        d = data
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=4)

# load global data
data = load_data()

# small wrapper to safely update and save
def update_and_save(path_list, value):
    """Set nested value in data by path_list and save."""
    node = data
    for key in path_list[:-1]:
        node = node.setdefault(key, {})
    node[path_list[-1]] = value
    save_data()

# -------------------------
# Utilities
# -------------------------
def tz_now():
    return datetime.datetime.now(pytz.utc)

def format_dt(ts=None):
    if ts is None:
        ts = tz_now()
    if isinstance(ts, (int, float)):
        # convert epoch
        dt = datetime.datetime.fromtimestamp(ts, pytz.utc)
    else:
        dt = ts
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")

def format_seconds(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    parts.append(f"{sec}s")
    return " ".join(parts)

def parse_duration(s):
    """
    Accepts formats like:
    30s  (seconds)
    10m  (minutes)
    2h   (hours)
    1d   (days)
    If last char is digit -> interpret as seconds
    """
    s = s.strip().lower()
    if not s:
        raise ValueError("Empty duration")
    unit = s[-1]
    num = s[:-1] if not unit.isdigit() else s
    try:
        val = int(num)
    except:
        raise ValueError("Invalid duration number")
    if unit == "s":
        return val
    if unit == "m":
        return val * 60
    if unit == "h":
        return val * 3600
    if unit == "d":
        return val * 86400
    if unit.isdigit():
        return int(s)
    raise ValueError("Unknown duration unit; use s/m/h/d")

def is_rcache_role(member: discord.Member):
    if not member:
        return False
    return any(r.id in RCACHE_ROLES for r in member.roles)

def get_avatar_url(member_or_user):
    try:
        # display_avatar works across cases
        return member_or_user.display_avatar.url
    except:
        try:
            return member_or_user.avatar.url
        except:
            return None

def check_cooldown(user_id: int, command_name: str, cooldown_seconds: int = COOLDOWN_TIME):
    now_ts = tz_now().timestamp()
    user_cd = data.get("cooldowns", {}).get(str(user_id), {})
    last = user_cd.get(command_name, 0)
    if now_ts - last < cooldown_seconds:
        return False
    data.setdefault("cooldowns", {}).setdefault(str(user_id), {})[command_name] = now_ts
    save_data(data)
    return True

async def log_to_tracking(embed: discord.Embed):
    ch = bot.get_channel(TRACKING_CHANNEL_ID)
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception:
            # last resort: print
            print("Failed to send embed to tracking channel:", TRACKING_CHANNEL_ID)

async def send_for_danger(embed: discord.Embed):
    for uid in DANGEROUS_NOTIFY_IDS:
        try:
            u = bot.get_user(uid)
            if u:
                await send_dm(u, embed, force=True)
        except Exception:
            pass

async def send_dm(user: discord.User, embed: discord.Embed, force=False):
    """Sends DM unless user opted out, except when force=True."""
    if not user:
        return
    if str(user.id) in data.get("rdm_users", []) and not force:
        return
    try:
        await user.send(embed=embed)
    except Exception:
        # DM failed (maybe DMs closed), ignore
        pass

# -------------------------
# Timetrack: presence/message handling + periodic saver
# -------------------------
# Update user's online_start on presence change or message activity.
# Store per-user:
#   last_online (timestamp)
#   last_message (content)
#   last_edit (timestamp) - updated elsewhere
#   total_online_seconds (cumulative)
#   online_start (timestamp when became online)
# data["users"][str(uid)] = { ... }

@bot.event
async def on_presence_update(before: discord.Member, after: discord.Member):
    # Only track if member has RCACHE roles
    try:
        if after.bot:
            return
        if not is_rcache_role(after):
            return
        uid = str(after.id)
        now = tz_now().timestamp()
        udata = data.get("users", {}).get(uid, {})
        # statuses that count as "online"
        was_online = before.status != discord.Status.offline if before is not None else False
        now_online = after.status != discord.Status.offline
        if not was_online and now_online:
            # went online
            udata["online_start"] = udata.get("online_start") or now
        elif was_online and not now_online:
            # went offline -> accumulate session
            started = udata.get("online_start")
            if started:
                session = now - started
                udata["total_online_seconds"] = udata.get("total_online_seconds", 0) + session
                udata["online_start"] = None
        udata["last_online"] = now
        data.setdefault("users", {})[uid] = udata
        save_data(data)
    except Exception as e:
        print("on_presence_update error:", e)

@bot.event
async def on_message(message: discord.Message):
    # Update last_message and last_online for timetrack if appropriate
    try:
        # process commands first
        await bot.process_commands(message)
    except Exception:
        # still continue to update tracking
        pass

    # ignore bot messages
    if message.author.bot:
        return

    # timetrack: update last_message and last_online
    try:
        uid = str(message.author.id)
        if is_rcache_role(message.author):
            now = tz_now().timestamp()
            udata = data.get("users", {}).get(uid, {})
            udata["last_message"] = message.content or ""
            udata["last_message_channel"] = message.channel.id
            # if not currently online_start set (presence might not have fired), set it
            if not udata.get("online_start"):
                # treat sending a message as being online
                udata["online_start"] = now
            udata["last_online"] = now
            data.setdefault("users", {})[uid] = udata
            save_data(data)
    except Exception as e:
        print("on_message timetrack error:", e)

# periodic saver and fallback to close sessions if presence missed
@tasks.loop(seconds=60)
async def timetrack_loop():
    try:
        now = tz_now().timestamp()
        changed = False
        for guild in bot.guilds:
            for member in guild.members:
                if member.bot:
                    continue
                if not is_rcache_role(member):
                    continue
                uid = str(member.id)
                udata = data.get("users", {}).get(uid, {})
                # Use presence if available; if not, rely on online_start
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
                data.setdefault("users", {})[uid] = udata
                changed = True
        if changed:
            save_data(data)
    except Exception as e:
        print("timetrack_loop error:", e)

# -------------------------
# RMute / Runmute / Auto-unmute
# -------------------------
async def schedule_auto_unmute_for(uid_str, start_ts, duration_seconds):
    """
    Schedules an auto-unmute for an already-recorded mute.
    Called on startup to resume pending unmute tasks.
    """
    now_ts = tz_now().timestamp()
    elapsed = now_ts - start_ts
    remaining = duration_seconds - elapsed
    if remaining <= 0:
        return
    try:
        # find member object
        member = None
        for guild in bot.guilds:
            m = guild.get_member(int(uid_str))
            if m:
                member = m
                break
        if member:
            # create background task
            asyncio.create_task(auto_unmute(member, int(remaining)))
    except Exception:
        pass

async def auto_unmute(member: discord.Member, duration_seconds: int):
    await asyncio.sleep(duration_seconds)
    try:
        mute_role = member.guild.get_role(MUTE_ROLE_ID)
        if mute_role and mute_role in member.roles:
            await member.remove_roles(mute_role, reason="Auto-unmute after duration")
        uid = str(member.id)
        if uid in data.get("mutes", {}):
            del data["mutes"][uid]
            save_data(data)
        embed = discord.Embed(title="🔊 User Automatically Unmuted", color=discord.Color.green(), timestamp=tz_now())
        embed.set_thumbnail(url=get_avatar_url(member) or "")
        embed.add_field(name="User", value=f"{member} ({member.id})", inline=False)
        embed.add_field(name="Time", value=format_dt(tz_now()), inline=False)
        await log_to_tracking(embed)
    except Exception as e:
        print("auto_unmute error:", e)

def moderator_check(ctx):
    # default: require manage_roles permission
    return ctx.author.guild_permissions.manage_roles

@commands.has_permissions(manage_roles=True)
@bot.command(name="rmute", help="Mute multiple users: !rmute @a @b 10m reason")
async def cmd_rmute(ctx, members: commands.Greedy[discord.Member], duration: str, *, reason: str = "No reason provided"):
    if not check_cooldown(ctx.author.id, "rmute"):
        return await ctx.send(":stopwatch: Cooldown active. Try again shortly.", delete_after=6)
    if not members:
        return await ctx.send("No users provided. Usage: `!rmute @u1 @u2 10m reason`", delete_after=8)
    try:
        seconds = parse_duration(duration)
    except Exception as e:
        return await ctx.send(f"Invalid duration: {e}", delete_after=8)
    sent = 0
    for member in members:
        try:
            mute_role = ctx.guild.get_role(MUTE_ROLE_ID)
            if not mute_role:
                await ctx.send("Mute role not found on this server.", delete_after=8)
                return
            if mute_role in member.roles:
                continue
            await member.add_roles(mute_role, reason=f"Muted by {ctx.author} via rmute: {reason}")
            uid = str(member.id)
            now_ts = tz_now().timestamp()
            data.setdefault("mutes", {})[uid] = {"moderator": ctx.author.id, "start": now_ts, "duration": seconds, "reason": reason}
            data.setdefault("rmute_usage", {}).setdefault(str(ctx.author.id), 0)
            data["rmute_usage"][str(ctx.author.id)] += 1
            save_data(data)
            # DM embed
            dm = discord.Embed(title="🔇 You have been muted", color=discord.Color.red(), timestamp=tz_now())
            if get_avatar_url(member):
                dm.set_thumbnail(url=get_avatar_url(member))
            dm.add_field(name="Moderator", value=f"{ctx.author} ({ctx.author.id})", inline=True)
            dm.add_field(name="Duration", value=format_seconds(seconds), inline=True)
            dm.add_field(name="Reason", value=reason, inline=False)
            dm.set_footer(text="Use !rdm to opt-out of bot DMs")
            await send_dm(member, dm)
            # Log embed
            log = discord.Embed(title="🔇 User Muted", color=discord.Color.red(), timestamp=tz_now())
            log.set_thumbnail(url=get_avatar_url(member) or "")
            log.add_field(name="User", value=f"{member} ({member.id})", inline=True)
            log.add_field(name="Moderator", value=f"{ctx.author} ({ctx.author.id})", inline=True)
            log.add_field(name="Duration", value=format_seconds(seconds), inline=False)
            log.add_field(name="Reason", value=reason, inline=False)
            await send_for_danger(log)
            await log_to_tracking(log)
            # schedule auto-unmute
            asyncio.create_task(auto_unmute(member, seconds))
            sent += 1
        except Exception as e:
            print("Error muting member:", e)
    try:
        await ctx.message.delete()
    except Exception:
        pass
    await ctx.send(f"Muted {sent}/{len(members)} users.", delete_after=6)

@commands.has_permissions(manage_roles=True)
@bot.command(name="runmute", help="Mute single user: !runmute @user 10m reason")
async def cmd_runmute(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    if not check_cooldown(ctx.author.id, "runmute"):
        return await ctx.send(":stopwatch: Cooldown active. Try again shortly.", delete_after=6)
    try:
        seconds = parse_duration(duration)
    except Exception as e:
        return await ctx.send(f"Invalid duration: {e}", delete_after=8)
    try:
        mute_role = ctx.guild.get_role(MUTE_ROLE_ID)
        if not mute_role:
            return await ctx.send("Mute role not found on this server.", delete_after=8)
        if mute_role in member.roles:
            return await ctx.send("User is already muted.", delete_after=8)
        await member.add_roles(mute_role, reason=f"Muted by {ctx.author} via runmute: {reason}")
        uid = str(member.id)
        now_ts = tz_now().timestamp()
        data.setdefault("mutes", {})[uid] = {"moderator": ctx.author.id, "start": now_ts, "duration": seconds, "reason": reason}
        data.setdefault("rmute_usage", {}).setdefault(str(ctx.author.id), 0)
        data["rmute_usage"][str(ctx.author.id)] += 1
        save_data(data)
        # DM & logs
        dm = discord.Embed(title="🔇 You have been muted", color=discord.Color.red(), timestamp=tz_now())
        dm.set_thumbnail(url=get_avatar_url(member) or "")
        dm.add_field(name="Moderator", value=f"{ctx.author} ({ctx.author.id})", inline=True)
        dm.add_field(name="Duration", value=format_seconds(seconds), inline=True)
        dm.add_field(name="Reason", value=reason, inline=False)
        dm.set_footer(text="Use !rdm to opt-out of bot DMs")
        await send_dm(member, dm)
        log = discord.Embed(title="🔇 User Muted", color=discord.Color.red(), timestamp=tz_now())
        log.set_thumbnail(url=get_avatar_url(member) or "")
        log.add_field(name="User", value=f"{member} ({member.id})", inline=True)
        log.add_field(name="Moderator", value=f"{ctx.author} ({ctx.author.id})", inline=True)
        log.add_field(name="Duration", value=format_seconds(seconds), inline=False)
        log.add_field(name="Reason", value=reason, inline=False)
        await send_for_danger(log)
        await log_to_tracking(log)
        asyncio.create_task(auto_unmute(member, seconds))
        try:
            await ctx.message.delete()
        except Exception:
            pass
        await ctx.send(f"Muted {member.mention} for {format_seconds(seconds)}.", delete_after=7)
    except Exception as e:
        await ctx.send(f"Failed to mute: {e}", delete_after=8)

@bot.command(name="rmlb", help="Show top RMute users (by usage)")
async def cmd_rmlb(ctx):
    if not check_cooldown(ctx.author.id, "rmlb"):
        return await ctx.send(":stopwatch: Cooldown active. Try again shortly.", delete_after=6)
    usage = data.get("rmute_usage", {})
    if not usage:
        return await ctx.send("No rmute usage data yet.")
    sorted_usage = sorted(usage.items(), key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="🏆 RMute Leaderboard", color=discord.Color.purple(), timestamp=tz_now())
    for i, (uid, cnt) in enumerate(sorted_usage[:10], start=1):
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else f"User ID {uid}"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
        embed.add_field(name=f"{medal} {name}", value=f"Mutes: {cnt}", inline=True)
    await ctx.send(embed=embed)

# -------------------------
# Leaderboards (tlb / tdm)
# -------------------------
async def build_leaderboard_embed(ctx, non_rcache=False):
    now_ts = tz_now().timestamp()
    rows = []
    for uid, udata in data.get("users", {}).items():
        try:
            member = ctx.guild.get_member(int(uid))
            if not member:
                continue
            if non_rcache and is_rcache_role(member):
                continue
            if (not non_rcache) and (not is_rcache_role(member)):
                continue
            total = udata.get("total_online_seconds", 0)
            if udata.get("online_start"):
                total += now_ts - udata["online_start"]
            rows.append((total, member))
        except Exception:
            continue
    rows.sort(key=lambda x: x[0], reverse=True)
    title = "🏆 Timetrack Leaderboard" if not non_rcache else "🏆 Timetrack (Non-RCache)"
    color = discord.Color.blue() if not non_rcache else discord.Color.orange()
    embed = discord.Embed(title=title, color=color, timestamp=tz_now())
    for i, (total, member) in enumerate(rows[:10], start=1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"#{i}"
        name = member.display_name
        avatar = get_avatar_url(member)
        field = f"{format_seconds(total)}\nID: {member.id}"
        embed.add_field(name=f"{medal} {name}", value=field, inline=True)
        # set a thumbnail to the top user's avatar (first only)
        if i == 1 and avatar:
            embed.set_thumbnail(url=avatar)
    return embed

@bot.command(name="tlb", help="Top online users (RCACHE_ROLES only)")
async def cmd_tlb(ctx):
    if not check_cooldown(ctx.author.id, "tlb"):
        return await ctx.send(":stopwatch: Cooldown active. Try again shortly.", delete_after=6)
    embed = await build_leaderboard_embed(ctx, non_rcache=False)
    await ctx.send(embed=embed)

@bot.command(name="tdm", help="Top users without RCACHE_ROLES")
async def cmd_tdm(ctx):
    if not check_cooldown(ctx.author.id, "tdm"):
        return await ctx.send(":stopwatch: Cooldown active. Try again shortly.", delete_after=6)
    embed = await build_leaderboard_embed(ctx, non_rcache=True)
    await ctx.send(embed=embed)

# -------------------------
# Cache system: rcache
# -------------------------
@bot.command(name="rcache", help="Show cached deleted messages (RCACHE_ROLES only)")
async def cmd_rcache(ctx, limit: int = 20):
    if not is_rcache_role(ctx.author):
        return await ctx.send("❌ You don't have permission to use this (RCACHE_ROLES only).", delete_after=8)
    cached = data.get("cached_messages", {})
    if not cached:
        return await ctx.send("No cached deleted messages.")
    # list most recent first
    items = sorted(cached.items(), key=lambda x: x[1].get("timestamp", 0), reverse=True)[:limit]
    # send embeds in pages if many
    for mid, cdata in items:
        embed = discord.Embed(title="🗂 Deleted Message", color=discord.Color.dark_grey(), timestamp=datetime.datetime.fromtimestamp(cdata.get("timestamp", tz_now().timestamp()), pytz.utc))
        author_name = cdata.get("author_name", "Unknown")
        author_id = cdata.get("author_id", "Unknown")
        embed.add_field(name="Author", value=f"{author_name} ({author_id})", inline=True)
        channel_id = cdata.get("channel")
        if channel_id:
            embed.add_field(name="Channel ID", value=str(channel_id), inline=True)
        content = cdata.get("content", "")
        if content:
            preview = content if len(content) < 1000 else content[:997] + "..."
            embed.add_field(name="Content", value=preview, inline=False)
        if cdata.get("attachments"):
            embed.add_field(name="Attachments", value="\n".join(cdata.get("attachments")), inline=False)
        if cdata.get("deleted_by_name") or cdata.get("deleted_by"):
            embed.add_field(name="Deleted by", value=f"{cdata.get('deleted_by_name') or cdata.get('deleted_by')}", inline=True)
        if cdata.get("reply_to"):
            reply = cdata.get("reply_to")
            reply_content = reply.get("content") or ""
            embed.add_field(name="Reply ->", value=f"{reply.get('author_id') or ''}: {reply_content}", inline=False)
        if cdata.get("jump_url"):
            embed.set_footer(text="Jump link included")
            embed.add_field(name="Jump URL", value=cdata.get("jump_url"), inline=False)
        await ctx.send(embed=embed)

# -------------------------
# Staff ping commands
# -------------------------
async def send_staff_ping(ctx, role_id, title):
    if not check_cooldown(ctx.author.id, title):
        return await ctx.send(":stopwatch: Cooldown active. Try again shortly.", delete_after=6)
    role = ctx.guild.get_role(role_id)
    if not role:
        return await ctx.send("Role not found.", delete_after=8)
    embed = discord.Embed(title=title, color=discord.Color.gold(), timestamp=tz_now())
    embed.set_thumbnail(url=get_avatar_url(ctx.author) or "")
    embed.add_field(name="Pinged Role", value=role.mention, inline=False)
    if ctx.message.reference:
        try:
            ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            snippet = (ref.content[:800] + "...") if ref.content and len(ref.content) > 800 else (ref.content or "[embed/attachment]")
            embed.add_field(name="In reply to", value=f"{ref.author.display_name}: {snippet}", inline=False)
        except Exception:
            pass
    # send ping to channel (with role mention) and also log to staff log channels
    try:
        # Send a normal message tagging the role, then delete the invoking message
        msg = await ctx.send(f"{role.mention}", embed=embed)
    except Exception:
        # fallback send embed only
        await ctx.send(embed=embed)
    # send to log channels as well
    for chid in STAFF_PING_LOG_CHANNELS:
        ch = bot.get_channel(chid)
        if ch:
            try:
                await ch.send(embed=embed)
            except Exception:
                pass
    try:
        await ctx.message.delete()
    except Exception:
        pass

@bot.command(name="rping", help="Pings staff role")
async def cmd_rping(ctx):
    await send_staff_ping(ctx, STAFF_PING_ROLE, "📣 Staff Ping")

@bot.command(name="hsping", help="Pings higher staff role")
async def cmd_hsping(ctx):
    await send_staff_ping(ctx, HIGHER_STAFF_PING_ROLE, "📣 Higher Staff Ping")

# -------------------------
# DM opt-out: rdm
# -------------------------
@bot.command(name="rdm", help="Toggle opt-out/in from bot DMs")
async def cmd_rdm(ctx):
    uid = str(ctx.author.id)
    if uid in data.get("rdm_users", []):
        data["rdm_users"].remove(uid)
        save_data(data)
        embed = discord.Embed(title="✅ You will now receive bot DMs", color=discord.Color.green(), timestamp=tz_now())
        await ctx.send(embed=embed)
    else:
        data.setdefault("rdm_users", []).append(uid)
        save_data(data)
        embed = discord.Embed(title="🚫 You have opted out from bot DMs", color=discord.Color.red(), timestamp=tz_now())
        embed.set_footer(text="Dangerous actions may still DM you")
        await ctx.send(embed=embed)

# -------------------------
# Help command (fancy)
# -------------------------
@bot.command(name="rhelp", help="Show bot commands")
async def cmd_rhelp(ctx):
    embed = discord.Embed(title="📖 Bot Commands", color=discord.Color.blurple(), timestamp=tz_now())
    embed.set_thumbnail(url=get_avatar_url(bot.user) or "")
    cmds = {
        "!timetrack [user]": "Show online stats (if implemented)",
        "!rmute [users] [duration] [reason]": "Mute multiple users (requires Manage Roles)",
        "!runmute [user] [duration] [reason]": "Mute single user",
        "!rmlb": "Top RMute users",
        "!rcache": "Deleted messages cache (RCACHE_ROLES only)",
        "!tlb": "Top online users (RCACHE_ROLES only)",
        "!tdm": "Top users without RCACHE_ROLES",
        "!rping": "Ping staff",
        "!hsping": "Ping higher staff",
        "!rdm": "Toggle opt-out/opt-in bot DMs"
    }
    for k, v in cmds.items():
        embed.add_field(name=k, value=v, inline=False)
    embed.set_footer(text="All moderation actions are logged to tracking channel.")
    await ctx.send(embed=embed)

# -------------------------
# Message delete/edit events: caching and logs
# -------------------------
async def guess_deleter(message: discord.Message, lookback_seconds: int = 10):
    """Best-effort: search recent audit log entries for message_delete."""
    try:
        guild = message.guild
        if not guild:
            return None
        now = tz_now()
        cutoff = now - datetime.timedelta(seconds=lookback_seconds)
        async for entry in guild.audit_logs(limit=12, action=discord.AuditLogAction.message_delete):
            # entry.created_at is naive or tz-aware? convert
            entry_time = entry.created_at
            if not hasattr(entry_time, "tzinfo") or entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=pytz.utc)
            if entry_time < cutoff:
                continue
            # if the target of this audit entry matches the author of the message, it's likely
            if entry.target and getattr(entry.target, "id", None) == getattr(message.author, "id", None):
                return entry.user
        return None
    except Exception:
        return None

@bot.event
async def on_message_delete(message: discord.Message):
    try:
        # cache message
        deleter = await guess_deleter(message)
        cached = {
            "author_id": message.author.id if getattr(message, "author", None) else None,
            "author_name": message.author.display_name if getattr(message, "author", None) else "Unknown",
            "content": message.content if getattr(message, "content", None) else "",
            "attachments": [a.url for a in getattr(message, "attachments", [])],
            "channel": message.channel.id if getattr(message, "channel", None) else None,
            "timestamp": message.created_at.timestamp() if getattr(message, "created_at", None) else tz_now().timestamp(),
            "deleted_by": deleter.id if deleter else None,
            "deleted_by_name": getattr(deleter, "display_name", None) if deleter else None,
            "jump_url": getattr(message, "jump_url", None),
            "reply_to": None
        }
        # add reply info if present
        if getattr(message, "reference", None) and getattr(message.reference, "message_id", None):
            # attempt to resolve
            try:
                ref_id = message.reference.message_id
                ref_msg = await message.channel.fetch_message(ref_id)
                cached["reply_to"] = {
                    "id": ref_id,
                    "author_id": getattr(ref_msg.author, "id", None),
                    "author_name": getattr(ref_msg.author, "display_name", None),
                    "content": getattr(ref_msg, "content", None)
                }
            except Exception:
                cached["reply_to"] = {"id": message.reference.message_id}
        data.setdefault("cached_messages", {})[str(message.id)] = cached
        save_data(data)

        # log embed
        embed = discord.Embed(title="🗑 Message Deleted", color=discord.Color.red(), timestamp=tz_now())
        embed.add_field(name="Author", value=f"{message.author} ({getattr(message.author, 'id', 'Unknown')})", inline=False)
        embed.add_field(name="Channel", value=f"{getattr(message.channel, 'name', getattr(message.channel, 'id', 'Unknown'))}", inline=True)
        if message.content:
            preview = message.content if len(message.content) < 1000 else message.content[:997] + "..."
            embed.add_field(name="Content", value=preview, inline=False)
        if message.attachments:
            embed.add_field(name="Attachments", value="\n".join(a.url for a in message.attachments), inline=False)
        if deleter:
            embed.add_field(name="Deleted by", value=f"{deleter} ({deleter.id})", inline=True)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_message_delete error:", e)
        try:
            save_data(data)
        except:
            pass

@bot.event
async def on_bulk_message_delete(messages):
    try:
        embed = discord.Embed(title="🧹 Messages Purged", color=discord.Color.dark_red(), timestamp=tz_now())
        for message in messages:
            s = f"Author: {getattr(message.author, 'display_name', 'Unknown')} ({getattr(message.author, 'id', '')})\n"
            if getattr(message, "content", None):
                s += f"Content: {message.content}\n"
            if getattr(message, "attachments", None):
                s += f"Attachments: {', '.join(a.url for a in message.attachments)}\n"
            embed.add_field(name=f"Message ID: {message.id}", value=s, inline=False)
            # cache message
            data.setdefault("cached_messages", {})[str(message.id)] = {
                "author_id": message.author.id if message.author else None,
                "author_name": message.author.display_name if getattr(message, "author", None) else "Unknown",
                "content": message.content if getattr(message, "content", None) else "",
                "attachments": [a.url for a in getattr(message, "attachments", [])],
                "channel": message.channel.id if getattr(message, "channel", None) else None,
                "timestamp": message.created_at.timestamp() if getattr(message, "created_at", None) else tz_now().timestamp(),
                "deleted_by": None
            }
        save_data(data)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_bulk_message_delete error:", e)
        save_data(data)

@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    try:
        if before.content == after.content:
            return
        entry = {
            "message_id": before.id,
            "author_id": before.author.id if before.author else None,
            "author_name": before.author.display_name if getattr(before, "author", None) else "Unknown",
            "before": before.content,
            "after": after.content,
            "channel": before.channel.id if getattr(before, "channel", None) else None,
            "timestamp": tz_now().timestamp()
        }
        data.setdefault("logs", {}).setdefault("edits", []).append(entry)
        save_data(data)
        embed = discord.Embed(title="✏️ Message Edited", color=discord.Color.orange(), timestamp=tz_now())
        embed.add_field(name="Author", value=f"{before.author} ({before.author.id})", inline=False)
        embed.add_field(name="Channel", value=f"{before.channel.name}", inline=True)
        preview_before = before.content if before.content else "[embed/attachment]"
        preview_after = after.content if after.content else "[embed/attachment]"
        if len(preview_before) > 900:
            preview_before = preview_before[:897] + "..."
        if len(preview_after) > 900:
            preview_after = preview_after[:897] + "..."
        embed.add_field(name="Before", value=preview_before, inline=False)
        embed.add_field(name="After", value=preview_after, inline=False)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_message_edit error:", e)

# -------------------------
# Role & Channel & Webhook Events
# -------------------------
@bot.event
async def on_guild_channel_create(channel):
    try:
        embed = discord.Embed(title="📂 Channel Created", color=discord.Color.green(), timestamp=tz_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        embed.add_field(name="Guild", value=f"{channel.guild.name} ({channel.guild.id})", inline=False)
        if getattr(channel.guild, "icon", None):
            try:
                embed.set_thumbnail(url=channel.guild.icon.url)
            except:
                pass
        await log_to_tracking(embed)
    except Exception as e:
        print("on_guild_channel_create error:", e)

@bot.event
async def on_guild_channel_delete(channel):
    try:
        embed = discord.Embed(title="📂 Channel Deleted", color=discord.Color.red(), timestamp=tz_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        embed.add_field(name="Guild", value=f"{channel.guild.name} ({channel.guild.id})", inline=False)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_guild_channel_delete error:", e)

@bot.event
async def on_guild_channel_update(before, after):
    try:
        embed = discord.Embed(title="📂 Channel Updated", color=discord.Color.orange(), timestamp=tz_now())
        embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=True)
        embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=True)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_guild_channel_update error:", e)

@bot.event
async def on_guild_role_create(role):
    try:
        embed = discord.Embed(title="🆕 Role Created", color=discord.Color.green(), timestamp=tz_now())
        embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_guild_role_create error:", e)

@bot.event
async def on_guild_role_delete(role):
    try:
        embed = discord.Embed(title="🗑 Role Deleted", color=discord.Color.red(), timestamp=tz_now())
        embed.add_field(name="Role", value=f"{role.name} ({role.id})", inline=False)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_guild_role_delete error:", e)

@bot.event
async def on_guild_role_update(before, after):
    try:
        embed = discord.Embed(title="🔧 Role Updated", color=discord.Color.orange(), timestamp=tz_now())
        embed.add_field(name="Before", value=f"{before.name} ({before.id})", inline=True)
        embed.add_field(name="After", value=f"{after.name} ({after.id})", inline=True)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_guild_role_update error:", e)

@bot.event
async def on_webhook_update(channel):
    try:
        embed = discord.Embed(title="🔁 Webhooks Updated", color=discord.Color.gold(), timestamp=tz_now())
        embed.add_field(name="Channel", value=f"{channel.name} ({channel.id})", inline=False)
        await log_to_tracking(embed)
    except Exception as e:
        print("on_webhook_update error:", e)

# -------------------------
# On ready: start loops and resume unmutes
# -------------------------
@bot.event
async def on_ready():
    print(f"{bot.user} connected and ready. Guilds: {len(bot.guilds)}")
    try:
        if not timetrack_loop.is_running():
            timetrack_loop.start()
    except Exception as e:
        print("Failed to start timetrack loop:", e)
    # Resume auto-unmute tasks
    now_ts = tz_now().timestamp()
    mutes = data.get("mutes", {})
    for uid, info in list(mutes.items()):
        try:
            start = info.get("start", now_ts)
            duration = info.get("duration", 0)
            remaining = int(duration - (now_ts - start))
            if remaining <= 0:
                # try to remove role now if still present
                for g in bot.guilds:
                    m = g.get_member(int(uid))
                    if m:
                        mute_role = g.get_role(MUTE_ROLE_ID)
                        if mute_role and mute_role in m.roles:
                            try:
                                asyncio.create_task(m.remove_roles(mute_role, reason="Auto-unmute (post-restart)"))
                            except Exception:
                                pass
                del data["mutes"][uid]
                save_data(data)
            else:
                # schedule
                for g in bot.guilds:
                    m = g.get_member(int(uid))
                    if m:
                        asyncio.create_task(auto_unmute(m, remaining))
                        break
        except Exception as e:
            print("Error rescheduling mute for", uid, e)
    save_data(data)

# -------------------------
# Error handlers and command checks
# -------------------------
@cmd_rmute.error
async def rmute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need Manage Roles permission to run this command.", delete_after=8)
    else:
        await ctx.send(f"Error: {error}", delete_after=8)

@cmd_runmute.error
async def runmute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need Manage Roles permission to run this command.", delete_after=8)
    else:
        await ctx.send(f"Error: {error}", delete_after=8)

# -------------------------
# Start bot
# -------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
