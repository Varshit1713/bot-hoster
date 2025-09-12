# main.py
# Full merged bot: !rmute / !runmute / !timetrack / !rmlb / !rhelp / !rping
# Activity tracking (only for specified mod roles) + auto-unmute + moderation & server logs
# Keep-alive via Flask

import os
import json
import random
import asyncio
import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any

import discord
from discord.ext import commands, tasks
from flask import Flask

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287

# Main mod activity log channel (when mods go online/offline)
MOD_ACTIVITY_LOG_CHANNEL = 1403422664521023648

# Full server audit/log channel for role/channel/webhook/etc events
LOGGING_CHANNEL_ID = 1410458084874260592

# Roles that trigger activity logging
ACTIVE_LOG_ROLE_IDS = {
    1410422029236047975,
    1410419924173848626,
    1410420126003630122,
    1410423594579918860,
    1410421466666631279,
    1410421647265108038,
    1410419345234067568
}

# timezone display map (stacked)
TIMEZONES = {
    "🌍 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🌴 PST": ZoneInfo("America/Los_Angeles"),
    "🇪🇺 CET": ZoneInfo("Europe/Berlin"),
}

DATA_FILE = "activity_logs.json"

INACTIVITY_MIN = 50  # random lower bound
INACTIVITY_MAX = 60  # random upper bound

# ------------------ BOT & INTENTS ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ------------------ STORAGE ------------------
activity_logs: Dict[str, Dict[str, Any]] = {}
data_lock = asyncio.Lock()

def load_data():
    global activity_logs
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                activity_logs = json.load(f)
        else:
            activity_logs = {}
    except Exception:
        activity_logs = {}

async def save_data_async():
    async with data_lock:
        tmp = json.dumps(activity_logs, indent=4)
        with open(DATA_FILE, "w") as f:
            f.write(tmp)

def get_user_log(uid: int) -> Dict[str, Any]:
    key = str(uid)
    if key not in activity_logs:
        activity_logs[key] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "offline_start": None,
            "offline_delay": None,
            "last_message": None,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_daily_reset": None,
            "last_weekly_reset": None,
            "last_monthly_reset": None,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "inactive": False,
            "mute_count": 0,
            "rping_on": False
        }
    return activity_logs[key]

def fmt_duration(seconds: float) -> str:
    s = int(max(0, round(seconds)))
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if secs or not parts: parts.append(f"{secs}s")
    return " ".join(parts)

def parse_duration_abbrev(s: str) -> Optional[int]:
    if not s:
        return None
    s = s.strip().lower()
    if len(s) < 2:
        return None
    unit = s[-1]
    try:
        amount = int(s[:-1])
    except ValueError:
        return None
    mult = {"s":1,"m":60,"h":3600,"d":86400}
    if unit not in mult: return None
    return amount * mult[unit]

# ------------------ Flask keep-alive ------------------
app = Flask("botkeepalive")
@app.route("/")
def home(): return "✅ Bot is running!"
def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
asyncio.get_event_loop().run_in_executor(None, run_flask)

# ------------------ UTIL: timezones field ------------------
def stacked_timezones(dt: datetime.datetime) -> str:
    lines = []
    for emoji, tz in TIMEZONES.items():
        lines.append(f"{emoji} {dt.astimezone(tz).strftime('%b %d, %Y – %I:%M %p')}")
    return "\n".join(lines)

# ------------------ EMBED BUILDERS ------------------
def build_mute_embed(member: discord.Member, by: discord.Member, reason: str, duration_seconds: int) -> discord.Embed:
    expire_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
    embed = discord.Embed(
        title="🔇 User Muted",
        description=f"{member.mention} was muted",
        color=0xFF5A5F,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else None)
    embed.add_field(name="👤 Muted User", value=member.mention, inline=True)
    embed.add_field(name="🔒 Muted By", value=by.mention, inline=True)
    embed.add_field(name="⏳ Duration", value=fmt_duration(duration_seconds), inline=True)
    embed.add_field(name="📝 Reason", value=reason or "No reason provided", inline=False)
    embed.add_field(name="🕒 Unmute Time", value=stacked_timezones(expire_dt), inline=False)
    return embed

