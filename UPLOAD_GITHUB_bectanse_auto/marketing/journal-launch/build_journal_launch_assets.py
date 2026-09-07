#!/usr/bin/env python3
"""Exports déterministes du lancement du Journal Bectanse.

Les chiffres visibles sont volontairement identifiés comme données de
démonstration. Le design reprend les vrais modules du Journal en production
sans publier les informations privées d'un membre.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "static" / "marketing" / "journal-launch" / "source"
OUTPUT = ROOT / "static" / "marketing" / "journal-launch" / "final"
LOGO = ROOT / "static" / "icons" / "bectanse-app-icon-master.png"

FONT_BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"

ORANGE = "#ff5a1f"
ORANGE_LIGHT = "#ff9557"
WHITE = "#f7f5f0"
MUTED = "#9da29b"
GREEN = "#65e680"
RED = "#ff6067"
PANEL = "#0d100e"
CARD = "#151916"
LINE = "#30362f"


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


def story_background(filename: str) -> Image.Image:
    background = cover(Image.open(SOURCE / filename), (1080, 1920))
    background = ImageEnhance.Brightness(background).enhance(0.23).convert("RGBA")
    overlay = Image.new("RGBA", background.size, (0, 0, 0, 118))
    return Image.alpha_composite(background, overlay)


def draw_brand(canvas: Image.Image, x: int, y: int, icon_size: int = 76) -> None:
    logo = Image.open(LOGO).convert("RGBA").resize(
        (icon_size, icon_size), Image.Resampling.LANCZOS
    )
    canvas.alpha_composite(logo, (x, y))
    draw = ImageDraw.Draw(canvas)
    draw.text((x + icon_size + 18, y + 5), "BECTANSE",
              font=font(FONT_BOLD, 34), fill=WHITE)
    draw.text((x + icon_size + 18, y + 43), "ACADÉMIE",
              font=font(FONT_BOLD, 21), fill=ORANGE)


def centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                  label: str, typeface: ImageFont.FreeTypeFont,
                  fill: str) -> None:
    bounds = draw.textbbox((0, 0), label, font=typeface)
    x = box[0] + (box[2] - box[0] - (bounds[2] - bounds[0])) / 2
    y = box[1] + (box[3] - box[1] - (bounds[3] - bounds[1])) / 2 - 2
    draw.text((x, y), label, font=typeface, fill=fill)


def draw_pill(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
              label: str, text_fill: str = ORANGE_LIGHT, size: int = 20) -> None:
    draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2,
                           fill=(14, 16, 14, 240), outline="#64301f", width=2)
    centered_text(draw, box, label, font(FONT_BOLD, size), text_fill)


def draw_cta(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
             label: str, size: int = 30) -> None:
    draw.rounded_rectangle(box, radius=24, fill=ORANGE)
    centered_text(draw, box, label, font(FONT_BOLD, size), "white")


def _metric(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
            label: str, value: str, note: str, accent: str = WHITE) -> None:
    draw.rounded_rectangle(box, radius=18, fill=CARD, outline=LINE, width=2)
    draw.text((box[0] + 20, box[1] + 17), label,
              font=font(FONT_BOLD, 15), fill=MUTED)
    draw.text((box[0] + 20, box[1] + 49), value,
              font=font(FONT_BLACK, 31), fill=accent)
    draw.text((box[0] + 20, box[1] + 94), note,
              font=font(FONT_REGULAR, 13), fill="#747a73")


def make_dashboard(size: tuple[int, int]) -> Image.Image:
    """Reproduction fidèle des modules réels, avec données de démonstration."""
    base = Image.new("RGBA", (1200, 920), (5, 7, 6, 255))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((4, 4, 1196, 916), radius=34,
                           fill=PANEL, outline="#5e2b1b", width=3)
    draw.rounded_rectangle((4, 4, 1196, 14), radius=5, fill=ORANGE)

    draw.text((45, 42), "BECTANSE JOURNAL", font=font(FONT_BLACK, 28), fill=WHITE)
    draw.text((45, 81), "VUE D’ENSEMBLE  ·  ESPACE DE PERFORMANCE",
              font=font(FONT_BOLD, 15), fill=MUTED)
    draw.ellipse((940, 55, 958, 73), fill=GREEN)
    draw.text((970, 51), "SYNCHRONISÉ", font=font(FONT_BOLD, 17), fill=GREEN)
    draw_pill(draw, (760, 93, 1148, 137), "APERÇU DÉMO · DONNÉES ILLUSTRATIVES",
              text_fill="#bbc0b9", size=13)

    draw.rounded_rectangle((45, 156, 420, 405), radius=24,
                           fill=CARD, outline=LINE, width=2)
    draw.text((76, 185), "PERFORMANCE DU MOIS",
              font=font(FONT_BOLD, 16), fill=MUTED)
    draw.text((76, 235), "+312,80 €", font=font(FONT_BLACK, 48), fill=GREEN)
    draw_pill(draw, (76, 307, 214, 353), "+2,4 %", text_fill=GREEN, size=18)
    draw.text((76, 373), "Résultat net des positions clôturées",
              font=font(FONT_REGULAR, 14), fill="#747a73")

    draw.rounded_rectangle((444, 156, 1150, 405), radius=24,
                           fill=CARD, outline=LINE, width=2)
    draw.text((475, 183), "COURBE DE PERFORMANCE",
              font=font(FONT_BOLD, 15), fill=MUTED)
    draw.text((475, 214), "P&L cumulé", font=font(FONT_BOLD, 24), fill=WHITE)
    draw.text((1002, 189), "24 trades", font=font(FONT_BOLD, 14), fill=MUTED)
    chart = (482, 265, 1115, 366)
    for index in range(4):
        y = chart[1] + index * 32
        draw.line((chart[0], y, chart[2], y), fill="#262b27", width=1)
    points = [(490, 343), (562, 301), (628, 350), (700, 316), (772, 276),
              (844, 290), (915, 245), (985, 270), (1052, 222), (1110, 237)]
    polygon = points + [(1110, 366), (490, 366)]
    draw.polygon(polygon, fill=(255, 90, 31, 28))
    draw.line(points, fill=ORANGE_LIGHT, width=5, joint="curve")
    for x, y in points[::2]:
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=ORANGE)

    metric_width = 258
    metric_gap = 14
    labels = [
        ("BALANCE", "12 842,60 €", "Solde du compte", WHITE),
        ("WIN RATE", "68,7 %", "16 trades gagnants", GREEN),
        ("PROFIT FACTOR", "1.92", "Qualité gains / pertes", ORANGE_LIGHT),
        ("TRADE SCORE", "84 / 100", "Discipline mesurée", WHITE),
    ]
    for index, values in enumerate(labels):
        left = 45 + index * (metric_width + metric_gap)
        _metric(draw, (left, 432, left + metric_width, 570), *values)

    draw.rounded_rectangle((45, 598, 743, 858), radius=24,
                           fill=CARD, outline=LINE, width=2)
    draw.text((76, 627), "CE QUE DISENT TES TRADES",
              font=font(FONT_BOLD, 15), fill=MUTED)
    lines = [
        ("Gain moyen", "+41,30 €", GREEN),
        ("Perte moyenne", "−24,80 €", RED),
        ("R:R moyen", "2.1", WHITE),
        ("Risque moyen / trade", "0,8 %", ORANGE_LIGHT),
    ]
    for index, (label, value, color) in enumerate(lines):
        y = 674 + index * 43
        draw.text((76, y), label, font=font(FONT_REGULAR, 18), fill="#a9ada7")
        value_box = draw.textbbox((0, 0), value, font=font(FONT_BOLD, 19))
        draw.text((706 - (value_box[2] - value_box[0]), y), value,
                  font=font(FONT_BOLD, 19), fill=color)

    draw.rounded_rectangle((768, 598, 1150, 858), radius=24,
                           fill="#111713", outline="#245833", width=2)
    draw.text((798, 627), "BECTANSE COACH",
              font=font(FONT_BOLD, 15), fill=GREEN)
    draw.text((798, 672), "Ta meilleure discipline",
              font=font(FONT_BOLD, 22), fill=WHITE)
    draw.text((798, 710), "apparaît entre 08h et 11h",
              font=font(FONT_BOLD, 22), fill=WHITE)
    draw.line((798, 765, 1116, 765), fill="#2a3c2e", width=2)
    draw.text((798, 789), "Lecture basée sur l’historique synchronisé",
              font=font(FONT_REGULAR, 14), fill="#8a918a")

    return base.resize(size, Image.Resampling.LANCZOS)


def make_calendar(size: tuple[int, int]) -> Image.Image:
    base = Image.new("RGBA", (1200, 980), (7, 9, 8, 255))
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((4, 4, 1196, 976), radius=34,
                           fill=PANEL, outline="#5e2b1b", width=3)
    draw.rounded_rectangle((4, 4, 1196, 14), radius=5, fill=ORANGE)
    draw.text((45, 42), "CALENDRIER DE PERFORMANCE",
              font=font(FONT_BLACK, 28), fill=WHITE)
    draw.text((45, 82), "SEPTEMBRE 2026  ·  APERÇU DÉMO",
              font=font(FONT_BOLD, 15), fill=MUTED)
    draw_pill(draw, (820, 46, 1148, 99), "TRADE SCORE  ·  84 / 100",
              text_fill=GREEN, size=16)

    weekdays = ["LUN", "MAR", "MER", "JEU", "VEN"]
    left, top = 45, 145
    gap, cell_w, cell_h = 14, 180, 132
    for index, day in enumerate(weekdays):
        x = left + index * (cell_w + gap)
        draw.text((x + 8, top), day, font=font(FONT_BOLD, 14), fill=MUTED)
    values = [
        ("01", "+42 €", "3 trades", GREEN), ("02", "−18 €", "2 trades", RED),
        ("03", "+76 €", "4 trades", GREEN), ("04", "0 €", "pause", MUTED),
        ("05", "+31 €", "2 trades", GREEN), ("08", "+54 €", "3 trades", GREEN),
        ("09", "+28 €", "2 trades", GREEN), ("10", "−24 €", "2 trades", RED),
        ("11", "+67 €", "4 trades", GREEN), ("12", "+22 €", "1 trade", GREEN),
        ("15", "−12 €", "1 trade", RED), ("16", "+49 €", "3 trades", GREEN),
        ("17", "+38 €", "2 trades", GREEN), ("18", "+61 €", "3 trades", GREEN),
        ("19", "0 €", "pause", MUTED),
    ]
    for index, (day, value, note, color) in enumerate(values):
        row, column = divmod(index, 5)
        x = left + column * (cell_w + gap)
        y = top + 34 + row * (cell_h + gap)
        fill = "#122017" if color == GREEN else "#211416" if color == RED else CARD
        outline = "#285737" if color == GREEN else "#5c2a2d" if color == RED else LINE
        draw.rounded_rectangle((x, y, x + cell_w, y + cell_h), radius=17,
                               fill=fill, outline=outline, width=2)
        draw.text((x + 15, y + 13), day, font=font(FONT_BOLD, 16), fill="#858a83")
        draw.text((x + 15, y + 45), value, font=font(FONT_BLACK, 27), fill=color)
        draw.text((x + 15, y + 91), note, font=font(FONT_REGULAR, 14), fill="#838881")

    coach_top = 620
    draw.rounded_rectangle((45, coach_top, 1150, 914), radius=24,
                           fill="#111713", outline="#245833", width=2)
    draw.text((78, coach_top + 31), "BECTANSE COACH  ·  LECTURE DU MOIS",
              font=font(FONT_BOLD, 16), fill=GREEN)
    draw.text((78, coach_top + 79), "Tu protèges mieux ton capital quand ton risque reste stable",
              font=font(FONT_BLACK, 28), fill=WHITE)
    observations = [
        "68,7 % de réussite sur les trades documentés",
        "Meilleure régularité pendant la session de Londres",
        "Objectif suivant : conserver un risque inférieur à 1 %",
    ]
    for index, line in enumerate(observations):
        y = coach_top + 139 + index * 45
        draw.ellipse((80, y + 7, 94, y + 21), fill=ORANGE if index == 2 else GREEN)
        draw.text((112, y), line, font=font(FONT_BOLD, 19), fill="#d9ddd6")

    return base.resize(size, Image.Resampling.LANCZOS)


def paste_panel(canvas: Image.Image, panel: Image.Image, x: int, y: int) -> None:
    shadow = Image.new("RGBA", (panel.width + 40, panel.height + 40), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((20, 20, panel.width + 20, panel.height + 20),
                                  radius=35, fill=(0, 0, 0, 170))
    canvas.alpha_composite(shadow, (x - 20, y - 10))
    canvas.alpha_composite(panel, (x, y))


def export_story_hook() -> Path:
    canvas = story_background("story-hook-bg.png")
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 64, 58)
    draw_pill(draw, (64, 178, 570, 240), "NOUVEAU  ·  JOURNAL DE TRADING")
    draw.text((64, 285), "TES TRADES", font=font(FONT_BLACK, 86), fill=WHITE)
    draw.text((64, 380), "PARLENT ENFIN", font=font(FONT_BLACK, 79), fill=ORANGE)
    draw.text((64, 476), "Regarde ce qu’ils disent vraiment de toi",
              font=font(FONT_BOLD, 31), fill=WHITE)
    paste_panel(canvas, make_dashboard((952, 790)), 64, 570)
    draw.text((64, 1410), "PERFORMANCE  ·  DISCIPLINE  ·  HABITUDES",
              font=font(FONT_BOLD, 25), fill=ORANGE_LIGHT)
    draw.text((64, 1460), "Tout devient visible dans un seul tableau de bord",
              font=font(FONT_BOLD, 29), fill=WHITE)
    draw_cta(draw, (64, 1570, 1016, 1685), "DÉCOUVRIR LE JOURNAL  →")
    draw.text((64, 1730), "Disponible depuis ton espace Bectanse",
              font=font(FONT_REGULAR, 24), fill=MUTED)
    draw.text((64, 1780), "Données affichées à titre de démonstration",
              font=font(FONT_REGULAR, 18), fill="#676c66")
    path = OUTPUT / "story-01-hook.png"
    canvas.convert("RGB").save(path, quality=95)
    return path


def export_story_product() -> Path:
    canvas = story_background("story-product-bg.png")
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 64, 58)
    draw_pill(draw, (64, 178, 596, 240), "L’INTERFACE RÉELLE DU JOURNAL", text_fill=GREEN)
    draw.text((64, 286), "TA PERFORMANCE", font=font(FONT_BLACK, 78), fill=WHITE)
    draw.text((64, 376), "EN UN COUP D’ŒIL", font=font(FONT_BLACK, 68), fill=ORANGE)
    paste_panel(canvas, make_dashboard((952, 860)), 64, 500)
    draw.rounded_rectangle((64, 1410, 1016, 1576), radius=26,
                           fill=(10, 12, 10, 242), outline="#4d2c20", width=2)
    draw.text((96, 1442), "WIN RATE 68,7 %", font=font(FONT_BOLD, 27), fill=GREEN)
    draw.text((405, 1442), "PROFIT FACTOR 1.92", font=font(FONT_BOLD, 27), fill=ORANGE_LIGHT)
    draw.text((96, 1495), "TRADE SCORE 84 / 100", font=font(FONT_BOLD, 27), fill=WHITE)
    draw.text((500, 1495), "R:R MOYEN 2.1", font=font(FONT_BOLD, 27), fill=WHITE)
    draw_cta(draw, (64, 1635, 1016, 1750), "VOIR MES PROPRES CHIFFRES  →")
    draw.text((64, 1792), "Aperçu de démonstration · aucune performance promise",
              font=font(FONT_REGULAR, 18), fill="#757a74")
    path = OUTPUT / "story-02-produit.png"
    canvas.convert("RGB").save(path, quality=95)
    return path


def export_story_cta() -> Path:
    canvas = story_background("story-cta-bg.png")
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 64, 58)
    draw_pill(draw, (64, 178, 555, 240), "CALENDRIER  ·  SCORE  ·  COACH", text_fill=GREEN)
    draw.text((64, 285), "REPÈRE TES", font=font(FONT_BLACK, 86), fill=WHITE)
    draw.text((64, 380), "VRAIES HABITUDES", font=font(FONT_BLACK, 67), fill=ORANGE)
    paste_panel(canvas, make_calendar((952, 780)), 64, 505)
    draw.rounded_rectangle((64, 1338, 1016, 1499), radius=25,
                           fill=(9, 11, 9, 244), outline="#285737", width=2)
    draw.text((95, 1370), "BECTANSE COACH", font=font(FONT_BOLD, 23), fill=GREEN)
    draw.text((95, 1415), "Tes données deviennent un plan de progression",
              font=font(FONT_BOLD, 29), fill=WHITE)
    draw_cta(draw, (64, 1560, 1016, 1675), "DÉCOUVRIR GRATUITEMENT  →")
    draw.text((64, 1720), "Membre actif : Journal complet déjà inclus",
              font=font(FONT_BOLD, 23), fill=WHITE)
    draw.text((64, 1767), "Explorer : découvre l’application avant de choisir",
              font=font(FONT_REGULAR, 22), fill=MUTED)
    draw.text((64, 1812), "Données affichées à titre de démonstration",
              font=font(FONT_REGULAR, 18), fill="#676c66")
    path = OUTPUT / "story-03-cta.png"
    canvas.convert("RGB").save(path, quality=95)
    return path


def export_email_banner() -> Path:
    width, height = 1200, 628
    background = cover(Image.open(SOURCE / "story-hook-bg.png"), (width, height))
    canvas = ImageEnhance.Brightness(background).enhance(0.20).convert("RGBA")
    canvas = Image.alpha_composite(canvas, Image.new("RGBA", canvas.size, (0, 0, 0, 120)))
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 48, 36, icon_size=58)
    draw_pill(draw, (48, 132, 384, 181), "BECTANSE JOURNAL", size=17)
    draw.text((48, 220), "TES TRADES", font=font(FONT_BLACK, 54), fill=WHITE)
    draw.text((48, 279), "PARLENT ENFIN", font=font(FONT_BLACK, 46), fill=ORANGE)
    draw.text((48, 354), "PERFORMANCE · WIN RATE · SCORE",
              font=font(FONT_BOLD, 18), fill=WHITE)
    draw.text((48, 387), "CALENDRIER · BECTANSE COACH",
              font=font(FONT_BOLD, 18), fill=WHITE)
    draw_cta(draw, (48, 456, 470, 536), "DÉCOUVRIR MAINTENANT  →", size=21)
    draw.text((48, 564), "Aperçu de démonstration",
              font=font(FONT_REGULAR, 14), fill="#747a73")
    panel = make_dashboard((650, 500))
    paste_panel(canvas, panel, 520, 88)
    path = OUTPUT / "journal-launch-email.jpg"
    canvas.convert("RGB").save(path, quality=94, optimize=True)
    return path


def export_telegram_post() -> Path:
    width, height = 1280, 720
    background = cover(Image.open(SOURCE / "story-product-bg.png"), (width, height))
    canvas = ImageEnhance.Brightness(background).enhance(0.18).convert("RGBA")
    canvas = Image.alpha_composite(canvas, Image.new("RGBA", canvas.size, (0, 0, 0, 115)))
    draw = ImageDraw.Draw(canvas)
    draw_brand(canvas, 45, 35, icon_size=60)
    draw_pill(draw, (45, 137, 385, 187), "NOUVEAU JOURNAL", text_fill=GREEN, size=17)
    draw.text((45, 225), "VOIS CE QUE TES", font=font(FONT_BLACK, 48), fill=WHITE)
    draw.text((45, 279), "TRADES DISENT DE TOI", font=font(FONT_BLACK, 39), fill=ORANGE)
    draw.text((45, 354), "Win rate · Profit factor · Trade Score",
              font=font(FONT_BOLD, 19), fill=WHITE)
    draw.text((45, 388), "Calendrier · Coach · Historique MT5",
              font=font(FONT_BOLD, 19), fill=WHITE)
    draw_cta(draw, (45, 467, 490, 548), "DÉCOUVRIR LE JOURNAL  →", size=21)
    draw.text((45, 583), "Disponible maintenant dans l’application Bectanse",
              font=font(FONT_REGULAR, 16), fill=MUTED)
    draw.text((45, 615), "Données affichées à titre de démonstration",
              font=font(FONT_REGULAR, 13), fill="#6f746e")
    paste_panel(canvas, make_dashboard((705, 540)), 545, 80)
    path = OUTPUT / "journal-launch-telegram.jpg"
    canvas.convert("RGB").save(path, quality=94, optimize=True)
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
        "claim": "Tes trades parlent enfin",
        "cta": "https://acces.bectanse-academie.com/journal",
        "data_policy": "Interface réelle reproduite avec données de démonstration",
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
