import os
import discord
from discord.ext import commands, tasks
import datetime
import json

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = 1403359962369097739  # your server ID
MUTE_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD = 60  # seconds
DAY_SECONDS = 24 * 3600
WEEK_SECONDS = 7 * DAY_SECONDS
MONTH_SECONDS = 30 * DAY_SECONDS

# ------------------ BOT ------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ TIME TRACKING ------------------
activity_logs = {}
last_messages = {}

if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            raw_logs = json.load(f)
            for uid, data in raw_logs.items():
                activity_logs[int(uid)] = {
                    "total_seconds": data.get("total_seconds", 0),
                    "offline_seconds": data.get("offline_seconds", 0),
                    "daily_seconds": data.get("daily_seconds", 0),
                    "weekly_seconds": data.get("weekly_seconds", 0),
                    "monthly_seconds": data.get("monthly_seconds", 0),
                    "last_activity": datetime.datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else None,
                    "online": data.get("online", False),
                    "first_seen": datetime.datetime.fromisoformat(data.get("first_seen")) if data.get("first_seen") else datetime.datetime.now(datetime.timezone.utc),
                    "daily_start": datetime.datetime.fromisoformat(data.get("daily_start")) if data.get("daily_start") else datetime.datetime.now(datetime.timezone.utc),
                    "weekly_start": datetime.datetime.fromisoformat(data.get("weekly_start")) if data.get("weekly_start") else datetime.datetime.now(datetime.timezone.utc),
                    "monthly_start": datetime.datetime.fromisoformat(data.get("monthly_start")) if data.get("monthly_start") else datetime.datetime.now(datetime.timezone.utc),
                    "offline_start": datetime.datetime.fromisoformat(data.get("offline_start")) if data.get("offline_start") else None
                }
    except:
        activity_logs = {}

def save_logs():
    serializable = {}
    for uid, data in activity_logs.items():
        serializable[str(uid)] = {
            "total_seconds": data["total_seconds"],
            "offline_seconds": data["offline_seconds"],
            "daily_seconds": data["daily_seconds"],
            "weekly_seconds": data["weekly_seconds"],
            "monthly_seconds": data["monthly_seconds"],
            "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
            "online": data["online"],
            "first_seen": data["first_seen"].isoformat(),
            "daily_start": data["daily_start"].isoformat(),
            "weekly_start": data["weekly_start"].isoformat(),
            "monthly_start": data["monthly_start"].isoformat(),
            "offline_start": data["offline_start"].isoformat() if data["offline_start"] else None
        }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable, f, indent=4)

def update_user_time(user_id: int, delta: int):
    user = activity_logs.get(user_id)
    if not user:
        return
    user["total_seconds"] += delta
    user["daily_seconds"] += delta
    user["weekly_seconds"] += delta
    user["monthly_seconds"] += delta

def reset_periods():
    now = datetime.datetime.now(datetime.timezone.utc)
    for data in activity_logs.values():
        if (now - data["daily_start"]).total_seconds() > DAY_SECONDS:
            data["daily_seconds"] = 0
            data["daily_start"] = now
        if (now - data["weekly_start"]).total_seconds() > WEEK_SECONDS:
            data["weekly_seconds"] = 0
            data["weekly_start"] = now
        if (now - data["monthly_start"]).total_seconds() > MONTH_SECONDS:
            data["monthly_seconds"] = 0
            data["monthly_start"] = now

def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for data in activity_logs.values():
        if data["online"] and data["last_activity"]:
            elapsed = (now - data["last_activity"]).total_seconds()
            if elapsed > INACTIVITY_THRESHOLD:
                data["online"] = False
                data["offline_start"] = now
                data["last_activity"] = None

async def on_user_message(user_id: int):
    now = datetime.datetime.now(datetime.timezone.utc)
    user = activity_logs.get(user_id)
    if not user:
        activity_logs[user_id] = {
            "total_seconds": 0,
            "offline_seconds": 0,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_activity": now,
            "online": True,
            "first_seen": now,
            "daily_start": now,
            "weekly_start": now,
            "monthly_start": now,
            "offline_start": None
        }
        return
    if not user["online"] and user["offline_start"]:
        user["offline_seconds"] += int((now - user["offline_start"]).total_seconds())
    user["last_activity"] = now
    user["online"] = True
    user["offline_start"] = None

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await on_user_message(message.author.id)
    save_logs()
    await bot.process_commands(message)

@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)
    reset_periods()
    for uid, data in activity_logs.items():
        if data["online"] and data.get("last_activity"):
            delta = min(int((now - data["last_activity"]).total_seconds()), 10)
            if delta > 0:
                update_user_time(uid, delta)
            data["offline_start"] = None
        else:
            if data.get("offline_start"):
                delta_off = (now - data["offline_start"]).total_seconds()
                data["offline_seconds"] += int(delta_off)
                data["offline_start"] = now
    check_inactivity()
    save_logs()

