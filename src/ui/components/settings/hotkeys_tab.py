from collections.abc import Callable
from dataclasses import dataclass

import flet as ft

from src.core.config import AppConfig
from src.core.utils.keyboard import hotkey_from_event
from src.ui import theme

_PageLike = ft.Page | ft.BasePage


@dataclass
class _CaptureState:
    field: ft.TextField | None = None
    label: str = ""


def hotkeys_tab(cfg: AppConfig, on_save: Callable[[], None], status_text: ft.Text) -> ft.Control:
    """Seção Atalhos: captura de combinações de teclado."""

    fields: dict[str, ft.TextField] = {}
    capturing = _CaptureState()

    def stop_capture(page: _PageLike) -> None:
        capturing.field = None
        capturing.label = ""
        if isinstance(page, ft.Page):
            page.on_keyboard_event = None
        page.update()

    def on_capture_key(e: ft.KeyboardEvent) -> None:
        field = capturing.field
        label = capturing.label
        if field is None:
            return

        hotkey = hotkey_from_event(e)
        if not hotkey:
            return

        if hotkey == "esc":
            status_text.value = "captura cancelada"
            status_text.color = theme.DIM
            stop_capture(e.page)
            return

        field.value = hotkey
        on_save()
        status_text.value = f"{label.lower()} salvo: {hotkey}"
        status_text.color = theme.POSITIVE
        stop_capture(e.page)

    def start_capture(field: ft.TextField, label: str, page: _PageLike) -> None:
        capturing.field = field
        capturing.label = label
        status_text.value = f"pressione o novo atalho para {label.lower()} (esc cancela)"
        status_text.color = theme.ACCENT
        if isinstance(page, ft.Page):
            page.on_keyboard_event = on_capture_key
        page.update()

    def on_clear(label: str, field: ft.TextField, page: _PageLike) -> None:
        field.value = ""
        on_save()
        status_text.value = f"{label.lower()} limpo"
        status_text.color = theme.DIM
        page.update()

    def hotkey_row(label: str, key: str) -> ft.Control:
        field = ft.TextField(
            value=cfg.hotkeys.get(key, ""),
            border=ft.InputBorder.NONE,
            bgcolor=theme.PLATE_1,
            color=theme.ACCENT,
            text_style=ft.TextStyle(font_family=theme.FONT_MONO, size=12.5),
            dense=True,
            read_only=True,
            text_align=ft.TextAlign.RIGHT,
            expand=True,
        )
        fields[key] = field
        return theme.row(
            ft.Row(
                [
                    theme.row_text(label),
                    field,
                    ft.IconButton(
                        ft.Icons.KEYBOARD,
                        icon_color=theme.DIM,
                        icon_size=16,
                        tooltip="gravar",
                        on_click=lambda e: start_capture(field, label, e.page),
                    ),
                    ft.IconButton(
                        ft.Icons.CLOSE,
                        icon_color=theme.DIM,
                        icon_size=16,
                        tooltip="limpar",
                        on_click=lambda e: on_clear(label, field, e.page),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            )
        )

    content = ft.Column(
        [
            theme.panel_title("Atalhos"),
            theme.panel_subtitle("clique em gravar para capturar — salvamento automático"),
            theme.group_label("transporte"),
            hotkey_row("Play/Pause", "play_pause"),
            hotkey_row("Anterior", "previous_track"),
            hotkey_row("Próxima", "next_track"),
            theme.group_label("volume"),
            hotkey_row("Volume +", "volume_up"),
            hotkey_row("Volume -", "volume_down"),
            hotkey_row("Mute", "mute"),
        ],
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )
    content.data = fields
    return content
