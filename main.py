# discord_copytext_bot.py
# Simple Discord bot that fetches a URL and returns visible text.
# Usage in Discord:  !copytext https://example.com
# Optionally:       !copytext https://example.com redact

import os
import re
import aiohttp
import asyncio
from bs4 import BeautifulSoup
import discord
from discord.ext import commands

# --------- CONFIG ----------
COMMAND_PREFIX = "!"
MAX_EMBED_FIELD = 1900   # safe limit for embed fields
MAX_INLINE_TEXT = 1800   # fallback for in-message text
REDACT_DANGEROUS = False  # set True to enable redaction by default
# ---------------------------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)

# Optional redaction patterns (enable by setting REDACT_DANGEROUS True or using 'redact' arg)
DANGEROUS_PATTERNS = [
    r'loadstring\s*\([^)]*\)',
    r'game\s*:\s*HttpGet\s*\([^)]*\)',
    r'HttpGet\s*\([^)]*\)',
    r'HttpPost\s*\([^)]*\)',
    r'\beval\s*\([^)]*\)',
    r'\bexec\s*\([^)]*\)',
    r'fetch\s*\([^)]*\)',
    r'new\s+Function\s*\([^)]*\)',
]
REDACT_RE = re.compile("|".join(f'({p})' for p in DANGEROUS_PATTERNS), flags=re.IGNORECASE)
def redact(text: str) -> str:
    return REDACT_RE.sub("[REDACTED_EXECUTION_CODE]", text)

async def fetch_page(session: aiohttp.ClientSession, url: str, timeout: int = 25):
    """
    Fetches the URL following redirects, returns (final_url, html, status)
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; CopyTextBot/1.0)"}
    async with session.get(url, headers=headers, allow_redirects=True, timeout=timeout) as resp:
        final = str(resp.url)
        txt = await resp.text(errors="ignore")
        return final, txt, resp.status

def extract_visible_text(html: str) -> str:
    """
    Extract visible text from HTML using BeautifulSoup.
    Removes scripts/styles and returns cleaned text.
    """
    soup = BeautifulSoup(html, "html.parser")

    # remove script/style/meta/noscript
    for tag in soup(["script", "style", "noscript", "meta", "iframe", "svg"]):
        tag.decompose()

    # If the page uses <article>, prefer that
    article = soup.find("article")
    if article:
        text = article.get_text(separator="\n", strip=True)
    else:
        text = soup.get_text(separator="\n", strip=True)

    # collapse multiple blank lines
    text = re.sub(r'\n{2,}', '\n\n', text)
    return text.strip()

@bot.command(name="copytext", help="Fetch a URL and copy visible text. Usage: !copytext <url> [redact]")
async def copytext(ctx, url: str, mode: str = ""):
    """
    Example commands:
      !copytext https://example.com
      !copytext https://example.com redact
    """
    # validation
    if not (url.startswith("http://") or url.startswith("https://")):
        await ctx.reply("Please include http:// or https:// in the URL.", mention_author=False)
        return

    do_redact = REDACT_DANGEROUS or (mode.lower() == "redact")

    await ctx.trigger_typing()
    try:
        async with aiohttp.ClientSession() as sess:
            final_url, html, status = await fetch_page(sess, url)
    except Exception as e:
        await ctx.reply(f"Failed to fetch URL: {e}", mention_author=False)
        return

    text = extract_visible_text(html)
    if do_redact:
        text = redact(text)

    # If nothing interesting
    if not text:
        await ctx.reply(f"No visible text found (HTTP {status}) for: {final_url}", mention_author=False)
        return

    # If short, reply directly (but keep under Discord message limits)
    if len(text) <= MAX_INLINE_TEXT:
        reply = f"**Resolved:** {final_url}\n**HTTP:** {status}\n\n```\n{text}\n```"
        await ctx.reply(reply, mention_author=False)
        return

    # If longer, send as a file
    filename = "page_text.txt"
    path = f"/tmp/{filename}"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Source: {final_url}\nHTTP: {status}\n\n")
            f.write(text)
        # send file
        await ctx.reply("Text is long — uploading as file:", file=discord.File(path))
    except Exception as e:
        # fallback: send truncated embed fields
        truncated = text[:MAX_EMBED_FIELD] + "\n\n...[truncated]"
        embed = discord.Embed(title="Resolved text (truncated)", description=f"Source: {final_url}\nHTTP: {status}")
        embed.add_field(name="Text (truncated)", value=f"```text\n{truncated}\n```", inline=False)
        await ctx.reply(embed=embed, mention_author=False)
    finally:
        try:
            os.remove(path)
        except Exception:
            pass

@bot.command(name="helpme")
async def helpme(ctx):
    txt = ("Simple `copytext` bot.\n"
           "`!copytext <url>` — fetch and return visible text from the page.\n"
           "`!copytext <url> redact` — same but redact common loader/execution patterns.\n\n"
           "Use responsibly. Do not scan sites you don't have permission to access.")
    await ctx.reply(txt, mention_author=False)

if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_BOT_TOKEN") or "REPLACE_WITH_YOUR_TOKEN"
    if TOKEN == "REPLACE_WITH_YOUR_TOKEN":
        print("Set your bot token in DISCORD_BOT_TOKEN environment variable or edit the script.")
    else:
        bot.run(TOKEN)
