# Example/webserver.py
from aiohttp import web
import asyncio

# -----------------------------
# Simple handler
# -----------------------------
async def handle(request):
    return web.Response(text="Bot is running!")

# -----------------------------
# Main app
# -----------------------------
app = web.Application()
app.router.add_get("/", handle)

# -----------------------------
# Run server
# -----------------------------
def run():
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

# -----------------------------
# Optional: ping endpoint
# -----------------------------
async def ping_self(bot):
    import aiohttp
    import os
    import time
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                port = int(os.environ.get("PORT", 8080))
                url = f"http://localhost:{port}/"
                async with session.get(url) as resp:
                    await resp.text()
        except Exception:
            pass
        await asyncio.sleep(280)  # ping every ~4.5 minutes

if __name__ == "__main__":
    run()
