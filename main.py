# ------------------ IMPORTS ------------------
import os
import threading
import datetime
import json
import sys
import discord
from discord.ext import commands, tasks
from discord import app_commands
from zoneinfo import ZoneInfo

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    sys.exit(1)

DATA_FILE = "activity_logs.json"
MUTED_ROLE_ID = 123456789012345678  # replace with your server's Muted role ID

TIMEZONES = {
    "🌎": ZoneInfo("UTC"),
    "🗽": ZoneInfo("America/New_York"),
    "🌉": ZoneInfo("America/Los_Angeles"),
    "🗼": ZoneInfo("Europe/Paris"),
}

# ------------------ BOT ------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ DATA STORAGE ------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

data = load_data()

def get_user_log(user_id: int):
    if str(user_id) not in data:
        data[str(user_id)] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
        }
    return data[str(user_id)]

def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, sec = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{sec:02}"

# ------------------ COMMANDS ------------------
@bot.tree.command(name="rhelp", description="Show bot help with triggers")
async def rhelp(interaction: discord.Interaction):
    embed = discord.Embed(title="📖 RHelp Menu", color=0x00FFFF)
    embed.add_field(
        name="⏱️ !timetrack [@user]",
        value="Shows online, offline, daily, weekly, monthly times + timezones",
        inline=False,
    )
    embed.add_field(
        name="🔇 !rmute [@user] [duration minutes] [reason]",
        value="Mute a member with a duration and reason",
        inline=False,
    )
    embed.add_field(
        name="🔊 !runmute [@user]",
        value="Unmute a member manually",
        inline=False,
    )
    await interaction.response.send_message(embed=embed)

# TRIGGER: !timetrack
@bot.command(name="timetrack")
async def timetrack(ctx, member: discord.Member = None):
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
    embed.add_field(name="🟢 Online Time", value=online_time, inline=True)
    embed.add_field(name="🔴 Offline Time", value=offline_time, inline=True)
    embed.add_field(name="📅 Daily", value=daily_time, inline=True)
    embed.add_field(name="📅 Weekly", value=weekly_time, inline=True)
    embed.add_field(name="📅 Monthly", value=monthly_time, inline=True)
    embed.add_field(name="🕒 Timezones", value="\n".join(tz_lines), inline=False)

    await ctx.send(embed=embed)

# TRIGGER: !rmute
@bot.command(name="rmute")
async def rmute(ctx, member: discord.Member, duration: int, *, reason: str):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await ctx.send("⚠️ Muted role not found.")
        return

    try:
        await member.add_roles(muted_role)
    except discord.Forbidden:
        await ctx.send(f"⚠️ Missing permission to add Muted role to {member}.")
        return

    delta = datetime.timedelta(minutes=duration)
    log = get_user_log(member.id)
    log["mute_expires"] = (datetime.datetime.utcnow() + delta).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = ctx.author.id
    save_data()

    await ctx.send(f"✅ {member.mention} has been muted for {duration} minutes. Reason: {reason}")

# TRIGGER: !runmute
@bot.command(name="runmute")
async def runmute(ctx, member: discord.Member):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    log = get_user_log(member.id)

    if muted_role in member.roles:
        try:
            await member.remove_roles(muted_role)
        except discord.Forbidden:
            await ctx.send(f"⚠️ Missing permission to remove Muted role from {member}.")
            return

        log["mute_expires"] = None
        log["mute_reason"] = None
        log["mute_responsible"] = None
        save_data()
        await ctx.send(f"✅ {member.mention} has been unmuted by {ctx.author.mention}.")
    else:
        await ctx.send(f"ℹ️ {member.mention} is not muted.")

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
