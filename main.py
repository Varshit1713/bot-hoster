# main.py
# Merged: timetrack + advanced audit logging + rmute/runmute + invite tracking + purge html logs + reaction-role logging
# NOTES:
#  - Run in an environment with discord.py 2.x
#  - Bot needs Manage Roles, Moderate Members, View Audit Log, Manage Messages, Read Message History, Embed Links, Attach Files, Send Messages
#  - Set environment variable DISCORD_TOKEN to your bot token or replace below

import os
import io
import json
import datetime
import html
import tempfile
from typing import Optional, Dict, Any, List, Tuple
from flask import Flask
import random
import asyncio
from zoneinfo import ZoneInfo
import threading
import aiohttp

import discord
from discord.ext import commands, tasks
from discord import AuditLogAction, app_commands

# ------------------ FLASK ------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))  # Render sets PORT automatically
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask).start()

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("❌ DISCORD_TOKEN environment variable not set")

# IDs you provided
GUILD_ID = 1403359962369097739
MUTED_ROLE_ID = 1410423854563721287
ACTION_LOG_CHANNEL_ID = 1403422664521023648   # mod action / mute logs
AUDIT_LOG_CHANNEL_ID = 1410458084874260592    # general audit logs / purge files
DATA_FILE = "activity_logs.json"
INVITE_FILE = "invite_map.json"

# Roles that trigger active/notify logs (list you provided)
ACTIVE_LOG_ROLE_IDS = {
    1410422029236047975,
    1410419924173848626,
    1410420126003630122,
    1410423594579918860,
    1410421466666631279,
    1410421647265108038,
    1410419345234067568
}

# Timezones to show
TIMEZONES = {
    "UTC": ZoneInfo("UTC"),
    "EST": ZoneInfo("America/New_York"),
    "PST": ZoneInfo("America/Los_Angeles"),
    "IST": ZoneInfo("Asia/Kolkata"),
}

# Inactivity delay range (seconds)
INACTIVE_MIN = 50
INACTIVE_MAX = 60

# Save intervals
SAVE_INTERVAL = 10  # seconds for saving activity file

# ------------------ BOT & INTENTS ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
intents.guilds = True
intents.reactions = True
intents.messages = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ STORAGE ------------------
activity_logs: Dict[str, Dict[str, Any]] = {}
invite_snapshot: Dict[int, Dict[str, int]] = {}  # guild_id -> {invite.code: uses}
invite_owner_map: Dict[str, int] = {}  # user_id -> inviter id
message_cache: Dict[int, Dict[str, Any]] = {}  # message.id -> cached info to help on delete
rping_override_off: set = set()  # users who disabled DMs from the bot? (for rdm feature)
rdm_enabled = True  # whether DMs for critical events are enabled by default

# ------------------ UTIL ------------------
def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def fmt_dt(dt: datetime.datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %I:%M:%S %p")

def fmt_duration(seconds: float) -> str:
    s = int(max(0, round(seconds)))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)

async def safe_send(channel: discord.abc.Messageable, *args, **kwargs):
    try:
        return await channel.send(*args, **kwargs)
    except Exception:
        return None

async def write_json_async(path: str, data):
    loop = asyncio.get_running_loop()
    def _write():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, default=str)
    await loop.run_in_executor(None, _write)

# ------------------ PERSISTENCE ------------------
def load_activity():
    global activity_logs
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                activity_logs = json.load(f)
        except Exception:
            activity_logs = {}
    else:
        activity_logs = {}

