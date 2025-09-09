# ------------------ TIME TRACKING ------------------
DATA_FILE = "activity_logs.json"
TIMEZONES = {
    "UTC": datetime.timezone.utc,
    "EST": datetime.timezone(datetime.timedelta(hours=-5)),
    "PST": datetime.timezone(datetime.timedelta(hours=-8)),
    "CET": datetime.timezone(datetime.timedelta(hours=1)),
}
INACTIVITY_THRESHOLD = 60  # 1 minute inactivity timeout
DAY_SECONDS = 24 * 3600
WEEK_SECONDS = 7 * DAY_SECONDS
MONTH_SECONDS = 30 * DAY_SECONDS

activity_logs = {}
last_messages = {}

# Load existing logs
if os.path.exists(DATA_FILE):
    try:
        with open(DATA_FILE, "r") as f:
            raw_logs = json.load(f)
            activity_logs = {
                int(user_id): {
                    "total_seconds": data.get("total_seconds", 0),
                    "offline_seconds": data.get("offline_seconds", 0),
                    "weekly_seconds": data.get("weekly_seconds", 0),
                    "monthly_seconds": data.get("monthly_seconds", 0),
                    "last_activity": datetime.datetime.fromisoformat(data["last_activity"]) if data.get("last_activity") else None,
                    "online": data.get("online", False),
                    "first_seen": datetime.datetime.fromisoformat(data["first_seen"]) if data.get("first_seen") else datetime.datetime.now(datetime.timezone.utc),
                    "offline_start": data.get("offline_start", None)
                }
                for user_id, data in raw_logs.items()
            }
    except:
        activity_logs = {}

def save_logs():
    serializable_logs = {
        str(user_id): {
            "total_seconds": data["total_seconds"],
            "offline_seconds": data["offline_seconds"],
            "weekly_seconds": data["weekly_seconds"],
            "monthly_seconds": data["monthly_seconds"],
            "last_activity": data["last_activity"].isoformat() if data["last_activity"] else None,
            "online": data["online"],
            "first_seen": data["first_seen"].isoformat(),
            "offline_start": data.get("offline_start").isoformat() if data.get("offline_start") else None
        }
        for user_id, data in activity_logs.items()
    }
    with open(DATA_FILE, "w") as f:
        json.dump(serializable_logs, f, indent=4)

def format_time(seconds: int):
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m}m {s}s"

def convert_timezone(dt, tz_name: str):
    tz = TIMEZONES.get(tz_name.upper(), datetime.timezone.utc)
    return dt.astimezone(tz)

def update_user_time(user_id: int, delta: int):
    user_data = activity_logs.get(user_id)
    if not user_data:
        return
    user_data["total_seconds"] += delta
    user_data["weekly_seconds"] += delta
    user_data["monthly_seconds"] += delta

def check_inactivity():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user_id, data in activity_logs.items():
        if data["online"] and data["last_activity"]:
            elapsed = (now - data["last_activity"]).total_seconds()
            if elapsed > INACTIVITY_THRESHOLD:
                data["online"] = False
                data["offline_start"] = now
                data["last_activity"] = None

def reset_periods():
    now = datetime.datetime.now(datetime.timezone.utc)
    for user_id, data in activity_logs.items():
        # Daily reset
        if (now - data.get("first_seen", now)).total_seconds() > DAY_SECONDS:
            data["daily_seconds"] = 0
            data["first_seen"] = now
        if (now - data.get("first_seen", now)).total_seconds() > WEEK_SECONDS:
            data["weekly_seconds"] = 0
            data["first_seen"] = now
        if (now - data.get("first_seen", now)).total_seconds() > MONTH_SECONDS:
            data["monthly_seconds"] = 0
            data["first_seen"] = now

# ------------------ TIME EVENTS ------------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    now = datetime.datetime.now(datetime.timezone.utc)
    user_id = message.author.id
    if user_id not in activity_logs:
        activity_logs[user_id] = {
            "total_seconds": 0,
            "offline_seconds": 0,
            "weekly_seconds": 0,
            "monthly_seconds": 0,
            "last_activity": now,
            "online": True,
            "first_seen": now,
            "offline_start": None
        }
    else:
        # User became active
        activity_logs[user_id]["last_activity"] = now
        if not activity_logs[user_id].get("online", False):
            activity_logs[user_id]["offline_start"] = None  # reset offline timer
        activity_logs[user_id]["online"] = True

    last_messages[user_id] = {"content": message.content, "timestamp": now}
    save_logs()

# ------------------ TIME LOOP ------------------
@tasks.loop(seconds=10)
async def update_all_users():
    now = datetime.datetime.now(datetime.timezone.utc)
    reset_periods()
    for user_id, data in activity_logs.items():
        if data["online"] and data.get("last_activity"):
            elapsed = (now - data["last_activity"]).total_seconds()
            delta = int(min(elapsed, 10))
            update_user_time(user_id, delta)
            data["offline_start"] = None  # reset offline timer when online
        else:
            if "offline_start" in data and data["offline_start"]:
                delta_off = (now - data["offline_start"]).total_seconds()
                data["offline_seconds"] += int(delta_off)
                data["offline_start"] = now
    check_inactivity()
    save_logs()