def build_unmute_embed(member: discord.Member, by: discord.Member, original_reason: Optional[str], original_duration_seconds: Optional[int]) -> discord.Embed:
    embed = discord.Embed(
        title="🔊 User Unmuted",
        description=f"{member.mention} was unmuted",
        color=0x2ECC71,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else None)
    embed.add_field(name="👤 Unmuted User", value=member.mention, inline=True)
    embed.add_field(name="🔓 Unmuted By", value=by.mention, inline=True)
    if original_reason:
        embed.add_field(name="📝 Original Reason", value=original_reason, inline=False)
    if original_duration_seconds:
        embed.add_field(name="⏳ Original Duration", value=fmt_duration(original_duration_seconds), inline=True)
    embed.add_field(name="🕒 Unmuted At", value=stacked_timezones(datetime.datetime.now(datetime.timezone.utc)), inline=False)
    return embed

def build_timetrack_embed(member: discord.Member, log: Dict[str, Any]) -> discord.Embed:
    online_secs = log.get("online_seconds", 0)
    offline_secs = log.get("offline_seconds", 0)
    offline_start_iso = log.get("offline_start")
    offline_delta = 0
    if offline_start_iso:
        try:
            offline_dt = datetime.datetime.fromisoformat(offline_start_iso)
            offline_delta = (datetime.datetime.now(datetime.timezone.utc) - offline_dt).total_seconds()
        except Exception:
            offline_delta = 0
    embed = discord.Embed(
        title="⏳ Time Tracker",
        description=f"Tracking activity for **{member.mention}**",
        color=0x2ecc71 if not log.get("inactive", False) else 0xe74c3c,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else None)
    embed.add_field(name="🟢 Online time", value=f"`{fmt_duration(online_secs)}`", inline=True)
    embed.add_field(name="⚫ Offline time", value=f"`{fmt_duration(offline_secs + offline_delta)}`", inline=True)
    embed.add_field(name="📆 Daily", value=f"`{fmt_duration(log.get('daily_seconds',0))}`", inline=True)
    embed.add_field(name="📆 Weekly", value=f"`{fmt_duration(log.get('weekly_seconds',0))}`", inline=True)
    embed.add_field(name="📆 Monthly", value=f"`{fmt_duration(log.get('monthly_seconds',0))}`", inline=True)
    last_msg_iso = log.get("last_message")
    if last_msg_iso:
        try:
            last_dt = datetime.datetime.fromisoformat(last_msg_iso)
            # show last message in EST main line + other zones stacked
            lines = []
            for emoji, tz in TIMEZONES.items():
                lines.append(f"{emoji} {last_dt.astimezone(tz).strftime('%b %d, %Y – %I:%M %p')}")
            embed.add_field(name="🕒 Last message (timezones)", value="\n".join(lines), inline=False)
        except Exception:
            pass
    return embed

# ------------------ UTILS: audit helper ------------------
async def fetch_audit_executor(guild: discord.Guild, action: discord.AuditLogAction, target_id: Optional[int] = None):
    try:
        async for entry in guild.audit_logs(limit=6, action=action):
            # if target provided, match target id
            try:
                if target_id is None:
                    return entry.user
                # entry.target can be different types; many audit entries have entry.target.id
                tid = getattr(entry.target, "id", None)
                if tid is None:
                    # try str compare
                    if str(target_id) == str(entry.target):
                        return entry.user
                else:
                    if int(tid) == int(target_id):
                        return entry.user
            except Exception:
                continue
        return None
    except Exception:
        return None

# ------------------ LOG SENDER ------------------
async def send_server_log(embed: discord.Embed):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    ch = guild.get_channel(LOGGING_CHANNEL_ID)
    if ch:
        try:
            await ch.send(embed=embed)
        except Exception:
            pass

