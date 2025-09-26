# main.py
import os
import io
import threading
import logging
import random
from flask import Flask
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

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

# ---------- Discord bot ----------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Fonts ----------
try:
    FONT_REG = ImageFont.truetype("arial.ttf", 16)
    FONT_BOLD = ImageFont.truetype("arialbd.ttf", 16)
    FONT_SMALL = ImageFont.truetype("arial.ttf", 12)
except Exception:
    FONT_REG = ImageFont.load_default()
    FONT_BOLD = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()

# ---------- Helpers ----------
def circle_avatar(img, size=40):
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0,0,size,size), fill=255)
    out = Image.new("RGBA", (size, size), (0,0,0,0))
    out.paste(img.resize((size,size)), (0,0), mask)
    return out

def wrap_text(draw, text, font, max_width):
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

async def fetch_avatar_and_name(ctx, username):
    # Check if it's a mention
    if username.startswith("<@") and username.endswith(">"):
        try:
            user_id = int(username.replace("<@!", "").replace("<@", "").replace(">", ""))
            member = ctx.guild.get_member(user_id)
            if member is None:
                member = await bot.fetch_user(user_id)
            display_name = member.display_name if hasattr(member, "display_name") else member.name
            avatar_bytes = await member.avatar.read()
            img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            return display_name, circle_avatar(img, 40)
        except:
            pass
    # Fallback: just use the username string with initials
    color = random.choice([(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255)])
    img = Image.new("RGBA", (40,40), color)
    draw = ImageDraw.Draw(img)
    initials = "".join([x[0].upper() for x in username if x.isalnum()][:2])
    w,h = draw.textsize(initials, font=FONT_BOLD)
    draw.text(((40-w)/2,(40-h)/2), initials, font=FONT_BOLD, fill=(255,255,255))
    return username, circle_avatar(img, 40)

async def generate_mobile_prank(ctx, messages):
    WIDTH = 420
    BG = (54, 57, 63)
    TEXT_COLOR = (220, 221, 222)
    TIMESTAMP_COLOR = (114, 118, 125)
    AVATAR_SIZE = 40
    PADDING = 10
    LINE_SPACING = 4
    BUBBLE_COLOR = (64, 68, 75)
    MAX_TEXT_WIDTH = WIDTH - AVATAR_SIZE - 4*PADDING

    # Avatars and real usernames
    avatars = {}
    display_names = {}
    for m in messages:
        display_name, avatar = await fetch_avatar_and_name(ctx, m["username"])
        avatars[m["username"]] = avatar
        display_names[m["username"]] = display_name

    # Estimate height
    dummy = Image.new("RGB", (WIDTH,100))
    draw_tmp = ImageDraw.Draw(dummy)
    est_height = PADDING
    for m in messages:
        est_height += FONT_BOLD.size + 2
        lines = wrap_text(draw_tmp, m["message"], FONT_REG, MAX_TEXT_WIDTH)
        est_height += len(lines)*(FONT_REG.size+2) + FONT_SMALL.size + LINE_SPACING*2
        est_height += PADDING
    est_height += PADDING

    # Draw canvas
    img = Image.new("RGBA", (WIDTH, est_height), BG)
    draw = ImageDraw.Draw(img)
    y = PADDING
    for m in messages:
        x_avatar = PADDING
        x_text = x_avatar + AVATAR_SIZE + PADDING

        # Avatar
        img.paste(avatars[m["username"]], (x_avatar, y), avatars[m["username"]])

        # Username
        draw.text((x_text, y), display_names[m["username"]], font=FONT_BOLD, fill=TEXT_COLOR)
        y += FONT_BOLD.size + 2

        # Message bubble
        lines = wrap_text(draw, m["message"], FONT_REG, MAX_TEXT_WIDTH)
        bubble_height = len(lines)*(FONT_REG.size+2) + FONT_SMALL.size + 8
        draw.rounded_rectangle([x_text-6, y-4, WIDTH-PADDING, y+bubble_height], radius=10, fill=BUBBLE_COLOR)

        line_y = y
        for line in lines:
            draw.text((x_text, line_y), line, font=FONT_REG, fill=TEXT_COLOR)
            line_y += FONT_REG.size + 2

        # Timestamp below bubble
        ts_w = draw.textlength(m["time"], font=FONT_SMALL)
        draw.text((x_text + (WIDTH-x_text-PADDING-ts_w), line_y), m["time"], font=FONT_SMALL, fill=TIMESTAMP_COLOR)

        y += bubble_height + LINE_SPACING

    buf = io.BytesIO()
    img = img.crop((0,0,WIDTH,y+PADDING))
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---------- !prank command ----------
@bot.command(name="prank")
async def prank(ctx, *, content: str):
    try:
        messages_raw = content.split(";")
        prank_messages = []
        for raw in messages_raw:
            raw = raw.strip()
            if not raw:
                continue
            parts = raw.split(" ")
            if len(parts) < 2:
                await ctx.send(f"Invalid format: {raw}")
                return
            msg_time = parts[-1].strip()
            msg_text = " ".join(parts[1:-1]).strip()
            username = parts[0].strip()
            prank_messages.append({
                "username": username,
                "message": msg_text,
                "time": msg_time
            })

        buf = await generate_mobile_prank(ctx, prank_messages)
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
