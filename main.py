import discord
from discord.ext import commands, tasks
import datetime
from zoneinfo import ZoneInfo
import asyncio

# ------------------ CONSTANTS ------------------
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

TIMEZONES = {
    "🌍 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇪🇺 CET": ZoneInfo("Europe/Paris"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo"),
}

# ------------------ DATA ------------------
activity_logs = {}  # user_id: {online_seconds, offline_seconds, daily_seconds, weekly_seconds, monthly_seconds, mute_count, mute_expires, mute_reason, mute_responsible}

# ------------------ BOT INIT ------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ UTILS ------------------
def get_user_log(user_id):
    if str(user_id) not in activity_logs:
        activity_logs[str(user_id)] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "mute_count": 0,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "last_seen": None
        }
    return activity_logs[str(user_id)]

def format_duration(seconds):
    seconds = int(seconds)
    d, h, m, s = 0, 0, 0, 0
    if seconds >= 86400:
        d, seconds = divmod(seconds, 86400)
    if seconds >= 3600:
        h, seconds = divmod(seconds, 3600)
    if seconds >= 60:
        m, seconds = divmod(seconds, 60)
    s = seconds
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts) if parts else "0s"
    # ------------------ RMUTE ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """Mute a member, give role, send embed in log channel and DM them"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    # Convert duration string like '1h', '30m', '2d' into seconds
    unit = duration[-1].lower()
    try:
        val = int(duration[:-1])
    except ValueError:
        return await ctx.send("❌ Invalid duration format. Use e.g., 10m, 2h, 1d.")

    seconds = val
    if unit == "m": seconds *= 60
    elif unit == "h": seconds *= 3600
    elif unit == "d": seconds *= 86400
    else: return await ctx.send("❌ Invalid duration unit. Use m, h, or d.")

    try:
        await member.add_roles(muted_role)
        await member.timeout(datetime.timedelta(seconds=seconds))
        try:
            await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
        except:
            pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to mute {member}.")

    # Update log
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] += 1

    # Embed for log channel
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        tz_lines = [
            f"{emoji} {(datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}"
            for emoji, tz in TIMEZONES.items()
        ]
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
        embed.add_field(name="Unmute Times (TZ)", value="\n".join(tz_lines), inline=False)
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

# ------------------ BACKGROUND TASK FOR AUTO UNMUTE ------------------
@tasks.loop(seconds=10)
async def check_mutes():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    now = datetime.datetime.utcnow()
    for user_id, log in activity_logs.items():
        if log.get("mute_expires"):
            mute_time = datetime.datetime.fromisoformat(log["mute_expires"])
            if now >= mute_time:
                member = guild.get_member(int(user_id))
                if member:
                    muted_role = guild.get_role(MUTED_ROLE_ID)
                    try:
                        await member.remove_roles(muted_role)
                        await member.timeout(None)
                    except:
                        pass
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                save_data()
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

    tz_lines = [
        f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}"
        for emoji, tz in TIMEZONES.items()
    ]

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

# ------------------ BOT START ------------------
# Start background tasks
check_mutes.start()
track_online_time.start()  # Task to track online seconds every 1 second

# Run bot using environment variable for token
import os
TOKEN = os.environ.get("DISCORD_TOKEN")  # Set this in Render or GitHub Secrets
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN not set in environment variables")
else:
    bot.run(TOKEN)
