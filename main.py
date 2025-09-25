import discord
from discord.ext import commands
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import requests
import os
import threading
from flask import Flask

# ---- Discord Bot Setup ----
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Load font
FONT = ImageFont.load_default()

def draw_message(draw, x, y, avatar, username, content, width):
    avatar_size = 40
    img_avatar = avatar.resize((avatar_size, avatar_size))
    img.paste(img_avatar, (x, y))

    # Username
    draw.text((x + avatar_size + 10, y), username, font=FONT, fill=(114, 137, 218))

    # Message
    draw.text((x + avatar_size + 10, y + 18), content, font=FONT, fill=(220, 221, 222))


def messages_to_image(messages):
    width = 800
    line_height = 60
    padding = 20
    height = padding * 2 + line_height * len(messages)

    global img
    img = Image.new("RGB", (width, height), color=(54, 57, 63))
    draw = ImageDraw.Draw(img)

    y = padding
    for msg in messages:
        # Fetch avatar
        try:
            response = requests.get(str(msg.author.avatar.url))
            avatar = Image.open(BytesIO(response.content)).convert("RGB")
        except:
            avatar = Image.new("RGB", (40, 40), (100, 100, 100))

        username = msg.author.display_name
        content = msg.content if msg.content else "<embed/attachment>"
        draw_message(draw, 20, y, avatar, username, content, width)
        y += line_height

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


@bot.command()
async def show(ctx, channel_id: int, number: int = 10):
    channel = bot.get_channel(channel_id)
    if not channel:
        await ctx.send("Channel not found.")
        return

    messages = await channel.history(limit=number).flatten()
    messages.reverse()
    img_buffer = messages_to_image(messages)

    await ctx.send(file=discord.File(fp=img_buffer, filename="messages.png"))


# ---- Flask Keep-Alive ----
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))  # Default 8080 if no port env set
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_flask)
    t.start()


if __name__ == "__main__":
    keep_alive()
    bot.run(os.getenv("DISCORD_TOKEN"))  # Store your token in an environment variable
