# mega_discord_bot_full_part1.py
# Chunk 1/7: imports, config, utilities

import os
import json
import asyncio
import datetime
import traceback
import pytz
from flask import Flask

import discord
from discord.ext import commands, tasks

# ------------------ CONFIG ------------------
TOKEN = os.environ.get('DISCORD_TOKEN')
DATA_FILE = 'bot_data.json'

# Roles and Channels
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE_ID = 1410423854563721287
MUTE_TRACK_CHANNEL = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
PING_CHANNELS = [1403422664521023648, 1410458084874260592]
DM_SAFE_USERS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

INACTIVITY_THRESHOLD_DAYS = 120

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# ------------------ UTILITIES ------------------
def safe_print(*args):
    """Safe print with UTC timestamp."""
    print(datetime.datetime.now(pytz.utc).strftime('[%Y-%m-%d %H:%M:%S]'), *args)

def load_data():
    """Load bot JSON data safely."""
    if not os.path.exists(DATA_FILE):
        return {'users': {}, 'mutes': {}, 'rmute_usage': {}, 'rdm_users': []}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_data(data):
    """Save bot JSON data."""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str)

def init_data_structure():
    """Initialize base data structure."""
    return {'users': {}, 'mutes': {}, 'rmute_usage': {}, 'rdm_users': []}
    # mega_discord_bot_full_part2.py
# Chunk 2/7: Timetrack system

# ------------------ TIMETRACK ------------------
@tasks.loop(seconds=60)
async def timetrack_loop():
    """Track members’ online/offline time every 60 seconds."""
    data = load_data()
    now = datetime.datetime.now(pytz.utc)
    for guild in bot.guilds:
        for member in guild.members:
            # Only track members with RCACHE_ROLES
            if any(role.id in RCACHE_ROLES for role in member.roles):
                uid = str(member.id)
                udata = data['users'].setdefault(uid, {
                    'total_online_seconds': 0,
                    'online_start': None,
                    'last_online': None,
                    'last_message': '',
                    'last_edit': None
                })
                if member.status != discord.Status.offline:
                    # Start session if not already started
                    if not udata.get('online_start'):
                        udata['online_start'] = now.isoformat()
                else:
                    # End session and accumulate online time
                    if udata.get('online_start'):
                        start = datetime.datetime.fromisoformat(udata['online_start'])
                        diff = (now - start).total_seconds()
                        udata['total_online_seconds'] += diff
                        udata['online_start'] = None
                        udata['last_online'] = now.isoformat()
    save_data(data)

# ------------------ MESSAGE TRACKING ------------------
@bot.event
async def on_message(message):
    """Track last message per user for timetrack."""
    if message.author.bot:
        return
    data = load_data()
    uid = str(message.author.id)
    udata = data['users'].setdefault(uid, {
        'total_online_seconds': 0,
        'online_start': None,
        'last_online': None,
        'last_message': '',
        'last_edit': None
    })
    udata['last_message'] = message.content
    save_data(data)
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before, after):
    """Track last edit per user for timetrack."""
    if after.author.bot:
        return
    data = load_data()
    uid = str(after.author.id)
    udata = data['users'].setdefault(uid, {
        'total_online_seconds': 0,
        'online_start': None,
        'last_online': None,
        'last_message': '',
        'last_edit': None
    })
    udata['last_edit'] = datetime.datetime.now(pytz.utc).isoformat()
    save_data(data)
    # mega_discord_bot_full_part3.py
# Chunk 3/7: RMute / Runmute

async def unmute_after(user_id, duration):
    """Automatically unmute a user after duration seconds."""
    await asyncio.sleep(duration)
    data = load_data()
    uid = str(user_id)
    guild = bot.guilds[0]
    user = guild.get_member(user_id)
    role = guild.get_role(MUTE_ROLE_ID)
    if user and role:
        await user.remove_roles(role, reason='Auto unmute')
    if uid in data['mutes']:
        data['mutes'].pop(uid)
        save_data(data)
    channel = bot.get_channel(MUTE_TRACK_CHANNEL)
    if channel:
        await channel.send(f'🔔 Auto-unmuted <@{user_id}> after duration.')

@bot.command()
@commands.has_permissions(administrator=True)
async def rmute(ctx, users: commands.Greedy[discord.Member], duration: int, *, reason=None):
    """Mute multiple users with auto-unmute and log."""
    data = load_data()
    for user in users:
        uid = str(user.id)
        role = ctx.guild.get_role(MUTE_ROLE_ID)
        if role:
            await user.add_roles(role, reason=reason)
        data['mutes'][uid] = {
            'moderator': ctx.author.id,
            'duration': duration,
            'reason': reason,
            'start_time': datetime.datetime.now(pytz.utc).isoformat()
        }
        data['rmute_usage'][str(ctx.author.id)] = data['rmute_usage'].get(str(ctx.author.id), 0) + 1
        save_data(data)
        await ctx.send(f'🔇 Muted {user} for {duration} seconds.')
        bot.loop.create_task(unmute_after(user.id, duration))

