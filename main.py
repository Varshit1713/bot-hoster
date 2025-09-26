# main.py
import os
import io
import sys
import time
import asyncio
import logging
import tempfile
import traceback
import re
import subprocess
from typing import List, Dict

import discord
from discord.ext import commands
from flask import Flask
import threading

# Optional imports (may fail if not installed)
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageFont
    import aiohttp
    PIL_AVAILABLE = True
except Exception:
    Image = ImageDraw = ImageFont = None
    aiohttp = None
    PIL_AVAILABLE = False

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("prank_bot")

# ---------- Flask keep-alive (Render requires an open port) ----------
app = Flask("prank_bot")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    # bind to 0.0.0.0 so Render can reach it
    app.run(host="0.0.0.0", port=port)

# start flask server in background
threading.Thread(target=run_flask, daemon=True).start()
LOG.info("Flask keepalive started on port %s", os.environ.get("PORT", 8080))

# ---------- Discord setup ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Utilities ----------
def run_playwright_install():
    """Run playwright browser installer (sync). Returns True if succeeded."""
    LOG.info("Attempting to install Playwright browsers (this may take a while)...")
    try:
        # Use the same Python executable running this script
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        res = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
        LOG.info("Playwright install stdout: %s", res.stdout[:1000])
        LOG.info("Playwright install succeeded.")
        return True
    except Exception as e:
        LOG.exception("Playwright install failed: %s", e)
        return False

def safe_html_escape(s: str) -> str:
    """Minimal escaping for HTML insertion."""
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))

HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<style>
  body {{ margin:0; padding:20px; background:#36393f; font-family: 'Helvetica', Arial, sans-serif; }}
  .chat {{ max-width:420px; }}
  .message {{ display:flex; gap:10px; margin-bottom:12px; align-items:flex-start; }}
  .avatar {{ width:40px; height:40px; border-radius:50%; flex-shrink:0; }}
  .content {{ max-width:320px; }}
  .username {{ color:#fff; font-weight:700; font-size:14px; margin-bottom:4px; }}
  .bubble {{ background:#40444b; color:#dcddde; border-radius:16px; padding:8px 12px; font-size:14px; white-space:pre-wrap; word-break:break-word; }}
  .timestamp {{ color:#72767d; font-size:11px; margin-top:6px; text-align:right; }}
</style>
</head>
<body>
<div class="chat">
{messages_html}
</div>
</body>
</html>
"""

def build_messages_html_for_playwright(messages: List[Dict]) -> str:
    html = []
    for m in messages:
        avatar = safe_html_escape(m.get("avatar_url", ""))
        username = safe_html_escape(m.get("username", "User"))
        message = safe_html_escape(m.get("message", ""))
        time_ = safe_html_escape(m.get("time", ""))
        html.append(f"""
        <div class="message">
          <img class="avatar" src="{avatar}" />
          <div class="content">
            <div class="username">{username}</div>
            <div class="bubble">{message}</div>
            <div class="timestamp">{time_}</div>
          </div>
        </div>
        """)
    return "\n".join(html)

# ---------- Playwright renderer ----------
async def render_with_playwright(messages: List[Dict]) -> bytes:
    """Render messages to PNG using Playwright + headless Chromium."""
    LOG.info("Rendering image using Playwright (chromium).")
    html = HTML_TEMPLATE.format(messages_html=build_messages_html_for_playwright(messages))
    # write temporary HTML file
    tf = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tf.write(html.encode("utf-8"))
    tf.flush()
    tf_name = tf.name
    tf.close()
    try:
        # start playwright
        async with async_playwright() as p:
            # include no-sandbox for many containers (Render)
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page(viewport={"width": 420, "height": 800})
            await page.goto("file://" + tf_name)
            # wait for fonts/images to load
            await page.wait_for_load_state("networkidle", timeout=5000)
            # allow layout to finish
            await asyncio.sleep(0.12)
            # screenshot full page
            buf = await page.screenshot(full_page=True)
            await browser.close()
            LOG.info("Playwright render complete, size=%s bytes", len(buf))
            return buf
    finally:
        try:
            os.unlink(tf_name)
        except Exception:
            pass

# ---------- Pillow (fallback) renderer ----------
# This is a pretty-good "mobile style" static renderer — used if Playwright isn't available.
async def render_with_pillow(messages: List[Dict]) -> bytes:
    LOG.info("Rendering image using Pillow fallback.")
    if not PIL_AVAILABLE:
        raise RuntimeError("Pillow or aiohttp not installed for fallback renderer.")
    WIDTH = 420
    BG = (54,57,63)
    TEXT = (220,221,222)
    TS = (114,118,125)
    AV = 40
    P = 12
    LINE_SP = 4
    MAX_TEXT = WIDTH - (AV + 4*P)

    # load default fonts (best-effort)
    try:
        FONT_REG = ImageFont.truetype("Arial.ttf", 14)
        FONT_BOLD = ImageFont.truetype("Arial Bold.ttf", 14)
        FONT_SMALL = ImageFont.truetype("Arial.ttf", 11)
    except Exception:
        FONT_REG = ImageFont.load_default()
        FONT_BOLD = ImageFont.load_default()
        FONT_SMALL = ImageFont.load_default()

    async def fetch_avatar_bytes(url: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        return await resp.read()
        except Exception:
            LOG.exception("Avatar fetch failed for %s", url)
        return None

    # prefetch avatars
    avatar_images = {}
    for m in messages:
        url = m.get("avatar_url") or ""
        avatar_images[url] = None

    for url in list(avatar_images.keys()):
        if url:
            data = await fetch_avatar_bytes(url)
            if data:
                try:
                    img = Image.open(io.BytesIO(data)).convert("RGBA")
                    avatar_images[url] = img
                except Exception:
                    avatar_images[url] = None
            else:
                avatar_images[url] = None
        else:
            avatar_images[url] = None

    # estimate height
    dummy = Image.new("RGBA",(WIDTH,100),BG)
    d = ImageDraw.Draw(dummy)
    y = P
    for m in messages:
        y += FONT_BOLD.getsize(m["username"])[1] + 6
        # wrap message
        words = m["message"].split(" ")
        line = ""
        lines=[]
        for w in words:
            test = (line + " " + w).strip()
            if d.textlength(test, font=FONT_REG) <= MAX_TEXT:
                line = test
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)
        y += sum([FONT_REG.getsize(l)[1] + 2 for l in lines]) + FONT_SMALL.getsize("T")[1] + LINE_SP
        y += P
    height = max(y + P, 120)

    img = Image.new("RGBA",(WIDTH, height), BG)
    draw = ImageDraw.Draw(img)
    y = P
    for m in messages:
        # avatar
        avatar_url = m.get("avatar_url", "")
        avatar_img = avatar_images.get(avatar_url)
        if avatar_img:
            av = avatar_img.resize((AV,AV), Image.LANCZOS).convert("RGBA")
            # circle mask
            mask = Image.new("L", (AV,AV), 0)
            ImageDraw.Draw(mask).ellipse((0,0,AV,AV), fill=255)
            img.paste(av, (P, y), mask)
        else:
            # placeholder circle with initials
            circle = Image.new("RGBA",(AV,AV),(100,100,100))
            ic = ImageDraw.Draw(circle)
            initials = "".join([c[0].upper() for c in m["username"].split() if c and c[0].isalnum()][:2])
            w,h = ic.textsize(initials, font=FONT_BOLD)
            ic.text(((AV-w)/2,(AV-h)/2), initials, font=FONT_BOLD, fill=(255,255,255))
            mask = Image.new("L", (AV,AV), 0)
            ImageDraw.Draw(mask).ellipse((0,0,AV,AV), fill=255)
            img.paste(circle, (P,y), mask)

        x_text = P + AV + P
        # username
        draw.text((x_text, y), m["username"], font=FONT_BOLD, fill=TEXT)
        y += FONT_BOLD.getsize(m["username"])[1] + 6

        # wrap text again to draw
        words = m["message"].split(" ")
        line = ""
        lines=[]
        for w in words:
            test = (line + " " + w).strip()
            if draw.textlength(test, font=FONT_REG) <= MAX_TEXT:
                line = test
            else:
                if line:
                    lines.append(line)
                line = w
        if line:
            lines.append(line)

        # bubble rectangle
        bubble_h = sum([FONT_REG.getsize(l)[1] + 2 for l in lines]) + FONT_SMALL.getsize("T")[1] + 12
        left = x_text - 6
        top = y - 6
        right = WIDTH - P
        bottom = y + bubble_h - 6
        # rounded rectangle (simple)
        radius = 14
        # draw rounded rectangle manually
        draw.rounded_rectangle([left, top, right, bottom], radius=radius, fill=(64,68,75))
        # draw text lines
        ly = y
        for line in lines:
            draw.text((x_text, ly), line, font=FONT_REG, fill=TEXT)
            ly += FONT_REG.getsize(line)[1] + 2
        # timestamp
        ts = m.get("time","")
        ts_w = draw.textlength(ts, font=FONT_SMALL)
        draw.text((right - ts_w - 6, ly), ts, font=FONT_SMALL, fill=TS)
        y = bottom + LINE_SP + 4

    # crop to actual content
    bbox = (0,0,WIDTH, y + P)
    img = img.crop(bbox)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()

# ---------- Top-level render helper ----------
async def render_image(messages: List[Dict]) -> bytes:
    """
    Try Playwright first (if available). If Playwright is not available or fails,
    try to run playwright install (if allowed). If it still fails, fall back to Pillow.
    """
    global PLAYWRIGHT_AVAILABLE
    # If playwright import is available attempt to use it
    if PLAYWRIGHT_AVAILABLE and async_playwright is not None:
        try:
            return await render_with_playwright(messages)
        except Exception as e:
            LOG.warning("Playwright rendering failed: %s", e)
            LOG.debug(traceback.format_exc())
            # Attempt to run install if executable missing
            if "Executable doesn't exist" in str(e) or "Please run the following command" in str(e) or "playwright install" in str(e).lower():
                ok = run_playwright_install()
                if ok:
                    # refresh import flag and try again
                    try:
                        return await render_with_playwright(messages)
                    except Exception:
                        LOG.exception("Playwright still failing after install.")
                else:
                    LOG.error("Playwright installation attempt failed or was blocked.")
            # continue to fallback
    # Fallback to Pillow
    if PIL_AVAILABLE:
        return await render_with_pillow(messages)
    # Nothing available
    raise RuntimeError("No renderer available: Playwright failed and Pillow not installed.")

# ---------- Helper to parse command input ----------
def parse_prank_input(content: str):
    """
    Accepts something like:
      <@123> hello 2:52am
      John hello 2:52am; Jane hi 2:53pm
    Returns list of {username, message, time}
    """
    segments = [seg.strip() for seg in content.split(";") if seg.strip()]
    out = []
    for seg in segments:
        # time at end like 5:42am or 12:30 pm
        m = re.search(r'(\d{1,2}:\d{2}\s*(am|pm))$', seg, re.IGNORECASE)
        if not m:
            raise ValueError(f"Invalid time format in segment: {seg!r}. Use e.g. 5:42am or 12:30pm")
        time_str = m.group(1)
        front = seg[:m.start()].strip()
        if not front:
            raise ValueError(f"Missing username/message before time in: {seg!r}")
        # username is first token, rest is message
        parts = front.split(" ", 1)
        if len(parts) < 2:
            raise ValueError(f"Missing message text in segment: {seg!r}")
        username_input = parts[0].strip()
        message_text = parts[1].strip()
        out.append({"username_input": username_input, "message": message_text, "time": time_str})
    return out

# ---------- Fetch Discord user info ----------
async def resolve_user_info(ctx, username_input: str):
    """
    If username_input looks like <@id> or <@!id>, try to fetch member/user from guild and get display name + avatar url.
    If not mention-like, return username_input as display name and a generic avatar url.
    """
    default_avatar = "https://cdn.pixabay.com/photo/2015/10/05/22/37/blank-profile-picture-973460_960_720.png"
    if username_input.startswith("<@") and username_input.endswith(">"):
        try:
            uid = int(re.sub(r"[<@!>]", "", username_input))
            member = None
            if ctx.guild:
                member = ctx.guild.get_member(uid)
            if member:
                return (member.display_name, str(member.display_avatar.url))
            # fallback to fetch_user
            user = await bot.fetch_user(uid)
            return (user.name, str(user.avatar.url) if user.avatar else default_avatar)
        except Exception:
            LOG.exception("Failed to resolve mention %s", username_input)
            return (username_input, default_avatar)
    else:
        return (username_input, default_avatar)

# ---------- Discord command ----------
@bot.command(name="prank")
async def prank_cmd(ctx, *, content: str):
    """
    Usage examples:
      !prank <@123456789> hello 2:52am
      !prank John hello 2:52am; Jane hi 2:53pm
    """
    try:
        parsed = parse_prank_input(content)
    except ValueError as e:
        await ctx.send(f"Input error: {e}")
        return

    # Resolve usernames + avatars (async)
    messages = []
    for item in parsed:
        dn, avatar_url = await resolve_user_info(ctx, item["username_input"])
        messages.append({
            "username": dn,
            "message": item["message"],
            "time": item["time"],
            "avatar_url": avatar_url
        })

    await ctx.trigger_typing()
    try:
        png_bytes = await render_image(messages)
    except Exception as e:
        LOG.exception("Failed to render final image: %s", e)
        await ctx.send(f"Rendering error: {e}")
        return

    await ctx.send(file=discord.File(io.BytesIO(png_bytes), filename="prank.png"))

# ---------- Run ----------
if __name__ == "__main__":
    DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
    if not DISCORD_TOKEN:
        LOG.error("DISCORD_TOKEN not set in environment")
        raise SystemExit("DISCORD_TOKEN not set")
    LOG.info("Starting bot...")
    bot.run(DISCORD_TOKEN)
