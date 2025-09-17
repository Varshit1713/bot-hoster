# ------------------ IMPORTS ------------------
import os
import json
import asyncio
import datetime
from zoneinfo import ZoneInfo
from typing import Optional
from flask import Flask
import discord
from discord.ext import commands, tasks

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    exit(1)

GUILD_ID = 140335996236909773
DATA_FILE = "bot_data.json"
TIMEZONE = ZoneInfo("UTC")

# Roles & channels
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE = 1410423854563721287
TRACKING_CHANNEL = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
DANGEROUS_USER_IDS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

# ------------------ BOT SETUP ------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ------------------ DATA HANDLING ------------------
def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "mutes": {}, "rmute_usage": {}, "cached_messages": {}, "logs": [], "rdm_users": []}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4, default=str)

data = load_data()

# ------------------ HELPERS ------------------
def get_member_data(user_id: int):
    return data["users"].get(str(user_id), {"last_online": None,"last_message": None,"last_edit": None,"total_online_seconds":0,"online_start": None})

def update_member_data(user_id: int, **kwargs):
    member = get_member_data(user_id)
    for key, value in kwargs.items():
        member[key] = value
    data["users"][str(user_id)] = member
    save_data()

def format_seconds(seconds: int):
    h, m = divmod(seconds // 60, 60)
    s = seconds % 60
    return f"{h}h {m}m {s}s"

def is_rdm(user_id: int):
    return str(user_id) in data["rdm_users"]

async def send_dangerous_log(message: str):
    for uid in DANGEROUS_USER_IDS:
        user = bot.get_user(uid)
        if user:
            try:
                await user.send(embed=discord.Embed(description=message, color=discord.Color.red()))
            except: pass

def log_event(title: str, description: str):
    log_entry = {"title": title,"description": description,"timestamp": datetime.datetime.now(TIMEZONE).isoformat()}
    data["logs"].append(log_entry)
    save_data()
    channel = bot.get_channel(TRACKING_CHANNEL)
    if channel:
        embed = discord.Embed(title=title, description=description, color=discord.Color.orange())
        embed.timestamp = datetime.datetime.now(TIMEZONE)
        asyncio.create_task(channel.send(embed=embed))

# ------------------ TIMETRACK ------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    for guild in bot.guilds:
        for member in guild.members:
            if any(role.id in RCACHE_ROLES for role in member.roles):
                mdata = get_member_data(member.id)
                now = datetime.datetime.now(TIMEZONE)
                if member.status != discord.Status.offline:
                    if not mdata["online_start"]: mdata["online_start"] = now.isoformat()
                else:
                    if mdata["online_start"]:
                        start = datetime.datetime.fromisoformat(mdata["online_start"])
                        delta = (now - start).total_seconds()
                        mdata["total_online_seconds"] += int(delta)
                        mdata["online_start"] = None
                mdata["last_online"] = now.isoformat()
                data["users"][str(member.id)] = mdata
    save_data()

# ------------------ RMUTE / RUNMUTE ------------------
async def schedule_unmute(user: discord.Member, duration: int, moderator: discord.Member):
    await asyncio.sleep(duration)
    mute_role = discord.utils.get(user.guild.roles, id=MUTE_ROLE)
    if mute_role in user.roles:
        await user.remove_roles(mute_role, reason="Auto-unmute completed")
        log_event("Auto-Unmute", f"User {user} ({user.id}) auto-unmuted after {duration}s. Moderator: {moderator}")

@bot.command()
async def rmute(ctx, users: commands.Greedy[discord.Member], duration: int, *, reason: str):
    if not users: return await ctx.send("Mention at least one user.")
    mute_role = discord.utils.get(ctx.guild.roles, id=MUTE_ROLE)
    for user in users:
        await user.add_roles(mute_role, reason=reason)
        data["mutes"][str(user.id)] = {"moderator": ctx.author.id,"duration": duration,"reason": reason,"start_time": datetime.datetime.now(TIMEZONE).isoformat()}
        data["rmute_usage"][str(ctx.author.id)] = data["rmute_usage"].get(str(ctx.author.id),0)+1
        save_data()
        if not is_rdm(user.id):
            embed = discord.Embed(title="You have been muted", color=discord.Color.red())
            embed.add_field(name="Moderator", value=str(ctx.author), inline=False)
            embed.add_field(name="Duration", value=f"{duration}s", inline=False)
            embed.add_field(name="Reason", value=reason, inline=False)
            try: await user.send(embed=embed)
            except: pass
        log_event("RMute Applied", f"User: {user} ({user.id})\nModerator: {ctx.author}\nDuration: {duration}s\nReason: {reason}")
        asyncio.create_task(schedule_unmute(user, duration, ctx.author))
    await ctx.message.delete()

@bot.command()
async def runmute(ctx, user: discord.Member, duration: int, *, reason: str):
    await rmute(ctx, [user], duration, reason=reason)

@bot.command()
async def rmlb(ctx):
    sorted_usage = sorted(data["rmute_usage"].items(), key=lambda x:x[1], reverse=True)
    embed = discord.Embed(title="Top RMute Users", color=discord.Color.blue())
    for i,(uid,count) in enumerate(sorted_usage[:10],1):
        user = bot.get_user(int(uid))
        embed.add_field(name=f"{i}. {user}", value=f"RMutes used: {count}", inline=False)
    await ctx.send(embed=embed)

# ------------------ LEADERBOARDS ------------------
@bot.command()
async def tlb(ctx):
    leaderboard = []
    for uid, udata in data["users"].items():
        member = ctx.guild.get_member(int(uid))
        if member and any(role.id in RCACHE_ROLES for role in member.roles):
            leaderboard.append((member.display_name, udata.get("total_online_seconds",0)))
    leaderboard.sort(key=lambda x:x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard", color=discord.Color.gold())
    for i,(name,seconds) in enumerate(leaderboard[:10],1):
        embed.add_field(name=f"{i}. {name}", value=f"Total time: {format_seconds(seconds)}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def tdm(ctx):
    leaderboard = []
    for uid, udata in data["users"].items():
        member = ctx.guild.get_member(int(uid))
        if member and all(role.id not in RCACHE_ROLES for role in member.roles):
            leaderboard.append((member.display_name, udata.get("total_online_seconds",0)))
    leaderboard.sort(key=lambda x:x[1], reverse=True)
    embed = discord.Embed(title="DM Timetrack Leaderboard", color=discord.Color.purple())
    for i,(name,seconds) in enumerate(leaderboard[:10],1):
        embed.add_field(name=f"{i}. {name}", value=f"Total time: {format_seconds(seconds)}", inline=False)
    await ctx.send(embed=embed)

# ------------------ RCACHE ------------------
@bot.command()
async def rcache(ctx):
    if not any(role.id in RCACHE_ROLES for role in ctx.author.roles):
        return await ctx.send("You don't have permission.")
    embed = discord.Embed(title="Cached Deleted Messages", color=discord.Color.teal())
    for mid, mdata in list(data["cached_messages"].items())[-10:]:
        embed.add_field(name=f"Message ID: {mid}",
                        value=f"Author: {mdata['author']}\nDeleted by: {mdata.get('deleter','Unknown')}\nContent: {mdata.get('content','')}",
                        inline=False)
    await ctx.send(embed=embed)

# ------------------ STAFF PING ------------------
async def ping_staff(ctx, role_id: int):
    role = discord.utils.get(ctx.guild.roles, id=role_id)
    if not role: return
    content = role.mention
    if ctx.message.reference:
        ref_msg = ctx.message.reference.resolved
        content += f"\n> Original message by {ref_msg.author}: {ref_msg.content}"
    await ctx.send(content)
    await ctx.message.delete()

@bot.command()
async def rping(ctx): await ping_staff(ctx, STAFF_PING_ROLE)
@bot.command()
async def hsping(ctx): await ping_staff(ctx, HIGHER_STAFF_PING_ROLE)

# ------------------ RDM ------------------
@bot.command()
async def rdm(ctx):
    uid = str(ctx.author.id)
    if uid in data["rdm_users"]: await ctx.send("Already opted out.")
    else:
        data["rdm_users"].append(uid)
        save_data()
        await ctx.send("Opted out from DMs.")

# ------------------ HELP ------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.green())
    embed.add_field(name="!timetrack [user]", value="Check user's online time.", inline=False)
    embed.add_field(name="!rmute [users] [duration] [reason]", value="Mute multiple users.", inline=False)
    embed.add_field(name="!runmute [user] [duration] [reason]", value="Mute a single user.", inline=False)
    embed.add_field(name="!rmlb", value="Top RMute users.", inline=False)
    embed.add_field(name="!rcache", value="View cached deleted messages.", inline=False)
    embed.add_field(name="!tlb", value="Timetrack leaderboard.", inline=False)
    embed.add_field(name="!tdm", value="DM leaderboard.", inline=False)
    embed.add_field(name="!rping", value="Ping staff.", inline=False)
    embed.add_field(name="!hsping", value="Ping higher staff.", inline=False)
    embed.add_field(name="!rdm", value="Opt-out from DMs.", inline=False)
    await ctx.send(embed=embed)

# ------------------ FLASK KEEP-ALIVE ------------------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

async def run_flask_async():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_flask)

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    timetrack_loop.start()
    asyncio.create_task(run_flask_async())

@bot.event
async def on_message_delete(message):
    data["cached_messages"][str(message.id)] = {
        "author": str(message.author),
        "content": message.content,
        "attachments": [a.url for a in message.attachments],
        "deleter": getattr(message, "deleter", "Unknown")
    }
    save_data()
    log_event("Message Deleted",
              f"Author: {message.author}\nContent: {message.content}\nMessage ID: {message.id}")

@bot.event
async def on_message_edit(before, after):
    update_member_data(after.author.id, last_edit=datetime.datetime.now(TIMEZONE).isoformat())
    log_event("Message Edited",
              f"Author: {after.author}\nBefore: {before.content}\nAfter: {after.content}\nMessage ID: {after.id}")

@bot.event
async def on_guild_channel_create(channel):
    log_event("Channel Created", f"Channel: {channel.name} ({channel.id})\nType: {channel.type}")

@bot.event
async def on_guild_channel_delete(channel):
    log_event("Channel Deleted", f"Channel: {channel.name} ({channel.id})\nType: {channel.type}")

@bot.event
async def on_guild_channel_update(before, after):
    if before.name != after.name:
        log_event("Channel Updated", f"Channel: {before.id}\nBefore: {before.name}\nAfter: {after.name}")

@bot.event
async def on_guild_role_create(role):
    log_event("Role Created", f"Role: {role.name} ({role.id})")

@bot.event
async def on_guild_role_delete(role):
    log_event("Role Deleted", f"Role: {role.name} ({role.id})")

@bot.event
async def on_guild_role_update(before, after):
    log_event("Role Updated",
              f"Role: {before.name} ({before.id})\nBefore Permissions: {before.permissions}\nAfter Permissions: {after.permissions}")

@bot.event
async def on_webhooks_update(channel):
    log_event("Webhook Updated", f"Channel: {channel.name} ({channel.id})")

@bot.event
async def on_message(message):
    update_member_data(message.author.id, last_message=message.content)
    await bot.process_commands(message)

# ------------------ PURGE TRACKING ------------------
@bot.command()
async def purge(ctx, limit: int):
    messages = await ctx.channel.purge(limit=limit, bulk=True)
    for m in messages:
        data["cached_messages"][str(m.id)] = {
            "author": str(m.author),
            "content": m.content,
            "attachments": [a.url for a in m.attachments],
            "deleter": str(ctx.author)
        }
        log_event("Message Purged",
                  f"Purged by: {ctx.author}\nAuthor: {m.author}\nContent: {m.content}\nMessage ID: {m.id}")
    save_data()
    await ctx.send(f"Purged {len(messages)} messages.", delete_after=5)

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
