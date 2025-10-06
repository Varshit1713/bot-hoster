# antiscam_bot.py
"""
Discord Anti-Scam Moderation Bot (Render-compatible web service)
- Starts a tiny Flask webserver so Render treats this as a web service.
- Scans attachments for suspicious filenames / double extensions / header mismatches / HTML disguised as images.
- Auto-deletes suspicious messages, issues strikes, logs to a mod channel, optionally mutes.
- Configuration is via environment variables (no hard-coded token).
"""

import os
import io
import threading
import datetime
import logging
from collections import defaultdict

import aiohttp
from flask import Flask
import discord
from discord.ext import commands, tasks

# Optional deep image verification
try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

# -------------------------
# Configuration (via ENV)
# -------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
MOD_LOG_CHANNEL_ID = int(os.getenv("MOD_LOG_CHANNEL_ID")) if os.getenv("MOD_LOG_CHANNEL_ID") else None
GUILD_LIMIT = int(os.getenv("GUILD_LIMIT")) if os.getenv("GUILD_LIMIT") else None  # optional guild id to limit checks
MUTED_ROLE_ID = int(os.getenv("MUTED_ROLE_ID")) if os.getenv("MUTED_ROLE_ID") else None
WHITELISTED_CHANNEL_IDS = {int(x) for x in os.getenv("WHITELISTED_CHANNEL_IDS", "").split(",") if x.strip().isdigit()}
WHITELISTED_ROLE_IDS = {int(x) for x in os.getenv("WHITELISTED_ROLE_IDS", "").split(",") if x.strip().isdigit()}

MIN_ACCOUNT_AGE_DAYS = int(os.getenv("MIN_ACCOUNT_AGE_DAYS", "0"))
STRIKES_TO_MUTE = int(os.getenv("STRIKES_TO_MUTE", "3"))
AUTO_DELETE = os.getenv("AUTO_DELETE", "true").lower() in ("1", "true", "yes")
DM_ON_STRIKE = os.getenv("DM_ON_STRIKE", "true").lower() in ("1", "true", "yes")
STRIKE_EXPIRY_DAYS = int(os.getenv("STRIKE_EXPIRY_DAYS", "30"))
MAX_BYTES_TO_READ = int(os.getenv("MAX_BYTES_TO_READ", "8192"))

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("antiscam")

# -------------------------
# Bot & intents
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------
# In-memory strikes store
# (For production, replace with persistent DB or file)
# -------------------------
strikes = defaultdict(list)  # user_id -> list of (datetime, reason)

# -------------------------
# Heuristics / helpers
# -------------------------
SUSPICIOUS_EXTS = (
    ".exe", ".msi", ".scr", ".bat", ".cmd", ".ps1", ".jar", ".vbs", ".lnk", ".url",
    ".pif", ".gadget", ".com", ".cpl", ".wsf", ".hta", ".zip", ".rar", ".7z"
)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")

def lower_ext(name: str) -> str:
    return os.path.splitext(name.lower())[1]

def looks_like_jpeg(data: bytes) -> bool:
    return len(data) >= 3 and data[0:3] == b'\xff\xd8\xff'

def looks_like_png(data: bytes) -> bool:
    return len(data) >= 8 and data[0:8] == b'\x89PNG\r\n\x1a\n'

def looks_like_gif(data: bytes) -> bool:
    return len(data) >= 6 and data[0:6] in (b'GIF87a', b'GIF89a')

def looks_like_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[0:4] == b'RIFF' and data[8:12] == b'WEBP'

def looks_like_image(data: bytes) -> bool:
    return any([
        looks_like_jpeg(data),
        looks_like_png(data),
        looks_like_gif(data),
        looks_like_webp(data),
    ])

def has_html_start(data: bytes) -> bool:
    start = data[:256].lower()
    return b'<!doctype html' in start or b'<html' in start or b'<script' in start or b'javascript:' in start or b'<!doctype' in start

def user_is_whitelisted(member: discord.Member) -> bool:
    if not member:
        return False
    if member.guild_permissions.administrator:
        return True
    if any(role.id in WHITELISTED_ROLE_IDS for role in member.roles):
        return True
    return False

# -------------------------
# Strike management
# -------------------------
def add_strike(user_id: int, reason: str):
    now = datetime.datetime.datetime.utcnow()
    strikes[user_id].append((now, reason))
    log.info(f"Strike added: user={user_id} reason={reason}")

