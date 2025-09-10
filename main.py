# main.py
# Full merged script implementing /rhelp, !rmute, !runmute, !timetrack with logging + persistence.

import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import json
import random
from zoneinfo import ZoneInfo
from flask import Flask
import threading
import sys

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    sys.exit(1)

# IDs you provided
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

DATA_FILE = "activity_logs.json"

# Offline threshold random range (seconds)
INACTIVITY_THRESHOLD_MIN = 50
INACTIVITY_THRESHOLD_MAX = 60

# Timezones to display
TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo")
}

# ------------------ INTENTS & BOT ------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ FLASK (uptime) ------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ------------------ DATA ------------------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        activity_logs = json.load(f)
else:
    activity_logs = {}

def save_data():
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
            "last_message": None,            # ISO timestamp of last message (UTC)
            "mute_expires": None,            # ISO timestamp UTC
            "mute_reason": None,
            "mute_responsible": None,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_daily_reset": None,        # date str
            "last_weekly_reset": None,       # iso week number str
            "last_monthly_reset": None       # month number str
        }
    return activity_logs[uid]

def format_dhms(seconds: float) -> str:
    """Return D H M S string and also a compact HH:MM:SS for embed lines."""
    s = int(seconds)
    days, rem = divmod(s, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{days}D {hours}H {minutes}M {secs}S"

def format_hms(seconds: float) -> str:
    s = int(seconds)
    hours, rem = divmod(s, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"

# ------------------ STARTUP / COMMAND CLEANUP ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (id: {bot.user.id})")
    # Optional: clear guild commands once to avoid slash duplication, then register rhelp
    guild = discord.Object(id=GUILD_ID)
    try:
        # Delete all existing guild commands to avoid duplication (one-time)
        await bot.tree.clear_commands(guild=guild)
    except Exception:
        pass

    # Ensure rhelp is registered to the guild (slash)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)

    timetrack_update.start()
    mute_check.start()
    print("Background tasks started.")

# ------------------ MESSAGE TRACKING ------------------
@bot.event
async def on_message(message: discord.Message):
    # update last_message for any non-bot user
    if message.author.bot:
        return

    log = get_user_log(message.author.id)
    # Reset offline counters when user is active
    log["offline_seconds"] = 0
    log["offline_start"] = None
    log["offline_delay"] = None
    log["last_message"] = datetime.datetime.utcnow().isoformat()
    save_data()

    # allow commands to run
    await bot.process_commands(message)

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=5)
async def timetrack_update():
    now = datetime.datetime.utcnow()
    today = now.date()
    iso_week = today.isocalendar()[1]
    month = today.month

    for uid, log in list(activity_logs.items()):
        last_message_iso = log.get("last_message")
        # Randomize delay if not already set
        if last_message_iso:
            last_msg_time = datetime.datetime.fromisoformat(last_message_iso)
            if not log.get("offline_delay"):
                log["offline_delay"] = random.randint(INACTIVITY_THRESHOLD_MIN, INACTIVITY_THRESHOLD_MAX)

            delta_since = (now - last_msg_time).total_seconds()
            # user is offline if delta_since >= offline_delay
            if delta_since >= log["offline_delay"]:
                # ensure offline_start is set (time when offline timer began)
                if not log.get("offline_start"):
                    log["offline_start"] = (last_msg_time + datetime.timedelta(seconds=log["offline_delay"])).isoformat()
                # update offline_seconds
                offline_start = datetime.datetime.fromisoformat(log["offline_start"])
                log["offline_seconds"] = (now - offline_start).total_seconds()
                # *do not* increment online_seconds while offline
            else:
                # user considered online
                log["offline_start"] = None
                log["offline_seconds"] = 0
                log["online_seconds"] = log.get("online_seconds", 0) + 5

                # increment daily/weekly/monthly only when online
                # Reset periods if needed
                if not log.get("last_daily_reset") or log["last_daily_reset"] != str(today):
                    log["daily_seconds"] = 0
                    log["last_daily_reset"] = str(today)
                if not log.get("last_weekly_reset") or log["last_weekly_reset"] != str(iso_week):
                    log["weekly_seconds"] = 0
                    log["last_weekly_reset"] = str(iso_week)
                if not log.get("last_monthly_reset") or log["last_monthly_reset"] != str(month):
                    log["monthly_seconds"] = 0
                    log["last_monthly_reset"] = str(month)

                log["daily_seconds"] = log.get("daily_seconds", 0) + 5
                log["weekly_seconds"] = log.get("weekly_seconds", 0) + 5
                log["monthly_seconds"] = log.get("monthly_seconds", 0) + 5

        else:
            # no last message recorded — do nothing for now
            pass

    save_data()

@tasks.loop(seconds=5)
async def mute_check():
    now = datetime.datetime.utcnow()
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        # bot may not be in the guild or it's not cached yet
        return

    for uid, log in list(activity_logs.items()):
        mute_expires_iso = log.get("mute_expires")
        if mute_expires_iso:
            expires = datetime.datetime.fromisoformat(mute_expires_iso)
            if now >= expires:
                # time to unmute
                member = guild.get_member(int(uid))
                if member:
                    muted_role = guild.get_role(MUTED_ROLE_ID)
                    if muted_role and muted_role in member.roles:
                        try:
                            await member.remove_roles(muted_role, reason="Automatic unmute (duration expired)")
                        except discord.Forbidden:
                            print(f"⚠️ Missing permission to remove muted role from {member}.")
                        # also attempt to clear Discord timeout
                        try:
                            await member.edit(timeout=None, reason="Automatic unmute (duration expired)")
                        except Exception:
                            pass
                        # send unmute log
                        await send_unmute_log(member, log=log, auto=True)

                # clear mute data
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                save_data()

# ------------------ EMBED HELPERS / LOGGING ------------------
async def send_mute_log(member: discord.Member, reason: str, responsible: discord.Member, duration_display: str, unmute_dt_utc: datetime.datetime):
    """Send a fancy mute embed to the log channel."""
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        print("⚠️ Log channel not found or bot lacks access.")
        return

    embed = discord.Embed(title="🔒 User Muted", color=0xFF6B6B, timestamp=datetime.datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Muted User", value=f"{member.mention} (`{member}`)", inline=True)
    embed.add_field(name="👮 Muted By", value=f"{responsible.mention}", inline=True)
    embed.add_field(name="📝 Reason", value=reason, inline=False)
    embed.add_field(name="⏳ Duration", value=duration_display, inline=True)

    # show unmute time in four timezones
    tz_lines = []
    for label, tz in TIMEZONES.items():
        tz_time = unmute_dt_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
        tz_lines.append(f"{label} {tz_time}")
    embed.add_field(name="🕒 Unmute Time", value="\n".join(tz_lines), inline=False)

    try:
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        print("⚠️ Cannot send embed to log channel (missing permissions).")

async def send_unmute_log(member: discord.Member, log: dict, auto: bool=False, run_by: discord.Member=None):
    """Send a fancy unmute embed to the log channel. If auto=True it was automatic expiry."""
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        print("⚠️ Log channel not found or bot lacks access.")
        return

    embed = discord.Embed(title="✅ User Unmuted", color=0x66FF99, timestamp=datetime.datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 Unmuted User", value=f"{member.mention} (`{member}`)", inline=True)

    if auto:
        embed.add_field(name="🔁 Unmuted By", value="Automatic (mute expired)", inline=True)
    else:
        embed.add_field(name="🔁 Unmuted By", value=f"{run_by.mention if run_by else 'Unknown'}", inline=True)

    # include original reason and original duration if present in log
    orig_reason = log.get("mute_reason") or "N/A"
    embed.add_field(name="📝 Original Reason", value=orig_reason, inline=False)

    try:
        # If original mute_expires existed (it was cleared) we can't compute orig duration here reliably.
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        print("⚠️ Cannot send embed to log channel (missing permissions).")

# ------------------ TRIGGERS & SLASH /rhelp ------------------
# /rhelp - slash command (single help command)
@bot.tree.command(name="rhelp", description="Show help for the bot triggers")
async def rhelp(interaction: discord.Interaction):
    embed = discord.Embed(title="📘 Bot Help — Triggers", color=0x00CCFF, timestamp=datetime.datetime.utcnow())
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user else None)
    embed.add_field(name="🔹 !timetrack [@user]", value="Show online/offline/daily/weekly/monthly time and current timezones. If no user provided, shows your data.", inline=False)
    embed.add_field(name="🔹 !rmute [@user] [duration_minutes] [reason]", value="Mute a user (applies timeout + muted role). Logs to the log channel.", inline=False)
    embed.add_field(name="🔹 !runmute [@user] [optional reason]", value="Unmute a user (removes timeout + muted role). Logs to the log channel.", inline=False)
    embed.add_field(name="Notes", value="Offline time starts after 50–60 seconds of inactivity. Daily/Weekly/Monthly counters reset when user becomes active for those periods.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# TEXT: !timetrack
@bot.command(name="timetrack")
async def cmd_timetrack(ctx: commands.Context, member: discord.Member = None):
    member = member or ctx.author
    log = get_user_log(member.id)

    online_total = log.get("online_seconds", 0)
    offline_total = log.get("offline_seconds", 0)
    daily = log.get("daily_seconds", 0)
    weekly = log.get("weekly_seconds", 0)
    monthly = log.get("monthly_seconds", 0)

    # fallback: if daily/weekly/monthly are 0 but online_total >0 show online_total until they exceed
    daily_display = format_dhms(daily) if daily > 0 else format_dhms(online_total)
    weekly_display = format_dhms(weekly) if weekly > 0 else format_dhms(online_total)
    monthly_display = format_dhms(monthly) if monthly > 0 else format_dhms(online_total)

    tz_lines = []
    now_utc = datetime.datetime.utcnow().replace(tzinfo=ZoneInfo("UTC"))
    for label, tz in TIMEZONES.items():
        tz_lines.append(f"{label} {now_utc.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}")

    embed = discord.Embed(title=f"⏱️ Timetrack — {member.display_name}", color=0x88EE88, timestamp=datetime.datetime.utcnow())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🟢 Online Time", value=format_dhms(online_total), inline=True)
    embed.add_field(name="🔴 Offline Time", value=format_dhms(offline_total), inline=True)
    embed.add_field(name="📅 Daily", value=daily_display, inline=True)
    embed.add_field(name="🗓️ Weekly", value=weekly_display, inline=True)
    embed.add_field(name="🕒 Monthly", value=monthly_display, inline=True)
    embed.add_field(name="🌍 Timezones", value="\n".join(tz_lines), inline=False)

    await ctx.send(embed=embed)

# TEXT: !rmute
@bot.command(name="rmute")
@commands.has_permissions(moderate_members=True, manage_roles=True)
async def cmd_rmute(ctx: commands.Context, member: discord.Member, duration: int, *, reason: str = "No reason provided"):
    """Mute a member: apply role + Discord timeout, log to channel."""
    guild = ctx.guild
    if not guild:
        await ctx.send("❌ This command must be used in a server.")
        return

    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await ctx.send("❌ Muted role not found on this server.")
        return

    # Add role
    try:
        await member.add_roles(muted_role, reason=f"Muted by {ctx.author} — {reason}")
    except discord.Forbidden:
        await ctx.send("⚠️ I don't have permission to add the muted role. Make sure my role is above the muted role.")
        return

    # Apply Discord timeout (requires Moderate Members permission)
    delta = datetime.timedelta(minutes=duration)
    timeout_until = datetime.datetime.now(datetime.timezone.utc) + delta
    try:
        await member.edit(timeout=timeout_until, reason=f"Muted by {ctx.author} — {reason}")
    except discord.Forbidden:
        # Timeout failed — role still applied
        await ctx.send("⚠️ Could not apply Discord timeout (missing permission). Role was added though.")
    except Exception:
        # swallow other edit errors but continue
        pass

    # Save mute metadata
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + delta).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    save_data()

    # compute display duration and unmute dt in UTC
    duration_display = format_dhms(delta.total_seconds())
    unmute_dt_utc = datetime.datetime.utcnow() + delta

    # send log embed to log channel
    await send_mute_log(member=member, reason=reason, responsible=ctx.author, duration_display=duration_display, unmute_dt_utc=unmute_dt_utc)

    # confirm
    await ctx.send(f"✅ {member.mention} muted for {duration} minute(s). Reason: {reason}")

@cmd_rmute.error
async def cmd_rmute_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command (requires Moderate Members & Manage Roles).")
    else:
        await ctx.send(f"❌ Error: {error}")

# TEXT: !runmute
@bot.command(name="runmute")
@commands.has_permissions(moderate_members=True, manage_roles=True)
async def cmd_runmute(ctx: commands.Context, member: discord.Member, *, reason: str = "Unmuted"):
    guild = ctx.guild
    if not guild:
        await ctx.send("❌ This command must be used in a server.")
        return

    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await ctx.send("❌ Muted role not found on this server.")
        return

    if muted_role not in member.roles:
        await ctx.send(f"ℹ️ {member.mention} is not muted.")
        return

    # Remove the muted role
    try:
        await member.remove_roles(muted_role, reason=f"Unmuted by {ctx.author} — {reason}")
    except discord.Forbidden:
        await ctx.send("⚠️ I don't have permission to remove the muted role.")
        return

    # Clear Discord timeout
    try:
        await member.edit(timeout=None, reason=f"Unmuted by {ctx.author} — {reason}")
    except discord.Forbidden:
        await ctx.send("⚠️ Could not clear Discord timeout (missing permission). Role removed though.")
    except Exception:
        pass

    # fetch log metadata for this user (if any)
    log = get_user_log(member.id)
    # send unmute embed (include who unmuted)
    await send_unmute_log(member=member, log=log, auto=False, run_by=ctx.author)

    # clear stored mute info
    log["mute_expires"] = None
    log["mute_reason"] = None
    log["mute_responsible"] = None
    save_data()

    await ctx.send(f"✅ {member.mention} has been unmuted by {ctx.author.mention}. Reason: {reason}")

@cmd_runmute.error
async def cmd_runmute_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command (requires Moderate Members & Manage Roles).")
    else:
        await ctx.send(f"❌ Error: {error}")

# ------------------ START FLASK & RUN BOT ------------------
# Start Flask webserver in daemon thread so it won't block shutdown
threading.Thread(target=run_web, daemon=True).start()

# Run the bot
bot.run(TOKEN)
