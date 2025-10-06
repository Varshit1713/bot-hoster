# antiscam_bot.py
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import io
import os
import datetime
from collections import defaultdict

# Optional: Pillow for deeper image validation (not required)
try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

# -----------------------
# Configuration (edit)
# -----------------------
TOKEN = "YOUR_BOT_TOKEN_HERE"
MOD_LOG_CHANNEL_ID = 123456789012345678  # channel to send moderation logs
GUILD_ID = None  # optional: limit checks to this guild id (int) or None for all
WHITELISTED_CHANNEL_IDS = set()  # channels to ignore, e.g. {111111, 222222}
WHITELISTED_ROLE_IDS = set()     # roles that bypass checks, e.g. admins/mods
MUTED_ROLE_ID = None             # role id for 'Muted' role (if you want to mute)
MIN_ACCOUNT_AGE_DAYS = 1         # accounts younger than this are more strictly checked
STRIKES_TO_MUTE = 3              # strikes before applying mute (None to disable)
AUTO_DELETE = True               # delete suspicious messages
DM_ON_STRIKE = True              # DM user when they get a strike
MAX_BYTES_TO_READ = 8192        # how many bytes to download for inspection
STRIKE_EXPIRY_DAYS = 30         # strikes expire after this many days
# -----------------------

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory strike store (persist to file/db in production)
strikes = defaultdict(list)  # user_id -> list of (timestamp, reason)

# Common suspicious extensions (executable, shortcuts, archives)
SUSPICIOUS_EXTS = (
    ".exe", ".msi", ".scr", ".bat", ".cmd", ".ps1", ".jar", ".vbs",
    ".lnk", ".url", ".pif", ".gadget", ".com", ".cpl", ".wsf", ".hta", ".zip", ".rar", ".7z"
)

# Valid image magic headers
def looks_like_jpeg(data: bytes) -> bool:
    return len(data) >= 3 and data[0:3] == b'\xff\xd8\xff'

def looks_like_png(data: bytes) -> bool:
    return len(data) >= 8 and data[0:8] == b'\x89PNG\r\n\x1a\n'

def looks_like_gif(data: bytes) -> bool:
    return len(data) >= 6 and data[0:6] in (b'GIF87a', b'GIF89a')

def looks_like_webp(data: bytes) -> bool:
    # RIFF....WEBP
    return len(data) >= 12 and data[0:4] == b'RIFF' and data[8:12] == b'WEBP'

def looks_like_image(data: bytes) -> bool:
    return any([
        looks_like_jpeg(data),
        looks_like_png(data),
        looks_like_gif(data),
        looks_like_webp(data),
    ])

def has_html_start(data: bytes) -> bool:
    start = data[:64].lower()
    return b'<!doctype html' in start or b'<html' in start or b'<script' in start or b'javascript:' in start

# Utility: simplified extension extraction
def lower_ext(name: str) -> str:
    return os.path.splitext(name.lower())[1]

# Logging helper
async def log_mod(guild: discord.Guild, message: str):
    if not MOD_LOG_CHANNEL_ID:
        return
    ch = guild.get_channel(MOD_LOG_CHANNEL_ID) if guild else bot.get_channel(MOD_LOG_CHANNEL_ID)
    if ch:
        try:
            await ch.send(message)
        except Exception:
            pass

# Strike handling
def add_strike(user_id: int, reason: str):
    now = datetime.datetime.utcnow()
    strikes[user_id].append((now, reason))

def get_active_strikes(user_id: int):
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=STRIKE_EXPIRY_DAYS)
    return [s for s in strikes.get(user_id, []) if s[0] >= cutoff]

def clear_expired_strikes():
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=STRIKE_EXPIRY_DAYS)
    for uid in list(strikes.keys()):
        strikes[uid] = [s for s in strikes[uid] if s[0] >= cutoff]
        if not strikes[uid]:
            del strikes[uid]

@tasks.loop(hours=24)
async def daily_cleanup():
    clear_expired_strikes()

@bot.event
async def on_ready():
    print(f"AntiScam bot ready as {bot.user} (guilds: {len(bot.guilds)})")
    daily_cleanup.start()

def user_is_whitelisted(member: discord.Member):
    if not member:
        return False
    if member.guild_permissions.administrator:
        return True
    if any(role.id in WHITELISTED_ROLE_IDS for role in member.roles):
        return True
    return False

