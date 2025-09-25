# -----------------------------
# Command: !mcustomize
# Allows configuration of mute role, channels, log settings, and roles
# -----------------------------
@bot.command(name="mcustomize")
@commands.has_permissions(administrator=True)
async def mcustomize(ctx, option: str = None, *, value: str = None):
    """
    Example usage:
    !mcustomize mute_role <role_id>
    !mcustomize timetrack_channel <channel_id>
    !mcustomize log_channel <channel_id>
    !mcustomize staff_ping_role <role_id>
    !mcustomize higher_staff_ping_role <role_id>
    !mcustomize add_rcache_role <role_id>
    !mcustomize remove_rcache_role <role_id>
    """
    if not option or not value:
        await ctx.send("Usage: !mcustomize <option> <value>")
        return
    try:
        if option.lower() == "mute_role":
            DATA['mute_role_id'] = int(value)
        elif option.lower() == "timetrack_channel":
            DATA['timetrack_channel_id'] = int(value)
        elif option.lower() == "log_channel":
            DATA['log_channel_id'] = int(value)
        elif option.lower() == "staff_ping_role":
            DATA['staff_ping_role_id'] = int(value)
        elif option.lower() == "higher_staff_ping_role":
            DATA['higher_staff_ping_role_id'] = int(value)
        elif option.lower() == "add_rcache_role":
            DATA['rcache_roles'].append(int(value))
            DATA['rcache_roles'] = list(set(DATA['rcache_roles']))
        elif option.lower() == "remove_rcache_role":
            DATA['rcache_roles'] = [r for r in DATA['rcache_roles'] if r != int(value)]
        else:
            await ctx.send("Unknown option.")
            return
        await persist()
        await ctx.send(f"Option `{option}` updated successfully.")
    except Exception as e:
        await ctx.send(f"Error updating option: {e}")

# -----------------------------
# Command: !mrmute (Multiple users)
# -----------------------------
@bot.command(name="mrmute")
@commands.has_permissions(manage_roles=True)
async def mrmute(ctx, users: commands.Greedy[discord.Member], duration: str = None, *, reason: str = None):
    if not users:
        await ctx.send("You must mention at least one user.")
        return
    seconds = parse_duration_to_seconds(duration)
    if seconds == -1:
        await ctx.send("Invalid duration format.")
        return
    for user in users:
        try:
            await apply_mute(ctx.guild, user, ctx.author, seconds, reason or "No reason provided")
        except Exception as e:
            await ctx.send(f"Failed to mute {user}: {e}")
    # Send tracking embed
    tracking_channel_id = DATA.get('log_channel_id')
    if tracking_channel_id:
        ch = ctx.guild.get_channel(tracking_channel_id)
        if ch:
            embed = discord.Embed(title="RMute Applied", color=discord.Color.orange())
            embed.add_field(name="Moderator", value=str(ctx.author))
            embed.add_field(name="Users", value=", ".join([str(u) for u in users]))
            embed.add_field(name="Duration", value=format_timedelta(timedelta(seconds=seconds)) if seconds else "Permanent")
            embed.add_field(name="Reason", value=reason or "No reason provided")
            embed.timestamp = now_utc()
            await ch.send(embed=embed)
    await ctx.send("RMute applied successfully.")

# -----------------------------
# Command: !mrunmute (Single user)
# -----------------------------
@bot.command(name="mrunmute")
@commands.has_permissions(manage_roles=True)
async def mrunmute(ctx, user: discord.Member):
    await remove_mute(ctx.guild, user, ctx.author)
    tracking_channel_id = DATA.get('log_channel_id')
    if tracking_channel_id:
        ch = ctx.guild.get_channel(tracking_channel_id)
        if ch:
            embed = discord.Embed(title="Runmute Applied", color=discord.Color.green())
            embed.add_field(name="Moderator", value=str(ctx.author))
            embed.add_field(name="User", value=str(user))
            embed.timestamp = now_utc()
            await ch.send(embed=embed)
    await ctx.send(f"{user} has been unmuted.")

