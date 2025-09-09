# timetrack.py
import discord
from discord.ext import commands, tasks
import datetime
import json
import os

DATA_FILE = "activity_logs.json"
INACTIVITY_THRESHOLD = 60  # seconds

class TimeTrack(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.activity_logs = self.load_logs()
        self.update_all_users.start()

    def load_logs(self):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                try:
                    raw_logs = json.load(f)
                    logs = {}
                    for user_id, data in raw_logs.items():
                        logs[int(user_id)] = {
                            "total_seconds": data.get("total_seconds", 0),
                            "daily_seconds": data.get("daily_seconds", 0),
                            "weekly_seconds": data.get("weekly_seconds", 0),
                            "monthly_seconds": data.get("monthly_seconds", 0),
                            "last_message": datetime.datetime.fromisoformat(data["last_message"]) if data.get("last_message") else None,
                            "online": data.get("online", False),
                        }
                    return logs
                except:
                    print("⚠️ Corrupt logs, resetting...")
                    return {}
        return {}

    def save_logs(self):
        serializable = {}
        for uid, data in self.activity_logs.items():
            serializable[str(uid)] = {
                "total_seconds": data["total_seconds"],
                "daily_seconds": data.get("daily_seconds", 0),
                "weekly_seconds": data.get("weekly_seconds", 0),
                "monthly_seconds": data.get("monthly_seconds", 0),
                "last_message": data["last_message"].isoformat() if data.get("last_message") else None,
                "online": data.get("online", False)
            }
        with open(DATA_FILE, "w") as f:
            json.dump(serializable, f, indent=4)

    def format_time(self, seconds: int):
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s"

    def update_user_time(self, user_id: int):
        now = datetime.datetime.utcnow()
        user = self.activity_logs.get(user_id)
        if not user or not user["last_message"] or not user["online"]:
            return
        elapsed = (now - user["last_message"]).total_seconds()
        if elapsed <= INACTIVITY_THRESHOLD:
            user["total_seconds"] += int(elapsed)
            user["daily_seconds"] += int(elapsed)
            user["weekly_seconds"] += int(elapsed)
            user["monthly_seconds"] += int(elapsed)
        user["last_message"] = now

    @tasks.loop(seconds=10)
    async def update_all_users(self):
        for uid, user in self.activity_logs.items():
            self.update_user_time(uid)
        self.save_logs()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        uid = message.author.id
        now = datetime.datetime.utcnow()
        if uid not in self.activity_logs:
            self.activity_logs[uid] = {
                "total_seconds": 0,
                "daily_seconds": 0,
                "weekly_seconds": 0,
                "monthly_seconds": 0,
                "last_message": now,
                "online": True
            }
        else:
            self.activity_logs[uid]["online"] = True
            self.activity_logs[uid]["last_message"] = now
        self.save_logs()

    @commands.hybrid_command(name="timetrack", description="Check a user's tracked online/offline time")
    async def timetrack(self, ctx, member: discord.Member):
        uid = member.id
        if uid not in self.activity_logs:
            # If the user has never sent a message, initialize their log
            self.activity_logs[uid] = {
                "total_seconds": 0,
                "daily_seconds": 0,
                "weekly_seconds": 0,
                "monthly_seconds": 0,
                "last_message": None,
                "online": False
            }
        user = self.activity_logs[uid]
        online_time = user["total_seconds"]
        offline_time = 0
        if user["last_message"] and not user["online"]:
            offline_time = int((datetime.datetime.utcnow() - user["last_message"]).total_seconds())

        msg = (
            f"⏳ **{member.display_name}**\n"
            f"🟢 Online time: {self.format_time(online_time)}\n"
            f"⚫ Offline for: {self.format_time(offline_time)}\n"
            f"📆 Daily: {self.format_time(user['daily_seconds'])}, "
            f"Weekly: {self.format_time(user['weekly_seconds'])}, "
            f"Monthly: {self.format_time(user['monthly_seconds'])}"
        )
        await ctx.send(msg)

async def setup(bot):
    await bot.add_cog(TimeTrack(bot))
