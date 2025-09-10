# main.py
# Full bot with !rmute/runmute/timetrack/rmlb/rhelp + inactivity + auto-unmute + !rping + Render keep-alive

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

# Roles that trigger "came back active" log & rping access
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
        activity_logs[uid] = {
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
            "rping_on": False,  # NEW: per-user ping toggle
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

# ------------------ EMBED HELPERS ------------------
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
    tz_lines = [f"{emoji} {expire_dt.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
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
    now = datetime.datetime.now(datetime.timezone.utc)
    tz_lines = [f"{emoji} {now.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="🕒 Unmuted At (timezones)", value="\n".join(tz_lines), inline=False)
    return embed

# ------------------ COMMANDS ------------------
@bot.command(name="rmute")
@commands.has_permissions(moderate_members=True)
async def cmd_rmute(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    seconds = parse_duration_abbrev(duration)
    if seconds is None:
        await ctx.reply("❌ Invalid duration. Use formats like `1m`, `1h`, `1d`, `27d`.", mention_author=False)
        return
    guild = ctx.guild
    if not guild:
        await ctx.reply("❌ This command must be used in a guild.", mention_author=False)
        return
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await ctx.reply("❌ Muted role not found.", mention_author=False)
        return
    try:
        await member.add_roles(muted_role, reason=f"Muted by {ctx.author}")
        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        try:
            await member.timeout(until, reason=f"Muted by {ctx.author}: {reason}")
        except Exception:
            pass
        try:
            await member.send(f"You have been muted in **{guild.name}** for {fmt_duration(seconds)}.\nReason: {reason}")
        except Exception:
            pass
    except discord.Forbidden:
        await ctx.reply("⚠️ Permission error adding role or timeout.", mention_author=False)
        return

    log = get_user_log(member.id)
    now = datetime.datetime.now(datetime.timezone.utc)
    log.update({
        "mute_expires": (now + datetime.timedelta(seconds=seconds)).isoformat(),
        "mute_reason": reason,
        "mute_responsible": str(ctx.author.id),
    })
    muter_log = get_user_log(ctx.author.id)
    muter_log["mute_count"] = muter_log.get("mute_count", 0) + 1
    muter_log["last_mute_at"] = now.isoformat()
    await save_data_async()
    embed = build_mute_embed(member, ctx.author, reason, seconds)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        try: await log_channel.send(embed=embed)
        except Exception: pass
    await ctx.reply(f"✅ {member.mention} muted for {fmt_duration(seconds)}.", mention_author=False)

# ------------------ RHELP UPDATE WITH !rping ------------------
@bot.command(name="rhelp")
async def cmd_rhelp(ctx: commands.Context):
    embed = discord.Embed(title="🤖 Moderation Commands", color=0x3498db)
    embed.add_field(name="!rmute", value="`!rmute [user] [duration] [reason]` — Mute a user.", inline=False)
    embed.add_field(name="!runmute", value="`!runmute [user]` — Unmute a user.", inline=False)
    embed.add_field(name="!timetrack", value="`!timetrack [user]` — Shows online/offline and daily/weekly/monthly counters.", inline=False)
    embed.add_field(name="!rmlb", value="`!rmlb [true/false]` — Leaderboard of who used !rmute most.", inline=False)
    embed.add_field(name="!rping", value="`!rping [on/off] [user]` — Toggle ping when a user goes offline or back online. Only roles with access can use this.", inline=False)
    embed.set_footer(text="Use prefix commands only.")
    await ctx.send(embed=embed)
    # ------------------ RUNMUTE COMMAND ------------------
@bot.command(name="runmute")
@commands.has_permissions(moderate_members=True)
async def cmd_runmute(ctx: commands.Context, member: discord.Member):
    guild = ctx.guild
    if not guild:
        return await ctx.reply("❌ This command must be used in a guild.", mention_author=False)

    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role is None:
        return await ctx.reply("❌ Muted role not found.", mention_author=False)

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

    try:
        if muted_role in member.roles:
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
        return await ctx.reply("⚠️ I don't have permission to remove Muted role or timeout.", mention_author=False)
    except Exception as e:
        return await ctx.reply(f"⚠️ Failed to unmute: {e}", mention_author=False)

    log.update({
        "mute_expires": None,
        "mute_reason": None,
        "mute_responsible": None,
    })
    await save_data_async()

    embed = build_unmute_embed(member, ctx.author, orig_reason, orig_duration_seconds)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        try:
            await log_channel.send(embed=embed)
        except Exception:
            pass
    await ctx.reply(f"✅ {member.mention} has been unmuted.", mention_author=False)

# ------------------ TIMETRACK COMMAND ------------------
@bot.command(name="timetrack")
async def cmd_timetrack(ctx: commands.Context, member: discord.Member = None):
    member = member or ctx.author
    log = get_user_log(member.id)

    embed = discord.Embed(title=f"⏱️ Timetrack — {member.display_name}", color=0x00B894,
                          timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🟢 Online Time", value=fmt_duration(log.get("online_seconds", 0)), inline=True)
    embed.add_field(name="🔴 Offline Time", value=fmt_duration(log.get("offline_seconds", 0)), inline=True)
    embed.add_field(name="📅 Daily", value=fmt_duration(log.get("daily_seconds", 0)), inline=True)
    embed.add_field(name="📅 Weekly", value=fmt_duration(log.get("weekly_seconds", 0)), inline=True)
    embed.add_field(name="📅 Monthly", value=fmt_duration(log.get("monthly_seconds", 0)), inline=True)
    tz_lines = [f"{emoji} {datetime.datetime.now(datetime.timezone.utc).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
    await ctx.send(embed=embed)

# ------------------ RMLB COMMAND ------------------
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

# ------------------ RPING COMMAND ------------------
@bot.command(name="rping")
async def cmd_rping(ctx: commands.Context, toggle: str, member: discord.Member = None):
    """Toggle ping for offline/online notifications."""
    if not ctx.author.guild_permissions.administrator and not any(rid in [r.id for r in ctx.author.roles] for rid in ACTIVE_LOG_ROLE_IDS):
        return await ctx.reply("❌ You do not have permission to use !rping.", mention_author=False)
    toggle = toggle.lower()
    if toggle not in {"on", "off"}:
        return await ctx.reply("❌ Usage: `!rping [on/off] [user]`", mention_author=False)
    member = member or ctx.author
    user_log = get_user_log(member.id)
    user_log["rping_on"] = toggle == "on"
    await save_data_async()
    await ctx.reply(f"✅ Ping for {member.display_name} set to `{toggle}`.", mention_author=False)

# ------------------ ON_MESSAGE EVENT ------------------
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    uid = message.author.id
    now = datetime.datetime.now(datetime.timezone.utc)
    log = get_user_log(uid)

    # reset offline state
    log["last_message"] = now.isoformat()
    log["offline_seconds"] = 0
    log["offline_start"] = None
    if not log.get("offline_delay"):
        log["offline_delay"] = random.randint(50, 60)

    # daily/weekly/monthly counters
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

    log["daily_seconds"] += 1
    log["weekly_seconds"] += 1
    log["monthly_seconds"] += 1

    # check if user was inactive and now back
    if log.get("inactive", False):
        guild = message.guild
        member = message.author
        send_back_active = any(guild.get_role(rid) in member.roles for rid in ACTIVE_LOG_ROLE_IDS if guild.get_role(rid))
        if send_back_active:
            ping = member.mention if log.get("rping_on", False) else member.display_name
            lc = guild.get_channel(LOG_CHANNEL_ID)
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
            log["offline_delay"] = random.randint(50, 60)
        last_msg_iso = log.get("last_message")
        if last_msg_iso:
            try:
                last_msg_dt = datetime.datetime.fromisoformat(last_msg_iso)
            except Exception:
                last_msg_dt = now
                log["last_message"] = now.isoformat()
            delta = (now - last_msg_dt).total_seconds()
            delay = int(log.get("offline_delay", 53))
            if delta >= delay:
                if not log.get("offline_start"):
                    log["offline_start"] = (last_msg_dt + datetime.timedelta(seconds=delay)).isoformat()
                    try:
                        guild = bot.get_guild(GUILD_ID)
                        if guild:
                            member = guild.get_member(int(uid))
                            if member:
                                lc = guild.get_channel(LOG_CHANNEL_ID)
                                if lc:
                                    ping = member.mention if log.get("rping_on", False) else member.display_name
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
                log["offline_seconds"] = 0
                log["offline_start"] = None
                log["inactive"] = False
            if not log.get("inactive", False):
                log["online_seconds"] += 5
                log["daily_seconds"] += 5
                log["weekly_seconds"] += 5
                log["monthly_seconds"] += 5
    await save_data_async()


@tasks.loop(seconds=15)
async def auto_unmute_loop():
    """Every 15s, check mutes and unmute expired ones."""
    now = datetime.datetime.now(datetime.timezone.utc)
    for uid, log in list(activity_logs.items()):
        expire_iso = log.get("mute_expires")
        if expire_iso:
            try:
                expire_dt = datetime.datetime.fromisoformat(expire_iso)
            except Exception:
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
                        pass
                    # log channel message
                    log_channel = guild.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        try:
                            await log_channel.send(f"🔊 {member.mention} was auto-unmuted (mute expired).")
                        except Exception:
                            pass
                # clear mute info
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                await save_data_async()

# ------------------ FLASK KEEP-ALIVE ------------------
app = Flask("botkeepalive")

@app.route("/")
def home():
    return "✅ Bot is running!"

async def start_web():
    port = int(os.environ.get("PORT", 8080))
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: app.run(host="0.0.0.0", port=port))

# ------------------ BOT READY ------------------
@bot.event
async def on_ready():
    load_data()
    if not inactivity_poller.is_running():
        inactivity_poller.start()
    if not auto_unmute_loop.is_running():
        auto_unmute_loop.start()
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
