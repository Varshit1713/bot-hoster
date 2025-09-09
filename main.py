# ------------------ IMPORTS ------------------
import os
import sys
import threading
import datetime
import json
from flask import Flask
import discord
from discord.ext import commands, tasks

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ DISCORD_TOKEN environment variable not set")
    sys.exit(1)

# Server/mute config
GUILD_ID = 123456789012345678  # replace with your server ID
MUTE_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

# Time tracking config
DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD = 60  # seconds
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}

# ------------------ FLASK KEEP-ALIVE ------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ------------------ DISCORD BOT ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ TIME TRACKING STORAGE ------------------
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            raw_logs = json.load(f)
            activity_logs = {}
            for user_id, data in raw_logs.items():
                activity_logs[int(user_id)] = {
                    "total_seconds": data.get("total_seconds", 0),
                    "daily_seconds": data.get("daily_seconds", 0),
                    "weekly_seconds": data.get("weekly_seconds", 0),
                    "monthly_seconds": data.get("monthly_seconds", 0),
                    "last_activity": datetime.datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else None,
                    "online": data.get("online", False),
                    "last_message": datetime.datetime.fromisoformat(data["last_message"]) if data.get("last_message") else None,
                    "last_daily_reset": datetime.datetime.fromisoformat(data["last_daily_reset"]) if data.get("last_daily_reset") else None,
                    "last_weekly_reset": datetime.datetime.fromisoformat(data["last_weekly_reset"]) if data.get("last_weekly_reset") else None,
                    "last_monthly_reset": datetime.datetime.fromisoformat(data["last_monthly_reset"]) if data.get("last_monthly_reset") else None
                }
    except Exception:
        print("⚠️ Corrupt activity_logs.json, resetting...")
        activity_logs = {}
else:
    activity_logs = {}

def save_logs():
    serializable_logs = {}
    for user_id, data in activity_logs.items():
        serializable_logs[str(user_id)] = {
            "total_seconds": data["total_seconds"],
            "daily_seconds": data.get("daily_seconds", 0),
            "weekly_seconds": data.get("weekly_seconds", 0),
            "monthly_seconds": data.get("monthly_seconds", 0),
            "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
            "online": data["online"],
            "last_message": data["last_message"].isoformat() if data.get("last_message") else None,
            "last_daily_reset": data.get("last_daily_reset").isoformat() if data.get("last_daily_reset") else None,
            "last_weekly_reset": data.get("last_weekly_reset").isoformat() if data.get("last_weekly_reset") else None,
            "last_monthly_reset": data.get("last_monthly_reset").isoformat() if data.get("last_monthly_reset") else None
        }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_logs, f, indent=4)

# ------------------ TIME TRACKING HELPERS ------------------
def format_time(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

def convert_timezone(dt: datetime.datetime, tz_name: str):
    tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
    return dt.astimezone(tz)

def update_user_time(user_id: int):
    now = datetime.datetime.now(datetime.timezone.utc)
    user = activity_logs.get(user_id)
    if not user or not user["online"] or not user["last_message"]:
        return
    elapsed = (now - user["last_message"]).total_seconds()
    if elapsed <= INACTIVITY_THRESHOLD:
        user["total_seconds"] += int(elapsed)
        user["daily_seconds"] += int(elapsed)
        user["weekly_seconds"] += int(elapsed)
        user["monthly_seconds"] += int(elapsed)
        user["last_activity"] = now

def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user in activity_logs.values():
        if user["online"] and user["last_message"]:
            if (now - user["last_message"]).total_seconds() > INACTIVITY_THRESHOLD:
                user["online"] = False

def reset_periods():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user in activity_logs.values():
        if user.get("last_daily_reset") is None or (now - user.get("last_daily_reset")).days >= 1:
            user["daily_seconds"] = 0
            user["last_daily_reset"] = now
        if user.get("last_weekly_reset") is None or (now - user.get("last_weekly_reset")).days >= 7:
            user["weekly_seconds"] = 0
            user["last_weekly_reset"] = now
        if user.get("last_monthly_reset") is None or now.month != user.get("last_monthly_reset").month:
            user["monthly_seconds"] = 0
            user["last_monthly_reset"] = now

# ------------------ MUTE STORAGE ------------------
active_mutes = {}  # {user_id: {"end_time": datetime, "reason": str, "proof": str}}

def parse_duration(duration: str):
    if not duration:
        return 60
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

async def apply_mute(member: discord.Member, duration_seconds: int, reason: str, proof: str = None):
    role = member.guild.get_role(MUTE_ROLE_ID)
    if role and role not in member.roles:
        await member.add_roles(role)
    end_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
    active_mutes[member.id] = {"end_time": end_time, "reason": reason, "proof": proof}
    # DM user
    try:
        await member.send(f"You have been muted in {member.guild.name} until {end_time} UTC.\nReason: {reason}\nProof: {proof if proof else 'None'}")
    except: pass
    # Log
    log_channel = member.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="🔇 User Muted", color=discord.Color.red())
        embed.add_field(name="User", value=member.mention, inline=False)
        embed.add_field(name="Duration", value=str(datetime.timedelta(seconds=duration_seconds)), inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        if proof: embed.add_field(name="Proof", value=proof, inline=False)
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
    try: await member.send(f"You have been unmuted in {guild.name}.")
    except: pass
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="✅ User Unmuted", color=discord.Color.green())
        embed.add_field(name="User", value=member.mention)
        await log_channel.send(embed=embed)

