import discord
from discord.ext import commands
import openai
import io
import requests
import os

# ---------- CONFIG ----------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")  # Set in Render dashboard
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Set in Render dashboard
# ----------------------------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

openai.api_key = OPENAI_API_KEY

@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")

@bot.command()
async def gen(ctx, *, description: str):
    """
    Generate a realistic image based on the description.
    Usage: !gen A sunset over a mountain lake
    """
    embed = discord.Embed(title="Image Generation", description=f"Generating image for: `{description}` ⏳", color=0x00ff00)
    message = await ctx.send(embed=embed)

    try:
        # Request image generation from OpenAI
        response = openai.Image.create(
            prompt=description,
            n=1,
            size="1024x1024"  # High-resolution image
        )

        image_url = response['data'][0]['url']

        # Fetch the image content
        image_data = requests.get(image_url).content
        image_file = discord.File(io.BytesIO(image_data), filename="generated.png")

        # Send the image
        await ctx.send(file=image_file)
        await message.delete()  # Remove loading embed

    except Exception as e:
        await message.edit(content=f"⚠️ Error generating image: {e}")

bot.run(TOKEN)
