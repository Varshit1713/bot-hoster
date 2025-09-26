# main.py
import os
import io
import threading
import logging
import random
import datetime
from flask import Flask
from discord.ext import commands
import discord
from PIL import Image, ImageDraw, ImageFont
import aiohttp

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("discord_bot")

# ---------- Keep-alive ----------
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot alive!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    threading.Thread(target=run_flask, daemon=True).start()

# ---------- Discord bot setup ----------
intents = discord.Intents.all()
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
def circle_avatar(img, size=48):
    mask = Image.new("L", (size, size), 0)
    dr = ImageDraw.Draw(mask)
    dr.ellipse((0,0,size,size), fill=255)
    out = Image.new("RGBA", (size, size), (0,0,0,0))
    out.paste(img.resize((size,size)), (0,0), mask)
    return out

def wrap_text(draw, text, font, max_width):
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

# ---------- Prank image generator ----------
def generate_prank_image(messages):
    WIDTH = 550
    BG = (54, 57, 63)
    TEXT_COLOR = (220, 221, 222)
    TIMESTAMP_COLOR = (114, 118, 125)
    AVATAR_SIZE = 48
    PADDING = 15
    LINE_SPACING = 5
    BUBBLE_COLOR = (64, 68, 75)
    MAX_TEXT_WIDTH = WIDTH - (AVATAR_SIZE + 3*PADDING)

    # generate avatars
    avatars = {}
    for m in messages:
        if m["username"] not in avatars:
            color = random.choice([(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255)])
            img = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), color)
            avatars[m["username"]] = circle_avatar(img, AVATAR_SIZE)

    # estimate height
    dummy = Image.new("RGB", (WIDTH, 100))
    draw_tmp = ImageDraw.Draw(dummy)
    est_height = PADDING
    last_author = None
    for m in messages:
        if m["username"] != last_author:
            est_height += FONT_BOLD.size + 4
        lines = wrap_text(draw_tmp, m["message"], FONT_REG, MAX_TEXT_WIDTH)
        est_height += len(lines)*(FONT_REG.size+2) + LINE_SPACING
        last_author = m["username"]
    est_height += PADDING

    # draw canvas
    img = Image.new("RGBA", (WIDTH, max(est_height,200)), BG)
    draw = ImageDraw.Draw(img)
    y = PADDING
    last_author = None
    for m in messages:
        show_avatar = (m["username"] != last_author)
        x_text = PADDING + (AVATAR_SIZE + PADDING if show_avatar else 0)

        if show_avatar:
            img.paste(avatars[m["username"]], (PADDING, y), avatars[m["username"]])

        if show_avatar:
            draw.text((x_text, y), m["username"], font=FONT_BOLD, fill=TEXT_COLOR)
            ts_w = draw.textlength(m["time"], font=FONT_SMALL)
            draw.text((WIDTH-PADDING-ts_w, y+2), m["time"], font=FONT_SMALL, fill=TIMESTAMP_COLOR)
            y += FONT_BOLD.size + 2

        # message bubble
        lines = wrap_text(draw, m["message"], FONT_REG, MAX_TEXT_WIDTH)
        if lines:
            bubble_height = len(lines)*(FONT_REG.size+2)+8
            draw.rounded_rectangle([x_text-6, y-2, WIDTH-PADDING, y+bubble_height+y-2], radius=6, fill=BUBBLE_COLOR)
            for line in lines:
                draw.text((x_text, y), line, font=FONT_REG, fill=TEXT_COLOR)
                y += FONT_REG.size + 2
            y += LINE_SPACING
        last_author = m["username"]

    buf = io.BytesIO()
    img = img.crop((0,0,WIDTH, y+PADDING))
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---------- !prank command ----------
@bot.command(name="prank")
async def prank(ctx, *, content: str):
    """
    Multi-message prank generator.
    Usage:
    !prank John|Hey 5:42am; Jane|OMG 5:44am; John|LOL 5:45am
    """
    try:
        messages_raw = content.split(";")
        prank_messages = []
        for raw in messages_raw:
            raw = raw.strip()
            if not raw:
                continue
            if "|" not in raw:
                await ctx.send(f"Invalid format in segment: {raw}")
                return
            username_part, rest = raw.split("|",1)
            rest = rest.strip()
            if " " not in rest:
                await ctx.send(f"Missing time in segment: {raw}")
                return
            msg_text, msg_time = rest.rsplit(" ",1)
            prank_messages.append({
                "username": username_part.strip(),
                "message": msg_text.strip(),
                "time": msg_time.strip()
            })
        buf = generate_prank_image(prank_messages)
        await ctx.send(file=discord.File(buf, "prank.png"))
    except Exception as e:
        LOG.exception("Error in prank command")
        await ctx.send(f"An error occurred: {e}")

# ---------- Main ----------
if __name__ == "__main__":
    keep_alive()
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set")
    bot.run(TOKEN)
