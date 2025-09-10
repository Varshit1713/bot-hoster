# ------------------ IMPORTS ------------------
import os
import discord
from discord.ext import commands, tasks
import datetime, random, json, threading
from zoneinfo import ZoneInfo
from flask import Flask

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648
DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD_MIN = 50
INACTIVITY_THRESHOLD_MAX = 60
TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo")
}

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ FLASK SERVER ------------------
app = Flask("")
@app.route("/")
def home():
    return "Bot is running."

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web).start()

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
            "mutes_given": 0
        }
    return activity_logs[uid]

def format_duration(seconds):
    days, rem = divmod(int(seconds), 86400)
    hrs, rem = divmod(rem, 3600)
    mins, sec = divmod(rem, 60)
    return f"{days}d {hrs}h {mins}m {sec}s"

def parse_duration(duration_str):
    unit = duration_str[-1].lower()
    amount = int(duration_str[:-1])
    if unit == "s":
        return datetime.timedelta(seconds=amount)
    elif unit == "m":
        return datetime.timedelta(minutes=amount)
    elif unit == "h":
        return datetime.timedelta(hours=amount)
    elif unit == "d":
        return datetime.timedelta(days=amount)
    else:
        return datetime.timedelta(minutes=amount)

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
        # Calculate offline time
        last_msg = log.get("last_message")
        if last_msg:
            last_msg_time = datetime.datetime.fromisoformat(last_msg)
        else:
            last_msg_time = now

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
    for uid, log in activity_logs.items():
        if log.get("mute_expires"):
            expires = datetime.datetime.fromisoformat(log["mute_expires"])
            if now >= expires:
                guild = bot.get_guild(GUILD_ID)
                member = guild.get_member(int(uid))
                muted_role = guild.get_role(MUTED_ROLE_ID)
                if member:
                    try:
                        if muted_role in member.roles:
                            await member.remove_roles(muted_role)
                        await member.edit(timed_out_until=None)
                        try:
                            await member.send("✅ You have been unmuted.")
                        except:
                            pass
                        await send_mute_log(member, unmuted=True, log=log)
                    except discord.Forbidden:
                        print(f"⚠️ Cannot unmute {member}.")
                log["mute_expires"] = None
                log["mute_reason"] = None
                log["mute_responsible"] = None
                save_data()

# ------------------ HELP COMMAND ------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="📜 Help - Triggers", color=0x00FF00)
    embed.add_field(name="!rmute", value="!rmute [user] [duration] [reason] - Mute a user", inline=False)
    embed.add_field(name="!runmute", value="!runmute [user] [reason] - Unmute a user", inline=False)
    embed.add_field(name="!timetrack", value="!timetrack [user] - Show online/offline/daily/weekly/monthly time and timezones", inline=False)
    embed.add_field(name="!rmlb", value="!rmlb [true/false] - Show leaderboard of mutes given", inline=False)
    await ctx.send(embed=embed)

# ------------------ RMUTE ------------------
@bot.command()
async def rmute(ctx, member: discord.Member, duration: str, *, reason: str):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not muted_role or not log_channel:
        await ctx.send("⚠️ Missing role or log channel.")
        return

    delta = parse_duration(duration)
    mute_until = datetime.datetime.utcnow() + delta

    # Apply role and Discord API mute
    try:
        await member.add_roles(muted_role)
        await member.edit(timed_out_until=mute_until)
        try:
            await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}") except: pass except discord.Forbidden: await ctx.send(f"⚠️ Missing permissions to mute {member}.") return

# Update log
log = get_user_log(member.id)
log["mute_expires"] = mute_until.isoformat()
log["mute_reason"] = reason
log["mute_responsible"] = ctx.author.id
log["mutes_given"] = get_user_log(ctx.author.id).get("mutes_given", 0) + 1
save_data()

# Send embed to log channel
embed = discord.Embed(
    title="🔇 User Muted",
    color=0xFF0000,
    timestamp=datetime.datetime.utcnow()
)
embed.set_thumbnail(url=member.display_avatar.url)
embed.add_field(name="User", value=member.mention, inline=True)
embed.add_field(name="Muted By", value=ctx.author.mention, inline=True)
embed.add_field(name="Reason", value=reason, inline=False)
embed.add_field(name="Duration", value=duration, inline=True)

# Show unmute time in 4 timezones
tz_lines = [f"{emoji} {mute_until.replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
embed.add_field(name="⏰ Unmute Time", value="\n".join(tz_lines), inline=False)

await log_channel.send(embed=embed)
await ctx.send(f"✅ {member.mention} has been muted for {duration}.")

------------------ RUNMUTE ------------------

@bot.command() async def runmute(ctx, member: discord.Member, *, reason="Manual unmute"): guild = ctx.guild muted_role = guild.get_role(MUTED_ROLE_ID) log_channel = guild.get_channel(LOG_CHANNEL_ID) log = get_user_log(member.id)

try:
    if muted_role in member.roles:
        await member.remove_roles(muted_role)
    await member.edit(timed_out_until=None)
    try:
        await member.send("✅ You have been unmuted.")
    except:
        pass
    embed = discord.Embed(
        title="✅ User Unmuted",
        color=0x00FF00,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="User", value=member.mention, inline=True)
    embed.add_field(name="Unmuted By", value=ctx.author.mention, inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    await log_channel.send(embed=embed)
    log["mute_expires"] = None
    log["mute_reason"] = None
    log["mute_responsible"] = None
    save_data()
    await ctx.send(f"✅ {member.mention} has been unmuted.")
except discord.Forbidden:
    await ctx.send(f"⚠️ Missing permissions to unmute {member}.")

------------------ TIMETRACK ------------------

@bot.command() async def timetrack(ctx, member: discord.Member = None): member = member or ctx.author log = get_user_log(member.id)

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
embed.add_field(name="📊 Weekly", value=weekly_time, inline=True)
embed.add_field(name="📆 Monthly", value=monthly_time, inline=True)
embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)
await ctx.send(embed=embed)

------------------ MUTE LEADERBOARD ------------------

@bot.command() async def rmlb(ctx, public: bool = False): leaderboard = sorted([(uid, log.get("mutes_given", 0)) for uid, log in activity_logs.items()], key=lambda x: x[1], reverse=True) lines = [] for i, (uid, count) in enumerate(leaderboard[:10], start=1): member = ctx.guild.get_member(int(uid)) if member: lines.append(f"{i}. {member.display_name} - {count} mutes given") embed = discord.Embed(title="🏆 Top 10 Mute Givers", description="\n".join(lines) if lines else "No data yet.", color=0xFFD700) if public: await ctx.send(embed=embed) else: await ctx.author.send(embed=embed)

------------------ RUN BOT ------------------

bot.run(TOKEN)
