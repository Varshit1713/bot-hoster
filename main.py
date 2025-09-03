import os
from discord.ext import commands

BOT_TOKEN = os.getenv("DISCORD_TOKEN")  # pulls from Render environment

intents = commands.Intents.all()  # or whatever intents you want
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

bot.run(BOT_TOKEN)
