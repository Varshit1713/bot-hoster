# mega_discord_bot_full.py
# Comprehensive Discord moderation & timetrack bot
# Features:
# - Timetrack (presence-based approximate)
# - RMute / Runmute with auto-unmute and DMs
# - RCACHE: message & attachment cache, searches (user/date/search)
# - Leaderboards: tlb (tracked roles) and tdm (others)
# - rmlb: top rmute users
# - Logging for channel/role/webhook changes, message edits/deletes, purges
# - Audit reconciliation on startup
# - rping & hsping staff pings with reply context
# - rdm opt-out from DMs
# - rdump debug export, rhelp
# - Daily maintenance & backups
# - Flask keep-alive server
# - Uses JSON file bot_data.json for persistence and rotating backups
#
# WARNING: Review the IDs and permissions before running. Requires Discord bot token
# in the DISCORD_TOKEN environment variable and discord.py 2.x installed.

import os
import json
import asyncio
import datetime
import traceback
import re
import threading
from typing import Optional, List, Dict, Any
from flask import Flask
import discord
from discord.ext import commands, tasks

# ------------ CONFIG (KEPT FROM USER) ------------
TOKEN = os.environ.get('DISCORD_TOKEN')
if not TOKEN:
    raise SystemExit("DISCORD_TOKEN environment variable not set")

GUILD_ID = 140335996236909773
DATA_FILE = "bot_data.json"
BACKUP_DIR = "bot_backups"
MAX_BACKUPS = 30
TIMEZONE = datetime.timezone.utc

# IDs provided by user (preserved)
RCACHE_ROLES = [1410422029236047975, 1410422762895577088, 1406326282429403306]
MUTE_ROLE = 1410423854563721287
TRACKING_CHANNEL = 1410458084874260592
STAFF_PING_ROLE = 1410422475942264842
HIGHER_STAFF_PING_ROLE = 1410422656112791592
DANGEROUS_USER_IDS = [1406326282429403306, 1410422762895577088, 1410422029236047975]

# Operation settings
PRESENCE_INTERVAL = 60        # seconds for timetrack loop
OFFLINE_DELAY = 53            # seconds to confirm offline
AUTO_SAVE_INTERVAL = 120     # seconds to auto-save
AUDIT_LOOKBACK_SECONDS = 3600 # how far back to check audit logs on startup

# Bot setup
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ------------ DATA HANDLING ------------
def init_data() -> Dict[str, Any]:
    return {
        "users": {},            # user_id -> data (timetrack, last_message, etc.)
        "mutes": {},            # mute_id -> details
        "rmute_usage": {},      # moderator_id -> count
        "cached_messages": {},  # message_id -> cached content + attachments
        "logs": [],             # list of events
        "rdm_users": []         # users opted out of DMs (strings)
    }

def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return init_data()
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("Failed to load data:", e)
        # backup corrupt file
        try:
            os.rename(DATA_FILE, DATA_FILE + ".corrupt")
        except:
            pass
        return init_data()

def rotate_backups():
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        files = sorted(os.listdir(BACKUP_DIR))
        while len(files) > MAX_BACKUPS:
            os.remove(os.path.join(BACKUP_DIR, files.pop(0)))
    except Exception as e:
        print("Backup rotation error:", e)

def save_data(data: Dict[str, Any] = None):
    if data is None:
        data = bot_data
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        backup = os.path.join(BACKUP_DIR, f"backup_{ts}.json")
        with open(backup, "w", encoding="utf-8") as bf:
            json.dump(data, bf, indent=2, default=str)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        rotate_backups()
    except Exception as e:
        print("Error saving data:", e)
        traceback.print_exc()

bot_data = load_data()

# ------------ HELPERS ------------
def ensure_user(u_id: int):
    s = str(u_id)
    if s not in bot_data["users"]:
        bot_data["users"][s] = {
            "last_online": None,
            "last_message": None,
            "last_edit": None,
            "total_online_seconds": 0,
            "online_start": None,
            "daily_seconds": {},
            "offline_timer": 0
        }
    return bot_data["users"][s]

