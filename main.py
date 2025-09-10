# main.py
# Full bot: prefix commands only. Handles rmute/runmute/timetrack/rmlb/rhelp + inactivity + auto-unmute + Render keep-alive.

import os
import json
import random
import asyncio
import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import discord
from discord.ext import commands, tasks
from flask import Flask

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

# Roles that trigger "came back active" log (only send "back active" if user has ANY of these)
ACTIVE_LOG_ROLE_IDS = {
    1410422029236047975,
    1410419345234067568,
    1410421647265108038,
    1410421466666631279,
    1410423594579918860,
    1410420126003630122,
    1410419924173848626
}

TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo"),
}

DATA_FILE = "activity_logs.json"

# ------------------ BOT & INTENTS ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ------------------ DATA HANDLING ------------------
activity_logs = {}
data_lock = asyncio.Lock()

def load_data():
    global activity_logs
    try:
        with open(DATA_FILE, "r") as f:
            activity_logs = json.load(f)
    except Exception:
        activity_logs = {}

async def save_data_async():
    async with data_lock:
        with open(DATA_FILE, "w") as f:
            json.dump(activity_logs, f, indent=4)

def get_user_log(user_id: int):
    uid = str(user_id)
    if uid not in activity_logs:
        # initialize structure
        activity_logs[uid] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "offline_start": None,       # ISO datetime string when offline timer started
            "offline_delay": None,       # per-user random delay (seconds) between 50-60
            "last_message": None,        # ISO datetime string of last message
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_daily_reset": None,    # ISO date
            "last_weekly_reset": None,   # ISO week number (str)
            "last_monthly_reset": None,  # month number (str)
            "mute_expires": None,        # ISO datetime or None
            "mute_reason": None,
            "mute_responsible": None,    # id of muter (int or str)
            # track counts of who issued mutes: stored in muter record, not here
        }
    return activity_logs[uid]

def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, round(seconds)))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)

def parse_duration_abbrev(s: str) -> Optional[int]:
    """
    Parse abbreviated duration like '1m','2h','3d','27d' into seconds.
    Returns seconds or None if invalid.
    """
    s = s.strip().lower()
    if len(s) < 2:
        return None
    unit = s[-1]
    try:
        amount = int(s[:-1])
    except ValueError:
        return None
    multipliers = {"s":1, "m":60, "h":3600, "d":86400}
    if unit not in multipliers:
        return None
    return amount * multipliers[unit]