# ------------------ COMMANDS ------------------
@bot.command(name="rmute")
@commands.has_permissions(moderate_members=True)
async def cmd_rmute(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """Mute + timeout a member. Deletes the command message to preserve anonymity."""
    seconds = parse_duration_abbrev(duration)
    if seconds is None:
        return await ctx.reply("❌ Invalid duration. Use `1m`, `1h`, `1d`, etc.", mention_author=False)

    guild = ctx.guild
    if not guild:
        return await ctx.reply("❌ This command must be used in a guild.", mention_author=False)

    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        return await ctx.reply("❌ Muted role not found in this guild.", mention_author=False)

    # attempt to add role & timeout
    try:
        # add role (for channel-specific perms)
        await member.add_roles(muted_role, reason=f"Muted by {ctx.author}")
    except discord.Forbidden:
        return await ctx.reply("❌ Permission error when adding muted role.", mention_author=False)
    except Exception:
        pass

    # set timeout (discord member timeout)
    try:
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        # Member.timeout expects a datetime or timedelta in some versions; try both patterns:
        try:
            await member.timeout(until, reason=f"Muted by {ctx.author}: {reason}")
        except TypeError:
            # fallback: pass timedelta
            await member.timeout(datetime.timedelta(seconds=seconds), reason=f"Muted by {ctx.author}: {reason}")
        except Exception:
            # ignore but continue
            pass
    except discord.Forbidden:
        # remove role if we couldn't timeout (to avoid stuck role)
        try:
            await member.remove_roles(muted_role, reason="Failed to timeout after adding role")
        except Exception:
            pass
        return await ctx.reply("❌ Missing permissions to timeout this user.", mention_author=False)

    # DM user
    try:
        await member.send(
            f"You have been muted in **{guild.name}** until { (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).strftime('%Y-%m-%d %I:%M %p UTC') }\n"
            f"⏳ Duration: `{duration}`\n"
            f"Reason: ***{reason}***"
        )
    except Exception:
        pass

    # update persistent logs
    now = datetime.datetime.now(datetime.timezone.utc)
    log = get_user_log(member.id)
    log["mute_expires"] = (now + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = str(ctx.author.id)
    log["mute_count"] = log.get("mute_count", 0) + 1

    # log who muted where
    await save_data_async()

    # delete command message (anonymity)
    try:
        await ctx.message.delete()
    except Exception:
        pass

    # send embed to mod activity log channel (muted event)
    embed = build_mute_embed(member, ctx.author, reason, seconds)
    await send_server_log(embed)

    # reply in channel briefly (ephemeral style) via DM or ephemeral note (we use ctx.reply but it's deleted quickly)
    try:
        ack = await ctx.send(f"✅ {member.mention} has been muted for `{duration}`.")
        await asyncio.sleep(6)
        try:
            await ack.delete()
        except Exception:
            pass
    except Exception:
        pass

@bot.command(name="runmute")
@commands.has_permissions(moderate_members=True)
async def cmd_runmute(ctx: commands.Context, member: discord.Member):
    guild = ctx.guild
    if not guild:
        return await ctx.reply("❌ Use inside a guild.", mention_author=False)
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role and muted_role in member.roles:
        try:
            await member.remove_roles(muted_role, reason=f"Unmuted by {ctx.author}")
            try:
                await member.timeout(None, reason=f"Unmuted by {ctx.author}")
            except Exception:
                pass
            try:
                await member.send(f"You have been unmuted in **{guild.name}** by {ctx.author.display_name}.")
            except Exception:
                pass
        except discord.Forbidden:
            return await ctx.reply("❌ Missing permission to remove role.", mention_author=False)
    # clear stored mute
    log = get_user_log(member.id)
    orig_reason = log.get("mute_reason")
    orig_expires_iso = log.get("mute_expires")
    orig_duration_seconds = None
    if orig_expires_iso:
        try:
            dt = datetime.datetime.fromisoformat(orig_expires_iso)
            orig_duration_seconds = int((dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
            if orig_duration_seconds < 0:
                orig_duration_seconds = None
        except Exception:
            orig_duration_seconds = None
    log["mute_expires"] = None
    log["mute_reason"] = None
    log["mute_responsible"] = None
    await save_data_async()

    embed = build_unmute_embed(member, ctx.author, orig_reason, orig_duration_seconds)
    await send_server_log(embed)
    await ctx.reply(f"✅ {member.mention} has been unmuted.", mention_author=False)

@bot.command(name="rhelp")
async def cmd_rhelp(ctx: commands.Context):
    embed = discord.Embed(title="🤖 Moderation Commands", color=0x3498db)
    embed.add_field(name="!rmute", value="`!rmute [user] [duration] [reason]` — Mute a user (deletes your command message).", inline=False)
    embed.add_field(name="!runmute", value="`!runmute [user]` — Unmute a user.", inline=False)
    embed.add_field(name="!timetrack", value="`!timetrack [user]` — Shows online/offline and counters.", inline=False)
    embed.add_field(name="!rmlb", value="`!rmlb [true/false]` — Leaderboard of who used !rmute most.", inline=False)
    embed.add_field(name="!rping", value="`!rping [on/off] [user]` — Toggle ping for online/offline notices.", inline=False)
    embed.set_footer(text="Use prefix commands only.")
    await ctx.send(embed=embed)

@bot.command(name="rping")
async def cmd_rping(ctx: commands.Context, toggle: str, member: discord.Member = None):
    # allow admins or users with any of ACTIVE_LOG_ROLE_IDS
    allowed = ctx.author.guild_permissions.administrator or any(r.id in ACTIVE_LOG_ROLE_IDS for r in ctx.author.roles)
    if not allowed:
        return await ctx.reply("❌ You do not have permission to use !rping.", mention_author=False)
    toggle = toggle.lower()
    if toggle not in {"on", "off"}:
        return await ctx.reply("❌ Usage: `!rping [on/off] [user]`", mention_author=False)
    member = member or ctx.author
    log = get_user_log(member.id)
    log["rping_on"] = (toggle == "on")
    await save_data_async()
    await ctx.reply(f"✅ Ping for {member.display_name} set to `{toggle}`.", mention_author=False)

@bot.command(name="rmlb")
async def cmd_rmlb(ctx: commands.Context, public: Optional[bool] = False):
    scoreboard = []
    for uid, data in activity_logs.items():
        count = data.get("mute_count", 0)
        if count > 0:
            try:
                uid_int = int(uid)
            except Exception:
                continue
            member = ctx.guild.get_member(uid_int)
            name = member.display_name if member else f"User {uid}"
            scoreboard.append((name, count))
    scoreboard.sort(key=lambda x: x[1], reverse=True)
    if not scoreboard:
        embed = discord.Embed(title="📊 !rmute Leaderboard", description="No data yet.", color=0xFFD700)
    else:
        desc = "\n".join([f"🏆 {i+1}. {name} — {count} mutes" for i, (name, count) in enumerate(scoreboard[:10])])
        embed = discord.Embed(title="📊 !rmute Leaderboard (Top Muters)", description=desc, color=0xFFD700)
    if public:
        await ctx.send(embed=embed)
    else:
        await ctx.reply(embed=embed, mention_author=False)

# ------------------ timetrack (prefix) ------------------
@bot.command(name="timetrack")
async def cmd_timetrack(ctx: commands.Context, member: discord.Member = None):
    member = member or ctx.author
    log = get_user_log(member.id)
    embed = build_timetrack_embed(member, log)
    await ctx.send(embed=embed)

# ------------------ ON_MESSAGE (single handler) ------------------
@bot.event
async def on_message(message: discord.Message):
    # handle commands & activity tracking
    if message.author.bot:
        await bot.process_commands(message)
        return

    uid = message.author.id
    now = datetime.datetime.now(datetime.timezone.utc)
    log = get_user_log(uid)

    # Reset offline counters when they send a message
    log["last_message"] = now.isoformat()
    log["offline_seconds"] = 0
    log["offline_start"] = None
    if not log.get("offline_delay"):
        log["offline_delay"] = random.randint(INACTIVITY_MIN, INACTIVITY_MAX)

    # Reset/roll daily/weekly/monthly
    today = now.date()
    weeknum = now.isocalendar()[1]
    monthnum = now.month
    if log.get("last_daily_reset") != str(today):
        log["daily_seconds"] = 0
        log["last_daily_reset"] = str(today)
    if log.get("last_weekly_reset") != str(weeknum):
        log["weekly_seconds"] = 0
        log["last_weekly_reset"] = str(weeknum)
    if log.get("last_monthly_reset") != str(monthnum):
        log["monthly_seconds"] = 0
        log["last_monthly_reset"] = str(monthnum)

    # Increment a little (we rely on poller for larger increments)
    log["daily_seconds"] = log.get("daily_seconds", 0) + 1
    log["weekly_seconds"] = log.get("weekly_seconds", 0) + 1
    log["monthly_seconds"] = log.get("monthly_seconds", 0) + 1

    # If they were inactive and now back, send mod-activity notice only if they have one of ACTIVE_LOG_ROLE_IDS
    if log.get("inactive", False):
        guild = message.guild
        member = message.author
        if guild:
            has_role = any((guild.get_role(rid) and guild.get_role(rid) in member.roles) for rid in ACTIVE_LOG_ROLE_IDS)
            if has_role:
                ping = member.mention if log.get("rping_on", False) else member.display_name
                lc = guild.get_channel(MOD_ACTIVITY_LOG_CHANNEL)
                if lc:
                    try:
                        await lc.send(f"🟢 {ping} has come back online (sent a message).")
                    except Exception:
                        pass
    log["inactive"] = False

    await save_data_async()
    await bot.process_commands(message)

# ------------------ INACTIVITY POLLER ------------------
@tasks.loop(seconds=5)
async def inactivity_poller():
    now = datetime.datetime.now(datetime.timezone.utc)
    for uid, log in list(activity_logs.items()):
        if not log.get("offline_delay"):
            log["offline_delay"] = random.randint(INACTIVITY_MIN, INACTIVITY_MAX)
        last_msg_iso = log.get("last_message")
        if last_msg_iso:
            try:
                last_msg_dt = datetime.datetime.fromisoformat(last_msg_iso)
            except Exception:
                last_msg_dt = now
                log["last_message"] = now.isoformat()
            delta = (now - last_msg_dt).total_seconds()
            delay = int(log.get("offline_delay", INACTIVITY_MIN))
            if delta >= delay:
                if not log.get("offline_start"):
                    # set offline start to the moment they reached inactivity
                    offline_start = last_msg_dt + datetime.timedelta(seconds=delay)
                    log["offline_start"] = offline_start.isoformat()
                    # Send mod-only log if the user has one of the active roles
                    guild = bot.get_guild(GUILD_ID)
                    if guild:
                        member = guild.get_member(int(uid))
                        if member:
                            has_role = any((guild.get_role(rid) and guild.get_role(rid) in member.roles) for rid in ACTIVE_LOG_ROLE_IDS)
                            if has_role:
                                lc = guild.get_channel(MOD_ACTIVITY_LOG_CHANNEL)
                                ping = member.mention if log.get("rping_on", False) else member.display_name
                                if lc:
                                    try:
                                        await lc.send(f"⚫ {ping} has gone inactive ({delay}s without message).")
                                    except Exception:
                                        pass
                try:
                    offline_start_dt = datetime.datetime.fromisoformat(log["offline_start"])
                    log["offline_seconds"] = (now - offline_start_dt).total_seconds()
                except Exception:
                    log["offline_seconds"] = delta
                log["inactive"] = True
            else:
                # still active; reset offline counters
                log["offline_seconds"] = 0
                log["offline_start"] = None
                log["inactive"] = False
                # increment online counters for active users
                log["online_seconds"] = log.get("online_seconds", 0) + 5
                log["daily_seconds"] = log.get("daily_seconds", 0) + 5
                log["weekly_seconds"] = log.get("weekly_seconds", 0) + 5
                log["monthly_seconds"] = log.get("monthly_seconds", 0) + 5
    await save_data_async()

# ------------------ AUTO-UNMUTE LOOP ------------------
@tasks.loop(seconds=15)
async def auto_unmute_loop():
    now = datetime.datetime.now(datetime.timezone.utc)
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    muted_role = guild.get_role(MUTED_ROLE_ID)
    for uid, log in list(activity_logs.items()):
        expire_iso = log.get("mute_expires")
        if not expire_iso:
            continue
        try:
            expire_dt = datetime.datetime.fromisoformat(expire_iso)
        except Exception:
            log["mute_expires"] = None
            await save_data_async()
            continue
        if now >= expire_dt:
            member = guild.get_member(int(uid))
            if member and muted_role and muted_role in member.roles:
                try:
                    await member.remove_roles(muted_role, reason="Auto-unmute (mute expired)")
                except Exception:
                    pass
                try:
                    await member.timeout(None, reason="Auto-unmute (mute expired)")
                except Exception:
                    pass
                try:
                    await member.send(f"🔊 Your mute in **{guild.name}** has expired and you were unmuted.")
                except Exception:
                    pass
                # log unmute
                embed = build_unmute_embed(member, bot.user, log.get("mute_reason"), None)
                await send_server_log(embed)
            # clear stored mute
            log["mute_expires"] = None
            log["mute_reason"] = None
            log["mute_responsible"] = None
            await save_data_async()

# ------------------ LISTENERS: moderation/server events ------------------
@bot.event
async def on_guild_role_create(role: discord.Role):
    guild = role.guild
    embed = discord.Embed(title="🆕 Role Created", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Role", value=role.name, inline=True)
    # try to fetch who created it from audit logs
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.role_create, target_id=role.id)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_role_delete(role: discord.Role):
    guild = role.guild
    embed = discord.Embed(title="❌ Role Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Role name", value=role.name, inline=True)
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.role_delete, target_id=role.id)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_channel_create(channel):
    guild = channel.guild
    embed = discord.Embed(title="🆕 Channel Created", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Channel", value=channel.mention if hasattr(channel, "mention") else str(channel), inline=True)
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.channel_create)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_channel_delete(channel):
    guild = channel.guild
    embed = discord.Embed(title="❌ Channel Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Channel name", value=getattr(channel, "name", str(channel)), inline=True)
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.channel_delete)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_webhooks_update(channel):
    guild = channel.guild
    embed = discord.Embed(title="🔗 Webhooks Updated", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    # audit log lookup for webhook create/delete (best effort)
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.webhook_create)
    if not executor:
        executor = await fetch_audit_executor(guild, discord.AuditLogAction.webhook_update)
    embed.add_field(name="Recent audit (may be None)", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_emojis_update(guild, before, after):
    # detect creations/deletions by comparing lists
    before_ids = {e.id for e in before}
    after_ids = {e.id for e in after}
    created = after_ids - before_ids
    deleted = before_ids - after_ids
    # created
    for cid in created:
        emoji = discord.utils.get(after, id=cid)
        embed = discord.Embed(title="🎉 Emoji Created", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="Emoji", value=str(emoji), inline=True)
        executor = await fetch_audit_executor(guild, discord.AuditLogAction.emoji_create)
        embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
        await send_server_log(embed)
    for cid in deleted:
        # we don't have object for deleted emoji; use name from before list
        emoji = discord.utils.get(before, id=cid)
        embed = discord.Embed(title="❌ Emoji Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="Emoji", value=str(emoji) if emoji else str(cid), inline=True)
        executor = await fetch_audit_executor(guild, discord.AuditLogAction.emoji_delete)
        embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
        await send_server_log(embed)

@bot.event
async def on_member_ban(guild, user):
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.ban, target_id=getattr(user, "id", None))
    embed = discord.Embed(title="🔨 User Banned", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="User", value=getattr(user, "mention", str(user)), inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_member_unban(guild, user):
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.unban, target_id=getattr(user, "id", None))
    embed = discord.Embed(title="✅ User Unbanned", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="User", value=str(user), inline=True)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_member_remove(member):
    guild = member.guild
    embed = discord.Embed(title="👋 Member Left", color=0x95a5a6, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="User", value=member.mention, inline=True)
    await send_server_log(embed)

@bot.event
async def on_member_join(member):
    embed = discord.Embed(title="🟢 Member Joined", color=0x2ecc71, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="User", value=member.mention, inline=True)
    await send_server_log(embed)

@bot.event
async def on_message_delete(message):
    # message may be partial, attempt best-effort
    guild = getattr(message, "guild", None)
    embed = discord.Embed(title="🗑️ Message Deleted", color=0xff6347, timestamp=datetime.datetime.now(datetime.timezone.utc))
    author = getattr(message, "author", None)
    content = getattr(message, "content", None)
    channel = getattr(message, "channel", None)
    embed.add_field(name="Author", value=(author.mention if author else "Unknown"), inline=True)
    embed.add_field(name="Channel", value=(channel.mention if channel else "Unknown"), inline=True)
    embed.add_field(name="Content", value=(content[:1024] if content else "⚠️ (empty or embed)"), inline=False)
    # try to find moderator via audit logs (best-effort)
    if guild:
        executor = await fetch_audit_executor(guild, discord.AuditLogAction.message_delete)
        if executor:
            embed.add_field(name="Deleted by", value=executor.mention, inline=True)
    await send_server_log(embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    embed = discord.Embed(title="✏️ Message Edited", color=0xf39c12, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Author", value=before.author.mention, inline=True)
    embed.add_field(name="Channel", value=before.channel.mention if before.channel else "Unknown", inline=True)
    embed.add_field(name="Before", value=(before.content[:1024] or "(embed/attachment)"), inline=False)
    embed.add_field(name="After", value=(after.content[:1024] or "(embed/attachment)"), inline=False)
    await send_server_log(embed)

@bot.event
async def on_member_update(before, after):
    # nickname changes or timed out/un-timed events
    guild = after.guild
    if before.nick != after.nick:
        embed = discord.Embed(title="🔤 Nickname Changed", color=0x9b59b6, timestamp=datetime.datetime.now(datetime.timezone.utc))
        embed.add_field(name="User", value=after.mention, inline=True)
        embed.add_field(name="Before", value=(before.nick or "(none)"), inline=True)
        embed.add_field(name="After", value=(after.nick or "(none)"), inline=True)
        await send_server_log(embed)
    # detect manual untimeout (discord sets timed_out_until to None)
    # note: discord.py's Member.timed_out_until may be attribute named timed_out_until or timed_out_until
    try:
        before_to = getattr(before, "timed_out_until", None)
        after_to = getattr(after, "timed_out_until", None)
        if before_to and not after_to:
            # user was un-timed (manual unmute)
            # remove muted role if present
            role = after.guild.get_role(MUTED_ROLE_ID)
            if role and role in after.roles:
                try:
                    await after.remove_roles(role, reason="Manual untimeout detected")
                except Exception:
                    pass
            # log
            embed = build_unmute_embed(after, bot.user, None, None)
            await send_server_log(embed)
    except Exception:
        pass

@bot.event
async def on_guild_role_update(before, after):
    guild = after.guild
    embed = discord.Embed(title="⚙️ Role Updated", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Role", value=after.name, inline=True)
    executor = await fetch_audit_executor(guild, discord.AuditLogAction.role_update, target_id=after.id)
    embed.add_field(name="By", value=executor.mention if executor else "Unknown", inline=True)
    await send_server_log(embed)

@bot.event
async def on_member_ban(guild, user):
    # already handled above — keep for compatibility
    pass

@bot.event
async def on_guild_update(before, after):
    embed = discord.Embed(title="🏷️ Guild Updated", color=0xf1c40f, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Before Name", value=before.name, inline=True)
    embed.add_field(name="After Name", value=after.name, inline=True)
    await send_server_log(embed)

@bot.event
async def on_guild_integrations_update(guild):
    embed = discord.Embed(title="🔗 Integrations Updated", color=0xf39c12, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.add_field(name="Guild", value=guild.name, inline=True)
    await send_server_log(embed)

# ------------------ STARTUP & POLLERS ------------------
@bot.event
async def on_ready():
    # ensure data loaded
    load_data()
    # start background loops if not running
    if not inactivity_poller.is_running():
        inactivity_poller.start()
    if not auto_unmute_loop.is_running():
        auto_unmute_loop.start()
    print(f"✅ Bot ready: {bot.user} (Guilds: {len(bot.guilds)})")

# ------------------ BOOT ------------------
if __name__ == "__main__":
    load_data()
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ DISCORD_TOKEN environment variable not set. Exiting.")
        raise SystemExit(1)
    bot.run(TOKEN)