def format_seconds(seconds: int) -> str:
    sec = int(seconds)
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def parse_duration(s: str) -> Optional[int]:
    # accepts formats like 10s, 5m, 2h, 1d or plain seconds
    if s is None:
        return None
    s = s.strip().lower()
    m = re.match(r"^(\d+)([smhd])$", s)
    if m:
        val = int(m.group(1))
        unit = m.group(2)
        if unit == 's': return val
        if unit == 'm': return val * 60
        if unit == 'h': return val * 3600
        if unit == 'd': return val * 86400
    try:
        return int(s)
    except:
        return None

def is_rdm(user_id: int) -> bool:
    return str(user_id) in bot_data.get("rdm_users", [])

async def send_dm_safe(user: discord.User, embed: discord.Embed):
    if is_rdm(user.id):
        return False
    try:
        await user.send(embed=embed)
        return True
    except:
        return False

def log_event(title: str, description: str):
    entry = {"title": title, "description": description, "timestamp": datetime.datetime.utcnow().isoformat()}
    bot_data["logs"].append(entry)
    save_data()
    # try to send to tracking channel
    ch = bot.get_channel(TRACKING_CHANNEL)
    if ch:
        embed = discord.Embed(title=title, description=description, color=discord.Color.orange())
        embed.timestamp = datetime.datetime.utcnow()
        try:
            asyncio.create_task(ch.send(embed=embed))
        except:
            pass