def load_invites():
    global invite_snapshot, invite_owner_map
    if os.path.exists(INVITE_FILE):
        try:
            with open(INVITE_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                invite_snapshot = {int(k): v for k,v in d.get("snapshot", {}).items()}
                invite_owner_map = d.get("owner_map", {})
        except Exception:
            invite_snapshot = {}
            invite_owner_map = {}
    else:
        invite_snapshot = {}
        invite_owner_map = {}

async def save_all():
    await write_json_async(DATA_FILE, activity_logs)
    # invite snapshot + owner map
    await write_json_async(INVITE_FILE, {"snapshot": invite_snapshot, "owner_map": invite_owner_map})

# ------------------ TIMETRACK HELPERS ------------------
def ensure_user_log(uid: int):
    key = str(uid)
    if key not in activity_logs:
        activity_logs[key] = {
            "online_seconds": 0,
            "offline_seconds": 0,
            "offline_start": None,
            "offline_delay": None,
            "last_message": None,
            "daily_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_daily_reset": None,
            "last_weekly_reset": None,
            "last_monthly_reset": None,
            "mute_expires": None,
            "mute_reason": None,
            "mute_responsible": None,
            "inactive": False,
            "mute_count": 0,
            "rping_on": False,
            "invited_by": None
        }
    return activity_logs[key]

# ------------------ AUDIT HELPERS ------------------
async def find_audit_executor(guild: discord.Guild, action: AuditLogAction, target_id: Optional[int]=None, lookback: float=5.0) -> Optional[discord.Member]:
    """
    Fetch audit logs to find the member who performed `action` on `target_id`.
    Waits briefly to allow Discord to write the audit log.
    """
    await asyncio.sleep(1.0)  # short initial sleep to let the audit log register
    now = now_utc()
    end = now - datetime.timedelta(seconds=lookback + 5)
    try:
        async for entry in guild.audit_logs(limit=20, action=action):
            # entry.target can be user / role / channel / invite etc.
            if target_id:
                tid = None
                try:
                    tid = int(getattr(entry.target, "id", entry.target))
                except Exception:
                    tid = None
                if tid != target_id and str(entry.target) != str(target_id):
                    continue
            # return executor as Member if possible
            executor = entry.user
            if isinstance(executor, discord.Member):
                return executor
            else:
                # try to resolve member
                m = guild.get_member(executor.id) if executor else None
                return m
    except Exception:
        return None
    return None

# ------------------ INVITE TRACKING ------------------
async def snapshot_invites_for_guild(guild: discord.Guild):
    try:
        invites = await guild.invites()
        invite_snapshot[guild.id] = {inv.code: inv.uses for inv in invites}
    except Exception:
        invite_snapshot[guild.id] = {}

# ------------------ STARTUP ------------------
@bot.event
async def on_ready():
    load_activity()
    load_invites()
    # snapshot invites for all guilds we are in
    for g in bot.guilds:
        try:
            await snapshot_invites_for_guild(g)
        except Exception:
            pass
    if not inactivity_poller.is_running():
        inactivity_poller.start()
    if not auto_unmute_loop.is_running():
        auto_unmute_loop.start()
    if not save_loop.is_running():
        save_loop.start()
    if not prune_cache_loop.is_running():
        prune_cache_loop.start()
    print(f"✅ Bot ready: {bot.user} (guilds: {len(bot.guilds)})")

# ------------------ MESSAGE CACHING (for deleted messages) ------------------
@bot.event
async def on_message(message: discord.Message):
    # ignore bot messages (but still track mutes etc.)
    if message.author.bot:
        # some bots may issue moderation commands — still track invites & message cache for deletion by bots
        await bot.process_commands(message)
        return

    # cache basic info to help when message deleted (in case it's not in cache)
    message_cache[message.id] = {
        "author_id": message.author.id if message.author else None,
        "author_name": str(message.author) if message.author else None,
        "content": message.content,
        "attachments": [a.url for a in message.attachments],
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "channel_id": message.channel.id if message.channel else None,
        "is_reply": bool(message.reference and message.reference.message_id),
        "reply_to_id": message.reference.message_id if message.reference else None
    }

    # timetrack: update user log
    uid = message.author.id
    now = now_utc()
    log = ensure_user_log(uid)
    log["last_message"] = now.isoformat()
    log["offline_seconds"] = 0
    log["offline_start"] = None
    if not log.get("offline_delay"):
        log["offline_delay"] = int(__import__("random").randint(INACTIVE_MIN, INACTIVE_MAX))
    # daily/weekly/monthly resets
    today = now.date().isoformat()
    week = now.isocalendar()[1]
    month = now.month
    if log.get("last_daily_reset") != today:
        log["daily_seconds"] = 0
        log["last_daily_reset"] = today
    if log.get("last_weekly_reset") != str(week):
        log["weekly_seconds"] = 0
        log["last_weekly_reset"] = str(week)
    if log.get("last_monthly_reset") != str(month):
        log["monthly_seconds"] = 0
        log["last_monthly_reset"] = str(month)
    # add 1 second for this message (counts as activity)
    log["daily_seconds"] += 1
    log["weekly_seconds"] += 1
    log["monthly_seconds"] += 1
    # if previously inactive, log comeback for certain roles
    if log.get("inactive", False):
        guild = message.guild
        member = message.author
        try:
            # check if member has any of active roles
            if any(guild.get_role(rid) in member.roles for rid in ACTIVE_LOG_ROLE_IDS if guild.get_role(rid)):
                lc = guild.get_channel(ACTION_LOG_CHANNEL_ID)
                if lc:
                    ping = member.mention if log.get("rping_on", False) else member.display_name
                    await safe_send(lc, f"🟢 {ping} has come back online (sent a message).")
        except Exception:
            pass
    log["inactive"] = False
    await save_all()
    await bot.process_commands(message)

# ------------------ MESSAGE DELETE / EDIT HANDLERS ------------------
@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    # log before and after
    if before.author and before.author.bot:
        return
    guild = before.guild
    ch = before.channel
    embed = discord.Embed(title="✏️ Message Edited", color=0xf39c12, timestamp=now_utc())
    embed.add_field(name="Author", value=before.author.mention if before.author else "Unknown", inline=True)
    embed.add_field(name="Channel", value=ch.mention, inline=True)
    embed.add_field(name="Sent at", value=fmt_dt(before.created_at or now_utc(), TIMEZONES["EST"]), inline=False)
    embed.add_field(name="Before", value=(before.content[:1024] or "(empty)"), inline=False)
    embed.add_field(name="After", value=(after.content[:1024] or "(empty)"), inline=False)
    # attempt to find editor via audit logs (rare)
    exec_member = None
    try:
        exec_member = await find_audit_executor(guild, AuditLogAction.message_update, target_id=before.id)
    except Exception:
        exec_member = None
    embed.set_footer(text=f"Edited by: {exec_member.mention if exec_member else before.author.display_name}")
    # send to audit log
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

@bot.event
async def on_message_delete(message: discord.Message):
    # log deletes with timestamps, reply info, attachments
    guild = message.guild
    ch = message.channel
    deleted_at = now_utc()
    cached = message_cache.pop(message.id, None)
    author = message.author or (discord.Object(id=cached["author_id"]) if cached else None)
    sent_at = message.created_at or (datetime.datetime.fromisoformat(cached["created_at"]) if cached and cached.get("created_at") else None)

    embed = discord.Embed(title="🗑️ Message Deleted", color=0xff6b6b, timestamp=deleted_at)
    embed.set_thumbnail(url=(getattr(author, "display_avatar", None).url if hasattr(author, "display_avatar") else None))
    embed.add_field(name="Author", value=(author.mention if hasattr(author, "mention") else str(cached.get("author_name", "Unknown"))), inline=True)
    embed.add_field(name="Channel", value=ch.mention if ch else "Unknown", inline=True)
    if sent_at:
        embed.add_field(name="Sent at (EST)", value=fmt_dt(sent_at, TIMEZONES["EST"]), inline=False)
    embed.add_field(name="Deleted at (EST)", value=fmt_dt(deleted_at, TIMEZONES["EST"]), inline=False)

    # reply info
    if message.reference and message.reference.message_id:
        try:
            ref = message.reference.resolved
            if ref:
                embed.add_field(name="Was a reply to", value=f"{ref.author.mention} (ID: {ref.id})", inline=False)
            else:
                embed.add_field(name="Was a reply to", value=f"Message ID: {message.reference.message_id}", inline=False)
        except Exception:
            embed.add_field(name="Was a reply to", value=f"Message ID: {message.reference.message_id}", inline=False)

    content = message.content or (cached.get("content") if cached else "(empty)")
    embed.add_field(name="Content", value=(content[:1024] or "(empty)"), inline=False)

    # attachments
    atts = [a.url for a in message.attachments] or (cached.get("attachments") if cached else [])
    if atts:
        # show first attachment as preview (image/video)
        first = atts[0]
        embed.add_field(name="Attachments", value="\n".join(atts[:5]), inline=False)
        # attempt to display image preview for first image
        if any(first.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
            embed.set_image(url=first)
        else:
            # show as link
            pass

    # who deleted? check audit log
    deleted_by = None
    try:
        deleted_by = await find_audit_executor(guild, AuditLogAction.message_delete, target_id=(author.id if hasattr(author, "id") else None))
    except Exception:
        deleted_by = None
    embed.add_field(name="Deleted by", value=(deleted_by.mention if deleted_by else "Unknown"), inline=True)

    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

# ------------------ BULK DELETE (PURGE) HANDLER ------------------
@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    # payload contains message_ids and channel_id
    gid = payload.guild_id
    ch = bot.get_channel(payload.channel_id) or (bot.get_guild(gid).get_channel(payload.channel_id) if bot.get_guild(gid) else None)
    guild = bot.get_guild(gid)
    if not guild or not ch:
        return
    message_ids = payload.message_ids
    deleted_at = now_utc()

    # collect messages from cache to write HTML
    msgs = []
    for mid in message_ids:
        cached = message_cache.pop(mid, None)
        if not cached:
            continue
        msgs.append({
            "id": mid,
            "author_name": cached.get("author_name"),
            "author_id": cached.get("author_id"),
            "content": cached.get("content"),
            "attachments": cached.get("attachments", []),
            "created_at": cached.get("created_at")
        })

    # if nothing cached, still log the purge
    preview_lines = []
    for m in msgs[:5]:
        preview_lines.append(f"{m['author_name']} ({m['created_at']}): {m['content'][:200].replace('\\n',' ')}")

    # create HTML file
    html_parts = []
    html_parts.append("<html><meta charset='utf-8'><body>")
    html_parts.append(f"<h2>PURGE LOG - {html.escape(guild.name)} / #{html.escape(ch.name)}</h2>")
    html_parts.append(f"<p>Messages deleted: {len(msgs)} — Logged at {fmt_dt(deleted_at, TIMEZONES['EST'])}</p>")
    for m in msgs:
        html_parts.append("<hr>")
        html_parts.append(f"<h3>{html.escape(m['author_name'])} — {m['created_at']}</h3>")
        html_parts.append(f"<p>{html.escape(m['content'] or '')}</p>")
        if m.get("attachments"):
            for a in m["attachments"]:
                # show link + embed preview if image
                html_parts.append(f"<p><a href='{html.escape(a)}'>{html.escape(a)}</a></p>")
                if any(a.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    html_parts.append(f"<img src='{html.escape(a)}' style='max-width:600px;'><br>")
    html_parts.append("</body></html>")
    html_content = "\n".join(html_parts)

    # save to temp file and upload to audit channel
    fname = f"purge_{guild.id}_{ch.id}_{int(deleted_at.timestamp())}.html"
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".html", encoding="utf-8") as tmp:
        tmp.write(html_content)
        tmp.flush()
        tmp_name = tmp.name

    embed = discord.Embed(title="🗑️ Bulk Delete (Purge)", color=0xff4757, timestamp=deleted_at)
    embed.add_field(name="Channel", value=ch.mention, inline=True)
    embed.add_field(name="Messages Deleted", value=str(len(msgs)), inline=True)
    if preview_lines:
        embed.add_field(name="Preview (first 5)", value="\n".join(preview_lines[:5]), inline=False)
    # who did the purge? check audit logs for message_bulk_delete
    ex = None
    try:
        ex = await find_audit_executor(guild, AuditLogAction.message_bulk_delete, target_id=ch.id)
    except Exception:
        ex = None
    embed.add_field(name="Deleted by", value=(ex.mention if ex else "Unknown"), inline=True)

    audit_ch = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if audit_ch:
        try:
            # upload html file as attachment
            await audit_ch.send(embed=embed, file=discord.File(tmp_name, filename=fname))
        except Exception:
            await audit_ch.send(embed=embed)
    # clean up tmp file
    try:
        os.remove(tmp_name)
    except Exception:
        pass

# ------------------ ROLE & CHANNEL EVENT LOGS (with diffs + audit) ------------------
def role_permission_diff(before: discord.Role, after: discord.Role) -> List[str]:
    diffs = []
    for perm_name, _ in before.permissions:
        b = getattr(before.permissions, perm_name, None)
        a = getattr(after.permissions, perm_name, None)
        if b != a:
            diffs.append(f"{perm_name}: {b} -> {a}")
    return diffs

@bot.event
async def on_guild_role_create(role: discord.Role):
    guild = role.guild
    embed = discord.Embed(title="➕ Role Created", color=0x2ecc71, timestamp=now_utc())
    embed.add_field(name="Role", value=role.name, inline=True)
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    # find who created it
    who = None
    try:
        who = await find_audit_executor(guild, AuditLogAction.role_create, target_id=role.id)
    except Exception:
        who = None
    embed.add_field(name="Created by", value=(who.mention if who else "Unknown"), inline=True)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

@bot.event
async def on_guild_role_delete(role: discord.Role):
    guild = role.guild
    embed = discord.Embed(title="➖ Role Deleted", color=0xe74c3c, timestamp=now_utc())
    embed.add_field(name="Role (deleted)", value=role.name, inline=True)
    who = None
    try:
        who = await find_audit_executor(guild, AuditLogAction.role_delete, target_id=role.id)
    except Exception:
        who = None
    embed.add_field(name="Deleted by", value=(who.mention if who else "Unknown"), inline=True)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    guild = after.guild
    embed = discord.Embed(title="⚙️ Role Updated", color=0xf1c40f, timestamp=now_utc())
    embed.add_field(name="Role", value=after.name, inline=True)
    changes = []
    if before.name != after.name:
        changes.append(f"Name: `{before.name}` → `{after.name}`")
    perm_diffs = role_permission_diff(before, after)
    if perm_diffs:
        # limit to 12 lines
        changes.append("Permissions changed:\n" + "\n".join(perm_diffs[:12]))
    who = None
    try:
        who = await find_audit_executor(guild, AuditLogAction.role_update, target_id=after.id)
    except Exception:
        who = None
    embed.add_field(name="Changed by", value=(who.mention if who else "Unknown"), inline=True)
    if changes:
        embed.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

@bot.event
async def on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel):
    guild = after.guild
    embed = discord.Embed(title="🛠️ Channel Updated", color=0xf39c12, timestamp=now_utc())
    embed.add_field(name="Channel", value=after.mention if hasattr(after, "mention") else str(after), inline=True)
    changes = []
    if getattr(before, "name", None) != getattr(after, "name", None):
        changes.append(f"Name: `{before.name}` → `{after.name}`")
    # permissions diffs for overwrites (simplified)
    try:
        before_ows = {k.id: k for k in before.overwrites}
        after_ows = {k.id: k for k in after.overwrites}
    except Exception:
        before_ows, after_ows = {}, {}
    # Find simple diffs
    if changes:
        embed.add_field(name="Changes", value="\n".join(changes)[:1024], inline=False)
    who = None
    try:
        who = await find_audit_executor(guild, AuditLogAction.channel_update, target_id=after.id)
    except Exception:
        who = None
    embed.add_field(name="Changed by", value=(who.mention if who else "Unknown"), inline=True)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

# ------------------ MEMBER ROLE ADD/REMOVE LOGS ------------------
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles == after.roles:
        # possibly nickname change or similar - handle nickname
        if before.nick != after.nick:
            guild = after.guild
            embed = discord.Embed(title="✏️ Nickname Changed", color=0x9b59b6, timestamp=now_utc())
            embed.add_field(name="User", value=after.mention, inline=True)
            embed.add_field(name="Before", value=before.nick or "(none)", inline=True)
            embed.add_field(name="After", value=after.nick or "(none)", inline=True)
            lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
            if lc:
                await safe_send(lc, embed=embed)
        return

    # role differences
    added = [r for r in after.roles if r not in before.roles]
    removed = [r for r in before.roles if r not in after.roles]
    guild = after.guild
    for r in added:
        embed = discord.Embed(title="➕ Role Added to Member", color=0x2ecc71, timestamp=now_utc())
        embed.add_field(name="User", value=after.mention, inline=True)
        embed.add_field(name="Role Added", value=r.name, inline=True)
        # who added it? audit log: member role update
        who = None
        try:
            who = await find_audit_executor(guild, AuditLogAction.member_role_update, target_id=after.id)
        except Exception:
            who = None
        embed.add_field(name="Added by", value=(who.mention if who else "Unknown"), inline=True)
        lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
        if lc:
            await safe_send(lc, embed=embed)

    for r in removed:
        embed = discord.Embed(title="➖ Role Removed from Member", color=0xe74c3c, timestamp=now_utc())
        embed.add_field(name="User", value=after.mention, inline=True)
        embed.add_field(name="Role Removed", value=r.name, inline=True)
        who = None
        try:
            who = await find_audit_executor(guild, AuditLogAction.member_role_update, target_id=after.id)
        except Exception:
            who = None
        embed.add_field(name="Removed by", value=(who.mention if who else "Unknown"), inline=True)
        lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
        if lc:
            await safe_send(lc, embed=embed)

# ------------------ MUTE / UNMUTE (rmute & runmute) ------------------
@bot.command(name="rmute")
@commands.has_permissions(moderate_members=True)
async def cmd_rmute(ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "No reason provided"):
    # Try to delete the invoker message for anonymity
    try:
        await ctx.message.delete()
    except Exception:
        pass

    seconds = None
    try:
        seconds = parse_duration_input = None
        unit = duration[-1].lower()
        val = int(duration[:-1])
        mul = {"s":1, "m":60, "h":3600, "d":86400}.get(unit)
        if not mul:
            raise ValueError()
        seconds = val * mul
    except Exception:
        await ctx.reply("❌ Invalid duration format. Use like `10m`, `1h`, `2d`.", mention_author=False)
        return

    guild = ctx.guild
    ensure_user_log(member.id)
    # apply timeout
    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    try:
        # member.timeout expects datetime.timedelta or until param depending on discord.py version; use until for compatibility:
        await member.timeout(until, reason=f"Muted by {ctx.author}: {reason}")
    except Exception:
        # fallback: try member.edit(timeout=...)
        try:
            await member.edit(timeout=until)
        except Exception as e:
            await ctx.reply("❌ Failed to apply timeout. Ensure I have 'Moderate Members' permission.", mention_author=False)
            return

    # add mute role if exists
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role:
        try:
            await member.add_roles(muted_role, reason=f"Muted by {ctx.author}")
        except Exception:
            pass

    # DM the muted person
    dm_text = (
        f"You have been muted in __{guild.name}__ until\n"
        f"**{until.strftime('%Y-%m-%d')}**\n"
        f"**{until.strftime('%I:%M %p')} UTC**\n"
        f"**duration: {duration}**\n"
        f"Reason: `{reason}`"
    )
    try:
        await member.send(f"```{dm_text}```")
    except Exception:
        pass

    # update activity_logs
    log = ensure_user_log(member.id)
    log["mute_expires"] = (now_utc() + datetime.timedelta(seconds=seconds)).isoformat()
    log["mute_reason"] = reason
    log["mute_responsible"] = str(ctx.author.id)
    log["mute_count"] = log.get("mute_count", 0) + 1
    await save_all()

    # Build embed
    embed = discord.Embed(title="🔇 User Timed Out", description=f"**{member.display_name}** was muted", color=0xff7b50, timestamp=now_utc())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 User", value=member.mention, inline=True)
    embed.add_field(name="🔒 By", value=ctx.author.mention, inline=True)
    embed.add_field(name="⏳ Duration", value=f"`{duration}`", inline=True)
    embed.add_field(name="📝 Reason", value=f"***{reason}***", inline=False)
    # show unmute times across timezones
    unmute_dt = now_utc() + datetime.timedelta(seconds=seconds)
    tz_lines = [f"{tz_name}: {unmute_dt.astimezone(tz).strftime('%Y-%m-%d %I:%M %p')}" for tz_name, tz in TIMEZONES.items()]
    embed.add_field(name="🕒 Unmute Time", value="\n".join(tz_lines), inline=False)
    # DM content bottom as codeblock
    embed.add_field(name="💬 DM sent to user", value=f"```{dm_text}```", inline=False)
    # send to action log channel
    act_ch = guild.get_channel(ACTION_LOG_CHANNEL_ID)
    if act_ch:
        try:
            await act_ch.send(embed=embed)
        except Exception:
            pass

    # reply to executor
    await ctx.send(f"✅ {member.mention} has been muted for `{duration}`.", delete_after=8)

@bot.command(name="runmute")
@commands.has_permissions(moderate_members=True)
async def cmd_runmute(ctx: commands.Context, member: discord.Member):
    guild = ctx.guild
    muted_role = guild.get_role(MUTED_ROLE_ID)
    if muted_role and muted_role in member.roles:
        try:
            await member.remove_roles(muted_role, reason=f"Unmuted by {ctx.author}")
        except Exception:
            pass
    try:
        await member.timeout(None, reason=f"Unmuted by {ctx.author}")
    except Exception:
        pass

    # update logs
    log = ensure_user_log(member.id)
    orig_reason = log.get("mute_reason")
    orig_expires = log.get("mute_expires")
    log["mute_expires"] = None
    log["mute_reason"] = None
    log["mute_responsible"] = None
    await save_all()

    embed = discord.Embed(title="🔊 User Unmuted", color=0x2ecc71, timestamp=now_utc())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="👤 User", value=member.mention, inline=True)
    embed.add_field(name="🔓 By", value=ctx.author.mention, inline=True)
    if orig_reason:
        embed.add_field(name="📝 Original Reason", value=orig_reason, inline=False)
    act_ch = guild.get_channel(ACTION_LOG_CHANNEL_ID)
    if act_ch:
        try:
            await act_ch.send(embed=embed)
        except Exception:
            pass
    await ctx.reply(f"✅ {member.mention} has been unmuted.", mention_author=False)

