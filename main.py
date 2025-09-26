import discord
from discord.ext import commands
import requests
import os
import io

# ---------------- CONFIG ----------------
TOKEN = os.getenv("DISCORD_BOT_TOKEN")         # Discord Bot Token
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")  # Hugging Face API Token

# Hugging Face models for different styles
MODELS = {
    "realistic": "stabilityai/stable-diffusion-2",
    "cartoon": "akhaliq/cartoon-diffusion",
    "anime": "hakurei/waifu-diffusion"
}
# ----------------------------------------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}

@bot.event
async def on_ready():
    print(f"Bot online as {bot.user}")

@bot.command()
async def gen(ctx, style: str, *, prompt: str):
    """
    Generate an image with a chosen style.
    Usage: !gen realistic A sunset over mountains
           !gen cartoon Cute cat in a forest
           !gen anime Magical girl flying
    """
    style = style.lower()
    if style not in MODELS:
        await ctx.send(f"⚠️ Invalid style! Choose from: {', '.join(MODELS.keys())}")
        return

    embed = discord.Embed(
        title=f"Generating {style} image...",
        description=f"Prompt: `{prompt}` ⏳",
        color=0x00ff00
    )
    placeholder = await ctx.send(embed=embed)

    try:
        model_url = f"https://api-inference.huggingface.co/models/{MODELS[style]}"
        response = requests.post(model_url, headers=headers, json={"inputs": prompt})

        if response.status_code == 200:
            image_data = response.content
            image_file = discord.File(io.BytesIO(image_data), filename="generated.png")
            await ctx.send(file=image_file)
            await placeholder.delete()
        else:
            await placeholder.edit(content=f"⚠️ Failed to generate image. Status: {response.status_code}")

    except Exception as e:
        await placeholder.edit(content=f"⚠️ Error: {e}")

bot.run(TOKEN)
