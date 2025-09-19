import discord from discord.ext import commands, tasks import asyncio import datetime import json import os import traceback from flask import Flask import threading

------------------ CONFIG ------------------

TOKEN = os.environ.get('DISCORD_TOKEN') DATA_FILE = "data.json"

------------------ HELPER FUNCTIONS ------------------

def safe_print(*args, **kwargs): print(*args, **kwargs)

def load_data(): if os.path.exists(DATA_FILE): with open(DATA_FILE, "r") as f: return json.load(f) return {}

def save_data(d): with open(DATA_FILE, "w") as f: json.dump(d, f, indent=2, default=str)

------------------ BOT SETUP ------------------

intents = discord.Intents.all() bot = commands.Bot(command_prefix="!", intents=intents)

------------------ COMMAND EXAMPLES ------------------

@bot.command() async def dump(ctx): data = load_data() path = "dump.json" with open(path, "w") as f: json.dump(data, f, indent=2, default=str) await ctx.send("📦 Data dump:", file=discord.File(path)) try: os.remove(path) except: pass

------------------ DAILY ARCHIVE / CLEANUP ------------------

@tasks.loop(hours=24) async def daily_maintenance_task(): try: data = load_data() cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=120) for uid, u in data.get("users", {}).items(): daily = u.get("daily_seconds", {}) keys_to_remove = [] for k in list(daily.keys()): try: ddt = datetime.datetime.strptime(k, "%Y-%m-%d") if ddt < cutoff: keys_to_remove.append(k) except: pass for k in keys_to_remove: daily.pop(k, None) save_data(data) except Exception as e: safe_print("⚠️ daily maintenance error:", e) traceback.print_exc()

------------------ FLASK SERVER ------------------

app = Flask(name)

@app.route('/') def home(): return 'Bot is running!'

def run_flask(): app.run(host='0.0.0.0', port=8080)

------------------ STARTUP & RUN ------------------

if name == "main": try: safe_print("🚀 Starting mega bot with audit reconciliation...") if not os.path.exists(DATA_FILE): save_data({"users": {}})

# Start Flask in separate thread
    threading.Thread(target=run_flask, daemon=True).start()

    # Start daily maintenance task
    daily_maintenance_task.start()

    bot.run(TOKEN)

except Exception as e:
    safe_print("❌ Fatal error while running bot:", e)
    traceback.print_exc()