# ------------------ AUTO UNMUTE LOOP ------------------
@tasks.loop(seconds=15)
async def auto_unmute_loop():
    now = now_utc()
    for uid, log in list(activity_logs.items()):
        exp = log.get("mute_expires")
        if not exp:
            continue
        try:
            exp_dt = datetime.datetime.fromisoformat(exp)
        except Exception:
            log["mute_expires"] = None
            await save_all()
            continue
        if now >= exp_dt:
            guild = bot.get_guild(GUILD_ID)
            if not guild:
                log["mute_expires"] = None
                await save_all()
                continue
            member = guild.get_member(int(uid))
            muted_role = guild.get_role(MUTED_ROLE_ID)
            if member:
                try:
                    if muted_role and muted_role in member.roles:
                        await member.remove_roles(muted_role, reason="Auto-unmute (mute expired)")
                except Exception:
                    pass
                try:
                    await member.timeout(None, reason="Auto-unmute (mute expired)")
                except Exception:
                    pass
                try:
                    await member.send(f"🔊 Your mute in **{guild.name}** has expired and you were unmuted.")
                except Exception:
                    pass
                # send log
                embed = discord.Embed(title="🔊 Auto Unmuted (mute expired)", color=0x2ecc71, timestamp=now_utc())
                embed.add_field(name="User", value=member.mention, inline=True)
                act_ch = guild.get_channel(ACTION_LOG_CHANNEL_ID)
                if act_ch:
                    try:
                        await act_ch.send(embed=embed)
                    except Exception:
                        pass
            log["mute_expires"] = None
            log["mute_reason"] = None
            log["mute_responsible"] = None
            await save_all()