# ------------------ EMBED HELPER ------------------
def build_mute_embed(member: discord.Member, by: discord.Member, reason: str, duration_seconds: int):
    expire_dt = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)
    embed = discord.Embed(
        title="🔇 User Muted",
        description=f"{member.mention} was muted",
        color=0xFF5A5F,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Muted User", value=member.mention, inline=True)
    embed.add_field(name="🔒 Muted By", value=by.mention, inline=True)
    embed.add_field(name="⏳ Duration", value=fmt_duration(duration_seconds), inline=True)
    embed.add_field(name="📝 Reason", value=reason or "No reason provided", inline=False)
    # 4 tz lines
    tz_lines = []
    for emoji, tz in TIMEZONES.items():
        tz_lines.append(f"{emoji} {expire_dt.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}")
    embed.add_field(name="🕒 Unmute Time", value="\n".join(tz_lines), inline=False)
    return embed

def build_unmute_embed(member: discord.Member, by: discord.Member, original_reason: Optional[str], original_duration_seconds: Optional[int]):
    embed = discord.Embed(
        title="🔊 User Unmuted",
        description=f"{member.mention} was unmuted",
        color=0x2ECC71,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Unmuted User", value=member.mention, inline=True)
    embed.add_field(name="🔓 Unmuted By", value=by.mention, inline=True)
    if original_reason:
        embed.add_field(name="📝 Original Reason", value=original_reason, inline=False)
    if original_duration_seconds:
        embed.add_field(name="⏳ Original Duration", value=fmt_duration(original_duration_seconds), inline=True)
    # show unmute time in 4 timezones (now)
    now = datetime.datetime.now(datetime.timezone.utc)
    tz_lines = [f"{emoji} {now.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="🕒 Unmuted At (timezones)", value="\n".join(tz_lines), inline=False)
    return embed

# ------------------ COMMANDS ------------------

@bot.command(name="rmute")
@commands.has_permissions(moderate_members=True)
async def cmd_rmute(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """
    !rmute [user] [duration] [reason]
    Mutes via Discord timeout and adds the muted role, DMs user, logs to log channel with embed.
    """
    # parse duration
    seconds = parse_duration_abbrev(duration)
    if seconds is None:
        await ctx.reply("❌ Invalid duration. Use formats like `1m`, `1h`, `1d`, `27d`.", mention_author=False)
        return

    guild = ctx.guild
    if not guild:
        await ctx.reply("❌ This command must be used in a guild.", mention_author=False)
        return

    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role is None:
        await ctx.reply("❌ Muted role not found in this guild.", mention_author=False)
        return

    # Apply role + timeout
    try:
        await member.add_roles(muted_role, reason=f"Muted by {ctx.author} ({ctx.author.id})")
    except discord.Forbidden:
        await ctx.reply("⚠️ I don't have permission to add the Muted role.", mention_author=False)
        return
    except Exception as e:
        await ctx.reply(f"⚠️ Failed to add role: {e}", mention_author=False)
        return

    try:
        # timeout via Discord API
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        await member.timeout(until, reason=f"Muted by {ctx.author}: {reason}")
    except discord.Forbidden:
        # role was added but timeout couldn't be set
        pass
    except Exception:
        pass

    # DM the user (best effort)
    try:
        await member.send(f"You have been muted in **{guild.name}** for {fmt_duration(seconds)}.\nReason: {reason}")
    except Exception:
        # ignore DM failures
        pass

    # Update data
    log = get_user_log(member.id)
    now = datetime.datetime.now(datetime.timezone.utc)
    log["mute_expires"] = (now + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = str(ctx.author.id)
    # count how many mutes this muter has done (store in the muter's log)
    muter_log = get_user_log(ctx.author.id)
    muter_log["mute_count"] = muter_log.get("mute_count", 0) + 1
    muter_log["last_mute_at"] = now.isoformat()

    # Save and send embed to log channel
    await save_data_async()
    embed = build_mute_embed(member, ctx.author, reason, seconds)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass

    await ctx.reply(f"✅ {member.mention} muted for {fmt_duration(seconds)}.", mention_author=False)

@cmd_rmute.error
async def rmute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ You don't have permission to use `!rmute` (moderate_members needed).", mention_author=False)
    else:
        await ctx.reply(f"❌ Error: {error}", mention_author=False)

@bot.command(name="runmute")
@commands.has_permissions(moderate_members=True)
async def cmd_runmute(ctx: commands.Context, member: discord.Member):
    """
    !runmute [user]
    Unmute: removes role, clears timeout, DMs user, logs embed with original reason/duration if available.
    """
    guild = ctx.guild
    if not guild:
        return await ctx.reply("❌ This command must be used in a guild.", mention_author=False)

    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role is None:
        return await ctx.reply("❌ Muted role not found in this guild.", mention_author=False)

    log = get_user_log(member.id)
    orig_reason = log.get("mute_reason")
    orig_expires = log.get("mute_expires")
    orig_duration_seconds = None
    if orig_expires:
        try:
            dt = datetime.datetime.fromisoformat(orig_expires)
            orig_duration_seconds = int((dt - datetime.datetime.now(datetime.timezone.utc)).total_seconds())
            if orig_duration_seconds < 0:
                orig_duration_seconds = None
        except Exception:
            orig_duration_seconds = None

    # remove role and timeout
    try:
        if muted_role in member.roles:
            await member.remove_roles(muted_role, reason=f"Unmuted by {ctx.author}")
        try:
            await member.timeout(None, reason=f"Unmuted by {ctx.author}")
        except Exception:
            pass
    except discord.Forbidden:
        return await ctx.reply("⚠️ I don't have permission to remove Muted role or timeout.", mention_author=False)
    except Exception as e:
        return await ctx.reply(f"⚠️ Failed to unmute: {e}", mention_author=False)

    # DM
    try:
        await member.send(f"You have been unmuted in **{guild.name}** by {ctx.author.display_name}.")
    except Exception:
        pass

    # clear stored mute
    log["mute_expires"] = None
    log["mute_reason"] = None
    log["mute_responsible"] = None
    await save_data_async()

    # log embed
    embed = build_unmute_embed(member, ctx.author, orig_reason, orig_duration_seconds)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass

    await ctx.reply(f"✅ {member.mention} has been unmuted.", mention_author=False)

@cmd_runmute.error
async def runmute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.reply("❌ You don't have permission to use `!runmute` (moderate_members needed).", mention_author=False)
    else:
        await ctx.reply(f"❌ Error: {error}", mention_author=False)

# ------------------ INACTIVITY + TIMETRACKING STATE ------------------
# We'll increment online_seconds while user sends messages and while they are active.
# Offline counting starts after user hasn't sent messages for their random offline_delay (50-60s).

# NOTE: We do NOT rely on presence status; we rely on messages for activity.

@bot.event
async def on_message(message: discord.Message):
    # ensure commands still work
    if message.author.bot:
        return

    uid = message.author.id
    now = datetime.datetime.now(datetime.timezone.utc)
    user_log = get_user_log(uid)

    # reset offline stuff when message arrives
    user_log["last_message"] = now.isoformat()
    user_log["offline_seconds"] = 0
    user_log["offline_start"] = None
    # ensure offline_delay exists
    if not user_log.get("offline_delay"):
        user_log["offline_delay"] = random.randint(50, 60)
    await save_data_async()

    # update online counters immediately
    user_log["online_seconds"] = user_log.get("online_seconds", 0) + 1  # increment by message (approx second of activity)
    # daily/weekly/monthly updates + resets
    today = now.date()
    weeknum = now.isocalendar()[1]
    monthnum = now.month

    if user_log.get("last_daily_reset") != str(today):
        user_log["daily_seconds"] = 0
        user_log["last_daily_reset"] = str(today)
    if user_log.get("last_weekly_reset") != str(weeknum):
        user_log["weekly_seconds"] = 0
        user_log["last_weekly_reset"] = str(weeknum)
    if user_log.get("last_monthly_reset") != str(monthnum):
        user_log["monthly_seconds"] = 0
        user_log["last_monthly_reset"] = str(monthnum)

    user_log["daily_seconds"] = user_log.get("daily_seconds", 0) + 1
    user_log["weekly_seconds"] = user_log.get("weekly_seconds", 0) + 1
    user_log["monthly_seconds"] = user_log.get("monthly_seconds", 0) + 1

    # If user was previously inactive (offline_status True), and now they have returned,
    # send a "back active" log but only if they have any of ACTIVE_LOG_ROLE_IDS.
    # We track that via user_log["inactive"] (boolean)
    was_inactive = user_log.get("inactive", False)
    if was_inactive:
        # check roles
        guild = message.guild
        member = message.author
        send_back_active = False
        try:
            for rid in ACTIVE_LOG_ROLE_IDS:
                if guild.get_role(rid) in member.roles:
                    send_back_active = True
                    break
        except Exception:
            send_back_active = False

        if send_back_active:
            log_channel = guild.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                try:
                    await log_channel.send(f"🟢 {member.mention} has come back online (sent a message).")
                except Exception:
                    pass
    # reset inactive flag always when message seen
    user_log["inactive"] = False
    await save_data_async()

    await bot.process_commands(message)

# ------------------ INACTIVITY POLLER (background) ------------------
@tasks.loop(seconds=5)
async def inactivity_poller():
    """Runs every 5 seconds, checks last_message timestamps and updates offline_seconds.
       Marks users inactive when last_message is older than their offline_delay.
       Sends 'went inactive' logs immediately when they cross threshold.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    for uid, log in list(activity_logs.items()):
        # ensure offline_delay
        if not log.get("offline_delay"):
            log["offline_delay"] = random.randint(50, 60)
        last_msg_iso = log.get("last_message")
        if last_msg_iso:
            try:
                last_msg_dt = datetime.datetime.fromisoformat(last_msg_iso)
            except Exception:
                # corrupt value: reset to now
                last_msg_dt = now
                log["last_message"] = now.isoformat()

            delta = (now - last_msg_dt).total_seconds()
            delay = int(log.get("offline_delay", 53))
            if delta >= delay:
                # user should be counted offline
                if not log.get("offline_start"):
                    # set offline_start to the exact time they passed threshold
                    log["offline_start"] = (last_msg_dt + datetime.timedelta(seconds=delay)).isoformat()
                    # send went inactive log (best effort)
                    try:
                        guild = bot.get_guild(GUILD_ID)
                        if guild:
                            user = guild.get_member(int(uid))
                            if user:
                                lc = guild.get_channel(LOG_CHANNEL_ID)
                                if lc:
                                    await lc.send(f"⚫ {user.mention} has gone inactive ({delay}s without message).")
                    except Exception:
                        pass
                # update offline_seconds
                try:
                    offline_start_dt = datetime.datetime.fromisoformat(log["offline_start"])
                    log["offline_seconds"] = (now - offline_start_dt).total_seconds()
                except Exception:
                    log["offline_seconds"] = delta
                # mark as inactive flag
                log["inactive"] = True
            else:
                # still active period
                log["offline_seconds"] = 0
                log["offline_start"] = None
                log["inactive"] = False

            # NOTE: increment online_seconds in a lightweight manner:
            # If they are not marked inactive, count the time since last poll as online seconds.
            if not log.get("inactive", False):
                # add 5 seconds (loop period) to online counters
                log["online_seconds"] = log.get("online_seconds", 0) + 5
                # update daily/weekly/monthly similarly (adds 5s)
                # do daily/weekly/monthly resets and increments
                now_date = now.date()
                weeknum = now.isocalendar()[1]
                monthnum = now.month
                if log.get("last_daily_reset") != str(now_date):
                    log["daily_seconds"] = 0
                    log["last_daily_reset"] = str(now_date)
                if log.get("last_weekly_reset") != str(weeknum):
                    log["weekly_seconds"] = 0
                    log["last_weekly_reset"] = str(weeknum)
                if log.get("last_monthly_reset") != str(monthnum):
                    log["monthly_seconds"] = 0
                    log["last_monthly_reset"] = str(monthnum)
                log["daily_seconds"] = log.get("daily_seconds", 0) + 5
                log["weekly_seconds"] = log.get("weekly_seconds", 0) + 5
                log["monthly_seconds"] = log.get("monthly_seconds", 0) + 5

    # persist
    await save_data_async()

# ------------------ AUTO-UNMUTE LOOP ------------------
@tasks.loop(seconds=15)
async def auto_unmute_loop():
    """Every 15s check mutes and unmute anyone expired."""
    now = datetime.datetime.now(datetime.timezone.utc)
    for uid, log in list(activity_logs.items()):
        expire_iso = log.get("mute_expires")
        if expire_iso:
            try:
                expire_dt = datetime.datetime.fromisoformat(expire_iso)
            except Exception:
                # corrupt
                log["mute_expires"] = None
                await save_data_async()
                continue
            if now >= expire_dt:
                guild = bot.get_guild(GUILD_ID)
                if not guild:
                    continue
                member = guild.get_member(int(uid))
                muted_role = guild.get_role(MUTED_ROLE_ID)
                if member and muted_role and muted_role in member.roles:
                    try:
                        await member.remove_roles(muted_role, reason="Auto-unmute (mute expired)")
                        try:
                            await member.timeout(None, reason="Auto-unmute (mute expired)")
                        except Exception:
                            pass
                        try:
                            await member.send(f"🔊 Your mute in **{guild.name}** has expired and you were unmuted.")
                        except Exception:
                            pass
                    except Exception:
                        # permission issues or others — continue
                        pass
                    # log
                    log_channel = guild.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        try:
                            await log_channel.send(f"🔊 {member.mention} was auto-unmuted (mute expired).")
                        except Exception:
                            pass
                # clear stored mute info
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                await save_data_async()

# ------------------ TIMETRACK COMMAND ------------------
@bot.command(name="timetrack")
async def cmd_timetrack(ctx: commands.Context, member: discord.Member = None):
    """
    !timetrack [user]
    Shows online/offline times, daily/weekly/monthly counters and timezone list.
    """
    member = member or ctx.author
    uid = str(member.id)
    log = get_user_log(member.id)

    online = fmt_duration(log.get("online_seconds", 0))
    offline = fmt_duration(log.get("offline_seconds", 0))
    daily = fmt_duration(log.get("daily_seconds", 0))
    weekly = fmt_duration(log.get("weekly_seconds", 0))
    monthly = fmt_duration(log.get("monthly_seconds", 0))

    tz_lines = [f"{emoji} {datetime.datetime.now(datetime.timezone.utc).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]

    embed = discord.Embed(title=f"⏱️ Timetrack — {member.display_name}", color=0x00B894, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🟢 Online Time", value=online, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline, inline=True)
    embed.add_field(name="📅 Daily", value=daily, inline=True)
    embed.add_field(name="📅 Weekly", value=weekly, inline=True)
    embed.add_field(name="📅 Monthly", value=monthly, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)

    await ctx.send(embed=embed)

# ------------------ RMLB COMMAND ------------------
@bot.command(name="rmlb")
async def cmd_rmlb(ctx: commands.Context, public: Optional[bool] = False):
    """
    !rmlb [true/false] — shows who has used !rmute the most (the muters).
    If public is True, send in channel; otherwise reply privately to invoker.
    """
    # build scoreboard from activity_logs by reading the muter records (each muter stored in their own log)
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

# ------------------ RHELP COMMAND ------------------
@bot.command(name="rhelp")
async def cmd_rhelp(ctx: commands.Context):
    embed = discord.Embed(title="🤖 Moderation Commands", color=0x3498db)
    embed.add_field(name="!rmute", value="`!rmute [user] [duration] [reason]` \nMute a user (adds Muted role + Discord timeout).", inline=False)
    embed.add_field(name="!runmute", value="`!runmute [user]` \nUnmute a user (remove role + clear timeout).", inline=False)
    embed.add_field(name="!timetrack", value="`!timetrack [user]` \nShows online/offline and daily/weekly/monthly counters.", inline=False)
    embed.add_field(name="!rmlb", value="`!rmlb [true/false]` \nLeaderboard of who issued the most !rmute commands. true = public, false = private.", inline=False)
    embed.set_footer(text="Use prefix commands (e.g. !rmute). Slash commands are not used.")
    await ctx.send(embed=embed)

# ------------------ FLASK KEEP-ALIVE FOR RENDER ------------------
app = Flask("botkeepalive")

@app.route("/")
def home():
    return "✅ Bot is running!"

async def start_web():
    port = int(os.environ.get("PORT", 8080))
    loop = asyncio.get_event_loop()
    # run Flask in executor (non-blocking)
    await loop.run_in_executor(None, lambda: app.run(host="0.0.0.0", port=port))

# ------------------ BOT LIFECYCLE ------------------
@bot.event
async def on_ready():
    load_data()
    # start loops if not already
    if not inactivity_poller.is_running():
        inactivity_poller.start()
    if not auto_unmute_loop.is_running():
        auto_unmute_loop.start()
    # start webserver
    asyncio.create_task(start_web())
    print(f"✅ Bot ready: {bot.user} (guilds: {len(bot.guilds)})")

# ------------------ START BOT ------------------
if __name__ == "__main__":
    load_data()
    TOKEN = os.environ.get("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ DISCORD_TOKEN environment variable not set. Exiting.")
        raise SystemExit(1)
    bot.run(TOKEN)
