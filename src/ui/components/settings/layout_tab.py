from collections.abc import Callable
from typing import Any

import flet as ft

from src.core.config import AppConfig
from src.core.display import HUD_POSITION_PRESETS, list_monitors
from src.ui import theme


def _dropdown(value: str, options: list[ft.DropdownOption]) -> ft.Dropdown:
    return ft.Dropdown(
        value=value,
        options=options,
        border=ft.InputBorder.NONE,
        bgcolor=theme.PLATE_1,
        color=theme.ACCENT,
        text_style=ft.TextStyle(font_family=theme.FONT_MONO, size=12.5),
        width=180,
        content_padding=ft.Padding.symmetric(horizontal=8, vertical=6),
    )


def layout_tab(cfg: AppConfig, on_change: Callable[[Any], Any]) -> ft.Control:
    """Seção Exibição: posição do overlay e gatilhos de exibição do HUD."""

    monitors = list_monitors()
    monitor_dropdown = _dropdown(
        str(min(max(cfg.hud_monitor, 0), len(monitors) - 1)),
        [ft.DropdownOption(key=str(m.index), text=m.label) for m in monitors],
    )
    monitor_dropdown.on_select = on_change

    position_dropdown = _dropdown(
        cfg.hud_position if cfg.hud_position in HUD_POSITION_PRESETS else "bottom_right",
        [ft.DropdownOption(key=k, text=v.lower()) for k, v in HUD_POSITION_PRESETS.items()],
    )
    position_dropdown.on_select = on_change

    t_vol = theme.wyrm_switch(cfg.triggers["volume"], on_change=on_change)
    t_meta = theme.wyrm_switch(cfg.triggers["metadata"], on_change=on_change)
    t_play = theme.wyrm_switch(cfg.triggers["playback"], on_change=on_change)

    def _trigger_row(label: str, switch: ft.Container) -> ft.Container:
        return theme.row(
            ft.Row(
                [theme.row_text(label), switch],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )
        )

    content = ft.Column(
        [
            theme.panel_title("Exibição"),
            theme.panel_subtitle("posição do overlay e gatilhos de exibição"),
            theme.group_label("posição"),
            theme.row(
                ft.Row(
                    [theme.row_text("Tela do overlay"), monitor_dropdown],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            ),
            theme.row(
                ft.Row(
                    [theme.row_text("Posição do overlay"), position_dropdown],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            ),
            theme.group_label("gatilhos"),
            _trigger_row("Mudar volume", t_vol),
            _trigger_row("Mudar música", t_meta),
            _trigger_row("Pausar/play", t_play),
        ],
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    content.data = {
        "hud_monitor": monitor_dropdown,
        "hud_position": position_dropdown,
        "volume": t_vol,
        "metadata": t_meta,
        "playback": t_play,
    }

    return content