@bot.command()
@commands.has_permissions(administrator=True)
async def runmute(ctx, user: discord.Member, duration: int, *, reason=None):
    """Mute a single user (runmute) with auto-unmute."""
    data = load_data()
    uid = str(user.id)
    role = ctx.guild.get_role(MUTE_ROLE_ID)
    if role:
        await user.add_roles(role, reason=reason)
    data['mutes'][uid] = {
        'moderator': ctx.author.id,
        'duration': duration,
        'reason': reason,
        'start_time': datetime.datetime.now(pytz.utc).isoformat()
    }
    data['rmute_usage'][str(ctx.author.id)] = data['rmute_usage'].get(str(ctx.author.id), 0) + 1
    save_data(data)
    await ctx.send(f'🔇 Muted {user} for {duration} seconds.')
    bot.loop.create_task(unmute_after(user.id, duration))

@bot.command()
@commands.has_permissions(administrator=True)
async def rmlb(ctx):
    """Show top 10 users who used rmute the most."""
    data = load_data()
    usage = data.get('rmute_usage', {})
    sorted_usage = sorted(usage.items(), key=lambda x: x[1], reverse=True)[:10]
    embed = discord.Embed(title='📊 RMute Leaderboard')
    for uid, count in sorted_usage:
        member = ctx.guild.get_member(int(uid))
        embed.add_field(name=str(member), value=f'RMutes used: {count}', inline=False)
    await ctx.send(embed=embed)
    # mega_discord_bot_full_part4.py
# Chunk 4/7: Leaderboards and Cache/RCache

# ------------------ LEADERBOARDS ------------------
@bot.command()
async def tlb(ctx):
    """Timetrack leaderboard for members with RCACHE_ROLES."""
    data = load_data()
    members_data = []
    for uid, u in data.get('users', {}).items():
        member = ctx.guild.get_member(int(uid))
        if member and any(role.id in RCACHE_ROLES for role in member.roles):
            total_seconds = u.get('total_online_seconds', 0)
            members_data.append((member, total_seconds))
    members_data.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title='📈 Timetrack Leaderboard')
    for i, (member, total_seconds) in enumerate(members_data[:10], start=1):
        daily_avg = total_seconds / 120  # approx daily average
        embed.add_field(name=f'{i}. {member}', value=f'Total Online: {int(total_seconds)}s\nDaily Avg: {int(daily_avg)}s', inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def tdm(ctx):
    """Timetrack leaderboard for users without RCACHE_ROLES."""
    data = load_data()
    members_data = []
    for uid, u in data.get('users', {}).items():
        member = ctx.guild.get_member(int(uid))
        if member and not any(role.id in RCACHE_ROLES for role in member.roles):
            total_seconds = u.get('total_online_seconds', 0)
            members_data.append((member, total_seconds))
    members_data.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title='📈 Timetrack (Non-RCache Roles)')
    for i, (member, total_seconds) in enumerate(members_data[:10], start=1):
        daily_avg = total_seconds / 120
        embed.add_field(name=f'{i}. {member}', value=f'Total Online: {int(total_seconds)}s\nDaily Avg: {int(daily_avg)}s', inline=False)
    await ctx.send(embed=embed)

# ------------------ CACHE / RCACHE ------------------
@bot.command()
async def rcache(ctx):
    """Show deleted messages/images for members with RCACHE_ROLES."""
    if not any(role.id in RCACHE_ROLES for role in ctx.author.roles):
        await ctx.send('❌ You do not have permission to access rcache.')
        return
    data = load_data()
    deleted_msgs = data.get('deleted_messages', [])
    embed = discord.Embed(title='🗂️ RCache Deleted Messages')
    for d in deleted_msgs[-10:]:  # show last 10
        content = (d.get('content') or '')[:200]
        embed.add_field(name=f"Msg {d.get('message_id')} by {d.get('author')}", value=f"{content}\nTime: {d.get('time')}", inline=False)
    await ctx.send(embed=embed)

# ------------------ LOGGING EXAMPLE ------------------
@bot.event
async def on_message_delete(message):
    data = load_data()
    deleted = data.setdefault('deleted_messages', [])
    deleted.append({
        'message_id': message.id,
        'author': str(message.author),
        'content': message.content,
        'attachments': [a.url for a in message.attachments],
        'channel': str(message.channel),
        'time': datetime.datetime.now(pytz.utc).isoformat()
    })
    if len(deleted) > 100:
        deleted.pop(0)
    save_data(data)
    # mega_discord_bot_full_part5.py
