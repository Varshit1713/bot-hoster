# ------------------ IMPORTS ------------------
import os
import threading
from flask import Flask
import discord
from discord.ext import commands, tasks
import datetime
import json
import sys

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ ERROR: DISCORD_TOKEN environment variable not set")
    sys.exit(1)

DATA_FILE = "activity_logs.json"
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}
INACTIVITY_THRESHOLD = 60  # seconds (1 minute)

# ------------------ FLASK (Render port binding) ------------------
app = Flask(__name__)

@app.route("/")
def index():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

t = threading.Thread(target=run_flask, daemon=True)
t.start()

# ------------------ DISCORD BOT ------------------
intents = discord.Intents.default()
intents.members = True
intents.presences = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ LOAD / INIT ------------------
# Per-user schema:
# {
#   "total_seconds": int,
#   "online": bool,
#   "last_tick": ISO or None,    # last time we advanced the counter
#   "last_seen": ISO or None,    # last real activity (message or presence change)
#   "offline_since": ISO or None # when we marked them offline
# }
def _parse_time(v):
    return datetime.datetime.fromisoformat(v) if v else None

if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            raw = json.load(f)
        activity_logs = {}
        for uid, data in raw.items():
            uid_i = int(uid)
            activity_logs[uid_i] = {
                "total_seconds": int(data.get("total_seconds", 0)),
                "online": bool(data.get("online", False)),
                "last_tick": _parse_time(data.get("last_tick")),
                "last_seen": _parse_time(data.get("last_seen")),
                "offline_since": _parse_time(data.get("offline_since")),
            }
    except Exception:
        print("⚠️ Corrupt activity_logs.json, resetting...")
        activity_logs = {}
else:
    activity_logs = {}

last_messages = {}  # {user_id: {"content": str, "timestamp": datetime}}

def save_logs():
    out = {}
    for uid, d in activity_logs.items():
        out[str(uid)] = {
            "total_seconds": d.get("total_seconds", 0),
            "online": d.get("online", False),
            "last_tick": d["last_tick"].isoformat() if d.get("last_tick") else None,
            "last_seen": d["last_seen"].isoformat() if d.get("last_seen") else None,
            "offline_since": d["offline_since"].isoformat() if d.get("offline_since") else None,
        }
    with open(DATA_FILE, "w") as f:
        json.dump(out, f, indent=4)

# ------------------ HELPERS ------------------
def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def format_time(seconds: int):
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

def convert_timezone(dt: datetime.datetime, tz_name: str):
    tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
    return dt.astimezone(tz)

def ensure_user(uid: int):
    if uid not in activity_logs:
        activity_logs[uid] = {
            "total_seconds": 0,
            "online": False,
            "last_tick": None,
            "last_seen": None,
            "offline_since": None,
        }

def go_online(uid: int, when: datetime.datetime):
    d = activity_logs[uid]
    d["online"] = True
    d["last_seen"] = when           # real activity time
    if d["last_tick"] is None:
        d["last_tick"] = when       # start ticking from now
    d["offline_since"] = None