# ------------------ SEND TIME ------------------
async def send_time(interaction, username: discord.Member, seconds_online, seconds_offline, extra_msg=""):
    status = "🟢 Online" if activity_logs[username.id]["online"] else "⚫ Offline"
    msg = f"⏳ **{username.display_name}**\n"
    msg += f"🟢 Online time: `{format_time(seconds_online)}`\n"
    msg += f"⚫ Offline for: `{format_time(seconds_offline)}`\n\n"
    msg += "📆 **Periods**\n"
    msg += f"Daily: `{format_time(seconds_online)}`\n"
    msg += f"Weekly: `{format_time(activity_logs[username.id]['weekly_seconds'])}`\n"
    msg += f"Monthly: `{format_time(activity_logs[username.id]['monthly_seconds'])}`\n"
    if extra_msg:
        msg += f"\n{extra_msg}"
    await interaction.response.send_message(msg)

# ------------------ SLASH COMMANDS ------------------
@bot.tree.command(name="timetrack", description="Show current online/offline time")
async def timetrack(interaction: discord.Interaction, username: discord.Member, show_last_message: bool = False, timezone: str = "UTC"):
    user = activity_logs.get(username.id)
    offline_time = 0
    if not user["online"] and "offline_start" in user and user["offline_start"]:
        offline_time = int((datetime.datetime.now(datetime.timezone.utc) - user["offline_start"]).total_seconds())
    extra_msg = ""
    if show_last_message and username.id in last_messages:
        last_msg = last_messages[username.id]
        ts = convert_timezone(last_msg["timestamp"], timezone)
        extra_msg = f"💬 Last message ({timezone}): [{ts.strftime('%Y-%m-%d %H:%M:%S')}] {last_msg['content']}"
    await send_time(interaction, username, user["total_seconds"], user["offline_seconds"] + offline_time, extra_msg)

# ------------------ RMUTE INTEGRATION ------------------
active_mutes = {}  # {user_id: {"end_time": datetime, "reason": str, "proof": str}}
GUILD_ID = 123456789012345678  # replace with your server ID
MUTE_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

def parse_duration(duration: str):
    if not duration:
        return 60
    try:
        unit = duration[-1]
        val = int(duration[:-1])
        if unit == "s":
            return val
        elif unit == "m":
            return val * 60
        elif unit == "h":
            return val * 3600
        elif unit == "d":
            return val * 86400
    except:
        return 60
    return 60

async def apply_mute(member: discord.Member, duration_seconds: int, reason: str, proof: str = None):
    role = member.guild.get_role(MUTE_ROLE_ID)
    if role and role not in member.roles:
        await member.add_roles(role)

    end_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
    active_mutes[member.id] = {"end_time": end_time, "reason": reason, "proof": proof}

    try:
         await member.send(f"You have been muted in {member.guild.name} until {end_time} UTC.\nReason: {reason}\nProof: {proof if proof else 'None'}")
    except:
        pass

    log_channel = member.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="🔇 User Muted", color=discord.Color.red())
        embed.add_field(name="User", value=member.mention, inline=False)
        embed.add_field(name="Duration", value=str(datetime.timedelta(seconds=duration_seconds)), inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        if proof:
            embed.add_field(name="Proof", value=proof, inline=False)
        await log_channel.send(embed=embed)

async def remove_mute(user_id: int):
    data = active_mutes.pop(user_id, None)
    if not data:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    member = guild.get_member(user_id)
    if not member:
        return
    role = guild.get_role(MUTE_ROLE_ID)
    if role in member.roles:
        await member.remove_roles(role)
    try:
        await member.send(f"You have been unmuted in {guild.name}.")
    except:
        pass
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="✅ User Unmuted", color=discord.Color.green())
        embed.add_field(name="User", value=member.mention)
        await log_channel.send(embed=embed)

# ------------------ RMUTE LOOP ------------------
@tasks.loop(seconds=10)
async def check_mutes():
    now = datetime.datetime.utcnow()
    to_remove = [uid for uid, data in active_mutes.items() if now >= data["end_time"]]
    for uid in to_remove:
        await remove_mute(uid)

# ------------------ RMUTE COMMANDS ------------------
def has_mute_perm(ctx):
    return ctx.author.guild_permissions.mute_members

@bot.command(name="qmute")
@commands.check(has_mute_perm)
async def qmute(ctx, duration: str = None, *, reason: str = "No reason provided"):
    if not ctx.message.reference:
        await ctx.send("❌ You must reply to a message to mute a user.", delete_after=5)
        return

    replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
    member = replied_msg.author

    dur_seconds = parse_duration(duration)
    proof = f"[Message link](https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/{ctx.message.reference.message_id})"
    await apply_mute(member, dur_seconds, reason, proof)

    try:
        await ctx.message.delete()
    except:
        pass
    await ctx.send(f"✅ {member.mention} has been muted.", delete_after=5)

@bot.tree.command(name="rmute", description="Mute a user by replying to a message")
async def rmute(interaction: discord.Interaction, duration: str = None, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.mute_members:
        await interaction.response.send_message("❌ You do not have permission to mute members.", ephemeral=True)
        return

    if not interaction.data.get("resolved", {}).get("messages"):
        await interaction.response.send_message("❌ You must reply to a message.", ephemeral=True)
        return

    refs = interaction.data["resolved"]["messages"]
    message_id = list(refs.keys())[0]
    channel_id = int(refs[message_id]["channel_id"])
    channel = bot.get_channel(channel_id)
    message = await channel.fetch_message(int(message_id))
    member = message.author

    dur_seconds = parse_duration(duration)
    proof = f"[Message link](https://discord.com/channels/{interaction.guild.id}/{channel.id}/{message.id})"
    await apply_mute(member, dur_seconds, reason, proof)
    await interaction.response.send_message(f"✅ {member.mention} has been muted.", ephemeral=True)

# ------------------ BOT READY EVENT ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    if not update_all_users.is_running():
        update_all_users.start()
    if not check_mutes.is_running():
        check_mutes.start()

bot.run(TOKEN)
