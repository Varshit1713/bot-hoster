import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ COGS LOADING ------------------
async def load_cogs():
    # Load cogs asynchronously
    await bot.load_extension("timetrack")
    await bot.load_extension("rmute")

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    # Sync slash commands globally
    await bot.tree.sync()
    print("🌐 Slash commands synced")

# ------------------ START BOT ------------------
async def main():
    async with bot:
        await load_cogs()
        await bot.start(os.getenv("DISCORD_TOKEN"))

# Run the bot
import asyncio
asyncio.run(main())
