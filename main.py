import os
import discord
from discord.ext import commands

# Get your bot token from environment variables
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

# Set intents (full access)
intents = discord.Intents.all()

# Create the bot
bot = commands.Bot(command_prefix="!", intents=intents)

# Example ready event
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

bot.run(BOT_TOKEN)