def get_active_strikes(user_id: int):
    cutoff = datetime.datetime.datetime.utcnow() - datetime.timedelta(days=STRIKE_EXPIRY_DAYS)
    return [s for s in strikes.get(user_id, []) if s[0] >= cutoff]

def clear_expired_strikes():
    cutoff = datetime.datetime.datetime.utcnow() - datetime.timedelta(days=STRIKE_EXPIRY_DAYS)
    for uid in list(strikes.keys()):
        strikes[uid] = [s for s in strikes[uid] if s[0] >= cutoff]
        if not strikes[uid]:
            del strikes[uid]

@tasks.loop(hours=24)
async def daily_cleanup():
    clear_expired_strikes()

# -------------------------
# Moderation logging helper
# -------------------------
async def log_mod(guild: discord.Guild, text: str):
    try:
        if MOD_LOG_CHANNEL_ID:
            ch = None
            if guild:
                ch = guild.get_channel(MOD_LOG_CHANNEL_ID)
            if not ch:
                ch = bot.get_channel(MOD_LOG_CHANNEL_ID)
            if ch:
                await ch.send(text)
                return
        # fallback to logging to console if no mod channel set
        log.info(f"[MOD LOG] {text}")
    except Exception as e:
        log.exception("Failed to send mod log: %s", e)

# -------------------------
# Core inspection + action
# -------------------------
async def inspect_attachment_and_act(message: discord.Message, attach: discord.Attachment) -> bool:
    """
    Returns True if action was taken (e.g., message deleted).
    """
    author = message.author
    guild = message.guild
    filename = attach.filename or ""
    fname_lower = filename.lower()

    # Whitelists
    if guild and user_is_whitelisted(author):
        return False
    if message.channel.id in WHITELISTED_CHANNEL_IDS:
        return False
    if GUILD_LIMIT and guild and guild.id != GUILD_LIMIT:
        return False

    # Quick filename checks
    for ext in SUSPICIOUS_EXTS:
        # endswith or present earlier (double extension)
        if fname_lower.endswith(ext) or (ext in fname_lower and fname_lower.count('.') > 1):
            reason = f"suspicious extension detected: {filename}"
            await take_action_on_message(message, author, reason)
            return True

    # Double extension: e.g., photo.jpg.exe or photo.png.lnk
    parts = fname_lower.split('.')
    if len(parts) >= 3:
        # check second-last part
        second_last = "." + parts[-2]
        if second_last in SUSPICIOUS_EXTS:
            reason = f"double extension suspicious: {filename}"
            await take_action_on_message(message, author, reason)
            return True

    # Download HEAD of file safely
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attach.url) as resp:
                content = await resp.content.read(MAX_BYTES_TO_READ)
    except Exception as e:
        await log_mod(guild, f"Failed to fetch attachment for inspection ({filename}) from {author} : {e}")
        return False

    # Check for HTML/script disguised
    if has_html_start(content):
        reason = f"attachment appears to be HTML/script disguised as file: {filename}"
        await take_action_on_message(message, author, reason)
        return True

    # If extension says image but bytes do NOT match -> suspicious
    ext = lower_ext(filename)
    if ext in IMAGE_EXTS:
        if not looks_like_image(content):
            reason = f"extension says image but file header mismatch: {filename}"
            await take_action_on_message(message, author, reason)
            return True

        # Optional deeper PIL check
        if PIL_AVAILABLE:
            try:
                img = Image.open(io.BytesIO(content))
                img.verify()
            except Exception:
                reason = f"file failed image verification: {filename}"
                await take_action_on_message(message, author, reason)
                return True

    # Filename includes a URL-like string -> suspicious
    if "http://" in filename or "https://" in filename or "discord.gg" in filename:
        reason = f"filename contains url-like content: {filename}"
        await take_action_on_message(message, author, reason)
        return True

    # If file is not an image but has no obvious malicious ext, we allow it (admins can configure)
    return False