# ------------ EMBED BUILDERS ------------
def build_mute_dm_embed(moderator: discord.Member, duration_str: Optional[str], reason: str) -> discord.Embed:
    embed = discord.Embed(title="ðŸ”‡ You have been muted", color=discord.Color.red())
    embed.add_field(name="Moderator", value=str(moderator), inline=False)
    if duration_str:
        embed.add_field(name="Duration", value=duration_str, inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    return embed

def build_mute_log_embed(user: discord.User, moderator: Optional[discord.Member], duration_str: Optional[str], reason: str, unmute_at: Optional[str] = None):
    embed = discord.Embed(title="ðŸ”‡ RMute Applied", color=discord.Color.orange())
    embed.add_field(name="User", value=f"{user} ({getattr(user,'id', 'N/A')})", inline=False)
    embed.add_field(name="Moderator", value=str(moderator) if moderator else "System", inline=False)
    if duration_str:
        embed.add_field(name="Duration", value=duration_str, inline=True)
    if unmute_at:
        embed.add_field(name="Unmute at", value=unmute_at, inline=True)
    embed.add_field(name="Reason", value=reason or "No reason provided", inline=False)
    embed.timestamp = datetime.datetime.utcnow()
    return embed

# ------------ TIMETRACK LOOP ------------
@tasks.loop(seconds=PRESENCE_INTERVAL)
async def timetrack_loop():
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    now = datetime.datetime.utcnow()
    for member in guild.members:
        # only track members with RCACHE_ROLES set (as in spec)
        try:
            if not any(r.id in RCACHE_ROLES for r in member.roles):
                continue
        except:
            continue
        u = ensure_user(member.id)
        if member.status != discord.Status.offline:
            # became online
            if not u.get("online_start"):
                u["online_start"] = now.isoformat()
            # credit online seconds incrementally
            u["total_online_seconds"] = u.get("total_online_seconds", 0) + PRESENCE_INTERVAL
            today = now.strftime("%Y-%m-%d")
            u["daily_seconds"][today] = u["daily_seconds"].get(today, 0) + PRESENCE_INTERVAL
            u["last_online"] = now.isoformat()
            u["offline_timer"] = 0
        else:
            # offline
            if u.get("online_start"):
                try:
                    start = datetime.datetime.fromisoformat(u["online_start"])
                    delta = (now - start).total_seconds()
                    u["total_online_seconds"] = u.get("total_online_seconds", 0) + int(delta)
                except:
                    pass
                u["online_start"] = None
            # offline timer used to confirm offline transitions
            u["offline_timer"] = u.get("offline_timer", 0) + PRESENCE_INTERVAL
        # Save periodic changes
    save_data()

# ------------ RMUTE / RUNMUTE COMMANDS ------------
async def schedule_unmute(mute_id: str, user_id: int, seconds: int, moderator: Optional[discord.Member]):
    try:
        await asyncio.sleep(seconds)
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        member = guild.get_member(user_id)
        if not member:
            return
        role = discord.utils.get(guild.roles, id=MUTE_ROLE)
        if role and role in member.roles:
            try:
                await member.remove_roles(role, reason="Auto-unmute")
            except:
                pass
            # remove mute record
            if mute_id in bot_data.get("mutes", {}):
                bot_data["mutes"].pop(mute_id, None)
                save_data()
            ch = bot.get_channel(TRACKING_CHANNEL)
            if ch:
                embed = discord.Embed(title="âœ… Auto-Unmute", description=f"{member} was automatically unmuted.", color=discord.Color.green())
                embed.timestamp = datetime.datetime.utcnow()
                try:
                    await ch.send(embed=embed)
                except:
                    pass
    except Exception as e:
        print("schedule_unmute error:", e)
        traceback.print_exc()

@bot.command(name="rmute", help="Mute multiple users: !rmute @u1 @u2 10m reason")
@commands.has_permissions(manage_roles=True)
async def cmd_rmute(ctx: commands.Context, users: commands.Greedy[discord.Member], duration: str, *, reason: str = "No reason provided"):
    if not users:
        return await ctx.send("Please mention at least one user to mute.")
    seconds = parse_duration(duration) or 0
    role = discord.utils.get(ctx.guild.roles, id=MUTE_ROLE)
    if not role:
        return await ctx.send("Mute role not configured on this server.")
    for user in users:
        try:
            await user.add_roles(role, reason=f"rmute by {ctx.author}: {reason}")
            mute_id = f"rmute_{user.id}_{int(datetime.datetime.utcnow().timestamp())}"
            unmute_time = (datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)).isoformat() if seconds else None
            bot_data["mutes"][mute_id] = {
                "user": user.id, "moderator": ctx.author.id, "duration_seconds": seconds,
                "reason": reason, "start_time": datetime.datetime.utcnow().isoformat(),
                "unmute_utc": unmute_time, "auto": True
            }
            bot_data["rmute_usage"][str(ctx.author.id)] = bot_data.get("rmute_usage", {}).get(str(ctx.author.id), 0) + 1
            save_data()
            # DM user
            dm_embed = build_mute_dm_embed(ctx.author, duration, reason)
            await send_dm_safe(user, dm_embed)
            # log to tracking channel
            ch = bot.get_channel(TRACKING_CHANNEL)
            if ch:
                await ch.send(embed=build_mute_log_embed(user, ctx.author, duration, reason, unmute_time))
            # schedule unmute
            if seconds and seconds > 0:
                asyncio.create_task(schedule_unmute(mute_id, user.id, seconds, ctx.author))
        except Exception as e:
            await ctx.send(f"Failed to mute {user}: {e}")
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name="runmute", help="Mute a single user: !runmute @u 10m reason")
@commands.has_permissions(manage_roles=True)
async def cmd_runmute(ctx: commands.Context, user: discord.Member, duration: str, *, reason: str = "No reason provided"):
    await cmd_rmute(ctx, [user], duration, reason=reason)

@bot.command(name="rmlb", help="Top rmute command users")
async def cmd_rmlb(ctx: commands.Context):
    usage = bot_data.get("rmute_usage", {})
    sorted_usage = sorted(usage.items(), key=lambda kv: kv[1], reverse=True)[:10]
    embed = discord.Embed(title="ðŸ† RMute Leaderboard", color=discord.Color.gold())
    for uid, cnt in sorted_usage:
        user = ctx.guild.get_member(int(uid))
        name = user.display_name if user else f"User {uid}"
        embed.add_field(name=name, value=f"RMutes used: {cnt}", inline=False)
    await ctx.send(embed=embed)

