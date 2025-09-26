# main.py
import os
import io
import discord
from discord.ext import commands
from playwright.async_api import async_playwright
import tempfile
from flask import Flask
import threading
import re

# ---------- Flask keep-alive ----------
app = Flask("")

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask).start()

# ---------- Discord bot setup ----------
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- HTML template for mobile Discord ----------
HTML_TEMPLATE = """
<html>
<head>
<meta charset="utf-8">
<style>
body {{
  margin: 0;
  padding: 20px;
  font-family: "Arial", sans-serif;
  background: #36393f;
}}
.chat-container {{
  display: flex;
  flex-direction: column;
}}
.message {{
  display: flex;
  margin-bottom: 12px;
}}
.avatar {{
  width: 40px;
  height: 40px;
  border-radius: 50%;
  flex-shrink: 0;
}}
.content {{
  display: flex;
  flex-direction: column;
  margin-left: 10px;
  max-width: 300px;
}}
.username {{
  font-weight: bold;
  font-size: 14px;
  color: #fff;
  margin-bottom: 2px;
}}
.bubble {{
  background: #40444b;
  color: #dcddde;
  font-size: 14px;
  padding: 8px 12px;
  border-radius: 16px;
  word-wrap: break-word;
  white-space: pre-wrap;
}}
.timestamp {{
  font-size: 11px;
  color: #72767d;
  margin-top: 2px;
  text-align: right;
}}
</style>
</head>
<body>
<div class="chat-container">
{messages_html}
</div>
</body>
</html>
"""

def build_messages_html(messages):
    html = ""
    for m in messages:
        html += f"""
        <div class="message">
            <img class="avatar" src="{m['avatar_url']}" />
            <div class="content">
                <div class="username">{m['username']}</div>
                <div class="bubble">{m['message']}</div>
                <div class="timestamp">{m['time']}</div>
            </div>
        </div>
        """
    return html

async def render_image(messages):
    html_content = HTML_TEMPLATE.format(messages_html=build_messages_html(messages))
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
        f.write(html_content.encode("utf-8"))
        temp_file = f.name

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file://{temp_file}")
        await page.set_viewport_size({"width": 420, "height": 800})
        buf = await page.screenshot(full_page=True)
        await browser.close()
    return buf

# ---------- Helper: fetch user info ----------
async def get_user_info(ctx, username_input):
    if username_input.startswith("<@") and username_input.endswith(">"):
        user_id = int(username_input.replace("<@!", "").replace("<@", "").replace(">", ""))
        member = ctx.guild.get_member(user_id)
        if member:
            username = member.display_name
            avatar_url = member.display_avatar.url
        else:
            user = await bot.fetch_user(user_id)
            username = user.name
            avatar_url = user.avatar.url if user.avatar else "https://cdn.pixabay.com/photo/2015/10/05/22/37/blank-profile-picture-973460_960_720.png"
    else:
        username = username_input
        avatar_url = "https://cdn.pixabay.com/photo/2015/10/05/22/37/blank-profile-picture-973460_960_720.png"
    return username, avatar_url

# ---------- !prank command ----------
@bot.command(name="prank")
async def prank(ctx, *, content: str):
    """
    Usage:
    !prank <@user> message HH:MMam/pm
    Multiple messages separated by ';'
    Example:
    !prank <@123> Hello 5:42am; John LOL 5:43am
    """
    try:
        messages_raw = content.split(";")
        messages = []

        for raw in messages_raw:
            raw = raw.strip()
            if not raw:
                continue

            # Extract time
            time_match = re.search(r'(\d{1,2}:\d{2}\s*(am|pm))$', raw, re.IGNORECASE)
            if not time_match:
                await ctx.send(f"Invalid time format in: {raw}")
                return
            msg_time_str = time_match.group(1)

            # Extract username and message
            msg_text_part = raw[:time_match.start()].strip()
            parts = msg_text_part.split(" ", 1)
            if len(parts) < 2:
                await ctx.send(f"Invalid message format in: {raw}")
                return
            username_input, msg_text = parts[0].strip(), parts[1].strip()

            username, avatar_url = await get_user_info(ctx, username_input)

            messages.append({
                "username": username,
                "message": msg_text,
                "time": msg_time_str,
                "avatar_url": avatar_url
            })

        buf = await render_image(messages)
        await ctx.send(file=discord.File(io.BytesIO(buf), "prank.png"))

    except Exception as e:
        await ctx.send(f"Error: {e}")

# ---------- Run bot ----------
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set")
    bot.run(TOKEN)
