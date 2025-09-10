import discord
from discord.ext import commands, tasks
import datetime
from zoneinfo import ZoneInfo
import json
import asyncio

# ------------------ CONFIG ------------------
TOKEN = "YOUR_BOT_TOKEN_HERE"  # Replace manually in Render secrets
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

TIMEZONES = {
    "🌍 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo")
}

DATA_FILE = "activity_logs.json"

# ------------------ BOT SETUP ------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ DATA HANDLING ------------------
try:
    with open(DATA_FILE, "r") as f:
        activity_logs = json.load(f)
except FileNotFoundError:
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
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_seen": None,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "mute_count": 0
        }
    return activity_logs[uid]

def format_duration(seconds: int):
    seconds = int(seconds)
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)
    parts = []
    if d > 0: parts.append(f"{d}d")
    if h > 0: parts.append(f"{h}h")
    if m > 0: parts.append(f"{m}m")
    if s > 0 or not parts: parts.append(f"{s}s")
    return " ".join(parts)
    # ------------------ RMUTE ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """Mute a user, assign role, DM, and log"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        return await ctx.send("⚠️ Muted role not found.")

    # Convert duration string to seconds
    unit = duration[-1].lower()
    try:
        amount = int(duration[:-1])
    except:
        return await ctx.send("❌ Invalid duration format. Use 1m, 1h, 1d, etc.")
    if unit == "s": seconds = amount
    elif unit == "m": seconds = amount * 60
    elif unit == "h": seconds = amount * 3600
    elif unit == "d": seconds = amount * 86400
    else: return await ctx.send("❌ Invalid duration unit. Use s, m, h, d.")

    delta = datetime.timedelta(seconds=seconds)
    expire_time = datetime.datetime.utcnow() + delta

    try:
        await member.add_roles(muted_role)
        await member.timeout(delta)  # Discord API mute
        try:
            await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
        except:
            pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to mute {member}.")

    log = get_user_log(member.id)
    log["mute_expires"] = expire_time.isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count", 0) + 1
    save_data()

    # Log channel embed
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="🔇 User Muted",
        description=f"{member.mention} has been muted",
        color=0xFF0000,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Muted by", value=ctx.author.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Expires (UTC)", value=expire_time.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
    tz_lines = [f"{emoji} {expire_time.replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="Expires in Timezones", value="\n".join(tz_lines), inline=False)
    if log_channel:
        await log_channel.send(embed=embed)

    await ctx.send(f"✅ {member.mention} has been muted for {duration}.")

# ------------------ RUNMUTE ------------------
@bot.command()
async def runmute(ctx, member: discord.Member):
    """Unmute a member, remove role, send embed and DM"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    if muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
            await member.timeout(None)  # Remove Discord API timeout
            try:
                await member.send("✅ You have been unmuted.")
            except:
                pass
        except discord.Forbidden:
            return await ctx.send(f"⚠️ Missing permissions to unmute {member}.")

        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        save_data()

        # Log embed
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        embed = discord.Embed(
            title="✅ User Unmuted",
            description=f"{member.mention} has been unmuted",
            color=0x00FF00,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Unmuted by", value=ctx.author.mention, inline=True)
        if log_channel:
            await log_channel.send(embed=embed)
        await ctx.send(f"✅ {member.mention} has been unmuted.")
    else:
        await ctx.send(f"ℹ️ {member.mention} is not muted.")

# ------------------ TIMETRACK ------------------
@bot.command()
async def timetrack(ctx, member: discord.Member = None):
    """Shows online/offline, daily, weekly, monthly time with fancy emojis"""
    member = member or ctx.author
    log = get_user_log(member.id)

    online_time = format_duration(log.get("online_seconds", 0))
    offline_time = format_duration(log.get("offline_seconds", 0))
    daily_time = format_duration(log.get("daily_seconds", 0))
    weekly_time = format_duration(log.get("weekly_seconds", 0))
    monthly_time = format_duration(log.get("monthly_seconds", 0))

    tz_lines = [f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]

    embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🟢 Online Time", value=online_time, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline_time, inline=True)
    embed.add_field(name="📅 Daily", value=daily_time, inline=True)
    embed.add_field(name="📅 Weekly", value=weekly_time, inline=True)
    embed.add_field(name="📅 Monthly", value=monthly_time, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
    await ctx.send(embed=embed)

# ------------------ RMLB (Leaderboard) ------------------
@bot.command()
async def rmlb(ctx, public: bool = False):
    """Shows leaderboard of users who used !rmute most"""
    leaderboard = []
    for uid, data in activity_logs.items():
        user = ctx.guild.get_member(int(uid))
        if user and data.get("mute_count"):
            leaderboard.append((user.display_name, data["mute_count"]))
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    top10 = leaderboard[:10]

    desc = "\n".join([f"🏆 {i+1}. {name} → {count} mutes" for i, (name, count) in enumerate(top10)]) or "No data yet."
    embed = discord.Embed(title="📊 !rmute Leaderboard", description=desc, color=0xFFD700)

    if public:
        await ctx.send(embed=embed)
    else:
        await ctx.reply(embed=embed, mention_author=False)
        # ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=1)
async def update_online_times():
    now = datetime.datetime.utcnow()
    for guild in bot.guilds:
        for member in guild.members:
            if member.bot:
                continue
            log = get_user_log(member.id)
            # Consider user online if they sent a message in last 50-60 seconds
            last_msg = log.get("last_message", None)
            online = False
            if last_msg:
                last_msg_dt = datetime.datetime.fromisoformat(last_msg)
                delta = (now - last_msg_dt).total_seconds()
                if delta <= 60:
                    online = True

            # Update online/offline time
            if online:
                log["online_seconds"] = log.get("online_seconds", 0) + 1
                log["offline_seconds"] = 0  # reset offline
            else:
                log["offline_seconds"] = log.get("offline_seconds", 0) + 1

            # Update daily/weekly/monthly counters
            today = now.date()
            week = now.isocalendar()[1]
            month = now.month

            if log.get("daily_date") != today.isoformat():
                log["daily_seconds"] = 0
                log["daily_date"] = today.isoformat()
            if log.get("weekly_week") != week:
                log["weekly_seconds"] = 0
                log["weekly_week"] = week
            if log.get("monthly_month") != month:
                log["monthly_seconds"] = 0
                log["monthly_month"] = month

            save_data()

@tasks.loop(seconds=10)
async def check_mutes():
    now = datetime.datetime.utcnow()
    for guild in bot.guilds:
        muted_role = guild.get_role(MUTED_ROLE_ID)
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        for uid, data in activity_logs.items():
            if data.get("mute_expires"):
                expire_time = datetime.datetime.fromisoformat(data["mute_expires"])
                if now >= expire_time:
                    member = guild.get_member(int(uid))
                    if member:
                        try:
                            await member.remove_roles(muted_role)
                            await member.timeout(None)
                            try:
                                await member.send("✅ Your mute has expired.")
                            except:
                                pass
                        except:
                            pass
                        # Log embed
                        if log_channel and member:
                            embed = discord.Embed(
                                title="⏱️ Mute Expired",
                                description=f"{member.mention}'s mute has expired.",
                                color=0x00FF00,
                                timestamp=datetime.datetime.utcnow()
                            )
                            embed.set_thumbnail(url=member.display_avatar.url)
                            await log_channel.send(embed=embed)

                        # Clear mute info
                        data["mute_expires"] = None
                        data["mute_reason"] = None
                        data["mute_responsible"] = None
                        save_data()

# ------------------ ON MESSAGE UPDATE LAST MESSAGE ------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    log = get_user_log(message.author.id)
    log["last_message"] = datetime.datetime.utcnow().isoformat()
    save_data()
    await bot.process_commands(message)  # Ensure commands still work

# ------------------ START TASKS ------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    update_online_times.start()
    check_mutes.start()

import os

TOKEN = os.environ.get("DISCORD_TOKEN")  # Set this in your Render or local environment
bot.run(TOKEN)