# ------------ LEADERBOARDS: tlb & tdm ------------
@bot.command(name="tlb", help="Timetrack leaderboard for RCACHE_ROLES")
async def cmd_tlb(ctx: commands.Context):
    entries = []
    for uid, u in bot_data.get("users", {}).items():
        member = ctx.guild.get_member(int(uid))
        if member and any(r.id in RCACHE_ROLES for r in member.roles):
            entries.append((member.display_name, u.get("total_online_seconds", 0)))
    entries.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="ðŸ“Š Timetrack Leaderboard", color=discord.Color.blue())
    for i, (name, secs) in enumerate(entries[:10], 1):
        embed.add_field(name=f"{i}. {name}", value=format_seconds(secs), inline=False)
    await ctx.send(embed=embed)

@bot.command(name="tdm", help="Timetrack leaderboard for users without tracked roles")
async def cmd_tdm(ctx: commands.Context):
    entries = []
    for uid, u in bot_data.get("users", {}).items():
        member = ctx.guild.get_member(int(uid))
        if member and not any(r.id in RCACHE_ROLES for r in member.roles):
            entries.append((member.display_name, u.get("total_online_seconds", 0)))
    entries.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="ðŸ“Š DM Timetrack Leaderboard", color=discord.Color.purple())
    for i, (name, secs) in enumerate(entries[:10], 1):
        embed.add_field(name=f"{i}. {name}", value=format_seconds(secs), inline=False)
    await ctx.send(embed=embed)

# ------------ RCACHE: show deleted messages & searches ------------
@bot.command(name="rcache", help="View cached deleted messages or search: rcache user <@user> | date YYYY-MM-DD | search <keyword>")
async def cmd_rcache(ctx: commands.Context, mode: Optional[str] = None, *, query: Optional[str] = None):
    if not any(r.id in RCACHE_ROLES for r in ctx.author.roles):
        return await ctx.send("You don't have permission to use this command.")
    cached = bot_data.get("cached_messages", {})
    results = []
    if not mode:
        # show last 10 cached
        for mid, info in list(cached.items())[-10:]:
            results.append((mid, info))
    else:
        mode = mode.lower()
        if mode == "user" and query:
            uid = re.sub(r"[<@!>]", "", query)
            for mid, info in cached.items():
                if str(info.get("author_id")) == uid:
                    results.append((mid, info))
        elif mode == "date" and query:
            for mid, info in cached.items():
                if info.get("time", "").startswith(query):
                    results.append((mid, info))
        elif mode == "search" and query:
            q = query.lower()
            for mid, info in cached.items():
                if q in (info.get("content") or "").lower():
                    results.append((mid, info))
        else:
            return await ctx.send("Usage: `!rcache` or `!rcache user <@user>` or `!rcache date YYYY-MM-DD` or `!rcache search <keyword>`")
    if not results:
        return await ctx.send("No cached messages found.")
    embed = discord.Embed(title="ðŸ—‚ï¸ Cached Messages", color=discord.Color.teal())
    for mid, info in results[:25]:
        author = info.get("author") or "Unknown"
        deleter = info.get("deleter") or "Unknown"
        content = (info.get("content") or "")[:500]
        attachments = info.get("attachments") or []
        att_text = "\n".join(attachments) if attachments else "None"
        reply_info = ""
        if info.get("is_reply"):
            reply_info = f"\nReply to: {info.get('reply_to_author')} - {info.get('reply_to_content','')[:120]}"
        embed.add_field(name=f"Msg {mid} by {author}", value=f"Deleted by: {deleter}\nContent: {content}\nAttachments:\n{att_text}{reply_info}", inline=False)
    await ctx.send(embed=embed)

# ------------ LOGGING EVENTS: edits, deletions, channel/role/webhook updates ------------
@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.author and after.author.bot:
        return
    bot_data.setdefault("logs", []).append({
        "type": "edit",
        "author": after.author.id,
        "message_id": after.id,
        "before": (before.content or "")[:1900],
        "after": (after.content or "")[:1900],
        "time": datetime.datetime.utcnow().isoformat()
    })
    save_data()

