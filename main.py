# main.py
import os
import sys
import io
import time
import base64
import logging
import threading
import subprocess
import tempfile
import asyncio
import re

import discord
from discord.ext import commands
from flask import Flask
import aiohttp

# Try to import Playwright. If not installed at build time, we will run installer.
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_IMPORTABLE = True
except Exception:
    async_playwright = None
    PLAYWRIGHT_IMPORTABLE = False

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("prank_bot")

# ---------- Flask keep-alive (open port) ----------
app = Flask("prank_bot")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    LOG.info("Starting Flask keep-alive on port %s", port)
    # bind to all interfaces so external host can reach it
    app.run(host="0.0.0.0", port=port)

# Start Flask server in a daemon thread so it doesn't block the bot.
threading.Thread(target=run_flask, daemon=True).start()

# ---------- Ensure Playwright browsers exist at runtime ----------
def ensure_chromium_installed():
    """
    Synchronously run `python -m playwright install chromium` using the current Python executable.
    This runs before bot.run so the browser exists when Playwright is invoked by the bot.
    """
    try:
        LOG.info("Running Playwright browser installer (chromium). This may take a while...")
        cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
        subprocess.run(cmd, check=True)
        LOG.info("Playwright chromium install completed.")
        return True
    except Exception as e:
        LOG.exception("Playwright install failed: %s", e)
        return False

# Attempt install if importable but binary missing, or if not importable at all try installing package+browsers.
# Note: In Render, playwright package should be installed in build; this ensures browser binary exists at runtime.
if not PLAYWRIGHT_IMPORTABLE:
    LOG.info("Playwright not importable at runtime. Attempting to ensure package and browsers are installed.")
    try:
        # Try to pip install playwright into environment (best-effort). This can be slow and may fail if environment is locked down.
        subprocess.run([sys.executable, "-m", "pip", "install", "playwright"], check=True)
        PLAYWRIGHT_IMPORTABLE = True
    except Exception:
        LOG.exception("Failed to pip-install playwright at runtime. Continuing and rely on fallback (if any).")

# Always try to install chromium binary (safe no-op if already installed)
ensure_chromium_installed()

