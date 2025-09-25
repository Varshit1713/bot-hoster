# Is/timetrack.py
from discord.ext import commands, tasks
from discord import Embed, Colour
from datetime import datetime
import asyncio
import json
import os

# -----------------------------
# Data file
# -----------------------------
DATA_FILE = "bot_data.json"
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
# Helper to save DATA
# -----------------------------
async def persist():
    with open(DATA_FILE, "w") as f:
        json.dump(DATA, f, indent=4, default=str)

# -----------------------------
# Fancy embed helper
# -----------------------------
def fancy_embed(title, description, color=Colour.blue()):
    embed = Embed(title=title, description=description, colour=color)
    embed.timestamp = datetime.utcnow()
    return embed

# -----------------------------
# Helper: get current UTC
# -----------------------------
def now_utc():
    return datetime.utcnow()

# -----------------------------
# Timetrack Cog
# -----------------------------
class Timetrack(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.timetrack_loop.start()

    # -------------------------
    # Background loop for timetrack
    # -------------------------
    @tasks.loop(seconds=60)
    async def timetrack_loop(self):
        for guild in self.bot.guilds:
            for member in guild.members:
                # Only track members with roles in rcache_roles
                tracked_roles = DATA.get("rcache_roles", [])
                if any(role.id in tracked_roles for role in member.roles):
                    uid = str(member.id)
                    if uid not in DATA["users"]:
                        DATA["users"][uid] = {"total_online_seconds":0,"online_start":None}
                    rec = DATA["users"][uid]
                    # Online session start
                    if member.status != member.status.offline:
                        if not rec.get("online_start"):
                            rec["online_start"] = now_utc().isoformat()
                    else:
                        # Offline: calculate session duration
                        if rec.get("online_start"):
                            delta = now_utc() - datetime.fromisoformat(rec["online_start"])
                            rec["total_online_seconds"] += int(delta.total_seconds())
                            rec["online_start"] = None
        await persist()

    # -------------------------
    # !mtlb command: leaderboard for tracked roles
    # -------------------------
    @commands.command()
    async def mtlb(self, ctx, top: int = 10):
        leaderboard = []
        tracked_roles = DATA.get("rcache_roles", [])
        for uid, record in DATA.get("users", {}).items():
            member = ctx.guild.get_member(int(uid))
            if not member:
                continue
            if any(role.id in tracked_roles for role in member.roles):
                total_sec = record.get("total_online_seconds", 0)
                # Include current session if online
                if record.get("online_start"):
                    delta = now_utc() - datetime.fromisoformat(record["online_start"])
                    total_sec += int(delta.total_seconds())
                leaderboard.append((member.name, total_sec))

        # Sort descending
        leaderboard.sort(key=lambda x: x[1], reverse=True)
        description = ""
        for name, sec in leaderboard[:top]:
            hours, remainder = divmod(sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            description += f"**{name}** — {hours}h {minutes}m {seconds}s\n"

        await ctx.send(embed=fancy_embed(f"📊 Top {top} Timetrack Users", description))

    # -------------------------
    # !mtdm command: leaderboard for non-tracked roles
    # -------------------------
    @commands.command()
    async def mtdm(self, ctx, top: int = 10):
        leaderboard = []
        tracked_roles = DATA.get("rcache_roles", [])
        for uid, record in DATA.get("users", {}).items():
            member = ctx.guild.get_member(int(uid))
            if not member:
                continue
            # Skip tracked roles
            if any(role.id in tracked_roles for role in member.roles):
                continue
            total_sec = record.get("total_online_seconds", 0)
            if record.get("online_start"):
                delta = now_utc() - datetime.fromisoformat(record["online_start"])
                total_sec += int(delta.total_seconds())
            leaderboard.append((member.name, total_sec))

        leaderboard.sort(key=lambda x: x[1], reverse=True)
        description = ""
        for name, sec in leaderboard[:top]:
            hours, remainder = divmod(sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            description += f"**{name}** — {hours}h {minutes}m {seconds}s\n"

        await ctx.send(embed=fancy_embed(f"📊 Top {top} Non-Tracked Users", description))

# -----------------------------
# Cog setup
# -----------------------------
def setup(bot):
    bot.add_cog(Timetrack(bot))