# ------------------ INVITE TRACKING / !rinv ------------------
@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    # detect used invite
    try:
        before = invite_snapshot.get(guild.id, {})
        after_invites = await guild.invites()
        after = {inv.code: inv.uses for inv in after_invites}
        used = None
        used_inv = None
        for code, uses_before in before.items():
            uses_after = after.get(code, 0)
            if uses_after > uses_before:
                used = code
                # find invite object
                for inv in after_invites:
                    if inv.code == code:
                        used_inv = inv
                        break
                break
        # update snapshot
        invite_snapshot[guild.id] = {inv.code: inv.uses for inv in after_invites}
        if used_inv:
            inviter = used_inv.inviter
            invite_owner_map[str(member.id)] = inviter.id
            # store in activity logs
            log = ensure_user_log(member.id)
            log["invited_by"] = str(inviter.id)
            await save_all()
            # log join with inviter
            ch = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
            if ch:
                embed = discord.Embed(title="👥 Member Joined (invite)", color=0x2ecc71, timestamp=now_utc())
                embed.add_field(name="User", value=member.mention, inline=True)
                embed.add_field(name="Invited by", value=inviter.mention if inviter else "Unknown", inline=True)
                embed.add_field(name="Invite Code", value=used, inline=False)
                await safe_send(ch, embed=embed)
        else:
            # unknown invite
            invite_snapshot[guild.id] = {inv.code: inv.uses for inv in after_invites}
    except Exception:
        pass