# -----------------------------
# Command: !mrmlb
# Shows top 10 users who applied rmutes the most
# -----------------------------
@bot.command(name="mrmlb")
async def mrmlb(ctx):
    sorted_data = sorted(DATA.get('rmute_usage', {}).items(), key=lambda x: x[1], reverse=True)[:10]
    embed = discord.Embed(title="RMute Leaderboard", color=discord.Color.blurple())
    for idx, (user_id, count) in enumerate(sorted_data, start=1):
        user = ctx.guild.get_member(int(user_id))
        embed.add_field(name=f"{idx}. {user or user_id}", value=f"RMutes applied: {count}", inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# Command: !mrcache
# Shows deleted messages/images
# -----------------------------
@bot.command(name="mrcache")
async def mrcache(ctx, limit: int = 10):
    if not any(r.id in DATA.get('rcache_roles', []) for r in ctx.author.roles):
        await ctx.send("You do not have permission to view the cache.")
        return
    messages = list(DATA.get('cache', {}).values())[-limit:]
    if not messages:
        await ctx.send("Cache is empty.")
        return
    embed = discord.Embed(title="Recent Deleted Messages", color=discord.Color.dark_gold())
    for msg in messages:
        content = msg.get('content', 'No content')
        author = msg.get('author', 'Unknown')
        embed.add_field(name=f"Author: {author}", value=content, inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# Command: !mtlb (timetrack leaderboard for RCACHE_ROLES)
# -----------------------------
@bot.command(name="mtlb")
async def mtlb(ctx):
    records = []
    roles_filter = DATA.get('rcache_roles', [])
    for uid, rec in DATA.get('users', {}).items():
        member = ctx.guild.get_member(int(uid))
        if not member:
            continue
        if roles_filter and not any(r.id in roles_filter for r in member.roles):
            continue
        total_seconds = rec.get('total_online_seconds', 0)
        records.append((member.display_name, total_seconds))
    records.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard", color=discord.Color.blurple())
    for idx, (name, seconds) in enumerate(records[:10], start=1):
        embed.add_field(name=f"{idx}. {name}", value=f"Total online: {format_timedelta(timedelta(seconds=seconds))}", inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# Command: !mtdm (timetrack leaderboard for non RCACHE_ROLES)
# -----------------------------
@bot.command(name="mtdm")
async def mtdm(ctx):
    records = []
    roles_filter = DATA.get('rcache_roles', [])
    for uid, rec in DATA.get('users', {}).items():
        member = ctx.guild.get_member(int(uid))
        if not member:
            continue
        if roles_filter and any(r.id in roles_filter for r in member.roles):
            continue
        total_seconds = rec.get('total_online_seconds', 0)
        records.append((member.display_name, total_seconds))
    records.sort(key=lambda x: x[1], reverse=True)
    embed = discord.Embed(title="Timetrack Leaderboard (Other Users)", color=discord.Color.blurple())
    for idx, (name, seconds) in enumerate(records[:10], start=1):
        embed.add_field(name=f"{idx}. {name}", value=f"Total online: {format_timedelta(timedelta(seconds=seconds))}", inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# Command: !mrping (Staff ping)
# -----------------------------
@bot.command(name="mrping")
async def mrping(ctx):
    role_id = DATA.get('staff_ping_role_id')
    if not role_id:
        await ctx.send("Staff ping role not configured.")
        return
    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.send("Staff ping role not found.")
        return
    await ctx.send(f"{role.mention}", delete_after=5)

# -----------------------------
# Command: !mhsping (Higher staff ping)
# -----------------------------
@bot.command(name="mhsping")
async def mhsping(ctx):
    role_id = DATA.get('higher_staff_ping_role_id')
    if not role_id:
        await ctx.send("Higher staff ping role not configured.")
        return
    role = ctx.guild.get_role(int(role_id))
    if not role:
        await ctx.send("Higher staff ping role not found.")
        return
    await ctx.send(f"{role.mention}", delete_after=5)

# -----------------------------
# Command: !mrdm (opt-out from bot DMs)
# -----------------------------
@bot.command(name="mrdm")
async def mrdm(ctx):
    uid = str(ctx.author.id)
    if uid in DATA.get('rdm_users', []):
        DATA['rdm_users'].remove(uid)
        await ctx.send("You have opted back into bot DMs.")
    else:
        DATA['rdm_users'].append(uid)
        await ctx.send("You have opted out of bot DMs.")
    await persist()

# -----------------------------
# Command: !mpurge
# -----------------------------
@bot.command(name="mpurge")
@commands.has_permissions(manage_messages=True)
async def mpurge(ctx, limit: int = 10):
    if limit <= 0:
        await ctx.send("Limit must be positive.")
        return
    deleted = await ctx.channel.purge(limit=limit)
    tracking_channel_id = DATA.get('log_channel_id')
    if tracking_channel_id:
        ch = ctx.guild.get_channel(tracking_channel_id)
        if ch:
            embed = discord.Embed(title="Purge Action", color=discord.Color.red())
            embed.add_field(name="Moderator", value=str(ctx.author))
            embed.add_field(name="Channel", value=str(ctx.channel))
            embed.add_field(name="Messages Deleted", value=str(len(deleted)))
            embed.timestamp = now_utc()
            await ch.send(embed=embed)
    await ctx.send(f"Deleted {len(deleted)} messages.", delete_after=5)

# -----------------------------
# Command: !mhelp
# -----------------------------
@bot.command(name="mhelp")
async def mhelp(ctx):
    embed = discord.Embed(title="Bot Commands", color=discord.Color.blue())
    embed.add_field(name="!mcustomize", value="Configure roles, channels, and log settings", inline=False)
    embed.add_field(name="!mrmute [users] [duration] [reason]", value="Mute multiple users", inline=False)
    embed.add_field(name="!mrunmute [user]", value="Unmute a user", inline=False)
    embed.add_field(name="!mrmlb", value="Show RMute leaderboard", inline=False)
    embed.add_field(name="!mrcache", value="View deleted messages/images", inline=False)
    embed.add_field(name="!mtlb", value="Timetrack leaderboard (RCACHE roles)", inline=False)
    embed.add_field(name="!mtdm", value="Timetrack leaderboard (Other users)", inline=False)
    embed.add_field(name="!mrping", value="Ping staff", inline=False)
    embed.add_field(name="!mhsping", value="Ping higher staff", inline=False)
    embed.add_field(name="!mrdm", value="Opt-out/opt-in for bot DMs", inline=False)
    embed.add_field(name="!mpurge [limit]", value="Delete multiple messages", inline=False)
    await ctx.send(embed=embed)

# -----------------------------
# Run the bot
# -----------------------------
bot.run(os.environ.get("DISCORD_TOKEN"))
