# rmute.py
import discord
from discord.ext import commands, tasks
import datetime

MUTE_ROLE_ID = 1410423854563721287
LOG_CHANNEL_ID = 1403422664521023648
GUILD_ID = 1403359962369097739  # Replace with your guild ID

class MuteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_mutes = {}  # {user_id: {"end_time": datetime, "reason": str, "proof": str}}
        if not self.check_mutes.is_running():
            self.check_mutes.start()

    # ------------------ HELPERS ------------------
    def parse_duration(self, duration: str):
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

    async def apply_mute(self, member: discord.Member, duration_seconds: int, reason: str, proof: str = None):
        role = member.guild.get_role(MUTE_ROLE_ID)
        if role and role not in member.roles:
            await member.add_roles(role)

        end_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=duration_seconds)
        self.active_mutes[member.id] = {"end_time": end_time, "reason": reason, "proof": proof}

        # DM user
        try:
            await member.send(
                f"You have been muted in {member.guild.name} until {end_time} UTC.\n"
                f"Reason: {reason}\nProof: {proof if proof else 'None'}"
            )
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

    async def remove_mute(self, user_id: int):
        data = self.active_mutes.pop(user_id, None)
        if not data:
            return
        guild = self.bot.get_guild(GUILD_ID)
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
    async def check_mutes(self):
        now = datetime.datetime.utcnow()
        to_remove = [uid for uid, data in self.active_mutes.items() if now >= data["end_time"]]
        for uid in to_remove:
            await self.remove_mute(uid)

    # ------------------ COMMAND ------------------
    @commands.hybrid_command(name="rmute", description="Mute a user or reply target")
    @commands.has_permissions(mute_members=True)
    async def rmute(self, ctx_or_interaction, duration: str = None, reason: str = "No reason provided", user: discord.Member = None):
        # Determine if it's a slash command (Interaction) or text command (Context)
        interaction = None
        ctx = None
        target_member = user
        proof = None

        if isinstance(ctx_or_interaction, discord.Interaction):
            interaction = ctx_or_interaction
            ctx = await interaction.channel.fetch_message(interaction.id) if interaction.channel else None
            # Check replied-to message
            resolved = interaction.data.get("resolved", {}).get("messages")
            if resolved and not target_member:
                msg_id = list(resolved.keys())[0]
                channel_id = int(resolved[msg_id]["channel_id"])
                channel = self.bot.get_channel(channel_id)
                msg = await channel.fetch_message(int(msg_id))
                target_member = msg.author
                proof = f"[Message link](https://discord.com/channels/{interaction.guild.id}/{channel.id}/{msg.id})"
        else:
            ctx = ctx_or_interaction
            # Check replied-to message if no user specified
            if not target_member and ctx.message.reference:
                replied_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                target_member = replied_msg.author
                proof = f"[Message link](https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/{ctx.message.reference.message_id})"

        if not target_member:
            msg_text = "❌ You must specify a user or reply to their message."
            if interaction:
                await interaction.response.send_message(msg_text, ephemeral=True)
            else:
                await ctx.send(msg_text)
            return

        dur_seconds = self.parse_duration(duration)
        await self.apply_mute(target_member, dur_seconds, reason, proof)

        # Send response
        msg = f"✅ **User:** {target_member.display_name}\n**Duration:** {duration}\n**Reason:** {reason}"
        if proof:
            msg += f"\n**Proof:** {proof}"

        if interaction:
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            try:
                await ctx.message.delete()
            except:
                pass
            await ctx.send(msg)

async def setup(bot):
    await bot.add_cog(MuteCog(bot))
