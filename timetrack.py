# timetrack.py
import discord
from discord.ext import commands, tasks
import datetime
import json
import os

DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD = 60  # seconds
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}

class TimeTrack(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.activity_logs = self.load_logs()
        self.update_all_users.start()

    # ------------------ LOGS ------------------
    def load_logs(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    raw_logs = json.load(f)
                    activity_logs = {}
                    for user_id, data in raw_logs.items():
                        activity_logs[int(user_id)] = {
                            "total_seconds": data.get("total_seconds", 0),
                            "daily_seconds": data.get("daily_seconds", 0),
                            "weekly_seconds": data.get("weekly_seconds", 0),
                            "monthly_seconds": data.get("monthly_seconds", 0),
                            "last_activity": datetime.datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else None,
                            "online": data.get("online", False),
                            "last_message": datetime.datetime.fromisoformat(data["last_message"]) if data.get("last_message") else None
                        }
                    return activity_logs
            except Exception:
                print("⚠️ Corrupt activity_logs.json, resetting...")
                return {}
        else:
            return {}

    def save_logs(self):
        serializable_logs = {}
        for user_id, data in self.activity_logs.items():
            serializable_logs[str(user_id)] = {
                "total_seconds": data["total_seconds"],
                "daily_seconds": data.get("daily_seconds", 0),
                "weekly_seconds": data.get("weekly_seconds", 0),
                "monthly_seconds": data.get("monthly_seconds", 0),
                "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
                "online": data["online"],
                "last_message": data["last_message"].isoformat() if data.get("last_message") else None
            }
        with open(DATA_FILE, "w") as f:
            json.dump(serializable_logs, f, indent=4)

    # ------------------ HELPERS ------------------
    def format_time(self, seconds: int):
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s"

    def convert_timezone(self, dt: datetime.datetime, tz_name: str):
        tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
        return dt.astimezone(tz)

    def update_user_time(self, user_id: int):
        now = datetime.datetime.now(datetime.timezone.utc)
        user = self.activity_logs.get(user_id)
        if not user or not user["online"] or not user["last_message"]:
            return
        elapsed = (now - user["last_message"]).total_seconds()
        if elapsed <= INACTIVITY_THRESHOLD:
            user["total_seconds"] += int(elapsed)
            user["daily_seconds"] += int(elapsed)
            user["weekly_seconds"] += int(elapsed)
            user["monthly_seconds"] += int(elapsed)
            user["last_activity"] = now

    def check_inactivity(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for user_id, user in self.activity_logs.items():
            if user["online"] and user["last_message"]:
                if (now - user["last_message"]).total_seconds() > INACTIVITY_THRESHOLD:
                    user["online"] = False

    def reset_periods(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        for user in self.activity_logs.values():
            if user.get("last_daily_reset") is None or (now - user.get("last_daily_reset")).days >= 1:
                user["daily_seconds"] = 0
                user["last_daily_reset"] = now
            if user.get("last_weekly_reset") is None or (now - user.get("last_weekly_reset")).days >= 7:
                user["weekly_seconds"] = 0
                user["last_weekly_reset"] = now
            if user.get("last_monthly_reset") is None or now.month != user.get("last_monthly_reset").month:
                user["monthly_seconds"] = 0
                user["last_monthly_reset"] = now

    # ------------------ BACKGROUND TASK ------------------
    @tasks.loop(seconds=10)
    async def update_all_users(self):
        self.check_inactivity()
        self.reset_periods()
        for user_id, user in self.activity_logs.items():
            if user["online"]:
                self.update_user_time(user_id)
        self.save_logs()

    # ------------------ EVENTS ------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        user_id = message.author.id
        now = datetime.datetime.now(datetime.timezone.utc)
        if user_id not in self.activity_logs:
            self.activity_logs[user_id] = {
                "total_seconds": 0,
                "daily_seconds": 0,
                "weekly_seconds": 0,
                "monthly_seconds": 0,
                "last_activity": None,
                "online": True,
                "last_message": now
            }
        else:
            self.activity_logs[user_id]["online"] = True
            self.activity_logs[user_id]["last_message"] = now
        self.save_logs()

        # This is the key fix: allow other commands to run
        await self.bot.process_commands(message)

    # ------------------ COMMAND ------------------
    @commands.hybrid_command(name="timetrack", description="Check a user's tracked online/offline time")
    async def timetrack(self, ctx, username: discord.Member, show_last_message: bool = False, timezone: str = "UTC"):
        user_id = username.id
        if user_id not in self.activity_logs:
            await ctx.send("❌ No activity recorded for this user.", ephemeral=True)
            return

        user = self.activity_logs[user_id]
        self.update_user_time(user_id)
        online_time = user["total_seconds"]
        daily_time = user["daily_seconds"]
        weekly_time = user["weekly_seconds"]
        monthly_time = user["monthly_seconds"]

        now = datetime.datetime.now(datetime.timezone.utc)
        offline_seconds = 0
        if not user["online"] and user.get("last_message"):
            offline_seconds = int((now - user["last_message"]).total_seconds())

        msg = f"⏳ **{username.display_name}**\n"
        msg += f"🟢 Online time: {self.format_time(online_time)}\n"
        msg += f"⚫ Offline for: {self.format_time(offline_seconds)}\n"
        msg += f"📆 Daily: {self.format_time(daily_time)}, Weekly: {self.format_time(weekly_time)}, Monthly: {self.format_time(monthly_time)}\n"

        if show_last_message and user.get("last_message"):
            ts = self.convert_timezone(user["last_message"], timezone)
            msg += f"💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}]"

        await ctx.send(msg)

# ------------------ Setup ------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(TimeTrack(bot))
