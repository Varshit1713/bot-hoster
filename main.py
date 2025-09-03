import os
import discord
from discord.ext import commands
from aiohttp import web  # <-- For uptime ping
import asyncio

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("DISCORD_TOKEN")  # Make sure this is set in Render environment variables
WEB_PORT = int(os.getenv("PORT", 10000))  # Render automatically sets PORT
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

# -------------------
# Web server for uptime
# -------------------
async def handle(request):
    return web.Response(text="Bot is alive!")

async def start_web_server():
    app = web.Application()
    app.add_routes([web.get('/', handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', WEB_PORT)
    await site.start()
    print(f"Web server running on port {WEB_PORT}")

# -------------------
# Run bot + web server
# -------------------
async def main():
    # Start web server
    asyncio.create_task(start_web_server())
    # Start bot
    await bot.start(BOT_TOKEN)

asyncio.run(main())
