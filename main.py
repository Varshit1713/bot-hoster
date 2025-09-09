import os
from discord.ext import commands

intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Load cogs
bot.load_extension("timetrack")  # Add "rmute" later if needed

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

bot.run(os.getenv("DISCORD_TOKEN"))
