import os
import discord
from discord.ext import commands
import asyncio

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

async def main():
    async with bot:
        # Load your cogs asynchronously
        await bot.load_extension("timetrack")
        await bot.load_extension("rmute")
        # Start the bot
        await bot.start(os.getenv("DISCORD_TOKEN"))

# Run the async main function
asyncio.run(main())
