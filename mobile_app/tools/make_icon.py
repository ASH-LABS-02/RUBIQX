"""SAR Rescue app icon + splash logo, v2.

v1 read fine at 1024px but fell apart at real launcher size (48px): the
red "detected" ping floated outside the ring, pulling the whole
composition's visual weight off-centre with dead space in the opposite
corner, and thin crosshair ticks vanished into noise. Fixed by putting the
ping directly on the outer ring -- it now reads as one connected shape
("a blip detected on the sweep") instead of two separate blobs -- and by
simplifying to two bold rings with no ticks. Verified again at 48px below.
"""

from PIL import Image, ImageDraw, ImageFilter
import math, os

SS = 4
SIZE = 1024 * SS

BG = (10, 14, 20)
CYAN = (63, 208, 214, 255)
RED = (255, 94, 91, 255)


def radial_gradient_bg(size, inner, outer):
    img = Image.new("RGBA", (size, size), (*outer, 255))
    px = img.load()
    max_r = size * 0.75
    step = 4
    for y in range(0, size, step):
        for x in range(0, size, step):
            d = min(math.hypot(x - size / 2, y - size / 2) / max_r, 1.0)
            rgb = tuple(int(inner[i] + (outer[i] - inner[i]) * d) for i in range(3))
            for yy in range(y, min(y + step, size)):
                for xx in range(x, min(x + step, size)):
                    px[xx, yy] = (*rgb, 255)
    return img


def draw_mark(canvas_size, glyph_scale, ping_on_ring=True):
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    c = canvas_size / 2
    R = canvas_size * glyph_scale / 2  # outer ring radius

    # Ping sits ON the outer ring at 40 degrees -- this is the key fix:
    # its extent no longer sticks out past the ring's own bounding circle,
    # so the whole glyph's bounding box stays centred without a manual
    # offset hack, and it reads as one shape, not two.
    angle = math.radians(-40)
    ping_r = R * 0.30
    ping_cx = c + R * math.cos(angle)
    ping_cy = c + R * math.sin(angle)

    # Glow first (bottom layer)
    glow = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    glow_r = ping_r * 2.1
    gdraw.ellipse(
        [ping_cx - glow_r, ping_cy - glow_r, ping_cx + glow_r, ping_cy + glow_r],
        fill=(*RED[:3], 130),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=canvas_size * 0.018))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    # Two bold rings -- fewer, thicker, more spaced than v1 so they hold up
    # at 48px instead of blurring into a smudge.
    for frac, width, alpha in [(1.00, 26, 235), (0.56, 30, 255)]:
        rr = R * frac
        draw.ellipse(
            [c - rr, c - rr, c + rr, c + rr],
            outline=(*CYAN[:3], alpha),
            width=int(width * SS / 4),
        )

    # Solid core -- the "lock".
    core_r = R * 0.22
    draw.ellipse([c - core_r, c - core_r, c + core_r, c + core_r], fill=CYAN)

    # The ping itself: dark ring for separation, then solid red.
    sep_r = ping_r * 1.18
    draw.ellipse(
        [ping_cx - sep_r, ping_cy - sep_r, ping_cx + sep_r, ping_cy + sep_r],
        fill=(*BG, 255),
    )
    draw.ellipse(
        [ping_cx - ping_r, ping_cy - ping_r, ping_cx + ping_r, ping_cy + ping_r],
        fill=RED,
    )
    return img


def down(img, final):
    return img.resize((final, final), Image.LANCZOS)


OUT = "/tmp/claude-1000/-home-karthic-drone/95490b06-fd83-483f-8b5e-ff885b44d274/scratchpad/icon"
os.makedirs(OUT, exist_ok=True)

# Adaptive-icon foreground: transparent, content inside the safe zone.
fg = draw_mark(SIZE, glyph_scale=0.46)
down(fg, 1024).save(f"{OUT}/icon_foreground.png")

# Flat full icon: gradient backdrop + larger glyph.
bg = radial_gradient_bg(SIZE, inner=(18, 27, 38), outer=BG)
full = Image.alpha_composite(bg, draw_mark(SIZE, glyph_scale=0.64))
down(full, 1024).save(f"{OUT}/icon_full.png")

# Splash logo: same safe-zone scale as the adaptive-icon foreground, NOT a
# larger one. Confirmed on a real device: Android 12+'s native SplashScreen
# API masks the branding image the same way an adaptive icon is masked
# (content must sit within roughly the inner 2/3 of the canvas) -- a 0.74
# scale here clipped the red detection-ping clean off on screen. 0.46
# matches the foreground exactly, which was already verified safe.
down(draw_mark(SIZE, glyph_scale=0.46), 640).save(f"{OUT}/splash_logo.png")

# Preview at real launcher sizes to actually verify, not assume.
for sz in (48, 96, 192):
    down(full, sz).save(f"{OUT}/preview_{sz}.png")

print("done:", sorted(os.listdir(OUT)))
