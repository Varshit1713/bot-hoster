# Is/admin.py
from discord.ext import commands
from discord import Embed, Colour
import json
import os

# -----------------------------
# Data file path
# -----------------------------
DATA_FILE = "bot_data.json"

# Load or initialize DATA
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
# Helper function to save DATA
# -----------------------------
async def persist():
    with open(DATA_FILE, "w") as f:
        json.dump(DATA, f, indent=4, default=str)

# -----------------------------
# Admin Cog
# -----------------------------
class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -------------------------
    # Reset timetrack data
    # -------------------------
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def mresettimetrack(self, ctx):
        DATA["users"] = {}
        await persist()
        embed = Embed(
            title="✅ Timetrack Reset",
            description="All timetrack data has been cleared.",
            colour=Colour.green()
        )
        await ctx.send(embed=embed)

    # -------------------------
    # Reset cache
    # -------------------------
    @commands.command()
    @commands.has_permissions(administrator=True)
    async def mresetcache(self, ctx):
        DATA["cache"] = {}
        await persist()
        embed = Embed(
            title="✅ Cache Reset",
            description="All cached messages and data have been cleared.",
            colour=Colour.green()
        )
        await ctx.send(embed=embed)

    # -------------------------
    # Show bot configuration
    # -------------------------
    @commands.command()
    async def mshowconfig(self, ctx):
        embed = Embed(
            title="⚙️ Bot Configuration",
            colour=Colour.blue()
        )
        config_keys = [
            "mute_role_id",
            "timetrack_channel_id",
            "log_channel_id",
            "staff_ping_role_id",
            "higher_staff_ping_role_id",
            "rcache_roles"
        ]
        for key in config_keys:
            embed.add_field(name=key, value=str(DATA.get(key, "Not set")), inline=False)
        await ctx.send(embed=embed)

# -----------------------------
# Cog setup function
# -----------------------------
def setup(bot):
    bot.add_cog(Admin(bot))
