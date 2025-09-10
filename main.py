# ------------------ PART 1 ------------------
import discord
from discord.ext import commands, tasks
import asyncio
import datetime
from zoneinfo import ZoneInfo
import os
import json
from aiohttp import web

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739
LOG_CHANNEL_ID = 1403422843894759534
MUTED_ROLE_ID = 1410423854563721287
TIMEZONES = {
    "🌎 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("America/New_York"),
    "🇬🇧 GMT": ZoneInfo("Europe/London"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo"),
}

DATA_FILE = "activity_logs.json"
activity_logs = {}

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
    """Mute a member with role, Discord API timeout, log, DM"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    # Convert duration
    multipliers = {"s":1, "m":60, "h":3600, "d":86400}
    try:
        amount, unit = int(duration[:-1]), duration[-1]
        seconds = amount * multipliers.get(unit, 60)
    except:
        return await ctx.send("❌ Invalid duration format. Use 1m, 1h, 1d, etc.")

    # Add role & timeout
    try:
        await member.add_roles(muted_role)
        await member.timeout(datetime.timedelta(seconds=seconds))
        try:
            await member.send(f"🔇 You have been muted for {duration}. Reason: {reason}")
        except: pass
    except discord.Forbidden:
        return await ctx.send(f"⚠️ Missing permissions to mute {member}.")

    # Update log
    log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat()
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
    tz_times = [f"{emoji} {(datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).replace(tzinfo=ZoneInfo('UTC')).astimezone(tz).strftime('%Y-%m-%d %H:%M:%S')}" for emoji, tz in TIMEZONES.items()]
    embed.add_field(name="Unmute Timezones", value="\n".join(tz_times), inline=False)
    if log_channel:
        await log_channel.send(embed=embed)
    await ctx.send(f"✅ {member.mention} has been muted.")

# ------------------ RUNMUTE ------------------
@bot.command()
async def runmute(ctx, member: discord.Member):
    """Unmute a member"""
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    if muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
            await member.timeout(None)
            try: await member.send("✅ You have been unmuted.")
            except: pass
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
    """Shows online/offline, daily/weekly/monthly time"""
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
        # ------------------ PART 2 ------------------

# ------------------ BACKGROUND LOOPS ------------------
@tasks.loop(seconds=1)
async def track_online_time():
    """Update online/offline seconds for members"""
    for guild in bot.guilds:
        for member in guild.members:
            log = get_user_log(member.id)

            if member.status != discord.Status.offline:
                log["online_seconds"] = log.get("online_seconds", 0) + 1
                log["offline_seconds"] = 0
                # Update daily/weekly/monthly
                log["daily_seconds"] = log.get("daily_seconds", 0) + 1
                log["weekly_seconds"] = log.get("weekly_seconds", 0) + 1
                log["monthly_seconds"] = log.get("monthly_seconds", 0) + 1
            else:
                log["offline_seconds"] = log.get("offline_seconds", 0) + 1
    save_data()

@tasks.loop(seconds=10)
async def check_mutes():
    """Automatically unmute expired mutes"""
    now = datetime.datetime.utcnow()
    for guild in bot.guilds:
        muted_role = guild.get_role(MUTED_ROLE_ID)
        log_channel = guild.get_channel(LOG_CHANNEL_ID)

        for uid, data in activity_logs.items():
            member = guild.get_member(int(uid))
            if not member or "mute_expires" not in data:
                continue
            expire_str = data["mute_expires"]
            if expire_str:
                expire_time = datetime.datetime.fromisoformat(expire_str)
                if now >= expire_time:
                    # Remove role & timeout
                    try:
                        await member.remove_roles(muted_role)
                        await member.timeout(None)
                        try: await member.send("✅ Your mute has expired.")
                        except: pass
                    except: pass

                    # Log
                    embed = discord.Embed(
                        title="⏱️ Mute Expired",
                        description=f"{member.mention} has been automatically unmuted",
                        color=0x00FF00,
                        timestamp=datetime.datetime.utcnow()
                    )
                    embed.set_thumbnail(url=member.display_avatar.url)
                    if log_channel:
                        await log_channel.send(embed=embed)

                    # Clear log
                    data["mute_expires"] = None
                    data["mute_reason"] = None
                    data["mute_responsible"] = None
    save_data()

# ------------------ BOT EVENTS ------------------
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    track_online_time.start()
    check_mutes.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    log = get_user_log(message.author.id)
    log["offline_seconds"] = 0
    save_data()
    await bot.process_commands(message)

# ------------------ RENDER WEB SERVER ------------------
async def handle(request):
    return web.Response(text="Bot is running!")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server running on port {port}")

# ------------------ MAIN FUNCTION ------------------
async def main():
    load_data()
    await start_web()
    TOKEN = os.environ.get("DISCORD_TOKEN")
    await bot.start(TOKEN)

# Run the bot
if __name__ == "__main__":
    asyncio.run(main())