# ------------------ BACKGROUND TASKS ------------------
@tasks.loop(seconds=10)
async def check_mutes():
    now = datetime.datetime.utcnow()
    to_remove = [uid for uid, data in active_mutes.items() if now >= data["end_time"]]
    for uid in to_remove:
        await remove_mute(uid)

@tasks.loop(seconds=10)
async def update_all_users():
    check_inactivity()
    reset_periods()
    for user_id, user in activity_logs.items():
        if user["online"]:
            update_user_time(user_id)
    save_logs()

# ------------------ COMMANDS ------------------
def has_mute_perm(ctx):
    return ctx.author.guild_permissions.mute_members

@bot.command(name="qmute")
@commands.check(has_mute_perm)
async def qmute(ctx, duration: str = None, *, reason: str = "No reason provided"):
    if not ctx.message.reference:
        await ctx.send("❌ You must reply to a message to mute a user.", delete_after=5)
        return
    replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
    member = replied_msg.author
    dur_seconds = parse_duration(duration)
    proof = f"[Message link](https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/{ctx.message.reference.message_id})"
    await apply_mute(member, dur_seconds, reason, proof)
    try: await ctx.message.delete()
    except: pass
    await ctx.send(f"✅ {member.mention} has been muted.", delete_after=5)

@bot.tree.command(name="rmute", description="Mute a user by replying to a message")
async def rmute(interaction: discord.Interaction, duration: str = None, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.mute_members:
        await interaction.response.send_message("❌ You do not have permission to mute members.", ephemeral=True)
        return
    if not interaction.data.get("resolved", {}).get("messages"):
        await interaction.response.send_message("❌ You must reply to a message.", ephemeral=True)
        return
    refs = interaction.data["resolved"]["messages"]
    message_id = list(refs.keys())[0]
    channel_id = int(refs[message_id]["channel_id"])
    channel = bot.get_channel(channel_id)
    message = await channel.fetch_message(int(message_id))
    member = message.author
    dur_seconds = parse_duration(duration)
    proof = f"[Message link](https://discord.com/channels/{interaction.guild.id}/{channel.id}/{message.id})"
    await apply_mute(member, dur_seconds, reason, proof)
    await interaction.response.send_message(f"✅ {member.mention} has been muted.", ephemeral=True)

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    if not check_mutes.is_running():
        check_mutes.start()
    if not update_all_users.is_running():
        update_all_users.start()
    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"⚠️ Slash sync failed: {e}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    user_id = message.author.id
    now = datetime.datetime.now(datetime.timezone.utc)
    if user_id not in activity_logs:
        activity_logs[user_id] = {
            "total_seconds": 0,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_activity": None,
            "online": True,
            "last_message": now,
            "last_daily_reset": None,
            "last_weekly_reset": None,
            "last_monthly_reset": None
        }
    else:
        activity_logs[user_id]["online"] = True
        activity_logs[user_id]["last_message"] = now
    save_logs()

# ------------------ TIME TRACKING SLASH COMMAND ------------------
@bot.tree.command(name="timetrack", description="Check a user's tracked online/offline time")
async def timetrack(
    interaction: discord.Interaction,
    username: discord.Member,
    show_last_message: bool = False,
    timezone: str = "UTC"
):
    user_id = username.id
    if user_id not in activity_logs:
        await interaction.response.send_message("❌ No activity recorded for this user.", ephemeral=True)
        return
    user = activity_logs[user_id]
    update_user_time(user_id)
    online_time = user["total_seconds"]
    daily_time = user["daily_seconds"]
    weekly_time = user["weekly_seconds"]
    monthly_time = user["monthly_seconds"]
    now = datetime.datetime.now(datetime.timezone.utc)
    offline_seconds = 0
    if not user["online"] and user.get("last_message"):
        offline_seconds = int((now - user["last_message"]).total_seconds())
    msg = f"⏳ **{username.display_name}**\n"
    msg += f"🟢 Online time: {format_time(online_time)}\n"
    msg += f"⚫ Offline for: {format_time(offline_seconds)}\n"
    msg += f"📆 Daily: {format_time(daily_time)}, Weekly: {format_time(weekly_time)}, Monthly: {format_time(monthly_time)}\n"
    if show_last_message and user.get("last_message"):
        ts = convert_timezone(user["last_message"], timezone)
        msg += f"💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}]"
    await interaction.response.send_message(msg)

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
