# ------------------ IMPORTS ------------------
import discord
from discord.ext import commands, tasks
import asyncio
import datetime
from zoneinfo import ZoneInfo
import os
import json
from flask import Flask

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739
LOG_CHANNEL_ID = 1403422664521023648
MUTED_ROLE_ID = 1410423854563721287

TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo"),
}

DATA_FILE = "activity_logs.json"
activity_logs = {}

# ------------------ BOT & INTENTS ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ HELPER FUNCTIONS ------------------
def load_data():
    global activity_logs
    try:
        with open(DATA_FILE, "r") as f:
            activity_logs = json.load(f)
    except:
        activity_logs = {}

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(activity_logs, f, indent=4)

def get_user_log(user_id):
    uid = str(user_id)
    if uid not in activity_logs:
        activity_logs[uid] = {}
    return activity_logs[uid]

def format_duration(seconds):
    seconds = int(seconds)
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts) if parts else "0s"

# ------------------ RMUTE ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str):
    """Mute a member"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    # Parse duration
    multipliers = {"s":1,"m":60,"h":3600,"d":86400}
    try:
        amount, unit = int(duration[:-1]), duration[-1]
        seconds = amount * multipliers.get(unit, 60)
    except:
        return await ctx.send("❌ Invalid duration format. Use 1m, 1h, 1d, etc.")

    # Add role + Discord timeout
    try:
        await member.add_roles(muted_role)
        await member.timeout(datetime.timedelta(seconds=seconds))
        try: await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
        except: pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to mute {member}.")

    # Log data
    log["mute_expires"] = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    log["mute_count"] = log.get("mute_count", 0) + 1
    save_data()

    # Fancy embed
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(title="🔇 User Muted", description=f"{member.mention} has been muted", color=0xFF0000, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Muted by", value=ctx.author.mention, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    tz_times = [
        f"{emoji} {(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=seconds)).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}"
        for emoji, tz in TIMEZONES.items()
    ]
    embed.add_field(name="Unmute Timezones", value="\n".join(tz_times), inline=False)
    if log_channel:
        await log_channel.send(embed=embed)
    await ctx.send(f"✅ {member.mention} has been muted.")

# ------------------ RUNMUTE ------------------
@bot.command()
async def runmute(ctx, member: discord.Member):
    """Unmute a member manually"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    try:
        await member.remove_roles(muted_role)
        await member.timeout(None)
        try: await member.send("🔊 You have been unmuted.")
        except: pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to unmute {member}.")

    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    embed = discord.Embed(title="🔊 User Unmuted", description=f"{member.mention} has been unmuted", color=0x00FF00, timestamp=datetime.datetime.now(datetime.timezone.utc))
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Unmuted by", value=ctx.author.mention, inline=True)
    embed.add_field(name="Original Reason", value=log.get("mute_reason", "N/A"), inline=False)
    if log_channel:
        await log_channel.send(embed=embed)

    # Clear mute info
    log["mute_expires"] = None
    log["mute_reason"] = None
    log["mute_responsible"] = None
    save_data()
    await ctx.send(f"✅ {member.mention} has been unmuted.")

# ------------------ TIMETRACK ------------------
last_active = {}   # last activity timestamp
inactive_status = {}  # whether user is inactive
daily_counters = {}  # daily counters
weekly_counters = {} # weekly counters
monthly_counters = {} # monthly counters

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    uid = message.author.id
    last_active[uid] = datetime.datetime.now(datetime.timezone.utc)
    inactive_status[uid] = False  # reset offline timer
    await bot.process_commands(message)
    # ------------------ OFFLINE TIMER LOOP ------------------
@tasks.loop(seconds=10)
async def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for uid, last_time in list(last_active.items()):
        if (now - last_time).total_seconds() > 53:  # 53s inactivity threshold
            if not inactive_status.get(uid, False):
                inactive_status[uid] = True
                log = get_user_log(uid)
                log["offline_seconds"] = log.get("offline_seconds", 0) + (now - last_time).total_seconds()
                save_data()
                user = bot.get_user(uid)
                if user:
                    log_channel = bot.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        await log_channel.send(f"⚫ {user.mention} has gone inactive (53s no activity).")

