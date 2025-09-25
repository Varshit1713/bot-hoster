# main.py
import os
import io
import asyncio
import logging
import threading
from io import BytesIO
from typing import Optional, List

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
import aiohttp
from flask import Flask, Response

# -----------------------
# Settings / constants
# -----------------------
LOG = logging.getLogger("discord_bot")
logging.basicConfig(level=logging.INFO)

# Visual constants
IMG_WIDTH = 920
AVATAR_SIZE = 48
PADDING = 16
BUBBLE_PADDING = 12
BUBBLE_RADIUS = 12
BACKGROUND_COLOR = (54, 57, 63)       # Discord-like background
BUBBLE_COLOR = (64, 68, 75)           # Bubble fill
USERNAME_COLOR = (114, 137, 218)      # discord-like username color
TEXT_COLOR = (220, 221, 222)          # message text color
MAX_MESSAGES = 100                    # safety cap

# Font: prefer a TTF in ./fonts/, otherwise fallback to default
FONT_SIZE = 16
FONT = None
try:
    FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "Inter-Regular.ttf")
    FONT = ImageFont.truetype(FONT_PATH, FONT_SIZE)
except Exception:
    LOG.info("Custom TTF not found — falling back to default PIL font.")
    FONT = ImageFont.load_default()

# -----------------------
# Discord bot setup
# -----------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------
# Utility drawing helpers
# -----------------------
def text_size(draw: ImageDraw.Draw, text: str, font: ImageFont.ImageFont):
    """Return (width, height) for a text chunk."""
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    """Wrap text by pixel width (preserves paragraphs)."""
    lines = []
    for paragraph in text.split("\n"):
        if paragraph == "":
            lines.append("")  # preserve blank lines
            continue
        words = paragraph.split(" ")
        line = ""
        for word in words:
            test = (line + " " + word).strip()
            w, _ = text_size(draw, test, font)
            if w <= max_width:
                line = test
            else:
                if line:
                    lines.append(line)
                # if single word longer than max_width, split it character-wise
                if text_size(draw, word, font)[0] > max_width:
                    cur = ""
                    for ch in word:
                        test2 = cur + ch
                        if text_size(draw, test2, font)[0] <= max_width:
                            cur = test2
                        else:
                            lines.append(cur)
                            cur = ch
                    if cur:
                        line = cur
                    else:
                        line = ""
                else:
                    line = word
        if line:
            lines.append(line)
    return lines

async def fetch_avatar(session: aiohttp.ClientSession, url: Optional[str], size: int) -> Image.Image:
    """Fetch avatar image (RGBA). Return a square avatar or a neutral placeholder on failure."""
    placeholder = Image.new("RGBA", (size, size), (100, 100, 100, 255))
    if not url:
        return placeholder
    try:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(BytesIO(data)).convert("RGBA")
                img = img.resize((size, size), Image.LANCZOS)
                return img
            else:
                LOG.warning(f"Avatar fetch returned status {resp.status} for {url}")
    except Exception:
        LOG.exception("Failed to fetch avatar")
    return placeholder

def circle_mask(img: Image.Image, size: int) -> Image.Image:
    """Return circular-cropped RGBA image sized (size,size)."""
    img = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out

