"""
Sistema visual "wyrm": tokens de cor/tipografia e componentes reutilizáveis
para a tela de configurações. Sem sombra, sem gradiente, sem canto
arredondado — profundidade só por luminância (VOID -> PLATE_1 -> PLATE_2) e
bordas hairline de 1px.
"""

from collections.abc import Callable
from typing import Any

import flet as ft

VOID = "#0B0D10"
PLATE_1 = "#13171D"
PLATE_2 = "#1C212A"
PLATE_3 = "#232936"
HAIRLINE = "#2A3241"
INK = "#E6E9EE"
DIM = "#8B96A8"
DIM_2 = "#5B6576"
ACCENT = "#D99A3A"
POSITIVE = "#10B981"
DANGER = "#EF4444"

FONT_DISPLAY = "Wyrm Display"
FONT_BODY = "Wyrm Body"
FONT_BODY_MEDIUM = "Wyrm Body Medium"
FONT_MONO = "Wyrm Mono"

# Registrados via page.fonts — caminhos relativos à pasta assets/.
PAGE_FONTS: dict[str, str] = {
    FONT_DISPLAY: "fonts/Archivo-SemiBold.ttf",
    FONT_BODY: "fonts/IBMPlexSans-Regular.ttf",
    FONT_BODY_MEDIUM: "fonts/IBMPlexSans-Medium.ttf",
    FONT_MONO: "fonts/JetBrainsMono-Regular.ttf",
}


def panel_title(text: str) -> ft.Text:
    return ft.Text(text, font_family=FONT_DISPLAY, size=19, color=INK)


def panel_subtitle(text: str) -> ft.Text:
    return ft.Text(text.lower(), font_family=FONT_MONO, size=11, color=DIM_2)


def group_label(text: str) -> ft.Container:
    return ft.Container(
        content=ft.Text(text.upper(), font_family=FONT_MONO, size=10.5, color=DIM_2),
        padding=ft.Padding.only(left=14, bottom=8, top=18),
        border=ft.Border.only(bottom=ft.BorderSide(1, HAIRLINE)),
    )


def mono(text: str, size: float = 11, color: str = DIM_2) -> ft.Text:
    return ft.Text(text, font_family=FONT_MONO, size=size, color=color)


def value_text(text: str, size: float = 12.5) -> ft.Text:
    """Valor em destaque (ex.: '5%', '3s') — mono, cor de accent."""
    return ft.Text(text, font_family=FONT_MONO, size=size, color=ACCENT)


def body(text: str, size: float = 13, color: str = INK) -> ft.Text:
    return ft.Text(text, font_family=FONT_BODY, size=size, color=color)


def diamond(size: float = 8, color: str = ACCENT) -> ft.Container:
    """Marcador em losango — mesmo motivo usado nos indicadores de accent do doc de identidade."""
    return ft.Container(width=size, height=size, bgcolor=color, rotate=ft.Rotate(0.785398))


def row(content: ft.Control, *, sub: bool = False) -> ft.Container:
    """Linha padrão dentro de um grupo: fundo plate-1, hairline inferior."""
    return ft.Container(
        content=content,
        padding=ft.Padding.only(left=30 if sub else 14, right=14, top=13, bottom=13),
        bgcolor=PLATE_1,
        border=ft.Border.only(bottom=ft.BorderSide(1, PLATE_3)),
    )


def row_text(
    title: str, description: str = "", *, muted: bool = False, expand: bool = False
) -> ft.Column:
    """
    `expand=True` faz o bloco (e a descrição, se houver) ocupar o espaço restante da
    Row e quebrar linha em vez de estourar a largura — use quando não houver outro
    controle com `expand=True` na mesma Row (ex.: um TextField), senão os dois vão
    disputar o espaço e encolher.
    """
    controls: list[ft.Control] = [
        ft.Text(
            title,
            font_family=FONT_BODY_MEDIUM if not muted else FONT_BODY,
            size=13.5 if not muted else 12.5,
            color=INK if not muted else DIM,
        )
    ]
    if description:
        controls.append(mono(description, size=10.5))
    return ft.Column(controls, spacing=3, tight=True, expand=expand)


def status_chip(text: str, *, ok: bool = True) -> ft.Container:
    color = POSITIVE if ok else ACCENT
    return ft.Container(
        content=mono(text.upper(), size=10.5, color=color),
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
        border=ft.border.all(1, color),
    )


def wyrm_switch(value: bool, on_change: Callable[[Any], Any] | None = None) -> ft.Container:
    """Switch quadrado (sem cantos arredondados) — substitui o ft.Switch padrão do Flet."""
    knob = ft.Container(
        width=10,
        height=10,
        bgcolor=ACCENT if value else DIM,
        left=18 if value else 2,
        top=2,
        animate_position=140,
    )
    track = ft.Container(
        content=ft.Stack([knob], width=32, height=16),
        width=32,
        height=16,
        bgcolor=PLATE_3,
        border=ft.border.all(1, ACCENT if value else HAIRLINE),
        data=value,
    )

    def _toggle(e: Any) -> None:
        new_value = not bool(track.data)
        track.data = new_value
        knob.left = 18 if new_value else 2
        knob.bgcolor = ACCENT if new_value else DIM
        track.border = ft.border.all(1, ACCENT if new_value else HAIRLINE)
        track.update()
        if on_change is not None:
            on_change(e)

    track.on_click = _toggle
    return track


def wyrm_slider(
    value: float,
    min_value: float,
    max_value: float,
    divisions: int,
    on_change: Callable[[Any], Any] | None = None,
) -> ft.Slider:
    """Slider temático (sem o balão de valor flutuante padrão do Material)."""
    return ft.Slider(
        value=value,
        min=min_value,
        max=max_value,
        divisions=divisions,
        active_color=ACCENT,
        inactive_color=PLATE_3,
        thumb_color=ACCENT,
        on_change=on_change,
    )