# ---------- Discord bot setup ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- Small mobile-style HTML template for Playwright ----------
HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<style>
  body {{ margin:0; padding:18px; background:#36393f; font-family: Arial, Helvetica, sans-serif; color:#dcddde; }}
  .chat {{ width: 375px; }}
  .message {{ display:flex; gap:10px; margin-bottom:12px; align-items:flex-start; }}
  .avatar {{ width:42px; height:42px; border-radius:50%; background-size:cover; flex-shrink:0; }}
  .content {{ max-width: 300px; }}
  .username {{ font-weight:700; color:#ffffff; font-size:15px; margin-bottom:4px; display:inline-block; }}
  .time {{ color:#72767d; font-size:12px; margin-left:8px; display:inline-block; vertical-align:top; }}
  .bubble {{ background:#40444b; padding:8px 12px; border-radius:14px; color:#dcddde; font-size:15px; white-space:pre-wrap; word-break:break-word; }}
</style>
</head>
<body>
<div class="chat">
{messages_html}
</div>
</body>
</html>
"""

def safe_html(s: str) -> str:
    return (s.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))

def build_messages_html(messages):
    parts = []
    for m in messages:
        avatar = safe_html(m.get("avatar_data_uri", m.get("avatar_url", "")))
        username = safe_html(m.get("username", "User"))
        message = safe_html(m.get("message", ""))
        time_ = safe_html(m.get("time", ""))
        parts.append(f"""
        <div class="message">
          <div class="avatar" style="background-image: url('{avatar}');"></div>
          <div class="content">
            <div><span class="username">{username}</span><span class="time">{time_}</span></div>
            <div class="bubble">{message}</div>
          </div>
        </div>
        """)
    return "\n".join(parts)

# ---------- Helper: fetch avatar and convert to data URI ----------
async def avatar_to_data_uri(session: aiohttp.ClientSession, url: str):
    """
    Downloads an image and returns a data URI (base64) for embedding in HTML.
    If download fails, returns a small empty data URL fallback.
    """
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.read()
                b64 = base64.b64encode(data).decode()
                # Try to infer mime-type from URL extension; default to png
                if url.lower().endswith(".jpg") or url.lower().endswith(".jpeg"):
                    mime = "image/jpeg"
                elif url.lower().endswith(".webp"):
                    mime = "image/webp"
                else:
                    mime = "image/png"
                return f"data:{mime};base64,{b64}"
    except Exception:
        LOG.exception("Failed to download avatar %s", url)
    # fallback 1x1 transparent png
    return "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="

# ---------- Playwright render helper ----------
async def render_with_playwright(messages):
    if not PLAYWRIGHT_IMPORTABLE:
        raise RuntimeError("Playwright not available at runtime.")
    html = HTML_TEMPLATE.format(messages_html=build_messages_html(messages))
    tf = tempfile.NamedTemporaryFile(suffix=".html", delete=False)
    tf.write(html.encode("utf-8"))
    tf.flush()
    tf_name = tf.name
    tf.close()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page(viewport={"width": 375, "height": 800})
            await page.goto("file://" + tf_name)
            await page.wait_for_load_state("networkidle")
            # small pause for layout/styling stability
            await asyncio.sleep(0.05)
            buf = await page.screenshot(full_page=True)
            await browser.close()
            return buf
    finally:
        try:
            os.unlink(tf_name)
        except Exception:
            pass

# ---------- Parse user input ----------
def parse_input(content: str):
    """
    Accepts semicolon-separated segments like:
      <@123> hello 5:42am; John hi 6:00pm
    Returns list of dicts: [{'username_input':'<@123>', 'message':'hello', 'time':'5:42am'}, ...]
    """
    out = []
    segs = [s.strip() for s in content.split(";") if s.strip()]
    for seg in segs:
        m = re.search(r'(\d{1,2}:\d{2}\s*(am|pm))$', seg, re.IGNORECASE)
        if not m:
            raise ValueError(f"Invalid time format in segment: {seg!r}. Use e.g. 5:42am or 12:30pm")
        time_str = m.group(1)
        front = seg[:m.start()].strip()
        if not front:
            raise ValueError(f"Missing username/message in segment: {seg!r}")
        parts = front.split(" ", 1)
        if len(parts) < 2:
            raise ValueError(f"Missing message text in segment: {seg!r}")
        username_input = parts[0].strip()
        message_text = parts[1].strip()
        out.append({"username_input": username_input, "message": message_text, "time": time_str})
    return out

# ---------- Resolve username and avatar URL ----------
async def resolve_user(ctx, username_input: str):
    default_avatar = "https://cdn.pixabay.com/photo/2015/10/05/22/37/blank-profile-picture-973460_960_720.png"
    # mention style <@id> or <@!id>
    if username_input.startswith("<@") and username_input.endswith(">"):
        try:
            uid = int(re.sub(r"[<@!>]", "", username_input))
            member = None
            if ctx.guild:
                member = ctx.guild.get_member(uid)
            if member:
                return member.display_name, str(member.display_avatar.url)
            user = await bot.fetch_user(uid)
            return user.name, str(user.avatar.url) if user.avatar else default_avatar
        except Exception:
            LOG.exception("Failed to resolve mention %s", username_input)
            return username_input, default_avatar
    else:
        # treat as plain display name
        return username_input, default_avatar

# ---------- Bot command ----------
@bot.command(name="prank")
async def prank(ctx, *, content: str):
    """
    Usage:
      !prank <@123456789> hello 5:42am
      !prank John hello 5:42am; Jane hi 5:44am
    """
    try:
        parsed = parse_input(content)
    except ValueError as e:
        await ctx.send(f"Input error: {e}")
        return

    # Resolve users & avatars
    messages_for_render = []
    async with aiohttp.ClientSession() as session:
        for item in parsed:
            display_name, avatar_url = await resolve_user(ctx, item["username_input"])
            # fetch avatar as data URI for embedding (best for reliability)
            avatar_data_uri = await avatar_to_data_uri(session, avatar_url)
            messages_for_render.append({
                "username": display_name,
                "message": item["message"],
                "time": item["time"],
                "avatar_url": avatar_url,
                "avatar_data_uri": avatar_data_uri
            })

    # Try to render with Playwright (preferred)
    try:
        await ctx.trigger_typing()
    except Exception:
        # trigger_typing might not be available in some contexts; ignore
        pass

    try:
        png = await render_with_playwright(messages_for_render)
    except Exception as e:
        LOG.exception("Playwright rendering failed: %s", e)
        # As fallback, try to return a text error + show minimal screenshotless info
        await ctx.send("Rendering failed (Playwright). Ensure playwright + browsers are installed on the host. Error: " + str(e))
        return

    # send file
    await ctx.send(file=discord.File(io.BytesIO(png), filename="prank.png"))

# ---------- Run ----------
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        LOG.error("DISCORD_TOKEN not set in environment")
        raise SystemExit("DISCORD_TOKEN not set")
    LOG.info("Starting bot...")
    bot.run(TOKEN)
