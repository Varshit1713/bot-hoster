# ------------------ IMPORTS ------------------
import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import json
import pytz
import os

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
TRACK_CHANNEL_ID = 1410458084874260592
RMUTE_ROLE_ID = 1410423854563721287
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
DAILY_AVG_RESET_HOUR = 0  # Reset at midnight UTC
DATA_FILE = "bot_data.json"

# ------------------ HELPER FUNCTIONS ------------------
def load_data():
    if not os.path.exists(DATA_FILE):
        return {"users": {}, "mutes": {}, "rmute_usage": {}, "images": {}, "logs": {}, "dangerous": {}}
    with open(DATA_FILE,"r") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE,"w") as f:
        json.dump(data,f,indent=4)

def parse_duration(s):
    try:
        units={"s":1,"m":60,"h":3600,"d":86400}
        amount=int(s[:-1])
        unit=s[-1]
        return amount*units[unit]
    except:
        return None

def format_time(dt, tzs=None):
    if tzs is None:
        tzs=["UTC","US/Eastern","US/Central","US/Pacific"]
    out=[]
    for tz in tzs:
        tz_obj=pytz.timezone(tz)
        out.append(dt.astimezone(tz_obj).strftime("%Y-%m-%d %H:%M:%S"))
    return "\n".join(out)

def safe_print(*args):
    print(*args)

# ------------------ BOT SETUP ------------------
intents=discord.Intents.all()
bot=commands.Bot(command_prefix="!", intents=intents)

# ------------------ TIMETRACK ------------------
@tasks.loop(seconds=53)
async def track_users():
    data=load_data()
    channel=bot.get_channel(TRACK_CHANNEL_ID)
    now=datetime.datetime.utcnow()
    for guild in bot.guilds:
        for member in guild.members:
            if any(r.id in [1410419924173848626,1410420126003630122,1410423594579918860,1410421466666631279,1410421647265108038,1410419345234067568,1410422029236047975,1410458084874260592] for r in member.roles):
                user_id=str(member.id)
                if user_id not in data["users"]:
                    data["users"][user_id]={"online":False,"last_online":None,"total_online_seconds":0,"daily_online_seconds":0,"weekly_online_seconds":0,"monthly_online_seconds":0,"last_message":None,"average_daily":0}
                prev_online=data["users"][user_id]["online"]
                if member.status != discord.Status.offline:
                    data["users"][user_id]["online"]=True
                    data["users"][user_id]["last_online"]=now.timestamp()
                    if not prev_online:
                        await channel.send(f"🟢 {member.display_name} is online")
                else:
                    data["users"][user_id]["online"]=False
                    if prev_online:
                        await channel.send(f"🔴 {member.display_name} is offline")
    save_data(data)

# ------------------ MODERATION ------------------
@bot.command()
async def rmute(ctx, targets: commands.Greedy[discord.Member], duration:str, *, reason:str="No reason provided"):
    seconds=parse_duration(duration)
    if seconds is None:
        await ctx.send("❌ Invalid duration format.")
        return
    data=load_data()
    channel=bot.get_channel(TRACK_CHANNEL_ID)
    for target in targets:
        try:
            await ctx.message.delete()
            await target.add_roles(ctx.guild.get_role(RMUTE_ROLE_ID))
            mute_id=f"{target.id}_{ctx.message.id}"
            data["mutes"][mute_id]={"user":target.id,"moderator":ctx.author.id,"duration":seconds,"reason":reason,"start":datetime.datetime.utcnow().timestamp()}
            data["rmute_usage"][str(ctx.author.id)]=data.get("rmute_usage",{}).get(str(ctx.author.id),0)+1
            embed=discord.Embed(title="🔇 Mute Applied", color=discord.Color.orange())
            embed.add_field(name="User", value=target.display_name)
            embed.add_field(name="Moderator", value=ctx.author.display_name)
            embed.add_field(name="Duration", value=duration)
            embed.add_field(name="Reason", value=reason)
            embed.add_field(name="Unmute At", value=format_time(datetime.datetime.utcnow()+datetime.timedelta(seconds=seconds)))
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
async def runmute(ctx, target:discord.Member, duration:str, *, reason:str="No reason provided"):
    seconds=parse_duration(duration)
    if seconds is None:
        await ctx.send("❌ Invalid duration format.")
        return
    data=load_data()
    try:
        await target.add_roles(ctx.guild.get_role(RMUTE_ROLE_ID))
        mute_id=f"{target.id}_{ctx.message.id}"
        data["mutes"][mute_id]={"user":target.id,"moderator":ctx.author.id,"duration":seconds,"reason":reason,"start":datetime.datetime.utcnow().timestamp()}
        save_data(data)
        channel=bot.get_channel(TRACK_CHANNEL_ID)
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

