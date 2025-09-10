import discord
from discord.ext import tasks
from discord import app_commands
import asyncio
import datetime
from zoneinfo import ZoneInfo
import json
import os

# ------------------ CONFIG ------------------
GUILD_ID = 1403359962369097739  # Your server/guild ID
LOG_CHANNEL_ID = 1403422664521023648  # Where mute/unmute embeds go
MUTED_ROLE_ID = 1410423854563721287  # Muted role ID

TIMEZONES = {
    "🌍 UTC": ZoneInfo("UTC"),
    "🇺🇸 EST": ZoneInfo("US/Eastern"),
    "🇪🇺 CET": ZoneInfo("CET"),
    "🇯🇵 JST": ZoneInfo("Asia/Tokyo")
}

DATA_FILE = "activity_logs.json"

# ------------------ BOT INIT ------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = discord.Client(intents=intents)
tree = app_commands.CommandTree(bot)

# ------------------ DATA LOAD / SAVE ------------------
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
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "mute_count": 0,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "last_message": None
        }
    return activity_logs[uid]

def format_duration(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, sec = divmod(rem, 60)
    parts = []
    if days: parts.append(f"{days}d")
    if hours: parts.append(f"{hours}h")
    if minutes: parts.append(f"{minutes}m")
    if sec: parts.append(f"{sec}s")
    return " ".join(parts) if parts else "0s"

# ------------------ TIMETRACK HELPERS ------------------
def update_online_time(user_id, seconds=1):
    log = get_user_log(user_id)
    log["online_seconds"] += seconds
    log["daily_seconds"] += seconds
    log["weekly_seconds"] += seconds
    log["monthly_seconds"] += seconds
    log["last_message"] = datetime.datetime.utcnow().isoformat()
    save_data()

def update_offline_time(user_id, seconds=1):
    log = get_user_log(user_id)
    log["offline_seconds"] += seconds
    save_data()
    # ------------------ RHELP ------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    # Update offline time if user was inactive
    log = get_user_log(message.author.id)
    last_msg_time = log.get("last_message")
    now = datetime.datetime.utcnow()
    if last_msg_time:
        last_time = datetime.datetime.fromisoformat(last_msg_time)
        delta = (now - last_time).total_seconds()
        if delta > 60:  # Reset offline counter if offline period >1 min
            log["offline_seconds"] = 0
    log["last_message"] = now.isoformat()
    save_data()

    # ---------- TRIGGERS ----------
    content = message.content.lower()
    
    if content.startswith("!rhelp"):
        embed = discord.Embed(title="📚 Bot Commands", color=0x00FFFF)
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
            value="!rmlb [true/false] → Shows top users who used !rmute, true=public, false=private reply", 
            inline=False
        )
        await message.channel.send(embed=embed)

    elif content.startswith("!rmute"):
        parts = message.content.split()
        if len(parts) < 4:
            return await message.channel.send("Usage: !rmute [user] [duration] [reason]")
        member = message.mentions[0] if message.mentions else None
        if not member:
            return await message.channel.send("User not found.")
        duration_raw = parts[2]
        reason = " ".join(parts[3:])

        # Convert duration like 1m, 1h, 1d to seconds
        try:
            if duration_raw.endswith("s"): duration = int(duration_raw[:-1])
            elif duration_raw.endswith("m"): duration = int(duration_raw[:-1])*60
            elif duration_raw.endswith("h"): duration = int(duration_raw[:-1])*3600
            elif duration_raw.endswith("d"): duration = int(duration_raw[:-1])*86400
            else: duration = int(duration_raw)  # assume seconds
        except:
            return await message.channel.send("Invalid duration format. Use 1m,1h,1d,...")

        log = get_user_log(member.id)
        guild = message.guild
        muted_role = guild.get_role(MUTED_ROLE_ID)
        try:
            await member.add_roles(muted_role)
            await member.timeout(datetime.timedelta(seconds=duration))
            try:
                await member.send(f"🔇 You have been muted for {duration_raw}. Reason: {reason}")
            except:
                pass
        except discord.Forbidden:
            return await message.channel.send("Missing permissions to mute this member.")

        # Update log
        log["mute_expires"] = (datetime.datetime.utcnow() + datetime.timedelta(seconds=duration)).isoformat()
        log["mute_reason"] = reason
        log["mute_responsible"] = message.author.id
        log["mute_count"] += 1
        save_data()

        # Send embed to log channel
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        embed = discord.Embed(
            title="🔇 User Muted",
            description=f"{member.mention} has been muted",
            color=0xFF0000,
            timestamp=datetime.datetime.utcnow()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Muted by", value=message.author.mention, inline=True)
        embed.add_field(name="Duration", value=duration_raw, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        if log_channel:
            await log_channel.send(embed=embed)
        await message.channel.send(f"✅ {member.mention} has been muted for {duration_raw}.")
        # ------------------ RUNMUTE ------------------
@bot.event
async def on_message(message):
    await bot.process_commands(message)  # Ensure triggers and commands still work

# Runmute trigger
@bot.command()
async def runmute(ctx, member: discord.Member, *, reason: str = "No reason provided"):
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
        
        # Clear mute log
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
        embed.add_field(name="Reason", value=reason, inline=True)
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

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=1)
async def update_online_time():
    now = datetime.datetime.utcnow()
    for uid, log in activity_logs.items():
        # Increment online if last_message within 50s
        last_msg = log.get("last_message")
        if last_msg:
            last_dt = datetime.datetime.fromisoformat(last_msg)
            delta = (now - last_dt).total_seconds()
            if delta <= 50:
                log["online_seconds"] = log.get("online_seconds", 0) + 1
                log["daily_seconds"] = log.get("daily_seconds", 0) + 1
                log["weekly_seconds"] = log.get("weekly_seconds", 0) + 1
                log["monthly_seconds"] = log.get("monthly_seconds", 0) + 1
            else:
                log["offline_seconds"] = log.get("offline_seconds", 0) + 1
    save_data()

@tasks.loop(seconds=10)
async def check_mutes():
    now = datetime.datetime.utcnow()
    for uid, log in activity_logs.items():
        mute_exp = log.get("mute_expires")
        if mute_exp:
            expire_dt = datetime.datetime.fromisoformat(mute_exp)
            if now >= expire_dt:
                guild = bot.get_guild(GUILD_ID)
                member = guild.get_member(int(uid))
                if member:
                    muted_role = guild.get_role(MUTED_ROLE_ID)
                    try:
                        await member.remove_roles(muted_role)
                        await member.timeout(None)
                        log["mute_expires"] = None
                        log["mute_reason"] = None
                        log["mute_responsible"] = None
                        save_data()
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        embed = discord.Embed(
                            title="✅ User Unmuted (Auto)",
                            description=f"{member.mention} was automatically unmuted.",
                            color=0x00FF00,
                            timestamp=datetime.datetime.utcnow()
                        )
                        embed.set_thumbnail(url=member.display_avatar.url)
                        if log_channel:
                            await log_channel.send(embed=embed)
                    except:
                        pass

# ------------------ START BOT ------------------
update_online_time.start()
check_mutes.start()
