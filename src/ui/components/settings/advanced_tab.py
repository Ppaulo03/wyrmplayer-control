from collections.abc import Callable
from typing import Any

import flet as ft

from src.core.config import AppConfig
from src.ui import theme

_LOG_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def _field(
    value: str,
    hint: str,
    *,
    on_submit: Callable[[Any], Any] | None = None,
    on_blur: Callable[[Any], Any] | None = None,
) -> ft.TextField:
    return ft.TextField(
        value=value,
        hint_text=hint,
        border=ft.InputBorder.NONE,
        bgcolor=theme.PLATE_1,
        color=theme.INK,
        text_style=ft.TextStyle(font_family=theme.FONT_MONO, size=12.5),
        dense=True,
        text_align=ft.TextAlign.RIGHT,
        width=160,
        on_submit=on_submit,
        on_blur=on_blur,
    )


def advanced_tab(cfg: AppConfig, on_change: Callable[[Any], Any]) -> ft.Control:
    """Seção Avançado: logging e porta do WebSocket."""

    log_level = ft.Dropdown(
        value=(cfg.log_level or "INFO").upper(),
        options=[ft.DropdownOption(key=k, text=k.lower()) for k in _LOG_LEVELS],
        border=ft.InputBorder.NONE,
        bgcolor=theme.PLATE_1,
        color=theme.ACCENT,
        text_style=ft.TextStyle(font_family=theme.FONT_MONO, size=12.5),
        width=160,
        content_padding=ft.Padding.symmetric(horizontal=8, vertical=6),
    )
    log_level.on_select = on_change

    log_file = _field(cfg.log_file, "wyrmplayer.log", on_submit=on_change, on_blur=on_change)
    websocket_port = _field(str(cfg.websocket_port), "8974", on_submit=on_change, on_blur=on_change)
    websocket_port.keyboard_type = ft.KeyboardType.NUMBER

    content = ft.Column(
        [
            theme.panel_title("Avançado"),
            theme.panel_subtitle("logging e rede — não mude sem necessidade"),
            theme.group_label("logging"),
            theme.row(
                ft.Row(
                    [theme.row_text("Nível de log"), log_level],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            ),
            theme.row(
                ft.Row(
                    [theme.row_text("Arquivo de log"), log_file],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            ),
            theme.group_label("rede"),
            theme.row(
                ft.Row(
                    [
                        theme.row_text(
                            "Porta do WebSocket",
                            "8974 fixa se usar a integração com Spotify",
                        ),
                        websocket_port,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            ),
        ],
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    content.data = {
        "log_level": log_level,
        "log_file": log_file,
        "websocket_port": websocket_port,
    }

    return content
