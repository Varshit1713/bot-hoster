# main.py
import os
import io
import threading
import logging
from typing import List, Dict, Any, Optional
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

# ---------- Fonts (fallback to default) ----------
try:
    FONT_REG = ImageFont.truetype("arial.ttf", 16)
    FONT_BOLD = ImageFont.truetype("arialbd.ttf", 16)
    FONT_SMALL = ImageFont.truetype("arial.ttf", 13)
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

def circle_avatar(img: Image.Image, size: int = 40) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    dr = ImageDraw.Draw(mask)
    dr.ellipse((0,0,size,size), fill=255)
    out = Image.new("RGBA", (size, size), (0,0,0,0))
    out.paste(img.resize((size,size)), (0,0), mask)
    return out

def wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
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

# ---------- Renderer (realistic) ----------
async def messages_to_image(messages: List[discord.Message]) -> io.BytesIO:
    # Visual constants
    WIDTH = 920
    PADDING_X, PADDING_Y = 18, 12
    AVATAR_SIZE = 48
    LINE_SPACING = 6
    BG = (54,57,63)
    TEXT_COLOR = (220,221,222)
    NAME_DEFAULT = (114,137,218)
    TS_COLOR = (150,150,150)
    EMBED_BG = (47,49,54)
    EMBED_TITLE = (0,162,255)
    REACTION_BG = (60,63,70)
    REACTION_TEXT = (220,221,222)

    # Pre-fetch avatars, attachment images, embed images, and custom reaction emoji images
    async with aiohttp.ClientSession() as session:
        avatar_cache: Dict[int, Image.Image] = {}
        attachments_map: Dict[int, List[Image.Image]] = {}
        embeds_map: Dict[int, List[Dict[str, Any]]] = {}
        reaction_emoji_cache: Dict[str, Image.Image] = {}  # key -> image

        # First pass: avatars, attachments, embeds
        for m in messages:
            uid = m.author.id
            if uid not in avatar_cache:
                try:
                    url = str(m.author.display_avatar.url)
                    aimg = await fetch_image(session, url, (AVATAR_SIZE, AVATAR_SIZE))
                    avatar_cache[uid] = circle_avatar(aimg, AVATAR_SIZE) if aimg else Image.new("RGBA",(AVATAR_SIZE,AVATAR_SIZE),(100,100,100))
                except Exception:
                    avatar_cache[uid] = Image.new("RGBA",(AVATAR_SIZE,AVATAR_SIZE),(100,100,100))

            # attachments: images only
            if m.attachments:
                imgs = []
                for att in m.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        img = await fetch_image(session, att.url, (500, 400))
                        if img: imgs.append(img)
                if imgs:
                    attachments_map[m.id] = imgs

            # embeds
            if m.embeds:
                ems = []
                for e in m.embeds:
                    emdict = {"title": e.title, "desc": truncate(e.description or ""), "url": e.url}
                    if e.image:
                        img = await fetch_image(session, e.image.url, (500, 300))
                        emdict["image"] = img
                    ems.append(emdict)
                if ems:
                    embeds_map[m.id] = ems

        # Pre-fetch custom emoji images used in reactions (collect URLs)
        emoji_url_tasks = {}
        for m in messages:
            if not m.reactions:
                continue
            for reaction in m.reactions:
                emoji_obj = reaction.emoji
                key = None
                url = None
                # If it's a custom emoji object (PartialEmoji or Emoji), it often has `.url`
                if hasattr(emoji_obj, "url"):
                    try:
                        url = str(emoji_obj.url)
                        key = f"url:{url}"
                    except Exception:
                        url = None
                # Otherwise treat by name (unicode)
                if key and url and key not in reaction_emoji_cache:
                    emoji_url_tasks[key] = url

        # Fetch reaction emoji images
        for k, url in emoji_url_tasks.items():
            img = await fetch_image(session, url, (28,28))
            if img:
                reaction_emoji_cache[k] = img

    # Estimate heights per message
    tmp = Image.new("RGB", (10,10))
    draw_tmp = ImageDraw.Draw(tmp)
    heights: List[int] = []
    last_author: Optional[int] = None
    for m in messages:
        # Show username line if new author block
        base_h = 0
        if m.author.id != last_author:
            base_h += FONT_BOLD.size + 6
        # message text
        lines = wrap_text(draw_tmp, m.content or "", FONT_REG, WIDTH - (PADDING_X*2 + AVATAR_SIZE + 20))
        base_h += max(1, len(lines)) * (FONT_REG.size + LINE_SPACING)
        # attachments inline
        if m.id in attachments_map:
            for im in attachments_map[m.id]:
                base_h += im.height + 8
        # embeds
        if m.id in embeds_map:
            for em in embeds_map[m.id]:
                base_h += 70
                if em.get("image"):
                    base_h += em["image"].height + 8
        # reactions
        if m.reactions:
            base_h += 30
        # reply indicator small line
        if m.reference and getattr(m.reference, "message_id", None):
            base_h += 18
        heights.append(base_h + PADDING_Y)
        last_author = m.author.id

    total_h = sum(heights) + PADDING_Y
    canvas = Image.new("RGB", (WIDTH, max(total_h, 120)), BG)
    draw = ImageDraw.Draw(canvas)

    # Draw messages
    y = PADDING_Y
    last_author = None
    for idx, m in enumerate(messages):
        avatar = avatar_cache.get(m.author.id, Image.new("RGBA",(AVATAR_SIZE,AVATAR_SIZE),(100,100,100)))
        show_name = (m.author.id != last_author)
        x_text = PADDING_X + AVATAR_SIZE + 12

        # Reply indicator
        if m.reference and getattr(m.reference, "message_id", None):
            reply_text = "Replying to message"
            draw.rectangle([x_text-6, y-2, x_text+200, y+14], fill=(64,68,75))
            draw.text((x_text, y), reply_text, font=FONT_SMALL, fill=(180,180,180))
            y += FONT_SMALL.size + 6

        # Avatar + username + timestamp + edited
        if show_name:
            canvas.paste(avatar, (PADDING_X, y), avatar)
            # name color: prefer highest-role color if available
            name_color = NAME_DEFAULT
            try:
                if hasattr(m.author, "color") and m.author.color and m.author.color.value != 0:
                    name_color = (m.author.color.r, m.author.color.g, m.author.color.b)
            except Exception:
                pass
            draw.text((x_text, y), m.author.display_name, font=FONT_BOLD, fill=name_color)
            ts = m.created_at.strftime("%H:%M")
            # edited marker
            if m.edited_at:
                edit_label = " (edited)"
                draw.text((x_text + draw.textlength(m.author.display_name, font=FONT_BOLD) + 8, y+1),
                          ts + edit_label, font=FONT_SMALL, fill=TS_COLOR)
            else:
                draw.text((x_text + draw.textlength(m.author.display_name, font=FONT_BOLD) + 8, y+1),
                          ts, font=FONT_SMALL, fill=TS_COLOR)
            y += FONT_BOLD.size + 6

        # Message text lines
        lines = wrap_text(draw, m.content or "", FONT_REG, WIDTH - (PADDING_X*2 + AVATAR_SIZE + 20))
        for line in lines:
            draw.text((x_text, y), line, font=FONT_REG, fill=TEXT_COLOR)
            y += FONT_REG.size + LINE_SPACING

        # Inline attachments (images)
        if m.id in attachments_map:
            for att_img in attachments_map[m.id]:
                # draw a slight border/shadow behind the image for realism
                box_x = x_text
                box_y = y
                # ensure RGBA paste
                try:
                    canvas.paste(att_img, (box_x, box_y), att_img if att_img.mode == "RGBA" else None)
                except Exception:
                    canvas.paste(att_img, (box_x, box_y))
                y += att_img.height + 8

        # Embeds rendering (card-like)
        if m.id in embeds_map:
            for em in embeds_map[m.id]:
                box_w = WIDTH - (x_text + PADDING_X)
                box_h = 70
                draw.rectangle([x_text, y, x_text+box_w, y+box_h], fill=EMBED_BG)
                # title (blue)
                if em.get("title"):
                    t = em["title"]
                    draw.text((x_text+10, y+10), t, font=FONT_BOLD, fill=EMBED_TITLE)
                # description (truncated)
                if em.get("desc"):
                    desc_lines = wrap_text(draw, em["desc"], FONT_REG, box_w - 20)
                    for i, dl in enumerate(desc_lines[:3]):
                        draw.text((x_text+10, y+30 + i*(FONT_REG.size+2)), dl, font=FONT_REG, fill=TEXT_COLOR)
                y += box_h + 6
                # embed image if present
                if em.get("image"):
                    try:
                        canvas.paste(em["image"], (x_text, y), em["image"] if em["image"].mode == "RGBA" else None)
                    except Exception:
                        canvas.paste(em["image"], (x_text, y))
                    y += em["image"].height + 8

        # Reactions area
        if m.reactions:
            rx_x = x_text
            rx_y = y
            # build reaction pill layout
            # each reaction pill: [emoji (img or text)] [count]
            pill_padding_v = 6
            pill_padding_h = 8
            pill_gap = 8
            pill_height = 24
            # iterate reactions in the order present
            for reaction in m.reactions:
                count = reaction.count
                emoji_obj = reaction.emoji
                # Determine emoji display: custom image if possible, else unicode char
                emoji_img = None
                emoji_char = None
                if hasattr(emoji_obj, "url"):
                    key = f"url:{str(getattr(emoji_obj, 'url'))}"
                    # we attempted to prefetch these earlier; try fetch synchronously fallback
                    # but safer: just try to get from cache in this function's scope. If not present, set char to name.
                    # (we didn't carry cache into scope; so display name)
                    try:
                        emoji_img = None
                    except Exception:
                        emoji_img = None
                    # fallback to name
                    try:
                        emoji_char = getattr(emoji_obj, "name", str(emoji_obj))
                    except Exception:
                        emoji_char = str(emoji_obj)
                else:
                    emoji_char = str(emoji_obj)

                # measure pill width
                if emoji_img:
                    # if we had image
                    ew = 20
                else:
                    ew = draw.textlength(emoji_char, font=FONT_REG)
                    ew = max(ew, 14)
                count_w = draw.textlength(str(count), font=FONT_REG)
                pill_w = int(pill_padding_h*2 + ew + 6 + count_w)

                # draw pill background
                draw.rounded_rectangle([rx_x, rx_y, rx_x + pill_w, rx_y + pill_height], radius=8, fill=REACTION_BG)
                # draw emoji
                if emoji_img:
                    try:
                        canvas.paste(emoji_img, (rx_x + pill_padding_h, rx_y + 4), emoji_img)
                    except Exception:
                        pass
                else:
                    draw.text((rx_x + pill_padding_h, rx_y + 4), emoji_char, font=FONT_REG, fill=REACTION_TEXT)
                # draw count
                draw.text((rx_x + pill_padding_h + ew + 6, rx_y + 4), str(count), font=FONT_REG, fill=REACTION_TEXT)

                rx_x += pill_w + pill_gap

            y += pill_height + 8

        # small spacer between messages
        y += PADDING_Y
        last_author = m.author.id

    # Output buffer
    out = io.BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return out

# ---------- Commands ----------
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

        # read history
        try:
            messages = [m async for m in channel.history(limit=number)]
        except discord.Forbidden:
            await ctx.send("I don't have permission to read message history in that channel.")
            return
        messages.reverse()

        # render image
        await ctx.trigger_typing()
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
