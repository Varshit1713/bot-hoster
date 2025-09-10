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
INACTIVITY_MIN = 50
INACTIVITY_MAX = 60

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
        data = json.load(f)
else:
    data = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_user_log(user_id):
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
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
            "mutes_done": 0
        }
    return data[uid]

def format_seconds(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, min_rem = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {min_rem}m"
    days, hr_rem = divmod(hours, 24)
    return f"{days}d {hr_rem}h"

def format_duration(seconds):
    # Full style D H M S
    days, rem = divmod(int(seconds), 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    return f"{days}D {hrs}H {mins}M {sec}S"

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    timetrack_task.start()
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
async def timetrack_task():
    now = datetime.datetime.utcnow()
    for uid, log in data.items():
        # Handle offline timer
        last_msg = log.get("last_message")
        if last_msg:
            last_time = datetime.datetime.fromisoformat(last_msg)
            if not log.get("offline_delay"):
                log["offline_delay"] = random.randint(INACTIVITY_MIN, INACTIVITY_MAX)
            delta = (now - last_time).total_seconds()
            if delta >= log["offline_delay"]:
                if not log.get("offline_start"):
                    log["offline_start"] = last_time + datetime.timedelta(seconds=log["offline_delay"])
                log["offline_seconds"] = (now - log["offline_start"]).total_seconds()
            else:
                log["online_seconds"] += 1
                log["offline_seconds"] = 0
                log["offline_start"] = None
        else:
            # user never sent a message
            log["offline_seconds"] += 1

        # Daily/weekly/monthly resets
        today = datetime.datetime.utcnow().date()
        week = today.isocalendar()[1]
        month = today.month
        if log.get("last_daily_reset") != str(today):
            log["daily_seconds"] = 0
            log["last_daily_reset"] = str(today)
        if log.get("last_weekly_reset") != str(week):
            log["weekly_seconds"] = 0
            log["last_weekly_reset"] = str(week)
        if log.get("last_monthly_reset") != str(month):
            log["monthly_seconds"] = 0
            log["last_monthly_reset"] = str(month)

        log["daily_seconds"] += 1
        log["weekly_seconds"] += 1
        log["monthly_seconds"] += 1
    save_data()

@tasks.loop(seconds=1)
async def mute_check():
    now = datetime.datetime.utcnow()
    guild = bot.get_guild(GUILD_ID)
    for uid, log in data.items():
        if log.get("mute_expires"):
            expire_time = datetime.datetime.fromisoformat(log["mute_expires"])
            if now >= expire_time:
                member = guild.get_member(int(uid))
                muted_role = guild.get_role(MUTED_ROLE_ID)
                if member:
                    try:
                        await member.remove_roles(muted_role)
                        # remove Discord timeout
                        await member.edit(timed_out_until=None)
                        await send_mute_embed(member, unmuted=True, log=log)
                    except discord.Forbidden:
                        print(f"⚠️ Missing permission to unmute {member}")
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
    save_data()

# ------------------ EMBED HELPERS ------------------
async def send_mute_embed(member, reason=None, responsible=None, duration=None, unmuted=False, log=None):
    guild = bot.get_guild(GUILD_ID)
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if not channel:
        return

    embed = discord.Embed(
        title="✅ Unmute" if unmuted else "🔒 Mute",
        color=0x00FF00 if unmuted else 0xFF0000,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=member.mention, inline=True)
    if responsible:
        embed.add_field(name="Moderator", value=responsible.mention, inline=True)
    if reason and not unmuted:
        embed.add_field(name="Reason", value=reason, inline=False)
    if duration and not unmuted:
        embed.add_field(name="Duration", value=duration, inline=True)
        expire_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=int(duration))
        tz_lines = [f"{emoji} {expire_time.replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
        embed.add_field(name="Unmute Time", value="\n".join(tz_lines), inline=False)
    if unmuted and log:
        embed.add_field(name="Original Reason", value=log.get("mute_reason", "N/A"), inline=False)

    await channel.send(embed=embed)

# ------------------ COMMANDS ------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="🛠️ Bot Triggers Help", color=0x00FFFF)
    embed.add_field(name="!rmute [user] [duration] [reason]", value="Mutes a user. Duration examples: 1m, 1h, 1d.", inline=False)
    embed.add_field(name="!runmute [user]", value="Unmutes a user manually.", inline=False)
    embed.add_field(name="!timetrack [user]", value="Shows online/offline time, daily/weekly/monthly, timezones.", inline=False)
    embed.add_field(name="!rmlb [true|false]", value="Shows mute leaderboard (true=public, false=private).", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await ctx.send("Muted role not found.")
        return

    # parse duration string
    try:
        if duration.endswith("s"):
            seconds = int(duration[:-1])
        elif duration.endswith("m"):
            seconds = int(duration[:-1]) * 60
        elif duration.endswith("h"):
            seconds = int(duration[:-1]) * 3600
        elif duration.endswith("d"):
            seconds = int(duration[:-1]) * 86400
        else:
            seconds = int(duration)  # default seconds
    except:
        await ctx.send("❌ Invalid duration format. Use 1s, 1m, 1h, 1d.")
        return

    # Apply Discord timeout
    try:
        await member.edit(timed_out_until=datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds))
    except discord.Forbidden:
        await ctx.send(f"⚠️ Missing permission to timeout {member}.")
        return

    # Add muted role
    try:
        await member.add_roles(muted_role)
    except discord.Forbidden:
        await ctx.send(f"⚠️ Missing permission to add role to {member}.")
        return

    # Update data
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mutes_done"] = log.get("mutes_done", 0) + 1
    save_data()

    # DM user
    try:
        await member.send(f"You have been muted in {guild.name} for {duration} due to: {reason}")
    except:
        pass

    # Send embed
    await send_mute_embed(member, reason=reason, responsible=ctx.author, duration=str(seconds))

    await ctx.send(f"✅ {member.mention} has been muted for {duration}.")

@bot.command()
async def runmute(ctx, member: discord.Member):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    if muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
            await member.edit(timed_out_until=None)
            await send_mute_embed(member, unmuted=True, log=log)
            try:
                await member.send(f"You have been unmuted in {guild.name}.")
            except:
                pass
        except discord.Forbidden:
            await ctx.send(f"⚠️ Missing permission to unmute {member}.")
            return

        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        save_data()
        await ctx.send(f"✅ {member.mention} has been unmuted.")
    else:
        await ctx.send(f"ℹ️ {member.mention} is not muted.")

@bot.command()
async def timetrack(ctx, member: discord.Member = None):
    member = member or ctx.author
    log = get_user_log(member.id)

    online = format_duration(log.get("online_seconds", 0))
    offline = format_duration(log.get("offline_seconds", 0))
    daily = format_duration(log.get("daily_seconds", 0))
    weekly = format_duration(log.get("weekly_seconds", 0))
    monthly = format_duration(log.get("monthly_seconds", 0))
    tz_lines = [f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]

    embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🟢 Online Time", value=online, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline, inline=True)
    embed.add_field(name="Daily", value=daily, inline=True)
    embed.add_field(name="Weekly", value=weekly, inline=True)
    embed.add_field(name="Monthly", value=monthly, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def rmlb(ctx, public: str = "false"):
    # Leaderboard for mutes done
    leaderboard = sorted(data.items(), key=lambda x: x[1].get("mutes_done",0), reverse=True)
    lines = []
    for idx, (uid, log) in enumerate(leaderboard[:10], start=1):
        member = ctx.guild.get_member(int(uid))
        if member:
            lines.append(f"{idx}. {member.display_name} - {log.get('mutes_done',0)} mutes")

    embed = discord.Embed(title="🏆 Mute Leaderboard", description="\n".join(lines), color=0xFFD700)
    if public.lower() == "true":
        await ctx.send(embed=embed)
    else:
        await ctx.author.send(embed=embed)
        await ctx.send("✅ Leaderboard sent privately.")

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
