"""
Gera os ícones do WyrmPlayerControl (símbolo "Cue" do sistema visual wyrm) e
sobrescreve assets/icon.ico, assets/icon2.ico, assets/tray.ico, assets/original.png
e assets/cue-mark.png.

Uso: uv run python scripts/generate_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

VOID = (11, 13, 16, 255)  # theme.VOID
ACCENT = (217, 154, 58, 255)  # theme.ACCENT

# Símbolo "Cue": coluna vertical + seta de play recortada (aberta, sem preenchimento).
# Equivalente ao path SVG: M14 8 V40 M14 17 L26 24 L14 31 (viewBox 0 0 48 48).
SPINE = ((14, 8), (14, 40))
NOTCH = ((14, 17), (26, 24), (14, 31))

WORKING_SIZE = 960
STROKE_RATIO = 0.071  # proporção que ficou legível tanto grande quanto em 16px
FILL_RATIO = 0.68  # quanto da tela o símbolo ocupa (maior dimensão), resto é margem
OPTICAL_NUDGE_RATIO = 0.035  # compensa o peso visual da coluna sólida vs. a ponta fina

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"


def _bbox(stroke: float) -> tuple[float, float, float, float]:
    half = stroke / 2
    xs = [p[0] for p in (*SPINE, *NOTCH)]
    ys = [p[1] for p in (*SPINE, *NOTCH)]
    return (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)


def render_master(canvas_bg: tuple[int, int, int, int] | None) -> Image.Image:
    img = Image.new("RGBA", (WORKING_SIZE, WORKING_SIZE), canvas_bg or (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    raw_stroke = 48 * STROKE_RATIO
    x0, y0, x1, y1 = _bbox(raw_stroke)
    mark_w, mark_h = x1 - x0, y1 - y0

    scale = (WORKING_SIZE * FILL_RATIO) / max(mark_w, mark_h)
    stroke = round(raw_stroke * scale)

    optical_nudge = WORKING_SIZE * OPTICAL_NUDGE_RATIO
    offset_x = WORKING_SIZE / 2 - ((x0 + x1) / 2) * scale + optical_nudge
    offset_y = WORKING_SIZE / 2 - ((y0 + y1) / 2) * scale

    def to_canvas(points: tuple[tuple[int, int], ...]) -> list[tuple[float, float]]:
        return [(x * scale + offset_x, y * scale + offset_y) for x, y in points]

    draw.line(to_canvas(SPINE), fill=ACCENT, width=stroke)
    draw.line(to_canvas(NOTCH), fill=ACCENT, width=stroke, joint="curve")

    # Reforça as pontas com pequenos quadrados (equivalente a stroke-linecap: square).
    half = stroke / 2
    for cx, cy in [*to_canvas(SPINE), *to_canvas(NOTCH)]:
        draw.rectangle([cx - half, cy - half, cx + half, cy + half], fill=ACCENT)

    return img


def save_ico(img: Image.Image, path: Path, sizes: list[int]) -> None:
    img.save(path, format="ICO", sizes=[(s, s) for s in sizes])


def main() -> None:
    master_with_bg = render_master(VOID)
    master_transparent = render_master(None)

    master_with_bg.resize((1024, 1024), Image.LANCZOS).save(ASSETS_DIR / "original.png")
    master_transparent.resize((64, 64), Image.LANCZOS).save(ASSETS_DIR / "cue-mark.png")

    # Fundo transparente para os ícones de UI (janela/tray) — um quadrado sólido
    # atrás do símbolo destoa da barra de título/tray do Windows. master_with_bg
    # fica só para o original.png (imagem "hero", não usada como ícone de chrome).
    icon_sizes = [16, 24, 32, 48, 64, 128, 256]
    save_ico(master_transparent, ASSETS_DIR / "icon.ico", icon_sizes)
    save_ico(master_transparent, ASSETS_DIR / "icon2.ico", icon_sizes)
    save_ico(master_transparent, ASSETS_DIR / "tray.ico", [16, 24, 32, 48])

    print(f"Ícones gerados em {ASSETS_DIR}")


if __name__ == "__main__":
    main()