@bot.event
async def on_message_delete(message: discord.Message):
    if message.author and message.author.bot:
        return
    attachments = [a.url for a in message.attachments] if message.attachments else []
    bot_data.setdefault("cached_messages", {})[str(message.id)] = {
        "author": str(message.author) if message.author else None,
        "author_id": message.author.id if message.author else None,
        "content": (message.content or "")[:1900],
        "attachments": attachments,
        "time": datetime.datetime.utcnow().isoformat(),
        "deleter": None,
        "is_reply": bool(message.reference),
        "reply_to_author": str(message.reference.resolved.author) if message.reference and message.reference.resolved and getattr(message.reference.resolved, 'author', None) else None,
        "reply_to_content": (message.reference.resolved.content if message.reference and getattr(message.reference.resolved, 'content', None) else None)
    }
    bot_data.setdefault("logs", []).append({
        "type": "delete",
        "author": message.author.id if message.author else None,
        "message_id": message.id,
        "content": (message.content or "")[:1900],
        "attachments": attachments,
        "time": datetime.datetime.utcnow().isoformat()
    })
    save_data()

@bot.event
async def on_bulk_message_delete(messages: List[discord.Message]):
    preview = []
    for m in messages[:15]:
        preview.append(f"{(m.author.display_name if m.author else 'Unknown')}: {(m.content or '')[:120]}")
        bot_data.setdefault("cached_messages", {})[str(m.id)] = {
            "author": str(m.author) if m.author else None,
            "author_id": m.author.id if m.author else None,
            "content": (m.content or "")[:1900],
            "attachments": [a.url for a in m.attachments] if m.attachments else [],
            "time": datetime.datetime.utcnow().isoformat(),
            "deleter": None,
            "bulk_deleted": True
        }
        bot_data.setdefault("logs", []).append({
            "type": "bulk_delete",
            "message_id": m.id,
            "author": m.author.id if m.author else None,
            "content": (m.content or "")[:1900],
            "time": datetime.datetime.utcnow().isoformat()
        })
    save_data()
    # attempt to attribute via audit logs
    guild = messages[0].guild if messages else None
    actor = None
    if guild:
        try:
            async for entry in guild.audit_logs(limit=8, action=discord.AuditLogAction.message_bulk_delete):
                actor = entry.user
                break
        except:
            pass
    ch = bot.get_channel(TRACKING_CHANNEL)
    when = datetime.datetime.utcnow().isoformat()
    emb = discord.Embed(title="ðŸ—‘ï¸ Purge Detected", description=f"Messages purged: {len(messages)}", color=discord.Color.dark_red())
    if actor:
        emb.add_field(name="Possible actor", value=f"{actor} ({actor.id})", inline=False)
    if preview:
        emb.add_field(name="Preview", value="\n".join(preview[:10]), inline=False)
    if ch:
        try:
            asyncio.create_task(ch.send(embed=emb))
        except:
            pass

@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    # try to attribute via audit logs
    guild = after.guild
    actor = None
    try:
        async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.role_update):
            if getattr(entry.target, "id", None) == after.id:
                actor = entry.user
                break
    except:
        pass
    bot_data.setdefault("logs", []).append({
        "type": "role_update",
        "role_id": after.id,
        "before": {"name": before.name, "perms": str(before.permissions)},
        "after": {"name": after.name, "perms": str(after.permissions)},
        "editor": actor.id if actor else None,
        "time": datetime.datetime.utcnow().isoformat()
    })
    save_data()

@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    guild = after.guild
    actor = None
    try:
        async for entry in guild.audit_logs(limit=6, action=discord.AuditLogAction.channel_update):
            if getattr(entry.target, "id", None) == after.id:
                actor = entry.user
                break
    except:
        pass
    bot_data.setdefault("logs", []).append({
        "type": "channel_update",
        "channel_id": after.id,
        "before": {"name": before.name},
        "after": {"name": after.name},
        "editor": actor.id if actor else None,
        "time": datetime.datetime.utcnow().isoformat()
    })
    save_data()

@bot.event
async def on_webhooks_update(channel: discord.abc.GuildChannel):
    bot_data.setdefault("logs", []).append({
        "type": "webhooks_update",
        "channel": channel.id,
        "time": datetime.datetime.utcnow().isoformat()
    })
    save_data()

