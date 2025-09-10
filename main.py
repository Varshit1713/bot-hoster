# ------------------ IMPORTS ------------------
import os
import discord
from discord.ext import commands, tasks
import datetime
import json
import random
from zoneinfo import ZoneInfo
import threading

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    exit()

GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648
DATA_FILE = "activity_logs.json"

INACTIVITY_THRESHOLD_MIN = 50  # seconds
INACTIVITY_THRESHOLD_MAX = 60  # seconds

TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo")
}

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ DATA HANDLING ------------------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        activity_logs = json.load(f)
else:
    activity_logs = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(activity_logs, f, indent=4)

def get_user_log(user_id):
    uid = str(user_id)
    if uid not in activity_logs:
        activity_logs[uid] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "offline_start": None,
            "offline_delay": None,
            "last_message": None,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_daily_reset": None,
            "last_weekly_reset": None,
            "last_monthly_reset": None,
            "mute_count": 0
        }
    return activity_logs[uid]

def format_duration(seconds):
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    result = ""
    if days > 0:
        result += f"{days}d "
    if hrs > 0:
        result += f"{hrs}h "
    if mins > 0:
        result += f"{mins}m "
    result += f"{sec}s"
    return result

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    timetrack_update.start()
    mute_check.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    log = get_user_log(message.author.id)
    log["offline_seconds"] = 0
    log["offline_start"] = None
    log["offline_delay"] = None
    log["last_message"] = datetime.datetime.utcnow().isoformat()
    save_data()
    await bot.process_commands(message)

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=1)
async def timetrack_update():
    now = datetime.datetime.utcnow()
    for uid, log in activity_logs.items():
        last_msg = log.get("last_message")
        if last_msg:
            last_msg_time = datetime.datetime.fromisoformat(last_msg)
            if not log.get("offline_delay"):
                log["offline_delay"] = random.randint(INACTIVITY_THRESHOLD_MIN, INACTIVITY_THRESHOLD_MAX)
            delta_since_last_msg = (now - last_msg_time).total_seconds()
            if delta_since_last_msg >= log["offline_delay"]:
                if not log.get("offline_start"):
                    log["offline_start"] = last_msg_time + datetime.timedelta(seconds=log["offline_delay"])
                log["offline_seconds"] = (now - log["offline_start"]).total_seconds()
            else:
                log["online_seconds"] += 1
                log["offline_start"] = None
                log["offline_seconds"] = 0
        else:
            # Count offline time for users never online
            log["offline_seconds"] += 1
        # Daily / Weekly / Monthly resets
        today = datetime.datetime.utcnow().date()
        weekday = today.isocalendar()[1]
        month = today.month
        if not log.get("last_daily_reset") or log["last_daily_reset"] != str(today):
            log["daily_seconds"] = 0
            log["last_daily_reset"] = str(today)
        if not log.get("last_weekly_reset") or log["last_weekly_reset"] != str(weekday):
            log["weekly_seconds"] = 0
            log["last_weekly_reset"] = str(weekday)
        if not log.get("last_monthly_reset") or log["last_monthly_reset"] != str(month):
            log["monthly_seconds"] = 0
            log["last_monthly_reset"] = str(month)
        log["daily_seconds"] += 1
        log["weekly_seconds"] += 1
        log["monthly_seconds"] += 1
    save_data()

@tasks.loop(seconds=5)
async def mute_check():
    now = datetime.datetime.utcnow()
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    for uid, log in activity_logs.items():
        if log.get("mute_expires"):
            expires = datetime.datetime.fromisoformat(log["mute_expires"])
            if now >= expires:
                member = guild.get_member(int(uid))
                if member:
                    muted_role = guild.get_role(MUTED_ROLE_ID)
                    if muted_role in member.roles:
                        try:
                            await member.remove_roles(muted_role)
                        except discord.Forbidden:
                            print(f"⚠️ Missing permission to remove Muted role from {member}.")
                    try:
                        await member.edit(timed_out_until=None)
                    except discord.Forbidden:
                        pass
                    await send_mute_log(member, unmuted=True, log=log)
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
    save_data()

# ------------------ HELP COMMAND ------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="📜 Bot Help", color=0x00FFFF)
    embed.add_field(
        name="!rmute",
        value="!rmute [user] [duration] [reason] → Mutes a user, gives role, sends embed in log channel, DMs user, tracks duration",
        inline=False
    )
    embed.add_field(
        name="!runmute",
        value="!runmute [user] [reason] → Unmutes a user, removes role, sends embed in log channel, DMs user",
        inline=False
    )
    embed.add_field(
        name="!timetrack",
        value="!timetrack [user] → Shows online/offline, daily/weekly/monthly times with fancy emojis",
        inline=False
    )
    embed.add_field(
        name="!rmlb",
        value="!rmlb [true/false] → Shows top users who used !rmute, true = public, false = privately",
        inline=False
    )
    await ctx.send(embed=embed)

