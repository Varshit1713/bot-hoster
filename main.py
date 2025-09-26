import discord
from discord.ext import commands
import asyncio
import subprocess
from playwright.async_api import async_playwright
import datetime
import aiohttp
import os

# -----------------------------
# Runtime Chromium Installer
# -----------------------------
async def ensure_chromium():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
        print("✅ Chromium installed successfully")
    except Exception as e:
        print("⚠️ Chromium install failed:", e)

# Run installer before bot starts
asyncio.run(ensure_chromium())

# -----------------------------
# Bot Setup
# -----------------------------
intents = discord.Intents.default()
intents.members = True  # required for fetching users
bot = commands.Bot(command_prefix="!", intents=intents)

# -----------------------------
# !prank Command
# -----------------------------
@bot.command()
async def prank(ctx, user: discord.User, *, message_with_time: str):
    """
    Usage:
    !prank @user message text here 5:42am
    """

    try:
        # Split last word as time
        *message_parts, time_str = message_with_time.split()
        message_text = " ".join(message_parts)

        # Default fallback time
        now = datetime.datetime.now()
        display_time = time_str if time_str else now.strftime("%-I:%M %p")

        # Grab user info
        username = f"{user.name}"
        avatar_url = user.display_avatar.url

        # Download avatar locally
        avatar_path = "avatar.png"
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url) as resp:
                if resp.status == 200:
                    with open(avatar_path, "wb") as f:
                        f.write(await resp.read())

        # Launch Playwright and render fake Discord message
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()

            # Embed avatar as base64
            avatar_data = ""
            if os.path.exists(avatar_path):
                with open(avatar_path, "rb") as f:
                    import base64
                    avatar_data = f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"

            html = f"""
            <html>
            <head>
                <style>
                    body {{
                        margin: 0;
                        padding: 15px;
                        font-family: 'Whitney', 'Helvetica Neue', Helvetica, Arial, sans-serif;
                        background-color: #36393f;
                        color: #dcddde;
                        width: 375px; /* mobile width */
                    }}
                    .message {{
                        display: flex;
                        align-items: flex-start;
                        margin-bottom: 14px;
                    }}
                    .pfp {{
                        width: 42px;
                        height: 42px;
                        border-radius: 50%;
                        margin-right: 10px;
                        flex-shrink: 0;
                        background-size: cover;
                        background-image: url('{avatar_data}');
                    }}
                    .content {{
                        flex: 1;
                    }}
                    .username {{
                        font-weight: 600;
                        color: #ffffff;
                        margin-right: 6px;
                        font-size: 15px;
                    }}
                    .time {{
                        color: #72767d;
                        font-size: 12px;
                    }}
                    .text {{
                        font-size: 15px;
                        color: #dcddde;
                        margin-top: 2px;
                        white-space: pre-wrap;
                    }}
                </style>
            </head>
            <body>
                <div class="message">
                    <div class="pfp"></div>
                    <div class="content">
                        <div>
                            <span class="username">{username}</span>
                            <span class="time">{display_time}</span>
                        </div>
                        <div class="text">{message_text}</div>
                    </div>
                </div>
            </body>
            </html>
            """

            await page.set_content(html)
            element = await page.query_selector("body")
            await element.screenshot(path="prank.png")
            await browser.close()

        # Send image in Discord
        await ctx.send(file=discord.File("prank.png"))

        # Cleanup
        if os.path.exists(avatar_path):
            os.remove(avatar_path)

    except Exception as e:
        await ctx.send(f"❌ Error: {e}")

# -----------------------------
# Run Bot
# -----------------------------
bot.run("YOUR_BOT_TOKEN")