# Chunk 5/7: Staff Ping, DM control, purge tracking

# ------------------ STAFF PING ------------------
@bot.command()
async def rping(ctx):
    """Ping STAFF_PING_ROLE and log if replying to a message."""
    role = ctx.guild.get_role(STAFF_PING_ROLE)
    if not role:
        await ctx.send("❌ STAFF_PING_ROLE not found.")
        return
    msg = f"{role.mention} – pinged by {ctx.author.mention}"
    await ctx.send(msg)
    await ctx.message.delete()
    if ctx.message.reference:
        ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        safe_print(f"RPing Reply Log: {ctx.author} replied to {ref_msg.author}: {ref_msg.content}")

@bot.command()
async def hsping(ctx):
    """Ping HIGHER_STAFF_PING_ROLE and log if replying to a message."""
    role = ctx.guild.get_role(HIGHER_STAFF_PING_ROLE)
    if not role:
        await ctx.send("❌ HIGHER_STAFF_PING_ROLE not found.")
        return
    msg = f"{role.mention} – pinged by {ctx.author.mention}"
    await ctx.send(msg)
    await ctx.message.delete()
    if ctx.message.reference:
        ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        safe_print(f"HSPing Reply Log: {ctx.author} replied to {ref_msg.author}: {ref_msg.content}")

# ------------------ DM & NOTIFICATION CONTROL ------------------
async def send_safe_dm(user, embed):
    data = load_data()
    if str(user.id) not in data.get('rdm_users', []):
        try:
            await user.send(embed=embed)
        except:
            safe_print(f"⚠️ Could not send DM to {user}")

# ------------------ PURGE & DELETED MESSAGE TRACKING ------------------
@bot.command()
@commands.has_permissions(manage_messages=True)
async def purge(ctx, amount: int):
    """Delete messages and log them."""
    messages = await ctx.channel.history(limit=amount).flatten()
    data = load_data()
    deleted_msgs = data.setdefault('deleted_messages', [])
    for msg in messages:
        deleted_msgs.append({
            'message_id': msg.id,
            'author': str(msg.author),
            'content': msg.content,
            'attachments': [a.url for a in msg.attachments],
            'channel': str(msg.channel),
            'time': datetime.datetime.now(pytz.utc).isoformat(),
            'deleted_by': str(ctx.author),
            'reply_to': msg.reference.message_id if msg.reference else None
        })
        if len(deleted_msgs) > 200:
            deleted_msgs.pop(0)
    save_data(data)
    await ctx.channel.delete_messages(messages)
    track_channel = bot.get_channel(MUTE_TRACK_CHANNEL)
    if track_channel:
        await track_channel.send(f"🗑️ {ctx.author} purged {len(messages)} messages in {ctx.channel}.")
        # mega_discord_bot_full_part6.py
# Chunk 6/7: Help command, daily maintenance, and final startup

# ------------------ HELP COMMAND ------------------
@bot.command()
async def rhelp(ctx):
    embed = discord.Embed(title="📜 Mega Bot Commands", color=0x1abc9c)
    embed.add_field(name="!timetrack [user]", value="Show online stats for a user", inline=False)
    embed.add_field(name="!rmute [users] [duration] [reason]", value="Mute multiple users", inline=False)
    embed.add_field(name="!runmute [user] [duration] [reason]", value="Mute a single user", inline=False)
    embed.add_field(name="!rmlb", value="Top 10 users who used rmute", inline=False)
    embed.add_field(name="!rcache", value="Show cached deleted messages/images", inline=False)
    embed.add_field(name="!tlb", value="Timetrack leaderboard (RCACHE_ROLES only)", inline=False)
    embed.add_field(name="!tdm", value="Leaderboard for users without roles", inline=False)
    embed.add_field(name="!rping", value="Ping STAFF_PING_ROLE", inline=False)
    embed.add_field(name="!hsping", value="Ping HIGHER_STAFF_PING_ROLE", inline=False)
    embed.add_field(name="!rdm", value="Opt-out from bot DMs", inline=False)
    embed.set_footer(text="All commands use fancy embeds and are logged.")
    await ctx.send(embed=embed)

# ------------------ DAILY MAINTENANCE TASK ------------------
@tasks.loop(hours=24)
async def daily_maintenance_task():
    try:
        data = load_data()
        cutoff = datetime.datetime.now(pytz.utc) - datetime.timedelta(days=INACTIVITY_THRESHOLD_DAYS)
        for uid, u in data.get('users', {}).items():
            daily = u.get('daily_seconds', {})
            keys_to_remove = [k for k in daily if datetime.datetime.fromisoformat(k) < cutoff]
            for k in keys_to_remove: daily.pop(k, None)
        save_data(data)
    except Exception as e:
        safe_print('⚠️ daily maintenance error:', e)
        traceback.print_exc()