# ------------------ MUTE COMMAND ------------------
def parse_duration(duration: str) -> int:
    """Convert 1d, 2h, 30m, 45s to seconds"""
    try:
        unit = duration[-1]
        val = int(duration[:-1])
        if unit == "d":
            return val * 86400
        elif unit == "h":
            return val * 3600
        elif unit == "m":
            return val * 60
        elif unit == "s":
            return val
    except:
        return None

async def send_mute_log(member, reason=None, responsible=None, duration=None, unmuted=False, log=None):
    guild = bot.get_guild(GUILD_ID)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        print("⚠️ Log channel not found.")
        return
    embed = discord.Embed(
        title="🔒 Mute Log" if not unmuted else "✅ Unmute Log",
        color=0xFF0000 if not unmuted else 0x00FF00,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=member.mention, inline=True)
    if responsible:
        embed.add_field(name="Responsible", value=responsible.mention, inline=True)
    if reason:
        embed.add_field(name="Reason", value=reason, inline=False)
    if duration and not unmuted:
        embed.add_field(name="Duration", value=duration, inline=True)
        unmute_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=parse_duration(duration))
        unmute_time = unmute_time.replace(tzinfo=ZoneInfo("UTC"))
        tz_lines = [f"{emoji} {unmute_time.astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
        embed.add_field(name="Unmute Time", value="\n".join(tz_lines), inline=False)
    if unmuted and log:
        embed.add_field(name="Original Reason", value=log.get("mute_reason", "N/A"), inline=False)
    try:
        await log_channel.send(embed=embed)
    except discord.Forbidden:
        pass

@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str):
    seconds = parse_duration(duration)
    if seconds is None:
        await ctx.send("❌ Invalid duration. Use formats like 1m, 2h, 1d, 30s.")
        return
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role:
        try:
            await member.add_roles(muted_role)
        except discord.Forbidden:
            await ctx.send(f"⚠️ Missing permissions to give muted role to {member}.")
    try:
        await member.edit(timed_out_until=datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds))
    except discord.Forbidden:
        await ctx.send(f"⚠️ Missing permissions to timeout {member}.")
    # Log
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] += 1
    save_data()
    try:
        await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
    except discord.Forbidden:
        pass
    await send_mute_log(member, reason=reason, responsible=ctx.author, duration=duration)
    await ctx.send(f"✅ {member.mention} has been muted for {duration}.")

@bot.command()
async def runmute(ctx, member: discord.Member, *, reason: str = "Manual unmute"):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
        except discord.Forbidden:
            await ctx.send(f"⚠️ Missing permissions to remove muted role from {member}.")
        try:
            await member.edit(timed_out_until=None)
        except discord.Forbidden:
            pass
        log = get_user_log(member.id)
        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        save_data()
        try:
            await member.send(f"✅ You have been unmuted. Reason: {reason}")
        except discord.Forbidden:
            pass
        await send_mute_log(member, unmuted=True, log=log)
        await ctx.send(f"✅ {member.mention} has been unmuted.")
    else:
        await ctx.send(f"ℹ️ {member.mention} is not muted.")

# ------------------ TIMETRACK COMMAND ------------------
@bot.command()
async def timetrack(ctx, member: discord.Member = None):
    member = member or ctx.author
    log = get_user_log(member.id)
    online_time = format_duration(log.get("online_seconds", 0))
    offline_time = format_duration(log.get("offline_seconds", 0))
    daily_time = format_duration(log.get("daily_seconds", 0))
    weekly_time = format_duration(log.get("weekly_seconds", 0))
    monthly_time = format_duration(log.get("monthly_seconds", 0))

    tz_lines = [
        f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}"
        for emoji, tz in TIMEZONES.items()
    ]

    embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🟢 Online Time", value=online_time, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline_time, inline=True)
    embed.add_field(name="Daily", value=daily_time, inline=True)
    embed.add_field(name="Weekly", value=weekly_time, inline=True)
    embed.add_field(name="Monthly", value=monthly_time, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
    await ctx.send(embed=embed)

# ------------------ RMUTE LEADERBOARD ------------------
@bot.command()
async def rmlb(ctx, public: str = "false"):
    public = public.lower() == "true"
    leaderboard = sorted(activity_logs.items(), key=lambda x: x[1].get("mute_count", 0), reverse=True)
    top10 = leaderboard[:10]
    lines = []
    for idx, (uid, data) in enumerate(top10, 1):
        member = ctx.guild.get_member(int(uid))
        if member:
            lines.append(f"{idx}. {member.display_name} - {data.get('mute_count', 0)} mutes")
    embed = discord.Embed(title="🏆 RMute Leaderboard", description="\n".join(lines) or "No data", color=0xFFD700)
    if public:
        await ctx.send(embed=embed)
    else:
        await ctx.author.send(embed=embed)

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
