import discord
from discord.ext import commands, tasks
import datetime
import asyncio
import os
import sys

# ------------------ CONFIG ------------------
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    print("❌ DISCORD_TOKEN environment variable not set")
    sys.exit(1)

GUILD_ID = 123456789012345678  # replace with your server ID
MUTE_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648

# ------------------ BOT SETUP ------------------
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ MUTE STORAGE ------------------
active_mutes = {}  # {user_id: {"end_time": datetime, "reason": str, "proof": str}}

# ------------------ HELPERS ------------------
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

    # DM user
    try:
        await member.send(f"You have been muted in {member.guild.name} until {end_time} UTC.\nReason: {reason}\nProof: {proof if proof else 'None'}")
    except:
        pass

    # Log embed
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

# ------------------ BACKGROUND TASK ------------------
@tasks.loop(seconds=10)
async def check_mutes():
    now = datetime.datetime.utcnow()
    to_remove = [uid for uid, data in active_mutes.items() if now >= data["end_time"]]
    for uid in to_remove:
        await remove_mute(uid)

# ------------------ COMMANDS ------------------
def has_mute_perm(ctx):
    return ctx.author.guild_permissions.mute_members

@bot.command(name="qmute")
@commands.check(has_mute_perm)
async def qmute(ctx, duration: str = None, *, reason: str = "No reason provided"):
    if not ctx.message.reference:
        await ctx.send("❌ You must reply to a message to mute a user.", delete_after=5)
        return

    # Get member from replied message
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

# ------------------ EVENTS ------------------
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    if not check_mutes.is_running():
        check_mutes.start()

bot.run(TOKEN)