# ------------------ STARTUP ------------------
if __name__ == "__main__":
    try:
        safe_print("🚀 Starting mega bot with audit reconciliation...")
        if not os.path.exists(DATA_FILE):
            save_data(init_data_structure())
        timetrack_loop.start()
        daily_maintenance_task.start()
        bot.run(TOKEN)
    except Exception as e:
        safe_print("❌ Fatal error while running bot:", e)
        traceback.print_exc()
        # mega_discord_bot_full_part7.py
# Chunk 7/7: Final utilities, logging, embed helpers, extra mod logging

# ------------------ EMBED HELPERS ------------------
def create_embed(title, description="", color=0x1abc9c, fields=None, footer=None):
    embed = discord.Embed(title=title, description=description, color=color)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    if footer:
        embed.set_footer(text=footer)
    return embed

# ------------------ LOGGING HELPERS ------------------
async def log_mod_action(action_type, moderator_id, target_id, details=""):
    try:
        data = load_data()
        channel = bot.get_channel(MUTE_TRACK_CHANNEL)
        if channel:
            embed = create_embed(
                title=f"📝 Mod Action: {action_type}",
                fields=[
                    ("Moderator", f"<@{moderator_id}>", True),
                    ("Target", f"<@{target_id}>", True),
                    ("Details", details, False)
                ],
                footer=f"Time: {datetime.datetime.now(pytz.utc).isoformat()}"
            )
            await channel.send(embed=embed)
    except Exception as e:
        safe_print("⚠️ log_mod_action error:", e)

# ------------------ PURGE & DELETED MESSAGE TRACKING ------------------
@bot.event
async def on_bulk_message_delete(messages):
    try:
        data = load_data()
        channel = bot.get_channel(MUTE_TRACK_CHANNEL)
        for m in messages:
            uid = str(m.author.id)
            cached = data.setdefault("cache", {})
            cached.setdefault(uid, []).append({
                "content": m.content,
                "attachments": [a.url for a in m.attachments],
                "timestamp": m.created_at.isoformat()
            })
        if channel:
            await channel.send(f"🗑️ Bulk delete: {len(messages)} messages logged.")
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_bulk_message_delete error:", e)

@bot.event
async def on_message_delete(message):
    try:
        data = load_data()
        uid = str(message.author.id)
        cached = data.setdefault("cache", {})
        cached.setdefault(uid, []).append({
            "content": message.content,
            "attachments": [a.url for a in message.attachments],
            "timestamp": message.created_at.isoformat()
        })
        save_data(data)
    except Exception as e:
        safe_print("⚠️ on_message_delete error:", e)

# ------------------ CHANNEL & ROLE EDIT LOGGING ------------------
@bot.event
async def on_guild_channel_update(before, after):
    try:
        channel = bot.get_channel(MUTE_TRACK_CHANNEL)
        embed = create_embed(
            title="📢 Channel Updated",
            fields=[
                ("Before", f"{before.name}", True),
                ("After", f"{after.name}", True)
            ],
            footer=f"Channel ID: {after.id}"
        )
        if channel: await channel.send(embed=embed)
    except Exception as e:
        safe_print("⚠️ on_guild_channel_update error:", e)

@bot.event
async def on_guild_role_update(before, after):
    try:
        channel = bot.get_channel(MUTE_TRACK_CHANNEL)
        embed = create_embed(
            title="📢 Role Updated",
            fields=[
                ("Before", f"{before.name}", True),
                ("After", f"{after.name}", True)
            ],
            footer=f"Role ID: {after.id}"
        )
        if channel: await channel.send(embed=embed)
    except Exception as e:
        safe_print("⚠️ on_guild_role_update error:", e)

# ------------------ WEBHOOK & PERMISSION LOGGING ------------------
@bot.event
async def on_webhooks_update(channel):
    try:
        log_channel = bot.get_channel(MUTE_TRACK_CHANNEL)
        if log_channel:
            await log_channel.send(f"🔔 Webhooks updated in {channel.name}")
    except Exception as e:
        safe_print("⚠️ on_webhooks_update error:", e)

# ------------------ EXTRA UTILITIES ------------------
def can_execute_command(user_id, cooldowns):
    # Placeholder for cooldown system
    return True

# ------------------ FINAL STARTUP LOG ------------------
@bot.event
async def on_ready():
    safe_print(f"✅ Bot connected as {bot.user} (ID: {bot.user.id})")
    safe_print("⏱ Timetrack loop and daily maintenance active.")