@bot.command(name="rinv")
@commands.has_permissions(administrator=True)
async def cmd_rinv(ctx: commands.Context, member: discord.Member):
    inv_by = invite_owner_map.get(str(member.id)) or activity_logs.get(str(member.id), {}).get("invited_by")
    if inv_by:
        try:
            inv_member = ctx.guild.get_member(int(inv_by))
            await ctx.reply(f"{member.mention} was invited by {inv_member.mention if inv_member else inv_by}", mention_author=False)
        except Exception:
            await ctx.reply(f"{member.mention} was invited by ID `{inv_by}`", mention_author=False)
    else:
        await ctx.reply(f"No invite info recorded for {member.mention}", mention_author=False)

# ------------------ REACTION ROLE / REACTION LOGS ------------------
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # log reaction add (possible reaction role)
    if payload.guild_id != GUILD_ID:
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    ch = guild.get_channel(payload.channel_id) if guild else None
    # fetch message snippet from cache if we have
    msg_cached = message_cache.get(payload.message_id)
    embed = discord.Embed(title="➕ Reaction Added", color=0x3498db, timestamp=now_utc())
    embed.add_field(name="User", value=(member.mention if member else f"<@{payload.user_id}>"), inline=True)
    embed.add_field(name="Channel", value=(ch.mention if ch else f"<#{payload.channel_id}>"), inline=True)
    embed.add_field(name="Emoji", value=str(payload.emoji), inline=True)
    embed.add_field(name="Message ID", value=str(payload.message_id), inline=False)
    # who first set the reaction? -- we cannot know reliably, but we can check if the message author added it earlier by message_cache
    if msg_cached and msg_cached.get("author_id"):
        embed.add_field(name="Message Author", value=f"<@{msg_cached.get('author_id')}>", inline=True)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.guild_id != GUILD_ID:
        return
    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id) if guild else None
    ch = guild.get_channel(payload.channel_id) if guild else None
    embed = discord.Embed(title="➖ Reaction Removed", color=0xe67e22, timestamp=now_utc())
    embed.add_field(name="User", value=(member.mention if member else f"<@{payload.user_id}>"), inline=True)
    embed.add_field(name="Channel", value=(ch.mention if ch else f"<#{payload.channel_id}>"), inline=True)
    embed.add_field(name="Emoji", value=str(payload.emoji), inline=True)
    embed.add_field(name="Message ID", value=str(payload.message_id), inline=False)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

