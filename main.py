import os
from discord.ext import commands

BOT_TOKEN = os.getenv("DISCORD_TOKEN")

bot = commands.Bot(command_prefix="!", intents=...)  # your bot code

bot.run(BOT_TOKEN)
