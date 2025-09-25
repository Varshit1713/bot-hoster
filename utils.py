# The/utils.py
import json
import os
import asyncio
from discord import Embed, Colour
from datetime import datetime, timedelta

DATA_FILE = "bot_data.json"

# -----------------------------
# Load DATA
# -----------------------------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        DATA = json.load(f)
else:
    DATA = {
        "users": {},
        "mutes": {},
        "rmute_usage": {},
        "rdm_users": [],
        "cache": {},
        "mute_role_id": None,
        "timetrack_channel_id": None,
        "log_channel_id": None,
        "staff_ping_role_id": None,
        "higher_staff_ping_role_id": None,
        "rcache_roles": []
    }

# -----------------------------
# Persist DATA
# -----------------------------
async def persist():
    with open(DATA_FILE, "w") as f:
        json.dump(DATA, f, indent=4, default=str)

# -----------------------------
# Fancy embed
# -----------------------------
def fancy_embed(title, description, color=Colour.orange()):
    embed = Embed(title=title, description=description, colour=color)
    embed.timestamp = datetime.utcnow()
    return embed

# -----------------------------
# Auto-unmute scheduler
# -----------------------------
async def schedule_unmute(bot, member_id, guild_id, delay):
    await asyncio.sleep(delay)
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    member = guild.get_member(member_id)
    role_id = DATA.get("mute_role_id")
    if role_id and member:
        role = guild.get_role(role_id)
        if role in member.roles:
            await member.remove_roles(role)
    # Remove from DATA
    DATA["mutes"].pop(str(member_id), None)
    await persist()
