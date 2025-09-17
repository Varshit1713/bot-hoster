# mega_discord_bot_full_all_features.py
# ALL FEATURES INCLUDED
# Python 3.9+, discord.py 2.x, pytz required

import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import pytz
import json
import os
import threading
import re
import traceback
from typing import Optional, List

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
GUILD_ID = 140335996236909773
TRACK_CHANNEL_ID = 1410458084874260592
TRACK_ROLES = [
    1410419924173848626, 1410420126003630122, 1410423594579918860,
    1410421466666631279, 1410421647265108038, 1410419345234067568,
    1410422029236047975, 1410458084874260592
]
RMUTE_ROLE_ID = 1410423854563721287
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
OFFLINE_DELAY = 53
STATUS_CHECK_INTERVAL = 5
AUTO_SAVE_INTERVAL = 120
DATA_FILE = "mega_bot_data.json"
BACKUP_DIR = "mega_bot_backups"
MAX_BACKUPS = 10
COMMAND_COOLDOWN = 5

# ------------------ INTENTS ------------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
data_lock = threading.Lock()
console_lock = threading.Lock()
command_cooldowns = {}

# ------------------ HELPER FUNCTIONS ------------------
def safe_print(*args, **kwargs):
    with console_lock:
        print(*args, **kwargs)

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE,"r") as f:
            return json.load(f)
    return {"users": {}, "mutes": {}, "images": {}, "logs": {}, "rmute_usage": {}}

def save_data(data):
    with data_lock:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        timestamp=datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file=os.path.join(BACKUP_DIR,f"backup_{timestamp}.json")
        try:
            with open(backup_file,"w") as bf:
                json.dump(data,bf,indent=4)
            backups=sorted(os.listdir(BACKUP_DIR))
            if len(backups)>MAX_BACKUPS:
                for old in backups[:len(backups)-MAX_BACKUPS]:
                    os.remove(os.path.join(BACKUP_DIR,old))
            with open(DATA_FILE,"w") as f:
                json.dump(data,f,indent=4)
        except Exception as e:
            safe_print("❌ Error saving data:",e)
            traceback.print_exc()

def ensure_user_data(uid,data):
    if uid not in data["users"]:
        data["users"][uid]={
            "status":"offline",
            "online_time":None,
            "offline_time":None,
            "last_message":None,
            "last_edit":None,
            "last_delete":None,
            "last_online_times":{},
            "offline_timer":0,
            "total_online_seconds":0,
            "daily_seconds":{},
            "weekly_seconds":{},
            "monthly_seconds":{},
            "average_online":0,
            "notify":True
        }