async def inspect_attachment_and_act(message: discord.Message, attach: discord.Attachment):
    author = message.author
    guild = message.guild
    filename = attach.filename or ""
    fname_lower = filename.lower()

    # If channel/role whitelist or owner/admin, ignore
    if guild and user_is_whitelisted(author):
        return False

    # Ignore whitelisted channels
    if message.channel.id in WHITELISTED_CHANNEL_IDS:
        return False

    # Quick checks: suspicious extension in filename
    for ext in SUSPICIOUS_EXTS:
        if fname_lower.endswith(ext) or (ext in fname_lower and fname_lower.count('.') > 1):
            reason = f"suspicious extension detected: {filename}"
            await take_action_on_message(message, author, reason)
            return True

    # Double-extension like file.jpg.exe or something with multiple dots where last is allowed image but earlier suspicious
    parts = fname_lower.split('.')
    if len(parts) >= 3:
        # e.g., photo.jpg.exe or photo.png.lnk
        second_last = "." + parts[-2]
        if second_last in SUSPICIOUS_EXTS:
            reason = f"double extension suspicious: {filename}"
            await take_action_on_message(message, author, reason)
            return True

    # Download small prefix of file to inspect magic bytes safely
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(attach.url) as resp:
                content = await resp.content.read(MAX_BYTES_TO_READ)
    except Exception as e:
        # If we can't download, log and skip (or choose to delete)
        await log_mod(guild or message.channel, f"Failed to fetch attachment for inspection: {e}")
        return False

    # Check for HTML/script payload disguised as .jpg/.png
    if has_html_start(content):
        reason = f"attachment appears to be HTML/script disguised as file: {filename}"
        await take_action_on_message(message, author, reason)
        return True

    # If extension indicates an image but magic bytes do NOT match known images -> suspicious
    ext = lower_ext(filename)
    if ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        if not looks_like_image(content):
            reason = f"extension says image but file header mismatch: {filename}"
            await take_action_on_message(message, author, reason)
            return True

    # Optional deeper PIL check (ensure file opens as image)
    if PIL_AVAILABLE and ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp'):
        try:
            img = Image.open(io.BytesIO(content))
            img.verify()  # verify will raise if not a valid image
        except Exception:
            reason = f"file failed image verification: {filename}"
            await take_action_on_message(message, author, reason)
            return True

    # If filename contains URL-looking text (long urls), or raw suspicious strings, flag (optional)
    if "http://" in filename or "https://" in filename or "discord.gg" in filename:
        reason = f"filename contains url-like content: {filename}"
        await take_action_on_message(message, author, reason)
        return True

    # No issue found
    return False

async def take_action_on_message(message: discord.Message, author: discord.Member, reason: str):
    guild = message.guild
    uid = author.id
    add_strike(uid, reason)
    active = get_active_strikes(uid)
    strike_count = len(active)

    # Delete the message if configured
    if AUTO_DELETE:
        try:
            await message.delete()
        except Exception:
            pass

    # Log to mod channel
    log_text = (
        f"Auto-moderation: removed message from {author} ({author.id}) in "
        f"{message.channel.mention if message.channel else 'DM'}\nReason: {reason}\n"
        f"Active strikes: {strike_count}"
    )
    await log_mod(guild or message.channel, log_text)

    # DM user (if enabled)
    if DM_ON_STRIKE:
        try:
            await author.send(
                f"Your message in **{guild.name if guild else 'a server'}** was removed because: {reason}.\n"
                f"This is strike {strike_count} (strikes expire after {STRIKE_EXPIRY_DAYS} days)."
            )
        except Exception:
            pass

    # Optionally apply mute if strikes exceed threshold
    if STRIKES_TO_MUTE and strike_count >= STRIKES_TO_MUTE and guild and MUTED_ROLE_ID:
        member = guild.get_member(uid)
        if member:
            try:
                muted_role = guild.get_role(MUTED_ROLE_ID)
                if muted_role:
                    await member.add_roles(muted_role, reason="Auto-mute: multiple suspicious attachments")
                    await log_mod(guild, f"Auto-muted {member} for {strike_count} strikes.")
            except Exception as e:
                await log_mod(guild, f"Failed to auto-mute {member}: {e}")

# Events
@bot.event
async def on_message(message: discord.Message):
    # Ignore bots
    if message.author.bot:
        return

    # If message has attachments, inspect them
    if message.attachments:
        for attach in message.attachments:
            try:
                acted = await inspect_attachment_and_act(message, attach)
                if acted:
                    # if we took action that removed the message, break
                    break
            except Exception as e:
                await log_mod(message.guild or message.channel, f"Error inspecting attachment: {e}")

    await bot.process_commands(message)

# Admin commands
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

# Simple config command examples (expand as needed)
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

# Run
if __name__ == "__main__":
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("Please set TOKEN in the script before running.")
    else:
        bot.run(TOKEN)
