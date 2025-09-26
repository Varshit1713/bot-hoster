# main.py
import os
import io
import asyncio
import base64
import logging
import threading
import subprocess
import sys

import discord
from discord.ext import commands
from flask import Flask
import aiohttp

# -------------------------------
# Logging
# -------------------------------
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("prank_bot")

# -------------------------------
# Flask keep-alive
# -------------------------------
app = Flask("prank_bot")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    LOG.info(f"Starting Flask on port {port}")
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# -------------------------------
# Ensure Playwright Chromium
# -------------------------------
def ensure_chromium():
    try:
        LOG.info("Installing Playwright Chromium if missing...")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
        LOG.info("Playwright Chromium ready.")
    except Exception as e:
        LOG.exception("Failed to install Chromium: %s", e)

ensure_chromium()

# -------------------------------
# Bot setup
# -------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------------
# HTML template for mobile style
# -------------------------------
HTML_TEMPLATE = """
<html>
<head>
<style>
body {{ background:#36393f; font-family: Arial, sans-serif; color:#dcddde; margin:0; padding:18px; width:375px; }}
.message {{ display:flex; gap:10px; margin-bottom:12px; align-items:flex-start; }}
.avatar {{ width:42px; height:42px; border-radius:50%; background-size:cover; flex-shrink:0; }}
.content {{ max-width:300px; }}
.username {{ font-weight:700; color:#fff; font-size:15px; margin-bottom:4px; display:inline-block; }}
.time {{ color:#72767d; font-size:12px; margin-left:8px; display:inline-block; vertical-align:top; }}
.bubble {{ background:#40444b; padding:8px 12px; border-radius:14px; font-size:15px; white-space:pre-wrap; }}
</style>
</head>
<body>
<div class="chat">
<div class="message">
<div class="avatar" style="background-image:url('{avatar}');"></div>
<div class="content">
<div><span class="username">{username}</span><span class="time">{time}</span></div>
<div class="bubble">{message}</div>
</div>
</div>
</div>
</body>
</html>
"""

# -------------------------------
# Helper: download avatar to data URI
# -------------------------------
async def avatar_to_data_uri(url):
    fallback = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    b64 = base64.b64encode(data).decode()
                    return f"data:image/png;base64,{b64}"
    except Exception:
        LOG.exception("Failed to fetch avatar %s", url)
    return fallback

# -------------------------------
# !prank command
# -------------------------------
@bot.command()
async def prank(ctx, user: discord.User, *, content):
    LOG.info(f"Command triggered by {ctx.author} -> target: {user}, content: {content}")
    await ctx.send("✅ Command received! Rendering...")

    # Split last word as time
    *msg_parts, time_str = content.split()
    message_text = " ".join(msg_parts)
    if not time_str.lower().endswith(("am","pm")):
        time_str = "12:00pm"

    # Fetch avatar
    avatar_uri = await avatar_to_data_uri(str(user.display_avatar.url))

    # Prepare HTML
    html = HTML_TEMPLATE.format(username=user.name, message=message_text, time=time_str, avatar=avatar_uri)

    # Render with Playwright
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page(viewport={"width": 375, "height": 800})
            await page.set_content(html)
            await asyncio.sleep(0.05)
            buf = await page.screenshot(full_page=True)
            await browser.close()
        await ctx.send(file=discord.File(io.BytesIO(buf), filename="prank.png"))
    except Exception as e:
        LOG.exception("Rendering failed: %s", e)
        await ctx.send(f"❌ Rendering failed: {e}")

# -------------------------------
# Run bot
# -------------------------------
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        LOG.error("DISCORD_TOKEN not set in environment!")
        raise SystemExit("DISCORD_TOKEN missing")
    LOG.info("Starting bot...")
    bot.run(TOKEN)
