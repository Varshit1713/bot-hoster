import discord
from discord.ext import commands, tasks
import asyncio
import datetime
from zoneinfo import ZoneInfo
import threading
import os
import json

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739
LOG_CHANNEL_ID = 1403422664521023648
MUTED_ROLE_ID = 1410423854563721287

TIMEZONES = {
    "🌍 UTC": ZoneInfo("UTC"),
    "🗽 EST": ZoneInfo("America/New_York"),
    "🌞 PST": ZoneInfo("America/Los_Angeles"),
    "🌏 IST": ZoneInfo("Asia/Kolkata")
}

DATA_FILE = "activity_logs.json"

# ------------------ BOT SETUP ------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

try:
    with open(DATA_FILE, "r") as f:
        activity_logs = json.load(f)
except FileNotFoundError:
    activity_logs = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(activity_logs, f, indent=4)

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
            "last_message": None
        }
    return activity_logs[str(user_id)]

def format_duration(seconds):
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    result = ""
    if days > 0:
        result += f"{days}d "
    if hours > 0:
        result += f"{hours}h "
    if minutes > 0:
        result += f"{minutes}m "
    result += f"{seconds}s"
    return result.strip()

# ------------------ FLASK WEB ------------------
from flask import Flask

app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()
# ------------------ RMUTE ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    """Mute a member, add role, send embed and DM"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    # Parse duration like 1m, 1h, 1d
    unit = duration[-1]
    try:
        time_val = int(duration[:-1])
    except:
        return await ctx.send("❌ Invalid duration format. Use 1m, 1h, 1d, etc.")

    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(unit.lower())
    if not multiplier:
        return await ctx.send("❌ Invalid duration unit. Use s, m, h, d.")
    total_seconds = time_val * multiplier

    try:
        await member.add_roles(muted_role)
        await member.timeout(datetime.timedelta(seconds=total_seconds))
        try:
            await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
        except:
            pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to mute {member}.")

    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=total_seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count", 0) + 1
    save_data()

    # Log embed
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(
        title="🔇 User Muted",
        description=f"{member.mention} has been muted",
        color=0xFF0000,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Muted by", value=ctx.author.mention, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)

    # Timezones
    tz_lines = [f"{emoji} {datetime.datetime.utcnow().replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="Timezones", value="\n".join(tz_lines), inline=False)

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
            await member.timeout(None)
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

# ------------------ RHELP ------------------
@bot.command()
async def rhelp(ctx):
    """Shows triggers info"""
    embed = discord.Embed(title="🤖 Bot Commands / Triggers", color=0x1ABC9C)
    embed.add_field(name="!rmute", value="!rmute [user] [duration] [reason] → Mutes a user, gives role, sends embed in log channel, DMs user, tracks duration", inline=False)
    embed.add_field(name="!runmute", value="!runmute [user] → Unmutes a user, removes role, sends embed in log channel, DMs user", inline=False)
    embed.add_field(name="!timetrack", value="!timetrack [user] → Shows online/offline, daily/weekly/monthly times with emojis", inline=False)
    embed.add_field(name="!rmlb", value="!rmlb [true/false] → Shows top users who used !rmute; true = public, false = privately", inline=False)
    await ctx.send(embed=embed)

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=1)
async def update_online_offline():
    for guild in bot.guilds:
        for member in guild.members:
            log = get_user_log(member.id)
            last_message = log.get("last_message_time")
            now = datetime.datetime.utcnow().timestamp()
            if last_message and now - last_message <= 50:
                log["online_seconds"] = log.get("online_seconds", 0) + 1
                log["offline_seconds"] = 0
            else:
                log["offline_seconds"] = log.get("offline_seconds", 0) + 1
            save_data()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    log = get_user_log(message.author.id)
    log["last_message_time"] = datetime.datetime.utcnow().timestamp()
    save_data()
    await bot.process_commands(message)

# ------------------ RUN BOT ------------------
import os

TOKEN = os.environ.get("DISCORD_TOKEN")  # Add your token in Render or environment
check_mutes.start()
update_online_offline.start()
bot.run(TOKEN)