async def take_action_on_message(message: discord.Message, author: discord.Member, reason: str):
    guild = message.guild
    uid = author.id

    add_strike(uid, reason)
    active = get_active_strikes(uid)
    strike_count = len(active)

    # Delete message if configured
    if AUTO_DELETE:
        try:
            await message.delete()
        except Exception as e:
            log.exception("Failed to delete message: %s", e)

    # Log to mod channel
    channel_name = message.channel.mention if message.channel else "unknown"
    log_text = (
        f"Auto-moderation: removed message from {author} ({author.id}) in {channel_name}\n"
        f"Reason: {reason}\nActive strikes: {strike_count}"
    )
    await log_mod(guild, log_text)

    # DM user
    if DM_ON_STRIKE:
        try:
            guild_name = guild.name if guild else "a server"
            await author.send(
                f"Your message in **{guild_name}** was removed for the following reason:\n\n{reason}\n\n"
                f"This is strike **{strike_count}**. Strikes expire after {STRIKE_EXPIRY_DAYS} days."
            )
        except Exception:
            # Could be DMs closed
            pass

    # Auto-mute if threshold reached
    if STRIKES_TO_MUTE and strike_count >= STRIKES_TO_MUTE and guild and MUTED_ROLE_ID:
        try:
            member = guild.get_member(uid)
            if member:
                muted_role = guild.get_role(MUTED_ROLE_ID)
                if muted_role and muted_role not in member.roles:
                    await member.add_roles(muted_role, reason="Auto-mute: multiple suspicious attachments")
                    await log_mod(guild, f"Auto-muted {member} for {strike_count} strikes.")
        except Exception as e:
            await log_mod(guild, f"Failed to auto-mute user {uid}: {e}")

# -------------------------
# Events
# -------------------------
@bot.event
async def on_ready():
    log.info(f"Bot ready: {bot.user} (ID: {bot.user.id})")
    if not daily_cleanup.is_running():
        daily_cleanup.start()

@bot.event
async def on_message(message: discord.Message):
    # Ignore other bots
    if message.author.bot:
        return

    # If message has attachments, inspect each
    if message.attachments:
        for attach in message.attachments:
            try:
                acted = await inspect_attachment_and_act(message, attach)
                if acted:
                    # We took an action (deleted or flagged) — stop further checks for this message
                    break
            except Exception as e:
                await log_mod(message.guild, f"Error inspecting attachment from {message.author}: {e}")

    # Process commands at end
    await bot.process_commands(message)

# -------------------------
# Admin Commands (examples)
# -------------------------
@commands.has_guild_permissions(manage_guild=True)
@bot.command(name="strikes")
async def cmd_strikes(ctx, member: discord.Member = None):
    member = member or ctx.author
    active = get_active_strikes(member.id)
    msg = f"{member} has {len(active)} active strike(s).\n"
    for ts, reason in active:
        msg += f"- {ts.isoformat()} UTC: {reason}\n"
    await ctx.send(msg)

@commands.has_guild_permissions(manage_guild=True)
@bot.command(name="clearstrikes")
async def cmd_clearstrikes(ctx, member: discord.Member):
    strikes.pop(member.id, None)
    await ctx.send(f"Cleared strikes for {member}.")

@commands.has_guild_permissions(administrator=True)
@bot.command(name="allowchannel")
async def cmd_allowchannel(ctx, channel: discord.TextChannel):
    WHITELISTED_CHANNEL_IDS.add(channel.id)
    await ctx.send(f"Channel {channel.mention} added to whitelist.")

@commands.has_guild_permissions(administrator=True)
@bot.command(name="denychannel")
async def cmd_denychannel(ctx, channel: discord.TextChannel):
    WHITELISTED_CHANNEL_IDS.discard(channel.id)
    await ctx.send(f"Channel {channel.mention} removed from whitelist.")

# -------------------------
# Flask keep-alive server for Render web service
# -------------------------
app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return "Anti-Scam Bot is alive", 200

def run_web():
    # Bind to PORT env var that Render sets (default 8080 if not present)
    port = int(os.environ.get("PORT", 8080))
    log.info(f"Starting Flask web server on port {port}")
    app.run(host="0.0.0.0", port=port)

# -------------------------
# Entrypoint
# -------------------------
def main():
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN environment variable not set. Exiting.")
        return

    # Start Flask in background thread so the main thread can run the bot
    t = threading.Thread(target=run_web, daemon=True)
    t.start()

    # Start bot (blocking)
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        log.exception("Bot terminated: %s", e)

if __name__ == "__main__":
    main()
