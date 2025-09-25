import os
import logging
from io import BytesIO
from flask import Flask
import threading
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
import aiohttp

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("showbot")

# ---------- Flask keep-alive ----------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is alive!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.start()

# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Events ----------
@bot.event
async def on_ready():
    LOG.info(f"READY: {bot.user} (id={bot.user.id})")
    LOG.info("Guilds: %s", [(g.id, g.name) for g in bot.guilds])

# ---------- Utility: build image ----------
def messages_to_image(messages):
    width, height = 800, 30 * len(messages) + 50
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except:
        font = ImageFont.load_default()

    y = 10
    for m in messages:
        text = f"{m.author.display_name}: {m.content}"
        draw.text((10, y), text, fill="black", font=font)
        y += 30

    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---------- Command ----------
@bot.command()
async def show(ctx, channel_id: int = None, number: int = 10):
    LOG.info("!show called by %s with channel_id=%s number=%s", ctx.author, channel_id, number)

    number = min(max(1, number), 50)  # clamp between 1 and 50

    # resolve channel
    if channel_id is None:
        channel = ctx.channel
        LOG.info("Using current channel: %s", channel.id)
    else:
        channel = bot.get_channel(channel_id)
        LOG.info("get_channel returned: %s", channel)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
                LOG.info("fetch_channel returned: %s", channel)
            except Exception as e:
                LOG.exception("Failed to fetch channel")
                await ctx.send(f"Could not fetch channel: {e}")
                return

    # fetch messages
    try:
        messages = [m async for m in channel.history(limit=number)]
        messages.reverse()
        LOG.info("Fetched %s messages", len(messages))
    except Exception as e:
        LOG.exception("Failed to fetch history")
        await ctx.send(f"Error reading history: {e}")
        return

    if not messages:
        await ctx.send("No messages found.")
        return

    # send image
    try:
        buf = messages_to_image(messages)
        await ctx.send(file=discord.File(buf, "messages.png"))
    except Exception as e:
        LOG.exception("Failed to send image")
        await ctx.send(f"Error creating image: {e}")

# ---------- Main ----------
if __name__ == "__main__":
    keep_alive()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("No DISCORD_TOKEN set in environment")
    bot.run(token)