# ------------------ AUTO UNMUTE LOOP ------------------
@tasks.loop(seconds=30)
async def auto_unmute():
    now = datetime.datetime.now(datetime.timezone.utc)
    for uid, log in list(activity_logs.items()):
        expire_str = log.get("mute_expires")
        if expire_str:
            expire_time = datetime.datetime.fromisoformat(expire_str)
            if now >= expire_time:
                guild = bot.get_guild(GUILD_ID)
                if not guild:
                    continue
                member = guild.get_member(int(uid))
                muted_role = guild.get_role(MUTED_ROLE_ID)
                if member and muted_role in member.roles:
                    try:
                        await member.remove_roles(muted_role)
                        await member.timeout(None)
                        try:
                            await member.send("🔊 Your mute has expired (auto-unmuted).")
                        except:
                            pass
                    except discord.Forbidden:
                        continue

                    log_channel = guild.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        await log_channel.send(f"🔊 {member.mention} was auto-unmuted (mute expired).")

                    # Clear mute info
                    log["mute_expires"] = None
                    log["mute_reason"] = None
                    log["mute_responsible"] = None
                    save_data()

# ------------------ TIMETRACK COMMAND ------------------
@bot.command()
async def timetrack(ctx, member: discord.Member = None):
    member = member or ctx.author
    uid = str(member.id)
    log = get_user_log(uid)

    online = format_duration(log.get("online_seconds", 0))
    offline = format_duration(log.get("offline_seconds", 0))
    daily = format_duration(log.get("daily_seconds", 0))
    weekly = format_duration(log.get("weekly_seconds", 0))
    monthly = format_duration(log.get("monthly_seconds", 0))

    tz_lines = [
        f"{emoji} {datetime.datetime.now(datetime.timezone.utc).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}"
        for emoji, tz in TIMEZONES.items()
    ]

    embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🟢 Online Time", value=online, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline, inline=True)
    embed.add_field(name="📅 Daily", value=daily, inline=True)
    embed.add_field(name="📅 Weekly", value=weekly, inline=True)
    embed.add_field(name="📅 Monthly", value=monthly, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
    await ctx.send(embed=embed)

# ------------------ RMLB LEADERBOARD ------------------
@bot.command()
async def rmlb(ctx, public: bool = False):
    leaderboard = []
    for uid, data in activity_logs.items():
        count = data.get("mute_count", 0)
        if count > 0:
            responsible = data.get("mute_responsible")
            if responsible:
                leaderboard.append((responsible, count))
    leaderboard.sort(key=lambda x: x[1], reverse=True)

    embed = discord.Embed(title="📊 !rmute Leaderboard", color=0xFFD700)
    for i, (uid, count) in enumerate(leaderboard[:10], start=1):
        user = ctx.guild.get_member(uid)
        name = user.display_name if user else f"User {uid}"
        embed.add_field(name=f"#{i} {name}", value=f"{count} mutes", inline=False)

    if public:
        await ctx.send(embed=embed)
    else:
        await ctx.reply(embed=embed, mention_author=False)

# ------------------ RHELP COMMAND ------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="📜 Bot Commands", color=0x3498db)
    embed.add_field(name="!rmute", value="`!rmute [user] [duration] [reason]` → Mute a user with DM + log", inline=False)
    embed.add_field(name="!runmute", value="`!runmute [user]` → Unmute a user with DM + log", inline=False)
    embed.add_field(name="!timetrack", value="`!timetrack [user]` → Shows online/offline + daily/weekly/monthly counters", inline=False)
    embed.add_field(name="!rmlb [true/false]", value="`true` = public, `false` = private → Shows who used !rmute the most", inline=False)
    embed.set_footer(text="Bot keeps track of activity and mutes")
    await ctx.send(embed=embed)

# ------------------ FLASK WEB SERVER ------------------
app = Flask(__name__)
@app.route("/")
def home():
    return "✅ Bot is running!"

async def start_web():
    port = int(os.environ.get("PORT", 8080))
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, app.run, "0.0.0.0", port)

# ------------------ ON READY ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    auto_unmute.start()
    check_inactivity.start()
    # start web server
    asyncio.create_task(start_web())

# ------------------ RUN BOT ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
load_data()
bot.run(TOKEN)
