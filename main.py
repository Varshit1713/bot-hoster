# main.py
import os
import io
import threading
import logging
from typing import List, Optional
from flask import Flask
from discord.ext import commands
import discord
from PIL import Image, ImageDraw, ImageFont
import aiohttp

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("discord_realistic_full")

# ---------- Keep-alive (Flask) ----------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot alive!"

def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

def keep_alive():
    threading.Thread(target=run_flask, daemon=True).start()

# ---------- Discord bot setup ----------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Fonts ----------
try:
    FONT_REG = ImageFont.truetype("arial.ttf", 18)
    FONT_BOLD = ImageFont.truetype("arialbd.ttf", 18)
    FONT_SMALL = ImageFont.truetype("arial.ttf", 15)
except Exception:
    FONT_REG = ImageFont.load_default()
    FONT_BOLD = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()

# ---------- Helpers ----------
async def fetch_image(session: aiohttp.ClientSession, url: str, size: Optional[tuple] = None) -> Optional[Image.Image]:
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data)).convert("RGBA")
                if size:
                    img.thumbnail(size, Image.LANCZOS)
                return img
    except Exception:
        LOG.exception("fetch_image failed for %s", url)
    return None

def circle_avatar(img: Image.Image, size: int = 48) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    dr = ImageDraw.Draw(mask)
    dr.ellipse((0,0,size,size), fill=255)
    out = Image.new("RGBA", (size, size), (0,0,0,0))
    out.paste(img.resize((size,size)), (0,0), mask)
    return out

def wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list:
    if not text:
        return [""]
    words = text.split(" ")
    lines = []
    cur = ""
    for word in words:
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines

def truncate(text: str, max_len: int = 220) -> str:
    return text if len(text) <= max_len else text[:max_len-3] + "..."

# ---------- Renderer (realistic with bubbles, embeds, reactions) ----------
async def messages_to_image(messages: List[discord.Message]) -> io.BytesIO:
    WIDTH = 550
    BG = (54, 57, 63)
    TEXT_COLOR = (220, 221, 222)
    TIMESTAMP_COLOR = (114, 118, 125)
    AVATAR_SIZE = 48
    PADDING = 15
    LINE_SPACING = 5
    MAX_TEXT_WIDTH = WIDTH - (AVATAR_SIZE + 3*PADDING)
    BUBBLE_COLOR = (64, 68, 75)
    EMBED_BG = (47, 49, 54)
    EMBED_TITLE_COLOR = (0, 162, 255)
    REACTION_BG = (60, 63, 70)
    REACTION_TEXT = (220, 221, 222)

    # --- Preload avatars ---
    async with aiohttp.ClientSession() as session:
        avatar_cache = {}
        for m in messages:
            uid = m.author.id
            if uid not in avatar_cache:
                try:
                    url = str(m.author.display_avatar.url)
                    avatar = await fetch_image(session, url, (AVATAR_SIZE, AVATAR_SIZE))
                    avatar_cache[uid] = circle_avatar(avatar, AVATAR_SIZE) if avatar else Image.new("RGBA",(AVATAR_SIZE,AVATAR_SIZE),(100,100,100))
                except Exception:
                    avatar_cache[uid] = Image.new("RGBA",(AVATAR_SIZE,AVATAR_SIZE),(100,100,100))

    # --- Estimate canvas height ---
    dummy = Image.new("RGB", (WIDTH, 100))
    draw_tmp = ImageDraw.Draw(dummy)
    est_height = PADDING
    last_author = None
    for m in messages:
        if m.author.id != last_author:
            est_height += FONT_BOLD.size + 4
        lines = wrap_text(draw_tmp, truncate(m.content), FONT_REG, MAX_TEXT_WIDTH)
        est_height += len(lines)*(FONT_REG.size + 2) + LINE_SPACING
        last_author = m.author.id
    est_height += PADDING

    # --- Draw canvas ---
    img = Image.new("RGBA", (WIDTH, max(est_height, 200)), BG)
    draw = ImageDraw.Draw(img)

    y = PADDING
    last_author = None
    for m in messages:
        show_avatar = (m.author.id != last_author)
        x_text = PADDING + (AVATAR_SIZE + PADDING if show_avatar else 0)

        # avatar
        if show_avatar:
            avatar = avatar_cache.get(m.author.id)
            if avatar:
                img.paste(avatar, (PADDING, y), avatar)

        # username + timestamp
        if show_avatar:
            name_color = TEXT_COLOR
            if hasattr(m.author, "color") and m.author.color.value != 0:
                try:
                    name_color = m.author.color.to_rgb()
                except Exception:
                    pass
            draw.text((x_text, y), m.author.display_name, font=FONT_BOLD, fill=name_color)
            ts = m.created_at.strftime("%I:%M %p").lstrip("0")
            ts_w = draw.textlength(ts, font=FONT_SMALL)
            draw.text((WIDTH - PADDING - ts_w, y + 2), ts, font=FONT_SMALL, fill=TIMESTAMP_COLOR)
            y += FONT_BOLD.size + 2

        # draw message bubble
        lines = wrap_text(draw, truncate(m.content), FONT_REG, MAX_TEXT_WIDTH)
        if lines:
            bubble_height = len(lines)*(FONT_REG.size+2)+8
            draw.rounded_rectangle([x_text-6, y-2, WIDTH-PADDING, y+bubble_height], radius=6, fill=BUBBLE_COLOR)
            for line in lines:
                draw.text((x_text, y), line, font=FONT_REG, fill=TEXT_COLOR)
                y += FONT_REG.size + 2
            y += LINE_SPACING

        last_author = m.author.id

    # --- Crop final image ---
    buf = io.BytesIO()
    img = img.crop((0, 0, WIDTH, y + PADDING))
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---------- Command ----------
@bot.command(name="show")
async def show_cmd(ctx: commands.Context, channel_id: Optional[int] = None, number: int = 10):
    try:
        number = max(1, min(number, 50))
        if channel_id is None:
            channel = ctx.channel
        else:
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except discord.NotFound:
                    await ctx.send("Channel not found.")
                    return
                except discord.Forbidden:
                    await ctx.send("I don't have access to that channel.")
                    return

        try:
            messages = [m async for m in channel.history(limit=number)]
        except discord.Forbidden:
            await ctx.send("I don't have permission to read message history in that channel.")
            return
        messages.reverse()

        async with ctx.typing():
            buf = await messages_to_image(messages)
        await ctx.send(file=discord.File(fp=buf, filename="discord_chat.png"))

    except Exception as e:
        LOG.exception("Error in show command")
        await ctx.send(f"An error occurred: {e}")

# ---------- Main ----------
if __name__ == "__main__":
    keep_alive()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set")
    bot.run(TOKEN)
