# Is/dm_control.py
from discord.ext import commands
from discord import Embed, Colour
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
def fancy_embed(title, description, color=Colour.green()):
    embed = Embed(title=title, description=description, colour=color)
    return embed

# -----------------------------
# DM Control Cog
# -----------------------------
class DMControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -------------------------
    # !mrdm command
    # -------------------------
    @commands.command()
    async def mrdm(self, ctx):
        """Toggle opt-out from bot DMs."""
        uid = str(ctx.author.id)
        if uid in DATA.get("rdm_users", []):
            DATA["rdm_users"].remove(uid)
            await ctx.send(embed=fancy_embed("✅ DM Opt-In", "You will now receive bot DMs."))
        else:
            DATA["rdm_users"].append(uid)
            await ctx.send(embed=fancy_embed("✅ DM Opt-Out", "You will no longer receive bot DMs."))
        await persist()

# -----------------------------
# Cog setup
# -----------------------------
def setup(bot):
    bot.add_cog(DMControl(bot))