# ------------------ CACHE ------------------
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

# ------------------ LEADERBOARDS ------------------
@bot.command()
async def tlb(ctx):
    data=load_data()
    users=data.get("users",{})
    top=sorted([(uid,info) for uid,info in users.items() if any(r.id in [1410419924173848626,1410420126003630122,1410423594579918860,1410421466666631279,1410421647265108038,1410419345234067568,1410422029236047975,1410458084874260592] for r in ctx.guild.get_member(int(uid)).roles)], key=lambda x:x[1].get("daily_online_seconds",0), reverse=True)[:10]
    embed=discord.Embed(title="📊 Timetrack Leaderboard (Daily Average)", color=discord.Color.green())
    for uid, info in top:
        member=ctx.guild.get_member(int(uid))
        name=member.display_name if member else f"User ID {uid}"
        embed.add_field(name=name, value=f"Daily Avg: {info.get('daily_online_seconds',0)//3600}h {(info.get('daily_online_seconds',0)%3600)//60}m", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def tdm(ctx):
    data=load_data()
    users=data.get("users",{})
    top=sorted([(uid,info) for uid,info in users.items() if not any(r.id in [1410419924173848626,1410420126003630122,1410423594579918860,1410421466666631279,1410421647265108038,1410419345234067568,1410422029236047975,1410458084874260592] for r in ctx.guild.get_member(int(uid)).roles)], key=lambda x:x[1].get("daily_online_seconds",0), reverse=True)[:10]
    embed=discord.Embed(title="📊 Timetrack Leaderboard (Non-Roles)", color=discord.Color.red())
    for uid, info in top:
        member=ctx.guild.get_member(int(uid))
        name=member.display_name if member else f"User ID {uid}"
        embed.add_field(name=name, value=f"Daily Avg: {info.get('daily_online_seconds',0)//3600}h {(info.get('daily_online_seconds',0)%3600)//60}m", inline=False)
    await ctx.send(embed=embed)

# ------------------ MESSAGE EVENTS ------------------
@bot.event
async def on_message_delete(message):
    if message.author.bot: return
    data=load_data()
    data["images"][str(message.id)]={"author": message.author.id, "deleter": None, "attachments":[a.url for a in message.attachments], "reply": message.reference.message_id if message.reference else None}
    save_data(data)
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot: return
    data=load_data()
    data["users"].setdefault(str(before.author.id),{})["last_edit"]=format_time(datetime.datetime.utcnow())
    save_data(data)
    await bot.process_commands(after)

@bot.event
async def on_message(message):
    if message.author.bot: return
    data=load_data()
    data["users"].setdefault(str(message.author.id),{})["last_message"]=message.content
    save_data(data)
    await bot.process_commands(message)

# ------------------ ROLE/CHANNEL/WEBHOOK LOGS ------------------
@bot.event
async def on_guild_role_create(role):
    data=load_data()
    data["logs"][str(role.id)]={"created_by":None,"permissions":str(role.permissions),"time":format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_role_delete(role):
    data=load_data()
    data["logs"][str(role.id)]={"deleted_by":None,"time":format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_role_update(before, after):
    data=load_data()
    data["logs"][str(after.id)]={"before":str(before.permissions),"after":str(after.permissions),"editor":None,"time":format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_channel_create(channel):
    data=load_data()
    data["logs"][str(channel.id)]={"created_by":None,"name":channel.name,"time":format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_channel_delete(channel):
    data=load_data()
    data["logs"][str(channel.id)]={"deleted_by":None,"name":channel.name,"time":format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_guild_channel_update(before, after):
    data=load_data()
    data["logs"][str(after.id)]={"before":before.name,"after":after.name,"editor":None,"time":format_time(datetime.datetime.utcnow())}
    save_data(data)

@bot.event
async def on_webhooks_update(channel):
    data=load_data()
    data["logs"]["webhook_update_"+str(channel.id)]={"time":format_time(datetime.datetime.utcnow())}
    save_data(data)

# ------------------ PING COMMANDS ------------------
@bot.command()
async def sping(ctx):
    try:
        await ctx.message.delete()
    except: pass
    channel=ctx.channel
    if ctx.message.reference:
        msg=await channel.fetch_message(ctx.message.reference.message_id)
        content=f"{ctx.author.display_name} pinged {msg.author.display_name} with reply: {msg.content}"
    else:
        content=f"{ctx.author.display_name} issued sping"
    for member in ctx.guild.members:
        if STAFF_PING_ROLE in [r.id for r in member.roles]:
            await channel.send(f"{member.mention} {content}")

@bot.command()
async def hsping(ctx):
    try:
        await ctx.message.delete()
    except: pass
    channel=ctx.channel
    if ctx.message.reference:
        msg=await channel.fetch_message(ctx.message.reference.message_id)
        content=f"{ctx.author.display_name} pinged {msg.author.display_name} with reply: {msg.content}"
    else:
        content=f"{ctx.author.display_name} issued hsping"
    for member in ctx.guild.members:
        if HIGHER_STAFF_PING_ROLE in [r.id for r in member.roles]:
            await channel.send(f"{member.mention} {content}")

# ------------------ DISABLE DMS ------------------
@bot.command()
async def rdm(ctx):
    data=load_data()
    data.setdefault("rdm",[]).append(ctx.author.id)
    save_data(data)
    await ctx.send("✅ You will no longer receive DMs from the bot.")

------------------ HELP ------------------

@bot.command() async def rhelp(ctx): embed=discord.Embed(title="🤖 Bot Commands", color=discord.Color.blue()) embed.add_field(name="!timetrack [user]", value="Shows online/offline status, last message, last online time, stats", inline=False) embed.add_field(name="!rmute [user(s)] [duration] [reason]", value="Mute user(s), auto-unmute, logs", inline=False) embed.add_field(name="!runmute [user] [duration] [reason]", value="Mute a user and logs the original duration", inline=False) embed.add_field(name="!rmlb", value="Shows top 10 users who used rmute", inline=False) embed.add_field(name="!rcache", value="Shows deleted images/files (roles only)", inline=False) embed.add_field(name="!tlb", value="Shows top online users leaderboard (daily average, filtered by roles)", inline=False) embed.add_field(name="!tdm", value="Shows top online users leaderboard (daily average, non-filtered roles)", inline=False) embed.add_field(name="!sping", value="Ping staff roles, includes reply info", inline=False) embed.add_field(name="!hsping", value="Ping higher staff roles, includes reply info", inline=False) embed.add_field(name="!rdm", value="Disable DMs from bot", inline=False) await ctx.send(embed=embed)

------------------ STARTUP ------------------

@bot.event async def on_ready(): safe_print(f"✅ Logged in as {bot.user}") track_users.start() safe_print("📡 Timetrack started.")

------------------ RUN BOT ------------------

bot.run(TOKEN)