# ------------ MEMBER UPDATE: role adds/removes (attribution) ------------
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    try:
        before_roles = {r.id for r in before.roles}
        after_roles = {r.id for r in after.roles}
        added = after_roles - before_roles
        removed = before_roles - after_roles
        guild = after.guild
        actor = None
        if added or removed:
            try:
                async for entry in guild.audit_logs(limit=20, action=discord.AuditLogAction.member_role_update):
                    if getattr(entry.target, "id", None) == after.id:
                        actor = entry.user
                        break
            except:
                pass
        # log additions/removals
        for rid in added:
            bot_data.setdefault("logs", []).append({
                "type": "member_role_add",
                "member": after.id,
                "role_added": rid,
                "by": actor.id if actor else None,
                "time": datetime.datetime.utcnow().isoformat()
            })
            # if mute role added externally, DM & log
            if rid == MUTE_ROLE:
                try:
                    ch = bot.get_channel(TRACKING_CHANNEL)
                    if ch:
                        emb = discord.Embed(title="ðŸ”‡ Mute Detected (role add)", description=f"{after} was muted via role add", color=discord.Color.orange())
                        emb.add_field(name="By", value=str(actor) if actor else "Unknown", inline=False)
                        await ch.send(embed=emb)
                    # DM user unless opted out
                    dm = discord.Embed(title="ðŸ”‡ You were muted", color=discord.Color.red())
                    dm.add_field(name="Moderator", value=str(actor) if actor else "Unknown", inline=False)
                    try:
                        await send_dm_safe(after, dm)
                    except:
                        pass
                except:
                    pass
        for rid in removed:
            bot_data.setdefault("logs", []).append({
                "type": "member_role_remove",
                "member": after.id,
                "role_removed": rid,
                "by": actor.id if actor else None,
                "time": datetime.datetime.utcnow().isoformat()
            })
            if rid == MUTE_ROLE:
                ch = bot.get_channel(TRACKING_CHANNEL)
                if ch:
                    emb = discord.Embed(title="ðŸ”ˆ Unmute Detected (role remove)", description=f"{after} was unmuted via role remove", color=discord.Color.green())
                    emb.add_field(name="By", value=str(actor) if actor else "Unknown", inline=False)
                    try:
                        await ch.send(embed=emb)
                    except:
                        pass
    except Exception as e:
        print("on_member_update error:", e)
        traceback.print_exc()
    save_data()

# ------------ STAFF PINGS (rping/hsping) ------------
async def ping_staff(ctx: commands.Context, role_id: int):
    role = discord.utils.get(ctx.guild.roles, id=role_id)
    if not role:
        return await ctx.send("Role not found")
    content = role.mention
    if ctx.message.reference and ctx.message.reference.resolved:
        ref = ctx.message.reference.resolved
        content += f"\n> Original by {ref.author}: {ref.content[:200]}"
    await ctx.send(content)
    try:
        await ctx.message.delete()
    except:
        pass

@bot.command(name="rping", help="Ping staff role")
async def cmd_rping(ctx: commands.Context):
    await ping_staff(ctx, STAFF_PING_ROLE)

@bot.command(name="hsping", help="Ping higher staff role")
async def cmd_hsping(ctx: commands.Context):
    await ping_staff(ctx, HIGHER_STAFF_PING_ROLE)

# ------------ RDM: opt-out of bot DMs ------------
@bot.command(name="rdm", help="Opt-out from bot DMs")
async def cmd_rdm(ctx: commands.Context):
    uid = str(ctx.author.id)
    if uid in bot_data.get("rdm_users", []):
        return await ctx.send("You have already opted out from DMs.")
    bot_data.setdefault("rdm_users", []).append(uid)
    save_data()
    await ctx.send("âœ… You have opted out from DMs from this bot.")