async def messages_to_image(messages: List[discord.Message]) -> BytesIO:
    """Render a list of messages to an image and return a BytesIO PNG buffer.
       messages should be ordered oldest -> newest.
    """
    if not messages:
        img = Image.new("RGB", (600, 100), BACKGROUND_COLOR)
        draw = ImageDraw.Draw(img)
        draw.text((20, 20), "No messages to display.", font=FONT, fill=TEXT_COLOR)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    max_bubble_w = IMG_WIDTH - AVATAR_SIZE - PADDING * 4

    # Create temporary drawer for measuring text
    temp_img = Image.new("RGB", (10, 10))
    temp_draw = ImageDraw.Draw(temp_img)

    # Collect avatar URLs (string) and content
    avatar_urls = []
    contents = []
    author_names = []
    for m in messages:
        # content plus attachments/embeds info
        text = m.content or ""
        if m.attachments:
            att_notes = " ".join([f"📎 {a.filename}" for a in m.attachments])
            text = (text + "\n" + att_notes) if text else att_notes
        if m.embeds:
            embed_notes = " ".join([f"[embed] {e.title}" for e in m.embeds if e.title])
            if embed_notes:
                text = (text + "\n" + embed_notes) if text else embed_notes
        contents.append(text)
        author_names.append(m.author.display_name if hasattr(m.author, "display_name") else str(m.author))
        try:
            avatar_urls.append(str(m.author.display_avatar.url))
        except Exception:
            avatar_urls.append(None)

    # Fetch avatars concurrently
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_avatar(session, url, AVATAR_SIZE) for url in avatar_urls]
        raw_avatars = await asyncio.gather(*tasks, return_exceptions=False)

    # Build message boxes with measured sizes
    boxes = []
    for name, text, raw_avatar in zip(author_names, contents, raw_avatars):
        avatar_circ = circle_mask(raw_avatar, AVATAR_SIZE)
        # determine wrapped lines
        free_width = max_bubble_w - BUBBLE_PADDING * 2
        lines = wrap_text(temp_draw, text if text else " ", FONT, free_width)  # ensure at least one line
        # compute widths
        widths = []
        for line in lines:
            w, h = text_size(temp_draw, line, FONT)
            widths.append(w)
        uname_w, uname_h = text_size(temp_draw, name, FONT)
        bubble_w = max(uname_w, max(widths) if widths else 0) + BUBBLE_PADDING * 2
        bubble_w = int(min(bubble_w, max_bubble_w))
        # line height
        _, line_h = text_size(temp_draw, "Ay", FONT)
        bubble_h = int(uname_h + 4 + len(lines) * line_h + BUBBLE_PADDING * 2)
        boxes.append({
            "name": name,
            "lines": lines,
            "bubble_w": bubble_w,
            "bubble_h": bubble_h,
            "avatar": avatar_circ,
            "line_h": line_h,
            "uname_h": uname_h,
        })

    # total height
    spacing = 12
    total_h = PADDING + sum(b["bubble_h"] for b in boxes) + spacing * (len(boxes) - 1) + PADDING
    img = Image.new("RGB", (IMG_WIDTH, max(total_h, 120)), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    y = PADDING
    for b in boxes:
        # paste avatar
        img.paste(b["avatar"], (PADDING, y), b["avatar"])
        bx = PADDING + AVATAR_SIZE + PADDING
        by = y
        # bubble rect
        draw.rounded_rectangle([bx, by, bx + b["bubble_w"], by + b["bubble_h"]],
                               radius=BUBBLE_RADIUS, fill=BUBBLE_COLOR)
        # username
        draw.text((bx + 8, by + 6), b["name"], font=FONT, fill=USERNAME_COLOR)
        # message lines
        text_x = bx + 8
        text_y = by + 6 + b["uname_h"] + 4
        for line in b["lines"]:
            draw.text((text_x, text_y), line, font=FONT, fill=TEXT_COLOR)
            text_y += b["line_h"]
        y += b["bubble_h"] + spacing

    # final buffer
    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out

# -----------------------
# Bot command
# -----------------------
@bot.command(name="show")
async def show_cmd(ctx: commands.Context, channel_id: Optional[int] = None, number: int = 10):
    """
    Usage:
      !show                -> latest 10 messages in this channel
      !show <channel_id>   -> latest 10 messages in that channel
      !show <channel_id> N -> latest N messages (N capped)
    """
    try:
        # normalize number and cap
        if number < 1:
            await ctx.send("Number must be >= 1.")
            return
        number = min(number, MAX_MESSAGES)

        # resolve channel
        if channel_id is None:
            channel = ctx.channel
        else:
            channel = bot.get_channel(channel_id)
            if channel is None:
                # try fetch (works even if not cached)
                try:
                    channel = await bot.fetch_channel(channel_id)
                except discord.NotFound:
                    await ctx.send("Channel not found.")
                    return
                except discord.Forbidden:
                    await ctx.send("I don't have permission to access that channel.")
                    return
                except Exception as e:
                    LOG.exception("fetch_channel failed")
                    await ctx.send("Could not access that channel.")
                    return

        # permissions check
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel, discord.GroupChannel)):
            await ctx.send("That channel type is not supported.")
            return

        # try to read history
        try:
            messages = [m async for m in channel.history(limit=number)]
        except discord.Forbidden:
            await ctx.send("I don't have permission to read message history in that channel.")
            return
        except Exception:
            LOG.exception("Failed to read channel history")
            await ctx.send("Failed to read channel history.")
            return

        # order oldest -> newest
        messages = list(reversed(messages))

        # render
        await ctx.trigger_typing()
        img_buf = await messages_to_image(messages)

        # send
        await ctx.send(file=discord.File(fp=img_buf, filename="messages.png"))

    except Exception:
        LOG.exception("Unhandled error in !show")
        await ctx.send("An error occurred while processing the command.")

# provide a friendly error message when user types wrong param types / too many args
@show_cmd.error
async def show_error(ctx, error):
    if isinstance(error, commands.BadArgument):
        await ctx.send("Bad argument — make sure channel ID and number are integers.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing arguments. Usage: `!show [channel_id] [number]`")
    else:
        LOG.exception("Command error")
        await ctx.send("Command error: " + str(error))

# -----------------------
# Flask keep-alive
# -----------------------
app = Flask(__name__)

@app.route("/")
def home():
    return Response("Bot is running.", status=200)

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

# -----------------------
# Start the bot
# -----------------------
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        LOG.error("DISCORD_TOKEN environment variable not set.")
        raise SystemExit("Set DISCORD_TOKEN environment variable and restart.")
    keep_alive()
    bot.run(TOKEN)
