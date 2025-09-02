import os
from discord.ext import commands
import discord

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("DISCORD_TOKEN")  # token stored in Render's Environment
COMMAND_PREFIX = "!"
# ----------------------------

# Define intents properly
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

# Create bot
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)

# Example: simple ready event
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready!")

# Example: simple ping command
@bot.command()
async def ping(ctx):
    await ctx.send("Pong!")

# Run bot
bot.run(BOT_TOKEN)
