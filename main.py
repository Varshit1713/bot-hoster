# ------------------ IMPORTS ------------------
import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import datetime
import json
import pytz

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    exit()

# Replace these IDs with your actual server info
GUILD_ID = 123456789012345678       # Your guild/server ID
MUTED_ROLE_ID = 987654321098765432  # Muted role ID
LOG_CHANNEL_ID = 112233445566778899  # Log channel ID

DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD = 300  # seconds until considered offline

TIMEZONES = {
    "🌎 UTC": pytz.UTC,
    "🇺🇸 EST": pytz.timezone("US/Eastern"),
    "🇬🇧 GMT": pytz.timezone("Europe/London"),
    "🇯🇵 JST": pytz.timezone("Asia/Tokyo")
}

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
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
            "last_message": None,
            "mute_expires": None
        }
    return activity_logs[uid]

def format_duration(seconds):
    mins, sec = divmod(int(seconds), 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs}h {mins}m {sec}s"

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    timetrack_update.start()
    mute_check.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    log = get_user_log(message.author.id)
    log["offline_seconds"] = 0
    log["offline_start"] = None
    log["last_message"] = datetime.datetime.utcnow().isoformat()
    save_data()
    await bot.process_commands(message)

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=5)
async def timetrack_update():
    now = datetime.datetime.utcnow()
    for uid, log in activity_logs.items():
        if log.get("offline_start"):
            delta = (now - datetime.datetime.fromisoformat(log["offline_start"])).total_seconds()
            log["offline_seconds"] = delta
        else:
            log["online_seconds"] += 5
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
                if member:
                    muted_role = guild.get_role(MUTED_ROLE_ID)
                    if muted_role in member.roles:
                        await member.remove_roles(muted_role)
                        await send_mute_log(member, unmuted=True)
                log["mute_expires"] = None
                save_data()

# ------------------ EMBED HELPERS ------------------
async def send_mute_log(member, reason=None, responsible=None, duration=None, unmuted=False):
    guild = bot.get_guild(GUILD_ID)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if not log_channel:
        return

    embed = discord.Embed(
        title="🔒 Mute Log" if not unmuted else "✅ Unmute Log",
        color=0xFF0000 if not unmuted else 0x00FF00,
        timestamp=datetime.datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🔒 Muted User", value=member.mention, inline=True)
    if responsible:
        embed.add_field(name="👤 Responsible", value=responsible.mention, inline=True)
    if reason:
        embed.add_field(name="📝 Reason", value=reason, inline=False)
    if duration and not unmuted:
        embed.add_field(name="⏳ Duration", value=duration, inline=True)

        # Calculate unmute time in all timezones
        unmute_time = datetime.datetime.utcnow() + duration
        tz_lines = []
        unmute_time = unmute_time.replace(tzinfo=pytz.UTC)
        for emoji, tz in TIMEZONES.items():
            tz_time = unmute_time.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
            tz_lines.append(f"{emoji} {tz_time}")
        embed.add_field(name="🕒 Unmute Time", value="\n".join(tz_lines), inline=False)
    await log_channel.send(embed=embed)

# ------------------ SLASH COMMANDS ------------------
@bot.tree.command(name="timetrack", description="Shows online/offline time and timezones")
@app_commands.describe(member="Member to check timetrack for")
async def timetrack(interaction: discord.Interaction, member: discord.Member = None):
    member = member or interaction.user
    log = get_user_log(member.id)

    online_time = format_duration(log.get("online_seconds", 0))
    offline_time = format_duration(log.get("offline_seconds", 0))

    tz_lines = []
    utc_now = datetime.datetime.utcnow().replace(tzinfo=pytz.UTC)
    for emoji, tz in TIMEZONES.items():
        tz_time = utc_now.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
        tz_lines.append(f"{emoji} {tz_time}")

    embed = discord.Embed(title=f"⏱️ Timetrack for {member.display_name}", color=0x00FF00)
    embed.add_field(name="🟢 Online Time", value=online_time, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline_time, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="mute", description="Mute a member with duration and reason")
@app_commands.describe(member="Member to mute", duration="Duration in minutes", reason="Reason for mute")
async def mute(interaction: discord.Interaction, member: discord.Member, duration: int, reason: str):
    guild = interaction.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("Muted role not found.", ephemeral=True)
        return

    # Assign role
    await member.add_roles(muted_role)
    delta = datetime.timedelta(minutes=duration)

    # Store mute expiration
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + delta).isoformat()
    save_data()

    await send_mute_log(member, reason=reason, responsible=interaction.user, duration=f"{duration} min")
    await interaction.response.send_message(f"✅ {member.mention} has been muted for {duration} minutes.")

@bot.tree.command(name="unmute", description="Unmute a member manually")
@app_commands.describe(member="Member to unmute")
async def unmute(interaction: discord.Interaction, member: discord.Member):
    guild = interaction.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role in member.roles:
        await member.remove_roles(muted_role)
        log = get_user_log(member.id)
        log["mute_expires"] = None
        save_data()
        await send_mute_log(member, unmuted=True)
        await interaction.response.send_message(f"✅ {member.mention} has been unmuted.")
    else:
        await interaction.response.send_message(f"ℹ️ {member.mention} is not muted.", ephemeral=True)

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