# ------------ PURGE COMMAND WITH LOGGING ------------
@bot.command(name="purge", help="Delete messages and log them: !purge <limit>")
@commands.has_permissions(manage_messages=True)
async def cmd_purge(ctx: commands.Context, limit: int):
    if limit <= 0 or limit > 1000:
        return await ctx.send("Limit must be between 1 and 1000.")
    messages = await ctx.channel.purge(limit=limit, bulk=True)
    # Note: bulk deletions may not give message objects for all; we rely on audit logs
    for m in messages:
        try:
            bot_data.setdefault("cached_messages", {})[str(m.id)] = {
                "author": str(m.author) if m.author else None,
                "author_id": m.author.id if m.author else None,
                "content": (m.content or "")[:1900],
                "attachments": [a.url for a in m.attachments] if m.attachments else [],
                "time": datetime.datetime.utcnow().isoformat(),
                "deleter": str(ctx.author)
            }
            bot_data.setdefault("logs", []).append({
                "type": "purge",
                "actor": ctx.author.id,
                "message_id": m.id,
                "content": (m.content or "")[:1900],
                "time": datetime.datetime.utcnow().isoformat()
            })
        except Exception:
            pass
    save_data()
    ch = bot.get_channel(TRACKING_CHANNEL)
    if ch:
        emb = discord.Embed(title="ðŸ—‘ï¸ Messages Purged", description=f"{len(messages)} messages purged in {ctx.channel.mention} by {ctx.author}", color=discord.Color.dark_red())
        emb.timestamp = datetime.datetime.utcnow()
        try:
            await ch.send(embed=emb)
        except:
            pass
    await ctx.send(f"Purged {len(messages)} messages.", delete_after=6)

# ------------ HELP COMMAND ------------
@bot.command(name="rhelp", help="Show available commands")
async def cmd_rhelp(ctx: commands.Context):
    embed = discord.Embed(title="Commands", color=discord.Color.green())
    embed.add_field(name="!timetrack [user]", value="Show timetrack info", inline=False)
    embed.add_field(name="!rmute [users] [duration] [reason]", value="Mute users", inline=False)
    embed.add_field(name="!runmute [user] [duration] [reason]", value="Single mute", inline=False)
    embed.add_field(name="!rmlb", value="RMute leaderboard", inline=False)
    embed.add_field(name="!rcache", value="Cached deletes: rcache user/date/search", inline=False)
    embed.add_field(name="!tlb", value="Timetrack leaderboard", inline=False)
    embed.add_field(name="!tdm", value="Timetrack for non-tracked roles", inline=False)
    embed.add_field(name="!rping / !hsping", value="Ping staff roles", inline=False)
    embed.add_field(name="!rdm", value="Opt-out from bot DMs", inline=False)
    await ctx.send(embed=embed)

# ------------ TIMETRACK / TIMETRACK COMMAND ------------
@bot.command(name="timetrack", help="Show timetrack data for a user")
async def cmd_timetrack(ctx: commands.Context, member: Optional[discord.Member] = None):
    member = member or ctx.author
    uid = str(member.id)
    u = bot_data.get("users", {}).get(uid)
    if not u:
        return await ctx.send("No timetrack data found for user.")
    embed = discord.Embed(title=f"Timetrack â€” {member}", color=discord.Color.blue())
    embed.add_field(name="Last Online", value=u.get("last_online") or u.get("last_seen") or "N/A", inline=False)
    total = u.get("total_online_seconds", 0)
    embed.add_field(name="Total Online (all time)", value=format_seconds(total), inline=False)
    avg = 0
    daily = u.get("daily_seconds", {})
    if daily:
        avg = int(sum(daily.values()) / max(len(daily),1))
    embed.add_field(name="Average Daily", value=format_seconds(avg), inline=False)
    await ctx.send(embed=embed)

