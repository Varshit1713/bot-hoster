import os
import discord
from discord.ext import commands

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("DISCORD_TOKEN")  # Make sure this is set in Render environment variables
# ----------------------------

# Set intents
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

# No prefix commands
bot = commands.Bot(command_prefix="", intents=intents)

@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}!")

# Example command without prefix
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

bot.run(BOT_TOKEN)
