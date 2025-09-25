# Is/cache.py
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
def fancy_embed(title, description, color=Colour.purple()):
    embed = Embed(title=title, description=description, colour=color)
    return embed

# -----------------------------
# Cache Cog
# -----------------------------
class Cache(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Listen to message deletions
        bot.add_listener(self.on_message_delete)

    # -------------------------
    # Log deleted messages
    # -------------------------
    async def on_message_delete(self, message):
        if message.author.bot:
            return
        # Store in cache
        msg_id = str(message.id)
        DATA["cache"][msg_id] = {
            "author": str(message.author),
            "content": message.content,
            "attachments": [a.url for a in message.attachments],
            "channel": str(message.channel),
            "timestamp": message.created_at.isoformat(),
            "reply_to": str(message.reference) if message.reference else None,
            "deleter": None  # Could be updated if a mod deletes via command
        }
        # Keep last 100 items
        if len(DATA["cache"]) > 100:
            first_key = list(DATA["cache"].keys())[0]
            DATA["cache"].pop(first_key)
        await persist()

    # -------------------------
    # !mrcache command
    # -------------------------
    @commands.command()
    async def mrcache(self, ctx, limit: int = 10):
        # Check if user has access
        if not any(role.id in DATA.get("rcache_roles", []) for role in ctx.author.roles):
            await ctx.send("❌ You do not have permission to access the cache.")
            return

        cache_items = list(DATA.get("cache", {}).values())[-limit:]
        if not cache_items:
            await ctx.send("📦 No cached messages found.")
            return

        description = ""
        for item in cache_items:
            desc = f"**Author:** {item['author']}\n"
            desc += f"**Content:** {item['content'] or 'None'}\n"
            if item["attachments"]:
                desc += f"**Attachments:** {', '.join(item['attachments'])}\n"
            if item["reply_to"]:
                desc += f"**Reply To:** {item['reply_to']}\n"
            desc += f"**Channel:** {item['channel']}\n"
            desc += f"**Timestamp:** {item['timestamp']}\n"
            desc += "─" * 20 + "\n"
            description += desc

        await ctx.send(embed=fancy_embed(f"📦 Last {limit} Cached Messages", description))

# -----------------------------
# Cog setup
# -----------------------------
def setup(bot):
    bot.add_cog(Cache(bot))