def go_offline_at(uid: int, offline_time: datetime.datetime):
    """Stop counting exactly at offline_time."""
    d = activity_logs[uid]
    if d["online"] and d.get("last_tick"):
        if offline_time > d["last_tick"]:
            add = (offline_time - d["last_tick"]).total_seconds()
            d["total_seconds"] += int(add)
    d["online"] = False
    d["last_tick"] = None
    d["offline_since"] = offline_time
    # keep last_seen as the last activity moment (optional)

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    now = now_utc()

    # Reconcile state across restarts:
    for uid, d in activity_logs.items():
        if d["online"]:
            # Compute where counting should have stopped at most
            limit = now
            if d.get("last_seen"):
                inactivity_cutoff = d["last_seen"] + datetime.timedelta(seconds=INACTIVITY_THRESHOLD)
                if inactivity_cutoff < limit:
                    limit = inactivity_cutoff
            if d.get("last_tick") and limit > d["last_tick"]:
                d["total_seconds"] += int((limit - d["last_tick"]).total_seconds())
            # If we already exceeded inactivity, mark offline at cutoff
            if d.get("last_seen") and now >= (d["last_seen"] + datetime.timedelta(seconds=INACTIVITY_THRESHOLD)):
                go_offline_at(uid, d["last_seen"] + datetime.timedelta(seconds=INACTIVITY_THRESHOLD))
            else:
                # Still considered online; restart ticking from 'now'
                d["last_tick"] = now
                d["offline_since"] = None

    # If Discord currently shows members online, bring them online now
    for guild in bot.guilds:
        for member in guild.members:
            if member.status != discord.Status.offline:
                ensure_user(member.id)
                go_online(member.id, now)

    save_logs()

    if not update_all_users.is_running():
        update_all_users.start()

    try:
        await bot.tree.sync()
        print("✅ Slash commands synced.")
    except Exception as e:
        print(f"⚠️ Slash sync failed: {e}")

    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_presence_update(before, after):
    now = now_utc()
    uid = after.id
    ensure_user(uid)

    # offline -> non-offline : user became active
    if before.status == discord.Status.offline and after.status != discord.Status.offline:
        go_online(uid, now)

    # non-offline -> offline : user left
    elif before.status != discord.Status.offline and after.status == discord.Status.offline:
        go_offline_at(uid, now)

    save_logs()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    now = now_utc()
    uid = message.author.id
    ensure_user(uid)

    # Message = activity. If offline, come online; if online, just refresh last_seen.
    if not activity_logs[uid]["online"]:
        go_online(uid, now)
    else:
        # Important: DO NOT touch last_tick here; only update last_seen.
        activity_logs[uid]["last_seen"] = now

    last_messages[uid] = {"content": message.content, "timestamp": now}
    save_logs()

# ------------------ BACKGROUND TASK ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    now = now_utc()

    # 1) Advance counters for users currently online (tick from last_tick -> now)
    for uid, d in activity_logs.items():
        if d["online"] and d.get("last_tick"):
            add = (now - d["last_tick"]).total_seconds()
            if add > 0:
                d["total_seconds"] += int(add)
                d["last_tick"] = now  # move the tick forward

    # 2) Auto mark offline if past inactivity threshold
    for uid, d in activity_logs.items():
        if d["online"] and d.get("last_seen"):
            cutoff = d["last_seen"] + datetime.timedelta(seconds=INACTIVITY_THRESHOLD)
            if now >= cutoff:
                # stop counting exactly at cutoff, not now
                go_offline_at(uid, cutoff)

    save_logs()

# ------------------ SLASH COMMAND ------------------
@bot.tree.command(name="timetrack", description="Check a user's tracked online time")
async def timetrack(
    interaction: discord.Interaction,
    username: discord.Member,
    show_last_message: bool = False,
    timezone: str = "UTC"
):
    uid = username.id
    ensure_user(uid)

    # Advance up to 'now' for display if still online
    now = now_utc()
    d = activity_logs[uid]
    display_total = d["total_seconds"]

    if d["online"] and d.get("last_tick"):
        # But cap at inactivity cutoff if we've silently gone inactive
        limit = now
        if d.get("last_seen"):
            cutoff = d["last_seen"] + datetime.timedelta(seconds=INACTIVITY_THRESHOLD)
            if cutoff < limit:
                limit = cutoff
        if limit > d["last_tick"]:
            display_total += int((limit - d["last_tick"]).total_seconds())

    status = "🟢 Online" if d["online"] else "⚫ Offline"
    msg = f"⏳ **{username.display_name}** has {format_time(display_total)} online.\n"
    msg += f"**Status:** {status}"

    if not d["online"] and d.get("offline_since"):
        offline_elapsed = (now - d["offline_since"]).total_seconds()
        msg += f"\n⚫ Offline for: {format_time(int(offline_elapsed))}"

    if show_last_message and uid in last_messages:
        last_msg = last_messages[uid]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        msg += f"\n💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"

    await interaction.response.send_message(msg)

# ------------------ RUN BOT ------------------
bot.run(TOKEN)
