# Is/ping.py
from discord.ext import commands
from discord import Embed, Colour
from datetime import datetime
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
def fancy_embed(title, description, color=Colour.gold()):
    embed = Embed(title=title, description=description, colour=color)
    embed.timestamp = datetime.utcnow()
    return embed

# -----------------------------
# Ping Cog
# -----------------------------
class Ping(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -------------------------
    # mrping: staff ping
    # -------------------------
    @commands.command()
    async def mrping(self, ctx):
        role_id = DATA.get("staff_ping_role_id")
        if not role_id:
            await ctx.send("❌ Staff ping role not set in config.")
            return
        role = ctx.guild.get_role(role_id)
        if not role:
            await ctx.send("❌ Staff ping role not found in guild.")
            return

        # Delete command message
        await ctx.message.delete()

        content = f"{role.mention} Staff ping!"
        # Include reply context if replying
        if ctx.message.reference:
            ref_msg = ctx.message.reference.resolved
            if ref_msg:
                content += f"\n**Original message by {ref_msg.author}:** {ref_msg.content}"

        # Send to log channel if set
        log_channel_id = DATA.get("log_channel_id")
        if log_channel_id:
            log_channel = ctx.guild.get_channel(log_channel_id)
            if log_channel:
                await log_channel.send(embed=fancy_embed("Staff Ping", content))

        await ctx.send(content)

    # -------------------------
    # mhsping: higher staff ping
    # -------------------------
    @commands.command()
    async def mhsping(self, ctx):
        role_id = DATA.get("higher_staff_ping_role_id")
        if not role_id:
            await ctx.send("❌ Higher staff ping role not set in config.")
            return
        role = ctx.guild.get_role(role_id)
        if not role:
            await ctx.send("❌ Higher staff ping role not found in guild.")
            return

        # Delete command message
        await ctx.message.delete()

        content = f"{role.mention} Higher staff ping!"
        if ctx.message.reference:
            ref_msg = ctx.message.reference.resolved
            if ref_msg:
                content += f"\n**Original message by {ref_msg.author}:** {ref_msg.content}"

        # Send to log channel if set
        log_channel_id = DATA.get("log_channel_id")
        if log_channel_id:
            log_channel = ctx.guild.get_channel(log_channel_id)
            if log_channel:
                await log_channel.send(embed=fancy_embed("Higher Staff Ping", content))

        await ctx.send(content)

# -----------------------------
# Cog setup
# -----------------------------
def setup(bot):
    bot.add_cog(Ping(bot))