# ------------------ MEMBER BAN / UNBAN / KICK LOGS ------------------
@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    # find audit log for ban to find who banned
    who = None
    try:
        async for entry in guild.audit_logs(limit=10, action=AuditLogAction.ban):
            if entry.target.id == user.id:
                who = entry.user
                break
    except Exception:
        who = None
    embed = discord.Embed(title="⛔ Member Banned", color=0xe74c3c, timestamp=now_utc())
    embed.add_field(name="User", value=f"{user}", inline=True)
    embed.add_field(name="Banned by", value=(who.mention if who else "Unknown"), inline=True)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

@bot.event
async def on_member_unban(guild: discord.Guild, user: discord.User):
    who = None
    try:
        async for entry in guild.audit_logs(limit=10, action=AuditLogAction.unban):
            if entry.target.id == user.id:
                who = entry.user
                break
    except Exception:
        who = None
    embed = discord.Embed(title="✅ Member Unbanned", color=0x2ecc71, timestamp=now_utc())
    embed.add_field(name="User", value=f"{user}", inline=True)
    embed.add_field(name="Unbanned by", value=(who.mention if who else "Unknown"), inline=True)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

# ------------------ CHANNEL / ROLE CREATION & DELETION (more) included above) ------------------

# ------------------ TIMETRACK POLLER (1s tick) ------------------
@tasks.loop(seconds=1.0)
async def inactivity_poller():
    # iterate through logs, mark inactive if last_message older than offline_delay
    now = now_utc()
    changed = False
    for uid_str, log in list(activity_logs.items()):
        try:
            last_iso = log.get("last_message")
            if not last_iso:
                # no message yet; do not count as active
                continue
            last_dt = datetime.datetime.fromisoformat(last_iso)
            delay = int(log.get("offline_delay", INACTIVE_MIN))
            delta = (now - last_dt).total_seconds()
            if delta >= delay:
                # they became inactive
                if not log.get("inactive", False):
                    # record offline_start at last_dt + delay
                    started = last_dt + datetime.timedelta(seconds=delay)
                    log["offline_start"] = started.isoformat()
                    log["inactive"] = True
                    # send "went inactive" notification if they have any of ACTIVE_LOG_ROLE_IDS
                    guild = bot.get_guild(GUILD_ID)
                    if guild:
                        member = guild.get_member(int(uid_str))
                        if member:
                            if any(guild.get_role(rid) in member.roles for rid in ACTIVE_LOG_ROLE_IDS if guild.get_role(rid)):
                                lc = guild.get_channel(ACTION_LOG_CHANNEL_ID)
                                ping = member.mention if log.get("rping_on", False) else member.display_name
                                if lc:
                                    await safe_send(lc, f"⚫ {ping} has gone inactive ({delay}s without message).")
                # compute offline_seconds
                try:
                    started_iso = log.get("offline_start")
                    started_dt = datetime.datetime.fromisoformat(started_iso)
                    log["offline_seconds"] = (now - started_dt).total_seconds()
                except Exception:
                    log["offline_seconds"] = delta
            else:
                # active
                if log.get("offline_start"):
                    log["offline_start"] = None
                log["inactive"] = False
                # increment online counters by 1 second
                log["online_seconds"] = log.get("online_seconds", 0) + 1
                log["daily_seconds"] = log.get("daily_seconds", 0) + 1
                log["weekly_seconds"] = log.get("weekly_seconds", 0) + 1
                log["monthly_seconds"] = log.get("monthly_seconds", 0) + 1
            changed = True
        except Exception:
            continue
    if changed:
        await save_all()