def format_time(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

# ------------------ RMUTE ------------------
active_mutes = {}  # {user_id: {"end_time": datetime, "reason": str, "moderator": str}}

def parse_duration(duration: str):
    try:
        unit = duration[-1]
        val = int(duration[:-1])
        if unit == "s": return val
        if unit == "m": return val*60
        if unit == "h": return val*3600
        if unit == "d": return val*86400
    except:
        return 60
    return 60

async def apply_mute(member: discord.Member, duration_seconds: int, reason: str, moderator: str):
    role = member.guild.get_role(MUTE_ROLE_ID)
    if role and role not in member.roles:
        await member.add_roles(role)

    end_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
    active_mutes[member.id] = {"end_time": end_time, "reason": reason, "moderator": moderator}

    # DM user
    dm_time = datetime.datetime.now().strftime("%I:%M %p")
    dm_msg = f"You have been muted in\n__𝚁𝙱𝚇𝚂𝙻 𝙲𝙾𝙼𝙼𝚄𝙽𝙸𝚃𝚈__ until\n**{end_time.date()}**\n**{dm_time}**\n**duration: {duration_seconds}s**\nReason: ***{reason}***"
    try:
        await member.send(dm_msg)
    except:
        dm_msg = "Could not DM user."

    # Embed log
    log_channel = member.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="🔇 User Muted", color=discord.Color.red())
        embed.set_author(name=member.display_name, icon_url=member.avatar.url if member.avatar else None)
        embed.add_field(name="Duration", value=f"`{duration_seconds}s`", inline=True)
        embed.add_field(name="Reason", value=f"***{reason}***", inline=True)
        embed.add_field(name="Responsible", value=f"**{moderator}**", inline=True)
        embed.add_field(name="DM Message", value=f"```{dm_msg}```", inline=False)
        await log_channel.send(embed=embed)

async def remove_mute(user_id: int):
    data = active_mutes.pop(user_id, None)
    if not data: return
    guild = bot.get_guild(GUILD_ID)
    if not guild: return
    member = guild.get_member(user_id)
    if not member: return
    role = guild.get_role(MUTE_ROLE_ID)
    if role in member.roles:
        await member.remove_roles(role)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="✅ User Unmuted", description=f"**User:** {member.mention}", color=discord.Color.green())
        await log_channel.send(embed=embed)

@tasks.loop(seconds=10)
async def check_mutes():
    now = datetime.datetime.utcnow()
    to_remove = [uid for uid, data in active_mutes.items() if now >= data["end_time"]]
    for uid in to_remove:
        await remove_mute(uid)

# ------------------ RMUTE COMMAND ------------------
@bot.tree.command(name="rmute", description="Mute a user with duration and reason")
async def rmute(interaction: discord.Interaction, user: discord.Member, duration: str, reason: str):
    if not interaction.user.guild_permissions.mute_members:
        await interaction.response.send_message("❌ You do not have permission.", ephemeral=True)
        return
    dur_seconds = parse_duration(duration)
    await apply_mute(user, dur_seconds, reason, interaction.user.display_name)
    await interaction.response.send_message(f"✅ *`{user.display_name}` has been muted for `{duration}`* with reason: ***{reason}***")

# ------------------ TIMETRACK COMMAND ------------------
@bot.tree.command(name="timetrack", description="Show online/offline time")
async def timetrack(interaction: discord.Interaction, username: discord.Member):
    user_data = activity_logs.get(username.id)
    if not user_data:
        await interaction.response.send_message("No activity tracked for this user.", ephemeral=True)
        return
    offline_time = 0
    if not user_data["online"] and user_data.get("offline_start"):
        offline_time = int((datetime.datetime.now(datetime.timezone.utc) - user_data["offline_start"]).total_seconds())
    msg = f"⏳ **{username.display_name}**\n"
    msg += f"🟢 Online time: `{format_time(user_data['total_seconds'])}`\n"
    msg += f"⚫ Offline for: `{format_time(user_data['offline_seconds'] + offline_time)}`\n\n"
    msg += "📆 **Periods**\n"
    msg += f"Daily: `{format_time(user_data['daily_seconds'])}`\n"
    msg += f"Weekly: `{format_time(user_data['weekly_seconds'])}`\n"
    msg += f"Monthly: `{format_time(user_data['monthly_seconds'])}`"
    await interaction.response.send_message(msg)

# ------------------ READY ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    if not update_all_users.is_running():
        update_all_users.start()
    if not check_mutes.is_running():
        check_mutes.start()
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced")
    except:
        pass

bot.run(TOKEN)
