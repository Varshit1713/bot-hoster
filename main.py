# main.py
import os
import io
import asyncio
import re
from datetime import datetime, timedelta
from discord.ext import commands
from playwright.async_api import async_playwright
import tempfile

bot = commands.Bot(command_prefix="!")

# ---------- HTML template ----------
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
.message {{
  display: flex;
  margin-bottom: 10px;
}}
.avatar {{
  width: 40px;
  height: 40px;
  border-radius: 50%;
  flex-shrink: 0;
}}
.content {{
  margin-left: 10px;
  max-width: 350px;
}}
.username {{
  font-weight: bold;
  color: #fff;
  font-size: 14px;
}}
.bubble {{
  background: #40444b;
  border-radius: 16px;
  padding: 6px 10px;
  color: #dcddde;
  font-size: 14px;
  margin-top: 2px;
  word-wrap: break-word;
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
{messages_html}
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

# ---------- Helper: get Discord user info ----------
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

# ---------- Scheduler ----------
scheduled_messages = []

async def check_scheduled():
    while True:
        now = datetime.now()
        ready = [m for m in scheduled_messages if m['send_time'] <= now]
        if ready:
            # Render all ready messages in a single screenshot
            messages_to_send = ready
            ctx = ready[0]['ctx']  # use ctx of first message
            buf = await render_image([m['data'] for m in messages_to_send])
            await ctx.send(file=commands.File(io.BytesIO(buf), "prank.png"))
            # Remove sent messages
            for m in ready:
                scheduled_messages.remove(m)
        await asyncio.sleep(5)

# Start scheduler task
@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")
    bot.loop.create_task(check_scheduled())

# ---------- !prank command ----------
@bot.command(name="prank")
async def prank(ctx, *, content: str):
    """
    Usage:
    !prank <@user> message HH:MMam/pm
    Multiple messages: separate with ';'
    Example:
    !prank <@123> Hello 5:42am; <@456> LOL 5:43am
    """
    try:
        messages_raw = content.split(";")
        now = datetime.now()

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
            msg_text_part = raw[:time_match.start()].strip()
            parts = msg_text_part.split(" ", 1)
            if len(parts) < 2:
                await ctx.send(f"Invalid message format in: {raw}")
                return
            username_input, msg_text = parts[0].strip(), parts[1].strip()
            username, avatar_url = await get_user_info(ctx, username_input)

            # Calculate send time
            msg_datetime = datetime.strptime(msg_time_str.lower(), "%I:%M%p")
            msg_datetime = msg_datetime.replace(year=now.year, month=now.month, day=now.day)
            if msg_datetime < now:
                msg_datetime += timedelta(days=1)

            scheduled_messages.append({
                'ctx': ctx,
                'send_time': msg_datetime,
                'data': {
                    "username": username,
                    "message": msg_text,
                    "time": msg_time_str,
                    "avatar_url": avatar_url
                }
            })

        await ctx.send(f"Prank scheduled with {len(messages_raw)} message(s)! They will appear in a single screenshot.")

    except Exception as e:
        await ctx.send(f"Error: {e}")

# ---------- Run ----------
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set")
    bot.run(TOKEN)