# ------------ AUDIT RECONCILIATION ON START ------------
async def reconcile_audit_on_start():
    data = bot_data
    guild = bot.get_guild(GUILD_ID)
    ch = bot.get_channel(TRACKING_CHANNEL)
    if not guild or not ch:
        return
    last_check = data.get("last_audit_check")
    now = datetime.datetime.utcnow()
    lookback_since = now - datetime.timedelta(seconds=AUDIT_LOOKBACK_SECONDS)
    if last_check:
        try:
            last_dt = datetime.datetime.fromisoformat(last_check)
        except:
            last_dt = lookback_since
    else:
        last_dt = lookback_since
    actions = [
        discord.AuditLogAction.role_create,
        discord.AuditLogAction.role_delete,
        discord.AuditLogAction.role_update,
        discord.AuditLogAction.channel_create,
        discord.AuditLogAction.channel_delete,
        discord.AuditLogAction.channel_update,
        discord.AuditLogAction.message_bulk_delete,
        discord.AuditLogAction.member_role_update
    ]
    for action in actions:
        try:
            async for entry in guild.audit_logs(limit=50, action=action):
                if entry.created_at.replace(tzinfo=None) < last_dt:
                    break
                # Post summaries for missed events
                if entry.action == discord.AuditLogAction.message_bulk_delete:
                    emb = discord.Embed(title="ðŸ—‘ï¸ Missed Bulk Delete", description=f"Actor: {entry.user}", color=discord.Color.dark_red())
                    emb.timestamp = entry.created_at
                    try:
                        await ch.send(embed=emb)
                    except:
                        pass
                else:
                    emb = discord.Embed(title="Audit Log Catchup", description=f"Action: {entry.action}\nActor: {entry.user}", color=discord.Color.orange())
                    emb.timestamp = entry.created_at
                    try:
                        await ch.send(embed=emb)
                    except:
                        pass
        except Exception as e:
            print("audit scan error:", e)
    bot_data["last_audit_check"] = datetime.datetime.utcnow().isoformat()
    save_data()

# ------------ AUTO SAVE TASK ------------
@tasks.loop(seconds=AUTO_SAVE_INTERVAL)
async def autosave_task():
    try:
        save_data()
    except Exception as e:
        print("autosave error:", e)

# ------------ DAILY MAINTENANCE TASK ------------
@tasks.loop(hours=24)
async def daily_cleanup_task():
    try:
        # prune daily entries older than 120 days, and messages older than 120 days
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=120)
        for uid, u in list(bot_data.get("users", {}).items()):
            ds = u.get("daily_seconds", {})
            for k in list(ds.keys()):
                try:
                    ddt = datetime.datetime.strptime(k, "%Y-%m-%d")
                    if ddt < cutoff:
                        ds.pop(k, None)
                except:
                    pass
        # prune cached messages
        for mid, m in list(bot_data.get("cached_messages", {}).items()):
            try:
                t = datetime.datetime.fromisoformat(m.get("time"))
                if t < cutoff:
                    bot_data["cached_messages"].pop(mid, None)
            except:
                pass
        save_data()
    except Exception as e:
        print("daily cleanup error:", e)
        traceback.print_exc()

# ------------ RDUMP (debug) ------------
@bot.command(name="rdump", help="Dump bot data to file (admin)")
@commands.has_permissions(administrator=True)
async def cmd_rdump_file(ctx: commands.Context):
    path = "rdump.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bot_data, f, indent=2, default=str)
    await ctx.send("ðŸ“¦ Data dump:", file=discord.File(path))
    try:
        os.remove(path)
    except:
        pass

# ------------ STARTUP HOOKS ------------
@bot.event
async def on_ready():
    safe_print(f"Logged in as {bot.user} ({bot.user.id})")
    # start background tasks
    if not timetrack_loop.is_running():
        timetrack_loop.start()
    if not autosave_task.is_running():
        autosave_task.start()
    if not daily_cleanup_task.is_running():
        daily_cleanup_task.start()
    # reconcile audit logs
    try:
        await reconcile_audit_on_start()
    except Exception as e:
        print("reconcile error:", e)
    save_data()

# ------------ FLASK KEEP-ALIVE -------------
flask_app = Flask("mega_keepalive")

@flask_app.route('/')
def status():
    return "Mega bot alive"

def run_flask_app():
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# ------------ MAIN RUN -------------
def start_bot():
    loop = asyncio.get_event_loop()
    # run flask in executor
    loop.run_in_executor(None, run_flask_app)
    bot.run(TOKEN)

if __name__ == "__main__":
    # ensure data file exists
    if not os.path.exists(DATA_FILE):
        save_data(init_data())
    # start bot
    start_bot()
