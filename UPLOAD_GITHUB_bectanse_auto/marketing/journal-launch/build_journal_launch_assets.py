#!/usr/bin/env python3
"""Exports déterministes pour le lancement du Journal Bectanse."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "static" / "marketing" / "journal-launch" / "source"
OUTPUT = ROOT / "static" / "marketing" / "journal-launch" / "final"
LOGO = ROOT / "static" / "icons" / "bectanse-app-icon-master.png"

FONT_BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"

ORANGE = "#ff5a1f"
ORANGE_LIGHT = "#ff924f"
WHITE = "#f7f5f0"
MUTED = "#a9ada7"
GREEN = "#65e680"


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


def cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    source = image.convert("RGB")
    scale = max(size[0] / source.width, size[1] / source.height)
    resized = source.resize(
        (round(source.width * scale), round(source.height * scale)),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def add_vertical_shade(image: Image.Image, top_alpha: int = 60,
                       bottom_alpha: int = 150) -> Image.Image:
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    pixels = overlay.load()
    height = image.height
    for y in range(height):
        progress = y / max(1, height - 1)
        alpha = int(top_alpha + (bottom_alpha - top_alpha) * progress)
        for x in range(image.width):
            pixels[x, y] = (0, 0, 0, alpha)
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def round_image(image: Image.Image, size: tuple[int, int], radius: int) -> Image.Image:
    fitted = cover(image, size).convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0], size[1]), radius=radius, fill=255)
    fitted.putalpha(mask)
    return fitted


def draw_brand(canvas: Image.Image, x: int, y: int, icon_size: int = 76) -> None:
    logo = Image.open(LOGO).convert("RGBA").resize((icon_size, icon_size), Image.Resampling.LANCZOS)
    canvas.alpha_composite(logo, (x, y))
    draw = ImageDraw.Draw(canvas)
    draw.text((x + icon_size + 18, y + 5), "BECTANSE", font=font(FONT_BOLD, 34), fill=WHITE)
    draw.text((x + icon_size + 18, y + 43), "ACADÉMIE", font=font(FONT_BOLD, 21), fill=ORANGE)


def draw_pill(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
              label: str, fill=(15, 16, 15, 220), outline="#4e2b1d",
              text_fill=ORANGE_LIGHT, size=22) -> None:
    draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=fill,
                           outline=outline, width=2)
    bbox = draw.textbbox((0, 0), label, font=font(FONT_BOLD, size))
    tx = box[0] + (box[2] - box[0] - (bbox[2] - bbox[0])) / 2
    ty = box[1] + (box[3] - box[1] - (bbox[3] - bbox[1])) / 2 - 2
    draw.text((tx, ty), label, font=font(FONT_BOLD, size), fill=text_fill)


def draw_cta(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
             label: str) -> None:
    draw.rounded_rectangle(box, radius=26, fill=ORANGE)
    bbox = draw.textbbox((0, 0), label, font=font(FONT_BOLD, 31))
    tx = box[0] + (box[2] - box[0] - (bbox[2] - bbox[0])) / 2
    ty = box[1] + (box[3] - box[1] - (bbox[3] - bbox[1])) / 2 - 3
    draw.text((tx, ty), label, font=font(FONT_BOLD, 31), fill="white")


def export_story_hook() -> Path:
    canvas = cover(Image.open(SOURCE / "story-hook-bg.png"), (1080, 1920)).convert("RGBA")
    canvas = add_vertical_shade(canvas, 80, 105)
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 64, 72)
    draw_pill(draw, (64, 205, 565, 270), "NOUVEAU  ·  JOURNAL DE TRADING")
    draw.text((64, 335), "TU TRADES", font=font(FONT_BLACK, 103), fill=WHITE)
    draw.text((64, 450), "MAIS EST-CE QUE", font=font(FONT_BLACK, 70), fill=WHITE)
    draw.text((64, 535), "TU PROGRESSES", font=font(FONT_BLACK, 85), fill=ORANGE)
    draw.text((64, 635), "VRAIMENT ?", font=font(FONT_BLACK, 92), fill=WHITE)
    draw.rounded_rectangle((64, 1370, 1016, 1585), radius=30,
                           fill=(9, 10, 9, 226), outline=(255, 100, 35, 110), width=2)
    draw.text((105, 1415), "Ton broker conserve l’historique", font=font(FONT_BOLD, 34), fill=WHITE)
    draw.text((105, 1470), "Bectanse transforme tes trades en leçons", font=font(FONT_BOLD, 31), fill=ORANGE_LIGHT)
    draw_cta(draw, (64, 1645, 1016, 1758), "DÉCOUVRIR LA NOUVEAUTÉ  →")
    draw.text((64, 1804), "acces.bectanse-academie.com", font=font(FONT_REGULAR, 25), fill=MUTED)
    path = OUTPUT / "story-01-hook.png"
    canvas.convert("RGB").save(path, quality=95)
    return path


def export_story_product() -> Path:
    canvas = cover(Image.open(SOURCE / "story-product-bg.png"), (1080, 1920)).convert("RGBA")
    canvas = add_vertical_shade(canvas, 120, 125)
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 64, 64)
    draw.text((64, 230), "TON BROKER", font=font(FONT_BLACK, 73), fill=WHITE)
    draw.text((64, 315), "ENREGISTRE TES TRADES", font=font(FONT_BLACK, 59), fill=WHITE)
    draw.text((64, 397), "BECTANSE T’EXPLIQUE", font=font(FONT_BLACK, 66), fill=ORANGE)
    draw.text((64, 475), "CE QU’ILS DISENT DE TOI", font=font(FONT_BLACK, 53), fill=WHITE)
    draw.rounded_rectangle((58, 1410, 1022, 1710), radius=32,
                           fill=(7, 8, 7, 232), outline=(255, 104, 38, 115), width=2)
    chips = [
        ("SYNC MT5", 84, 1465, 424),
        ("CALENDRIER", 449, 1465, 813),
        ("TRADE SCORE", 84, 1575, 424),
        ("COACH", 449, 1575, 813),
    ]
    for label, left, top, right in chips:
        draw_pill(draw, (left, top, right, top + 76), label,
                  fill=(21, 22, 20, 240), outline="#63301e", size=24)
    draw.text((64, 1788), "Chaque donnée devient un repère pour progresser",
              font=font(FONT_BOLD, 28), fill=WHITE)
    path = OUTPUT / "story-02-produit.png"
    canvas.convert("RGB").save(path, quality=95)
    return path


def export_story_cta() -> Path:
    canvas = cover(Image.open(SOURCE / "story-cta-bg.png"), (1080, 1920)).convert("RGBA")
    canvas = add_vertical_shade(canvas, 85, 70)
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 64, 64)
    draw_pill(draw, (64, 210, 516, 274), "DISPONIBLE MAINTENANT", text_fill=GREEN)
    draw.text((64, 350), "LE TRADE", font=font(FONT_BLACK, 105), fill=WHITE)
    draw.text((64, 465), "EST TERMINÉ", font=font(FONT_BLACK, 92), fill=WHITE)
    draw.text((64, 590), "L’APPRENTISSAGE", font=font(FONT_BLACK, 69), fill=ORANGE)
    draw.text((64, 675), "COMMENCE", font=font(FONT_BLACK, 101), fill=ORANGE)
    draw.rounded_rectangle((64, 1320, 1016, 1494), radius=28,
                           fill=(7, 8, 7, 226), outline=(255, 102, 33, 105), width=2)
    draw.text((103, 1360), "DÉJÀ MEMBRE ?", font=font(FONT_BOLD, 27), fill=ORANGE_LIGHT)
    draw.text((103, 1403), "Le Journal complet est inclus dans ton accès",
              font=font(FONT_BOLD, 31), fill=WHITE)
    draw_cta(draw, (64, 1575, 1016, 1688), "DÉCOUVRIR GRATUITEMENT  →")
    draw.text((64, 1733), "Crée ton accès Explorer · aucune carte demandée",
              font=font(FONT_REGULAR, 25), fill=MUTED)
    draw.text((64, 1792), "acces.bectanse-academie.com", font=font(FONT_BOLD, 25), fill=WHITE)
    path = OUTPUT / "story-03-cta.png"
    canvas.convert("RGB").save(path, quality=95)
    return path


def export_email_banner() -> Path:
    width, height = 1200, 628
    source = cover(Image.open(SOURCE / "story-hook-bg.png"), (width, height))
    source = ImageEnhance.Brightness(source).enhance(0.55).convert("RGBA")
    canvas = source
    shade = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    shade_draw = ImageDraw.Draw(shade)
    for x in range(width):
        alpha = int(210 * max(0, 1 - x / 850))
        shade_draw.line((x, 0, x, height), fill=(2, 3, 2, alpha))
    canvas = Image.alpha_composite(canvas, shade)
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 56, 48, icon_size=62)
    draw_pill(draw, (56, 154, 395, 206), "BECTANSE JOURNAL", size=18)
    draw.text((56, 242), "CHAQUE TRADE", font=font(FONT_BLACK, 52), fill=WHITE)
    draw.text((56, 304), "DEVIENT UNE LEÇON", font=font(FONT_BLACK, 43), fill=ORANGE)
    draw.text((56, 398), "SYNC MT5   ·   CALENDRIER",
              font=font(FONT_BOLD, 20), fill=WHITE)
    draw.text((56, 430), "TRADE SCORE   ·   COACH",
              font=font(FONT_BOLD, 20), fill=WHITE)
    draw.rounded_rectangle((56, 475, 483, 550), radius=18, fill=ORANGE)
    draw.text((91, 496), "DÉCOUVRIR MAINTENANT  →", font=font(FONT_BOLD, 22), fill="white")

    live = Image.open(SOURCE / "journal-ui-public.png").convert("RGB")
    live = round_image(live, (570, 350), 24)
    frame = Image.new("RGBA", (594, 374), (0, 0, 0, 0))
    frame_draw = ImageDraw.Draw(frame)
    frame_draw.rounded_rectangle((0, 0, 593, 373), radius=30,
                                 fill=(8, 9, 8, 230), outline=ORANGE, width=3)
    frame.alpha_composite(live, (12, 12))
    canvas.alpha_composite(frame, (582, 150))
    path = OUTPUT / "journal-launch-email.jpg"
    canvas.convert("RGB").save(path, quality=94, optimize=True)
    return path


def export_telegram_post() -> Path:
    email = Image.open(export_email_banner()).convert("RGB")
    canvas = cover(email, (1280, 720)).convert("RGB")
    path = OUTPUT / "journal-launch-telegram.jpg"
    canvas.save(path, quality=94, optimize=True)
    return path


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    exports = [
        export_story_hook(),
        export_story_product(),
        export_story_cta(),
        export_email_banner(),
        export_telegram_post(),
    ]
    manifest = {
        "campaign": "Bectanse Journal — lancement septembre 2026",
        "claim": "Le trade est terminé. L’apprentissage commence",
        "cta": "https://acces.bectanse-academie.com/journal",
        "exports": [],
    }
    for path in exports:
        with path.open("rb") as stream:
            digest = hashlib.sha256(stream.read()).hexdigest()
        with Image.open(path) as image:
            manifest["exports"].append({
                "file": path.name,
                "width": image.width,
                "height": image.height,
                "sha256": digest,
            })
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