def format_time(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def parse_duration(duration_str):
    regex=re.compile(r"(\d+)([smhd])")
    match=regex.fullmatch(duration_str.lower())
    if not match:
        return None
    amount,unit=match.groups()
    amount=int(amount)
    if unit=="s": return amount
    if unit=="m": return amount*60
    if unit=="h": return amount*3600
    if unit=="d": return amount*86400
    return None

def get_timezones():
    return {"UTC":pytz.utc,"EST":pytz.timezone("US/Eastern"),"PST":pytz.timezone("US/Pacific"),"CET":pytz.timezone("Europe/Paris")}

def ascii_progress_bar(current,total,length=20):
    try:
        ratio=min(max(float(current)/float(total),0),1)
        filled=int(length*ratio)
        empty=length-filled
        return "█"*filled+"░"*empty
    except:
        return "░"*length

def can_execute_command(user_id):
    last=command_cooldowns.get(user_id,0)
    now=datetime.datetime.now().timestamp()
    if now-last>=COMMAND_COOLDOWN:
        command_cooldowns[user_id]=now
        return True
    return False

def increment_total_online(user_id,seconds,data):
    uid=str(user_id)
    ensure_user_data(uid,data)
    data["users"][uid]["total_online_seconds"]+=seconds
    today=datetime.datetime.now().strftime("%Y-%m-%d")
    week=datetime.datetime.now().strftime("%Y-W%U")
    month=datetime.datetime.now().strftime("%Y-%m")
    data["users"][uid]["daily_seconds"][today]=data["users"][uid]["daily_seconds"].get(today,0)+seconds
    data["users"][uid]["weekly_seconds"][week]=data["users"][uid]["weekly_seconds"].get(week,0)+seconds
    data["users"][uid]["monthly_seconds"][month]=data["users"][uid]["monthly_seconds"].get(month,0)+seconds
    total_time=data["users"][uid]["total_online_seconds"]
    total_days=max(len(data["users"][uid]["daily_seconds"]),1)
    data["users"][uid]["average_online"]=total_time/total_days

async def update_last_online(user:discord.Member,data):
    tz_dict={}
    for tz_name,tz in get_timezones().items():
        tz_dict[tz_name]=format_time(datetime.datetime.now(tz))
    data["users"][str(user.id)]["last_online_times"]=tz_dict
    save_data(data)

def create_timetrack_embed(user_data,member:discord.Member):
    embed=discord.Embed(title=f"📊 Timetrack: {member.display_name}",color=discord.Color.blue())
    embed.add_field(name="Status",value=user_data.get("status","offline"),inline=True)
    embed.add_field(name="Online Since",value=user_data.get("online_time","N/A"),inline=True)
    embed.add_field(name="Offline Since",value=user_data.get("offline_time","N/A"),inline=True)
    embed.add_field(name="Last Message",value=user_data.get("last_message","N/A"),inline=False)
    embed.add_field(name="Last Edit",value=user_data.get("last_edit","N/A"),inline=False)
    embed.add_field(name="Last Delete",value=user_data.get("last_delete","N/A"),inline=False)
    tz_lines=""
    for tz,t in user_data.get("last_online_times",{}).items():
        tz_lines+=f"{tz}: {t}\n"
    embed.add_field(name="Last Online (Timezones)",value=tz_lines or "N/A",inline=False)
    total_sec=user_data.get("total_online_seconds",0)
    h=total_sec//3600
    m=(total_sec%3600)//60
    s=total_sec%60
    embed.add_field(name="Total Online Time",value=f"{h}h {m}m {s}s",inline=False)
    embed.add_field(name="Average Daily Online",value=f"{int(user_data.get('average_online',0))}s",inline=False)
    today=datetime.datetime.now().strftime("%Y-%m-%d")
    daily_sec=user_data.get("daily_seconds",{}).get(today,0)
    embed.add_field(name="Today's Activity",value=ascii_progress_bar(daily_sec,3600)+f" ({daily_sec}s)",inline=False)
    embed.set_footer(text="Timetrack Bot | Skibidisigma")
    return embed

# ------------------ TRACKING TASK ------------------
@tasks.loop(seconds=STATUS_CHECK_INTERVAL)
async def track_users():
    try:
        guild=bot.get_guild(GUILD_ID)
        if not guild: return
        channel=bot.get_channel(TRACK_CHANNEL_ID)
        if not channel: return
        data=load_data()
        now_utc=datetime.datetime.utcnow()
        for member in guild.members:
            if not any(r.id in TRACK_ROLES for r in member.roles): continue
            uid=str(member.id)
            ensure_user_data(uid,data)
            if member.status!=discord.Status.offline:
                if data["users"][uid]["status"]=="offline":
                    data["users"][uid]["status"]="online"
                    data["users"][uid]["online_time"]=format_time(now_utc)
                    data["users"][uid]["offline_timer"]=0
                    await update_last_online(member,data)
                    if data["users"][uid].get("notify",True):
                        await channel.send(f"✅ {member.display_name} is online")
                increment_total_online(member.id,STATUS_CHECK_INTERVAL,data)
            else:
                if data["users"][uid]["status"]=="online":
                    data["users"][uid]["offline_timer"]+=STATUS_CHECK_INTERVAL
                    if data["users"][uid]["offline_timer"]>=OFFLINE_DELAY:
                        data["users"][uid]["status"]="offline"
                        data["users"][uid]["offline_time"]=format_time(now_utc)
                        if data["users"][uid].get("notify",True):
                            await channel.send(f"❌ {member.display_name} is offline")
        save_data(data)
    except Exception as e:
        safe_print("❌ Error in track_users:", e)
        traceback.print_exc()
        # ------------------ MODERATION COMMANDS ------------------

@bot.command()
async def rmute(ctx, targets: commands.Greedy[discord.Member], duration: str, *, reason: str="No reason provided"):
    if not can_execute_command(ctx.author.id):
        await ctx.send("⌛ Command cooldown active.")
        return
    seconds=parse_duration(duration)
    if seconds is None:
        await ctx.send("❌ Invalid duration format.")
        return
    data=load_data()
    channel=ctx.guild.get_channel(TRACK_CHANNEL_ID)
    for target in targets:
        try:
            await ctx.message.delete()
            await target.add_roles(ctx.guild.get_role(RMUTE_ROLE_ID))
            mute_id=f"{target.id}_{ctx.message.id}"
            data["mutes"][mute_id]={
                "user": target.id,
                "moderator": ctx.author.id,
                "duration": seconds,
                "reason": reason,
                "start": datetime.datetime.utcnow().timestamp()
            }
            data["rmute_usage"][str(ctx.author.id)]=data["rmute_usage"].get(str(ctx.author.id),0)+1
            embed=discord.Embed(title="🔇 Mute Applied", color=discord.Color.orange())
            embed.add_field(name="User", value=target.display_name)
            embed.add_field(name="Moderator", value=ctx.author.display_name)
            embed.add_field(name="Duration", value=duration)
            embed.add_field(name="Reason", value=reason)
            unmute_time=format_time(datetime.datetime.utcnow()+datetime.timedelta(seconds=seconds))
            embed.add_field(name="Unmute At", value=unmute_time)
            await channel.send(embed=embed)
            async def auto_unmute(member=target):
                await asyncio.sleep(seconds)
                await member.remove_roles(ctx.guild.get_role(RMUTE_ROLE_ID))
                embed2=discord.Embed(title="✅ Auto Unmute", color=discord.Color.green())
                embed2.add_field(name="User", value=member.display_name)
                embed2.add_field(name="Moderator", value=ctx.author.display_name)
                embed2.add_field(name="Duration", value=duration)
                embed2.add_field(name="Reason", value=reason)
                await channel.send(embed=embed2)
            bot.loop.create_task(auto_unmute())
        except Exception as e:
            safe_print("❌ Error in rmute:", e)
    save_data(data)

@bot.command()
async def runmute(ctx, target: discord.Member, duration: str, *, reason: str="No reason provided"):
    if not can_execute_command(ctx.author.id):
        await ctx.send("⌛ Command cooldown active.")
        return
    seconds=parse_duration(duration)
    if seconds is None:
        await ctx.send("❌ Invalid duration format.")
        return
    data=load_data()
    try:
        await target.add_roles(ctx.guild.get_role(RMUTE_ROLE_ID))
        mute_id=f"{target.id}_{ctx.message.id}"
        data["mutes"][mute_id]={
            "user": target.id,
            "moderator": ctx.author.id,
            "duration": seconds,
            "reason": reason,
            "start": datetime.datetime.utcnow().timestamp()
        }
        save_data(data)
        channel=ctx.guild.get_channel(TRACK_CHANNEL_ID)
        embed=discord.Embed(title="🔇 Runmute Applied", color=discord.Color.orange())
        embed.add_field(name="User", value=target.display_name)
        embed.add_field(name="Moderator", value=ctx.author.display_name)
        embed.add_field(name="Duration", value=duration)
        embed.add_field(name="Reason", value=reason)
        await channel.send(embed=embed)
    except Exception as e:
        safe_print("❌ Error in runmute:", e)

@bot.command()
async def rmlb(ctx):
    data=load_data()
    usage=data.get("rmute_usage",{})
    top=sorted(usage.items(), key=lambda x:x[1], reverse=True)[:10]
    embed=discord.Embed(title="📋 Top RMute Users", color=discord.Color.gold())
    for uid,count in top:
        member=ctx.guild.get_member(int(uid))
        name=member.display_name if member else f"User ID {uid}"
        embed.add_field(name=name, value=f"Mutes: {count}", inline=False)
    await ctx.send(embed=embed)

# ------------------ CACHE COMMAND ------------------
@bot.command()
async def rcache(ctx):
    if not any(r.id in RCACHE_ROLES for r in ctx.author.roles):
        await ctx.send("❌ You don't have permission to view cache")
        return
    data=load_data()
    images=data.get("images",{})
    embed=discord.Embed(title="🗂️ Cached Deleted Images/Files", color=discord.Color.purple())
    for mid, info in images.items():
        author=ctx.guild.get_member(info.get("author"))
        deleter=ctx.guild.get_member(info.get("deleter"))
        attachments=", ".join(info.get("attachments",[]))
        embed.add_field(name=f"Message ID {mid}", value=f"Author: {author.display_name if author else info.get('author')}\nDeleted by: {deleter.display_name if deleter else info.get('deleter')}\nAttachments: {attachments}", inline=False)
    await ctx.send(embed=embed)

# ------------------ TOP LEADERBOARD ------------------
@bot.command()
async def tlb(ctx):
    data=load_data()
    users=data.get("users",{})
    top=sorted(users.items(), key=lambda x:x[1].get("total_online_seconds",0), reverse=True)[:10]
    embed=discord.Embed(title="📊 Timetrack Leaderboard", color=discord.Color.green())
    for uid, info in top:
        member=ctx.guild.get_member(int(uid))
        name=member.display_name if member else f"User ID {uid}"
        embed.add_field(name=name, value=f"Total Online: {info.get('total_online_seconds',0)}s", inline=False)
    await ctx.send(embed=embed)

# ------------------ LOGGING EVENTS ------------------
@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    data=load_data()
    attachments=[a.url for a in message.attachments]
    data["images"][str(message.id)]={"author": message.author.id, "deleter": None, "attachments": attachments}
    save_data(data)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot: return
    data=load_data()
    data["users"][str(before.author.id)]["last_edit"]=format_time(datetime.datetime.utcnow())
    save_data(data)

@bot.event
async def on_message(message):
    if message.author.bot: return
    data=load_data()
    data["users"][str(message.author.id)]["last_message"]=message.content
    save_data(data)
    await bot.process_commands(message)

@bot.event
async def on_guild_role_create(role):
    data=load_data()
    data["logs"][str(role.id)]={"created_by": None, "permissions": str(role.permissions), "time": format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_role_delete(role):
    data=load_data()
    data["logs"][str(role.id)]={"deleted_by": None, "time": format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_role_update(before, after):
    data=load_data()
    data["logs"][str(after.id)]={"before": str(before.permissions), "after": str(after.permissions), "editor": None, "time": format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_channel_create(channel):
    data=load_data()
    data["logs"][str(channel.id)]={"created_by": None, "name": channel.name, "time": format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_channel_delete(channel):
    data=load_data()
    data["logs"][str(channel.id)]={"deleted_by": None, "name": channel.name, "time": format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_channel_update(before, after):
    data=load_data()
    data["logs"][str(after.id)]={"before": before.name, "after": after.name, "editor": None, "time": format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_webhooks_update(channel):
    data=load_data()
    data["logs"]["webhook_update_"+str(channel.id)]={"time": format_time(datetime.datetime.utcnow())}
    save_data(data)

# ------------------ RHELP ------------------
@bot.command()
async def rhelp(ctx):
    embed=discord.Embed(title="🤖 Bot Commands", color=discord.Color.blue())
    embed.add_field(name="!timetrack [user]", value="Shows online/offline status, last message, last online time, stats", inline=False)
    embed.add_field(name="!rmute [user(s)] [duration] [reason]", value="Mute user(s), auto-unmute, logs", inline=False)
    embed.add_field(name="!runmute [user] [duration] [reason]", value="Mute a user and logs the original duration", inline=False)
    embed.add_field(name="!rmlb", value="Shows top 10 users who used rmute", inline=False)
    embed.add_field(name="!rcache", value="Shows deleted images/files (roles only)", inline=False)
    embed.add_field(name="!tlb", value="Shows top online users leaderboard", inline=False)
    await ctx.send(embed=embed)

# ------------------ STARTUP ------------------
@bot.event
async def on_ready():
    safe_print(f"✅ Logged in as {bot.user}")
    track_users.start()
    safe_print("📡 Timetrack started.")
    
# ------------------ RUN BOT ------------------
bot.run(TOKEN)