# ------------------ SAVE / PRUNE LOOPS ------------------
@tasks.loop(seconds=SAVE_INTERVAL)
async def save_loop():
    await save_all()

@tasks.loop(minutes=10)
async def prune_cache_loop():
    # prune old message_cache entries (older than 2 hours)
    cutoff = now_utc() - datetime.timedelta(hours=2)
    to_delete = []
    for mid, v in list(message_cache.items()):
        try:
            created = v.get("created_at")
            if created:
                dt = datetime.datetime.fromisoformat(created)
                if dt < cutoff:
                    to_delete.append(mid)
        except Exception:
            to_delete.append(mid)
    for mid in to_delete:
        message_cache.pop(mid, None)

# ------------------ PURGE COMMAND (convenience) - generates HTML and uploads ------------------
@bot.command(name="purge")
@commands.has_permissions(manage_messages=True)
async def cmd_purge(ctx: commands.Context, limit: int = 100):
    # delete messages and log them to html
    if limit <= 0 or limit > 1000:
        limit = 100
    msgs = []
    async for m in ctx.channel.history(limit=limit):
        # record messages to html export
        if m.author.bot:
            continue
        msgs.append(m)
    if not msgs:
        return await ctx.reply("No messages found.", mention_author=False)
    # create html similar to bulk handler
    html_parts = []
    html_parts.append("<html><meta charset='utf-8'><body>")
    html_parts.append(f"<h2>PURGE by {ctx.author} - #{ctx.channel.name}</h2>")
    html_parts.append(f"<p>Messages purged: {len(msgs)} — {fmt_dt(now_utc(), TIMEZONES['EST'])}</p>")
    for m in msgs:
        html_parts.append("<hr>")
        html_parts.append(f"<h3>{html.escape(str(m.author))} — {m.created_at.isoformat()}</h3>")
        html_parts.append(f"<p>{html.escape(m.content or '')}</p>")
        for a in m.attachments:
            html_parts.append(f"<p><a href='{html.escape(a.url)}'>{html.escape(a.url)}</a></p>")
            if any(a.url.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")):
                html_parts.append(f"<img src='{html.escape(a.url)}' style='max-width:600px;'><br>")
    html_parts.append("</body></html>")
    html_content = "\n".join(html_parts)
    fname = f"purge_{ctx.guild.id}_{ctx.channel.id}_{int(now_utc().timestamp())}.html"
    tmp = io.BytesIO(html_content.encode("utf-8"))
    embed = discord.Embed(title="🧾 Purge Log", description=f"{len(msgs)} messages purged in {ctx.channel.mention} by {ctx.author.mention}", color=0x95a5a6, timestamp=now_utc())
    preview = "\n".join([f"{m.author}: {m.content[:200].replace(chr(10),' ')}" for m in msgs[:5]])
    embed.add_field(name="Preview (first 5)", value=preview or "(none)", inline=False)
    lc = ctx.guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        try:
            await lc.send(embed=embed, file=discord.File(tmp, filename=fname))
        except Exception:
            await lc.send(embed=embed)
    # now purge messages
    try:
        await ctx.channel.delete_messages(msgs)
    except Exception:
        # fallback to bulk delete by ids
        ids = [m.id for m in msgs]
        try:
            await ctx.channel.delete_messages(ids)
        except Exception:
            pass
    await ctx.reply(f"✅ Purged {len(msgs)} messages and logged to {lc.mention if lc else 'audit channel'}.", mention_author=False)

# ------------------ MISC EVENTS (emoji/sticker/webhook/integration) ------------------
@bot.event
async def on_guild_emojis_update(guild: discord.Guild, before, after):
    embed = discord.Embed(title="🎭 Emoji updated", color=0x8e44ad, timestamp=now_utc())
    embed.add_field(name="Before count", value=str(len(before)), inline=True)
    embed.add_field(name="After count", value=str(len(after)), inline=True)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

@bot.event
async def on_webhooks_update(channel):
    guild = channel.guild
    embed = discord.Embed(title="🌐 Webhooks changed", color=0xf39c12, timestamp=now_utc())
    embed.add_field(name="Channel", value=channel.mention, inline=True)
    # who? check audit logs
    who = None
    try:
        who = await find_audit_executor(guild, AuditLogAction.webhook_create, target_id=channel.id)
    except Exception:
        who = None
    embed.add_field(name="Executor (if found)", value=(who.mention if who else "Unknown"), inline=True)
    lc = guild.get_channel(AUDIT_LOG_CHANNEL_ID)
    if lc:
        await safe_send(lc, embed=embed)

# ------------------ ADMIN: toggle rdm DMs for critical events ------------------
@bot.command(name="rdm")
@commands.has_permissions(administrator=True)
async def cmd_rdm(ctx: commands.Context, toggle: str):
    global rdm_enabled
    t = toggle.lower()
    if t in ("on", "true", "enable"):
        rdm_enabled = True
        await ctx.reply("✅ RDM/critical DMs enabled.", mention_author=False)
    elif t in ("off", "false", "disable"):
        rdm_enabled = False
        await ctx.reply("✅ RDM/critical DMs disabled.", mention_author=False)
    else:
        await ctx.reply("Usage: `!rdm on|off`", mention_author=False)

# ------------------ STARTUP: run bot ------------------
if __name__ == "__main__":
    load_activity()
    load_invites()
    bot.run(TOKEN)
