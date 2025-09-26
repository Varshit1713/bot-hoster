import io
import random
import datetime
from PIL import Image, ImageDraw, ImageFont

# ---------- Fonts ----------
try:
    FONT_REG = ImageFont.truetype("arial.ttf", 18)
    FONT_BOLD = ImageFont.truetype("arialbd.ttf", 18)
    FONT_SMALL = ImageFont.truetype("arial.ttf", 15)
except Exception:
    FONT_REG = ImageFont.load_default()
    FONT_BOLD = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()

# ---------- Helpers ----------
def circle_avatar(img, size=48):
    mask = Image.new("L", (size, size), 0)
    dr = ImageDraw.Draw(mask)
    dr.ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img.resize((size, size)), (0, 0), mask)
    return out

def wrap_text(draw, text, font, max_width):
    if not text:
        return [""]
    words = text.split(" ")
    lines = []
    cur = ""
    for word in words:
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=font) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines

# ---------- Prank Image Generator ----------
def generate_prank_image(messages):
    """
    messages: list of dicts:
    [
        {"username": "John", "message": "Hello!", "time": "5:42am"},
        {"username": "Jane", "message": "Hi John", "time": "5:43am"}
    ]
    """
    WIDTH = 550
    BG = (54, 57, 63)
    TEXT_COLOR = (220, 221, 222)
    TIMESTAMP_COLOR = (114, 118, 125)
    AVATAR_SIZE = 48
    PADDING = 15
    LINE_SPACING = 5
    BUBBLE_COLOR = (64, 68, 75)
    MAX_TEXT_WIDTH = WIDTH - (AVATAR_SIZE + 3*PADDING)

    # Generate random avatars for each user
    avatars = {}
    for m in messages:
        if m["username"] not in avatars:
            color = random.choice([(255,0,0),(0,255,0),(0,0,255),(255,255,0),(255,0,255),(0,255,255)])
            img = Image.new("RGBA", (AVATAR_SIZE, AVATAR_SIZE), color)
            avatars[m["username"]] = circle_avatar(img, AVATAR_SIZE)

    # Estimate height
    dummy = Image.new("RGB", (WIDTH, 100))
    draw_tmp = ImageDraw.Draw(dummy)
    est_height = PADDING
    last_author = None
    for m in messages:
        if m["username"] != last_author:
            est_height += FONT_BOLD.size + 4
        lines = wrap_text(draw_tmp, m["message"], FONT_REG, MAX_TEXT_WIDTH)
        est_height += len(lines)*(FONT_REG.size+2) + LINE_SPACING
        last_author = m["username"]
    est_height += PADDING

    # Create canvas
    img = Image.new("RGBA", (WIDTH, max(est_height, 200)), BG)
    draw = ImageDraw.Draw(img)
    y = PADDING
    last_author = None
    for m in messages:
        show_avatar = (m["username"] != last_author)
        x_text = PADDING + (AVATAR_SIZE + PADDING if show_avatar else 0)

        # avatar
        if show_avatar:
            img.paste(avatars[m["username"]], (PADDING, y), avatars[m["username"]])

        # username + timestamp
        if show_avatar:
            draw.text((x_text, y), m["username"], font=FONT_BOLD, fill=TEXT_COLOR)
            ts_w = draw.textlength(m["time"], font=FONT_SMALL)
            draw.text((WIDTH-PADDING-ts_w, y+2), m["time"], font=FONT_SMALL, fill=TIMESTAMP_COLOR)
            y += FONT_BOLD.size + 2

        # message bubble
        lines = wrap_text(draw, m["message"], FONT_REG, MAX_TEXT_WIDTH)
        if lines:
            bubble_height = len(lines)*(FONT_REG.size+2)+8
            draw.rounded_rectangle([x_text-6, y-2, WIDTH-PADDING, y+bubble_height+y-2], radius=6, fill=BUBBLE_COLOR)
            for line in lines:
                draw.text((x_text, y), line, font=FONT_REG, fill=TEXT_COLOR)
                y += FONT_REG.size + 2
            y += LINE_SPACING

        last_author = m["username"]

    # Crop and return
    buf = io.BytesIO()
    img = img.crop((0, 0, WIDTH, y+PADDING))
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---------- Example usage ----------
if __name__ == "__main__":
    # Example messages
    messages = [
        {"username": "JohnDoe", "message": "Hey, this is a prank!", "time": "5:42am"},
        {"username": "Jane", "message": "What???", "time": "5:43am"},
        {"username": "JohnDoe", "message": "LOL got you!", "time": "5:44am"}
    ]
    buf = generate_prank_image(messages)
    with open("prank_test.png", "wb") as f:
        f.write(buf.getvalue())
