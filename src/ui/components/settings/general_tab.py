from collections.abc import Callable
from typing import Any

import flet as ft

from src.core.config import AppConfig
from src.ui import theme


def general_tab(cfg: AppConfig, on_change: Callable[[Any], Any]) -> ft.Control:
    """Seção Geral: inicialização e comportamento de reprodução."""

    start_with_windows_elevated = theme.wyrm_switch(
        cfg.start_with_windows_elevated, on_change=on_change
    )
    start_with_windows_elevated_row = theme.row(
        ft.Row(
            [
                theme.row_text(
                    "Iniciar elevado",
                    "pede confirmação do UAC a cada login; garante atalhos sobre janelas elevadas",
                    expand=True,
                ),
                start_with_windows_elevated,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        sub=True,
    )
    start_with_windows_elevated_row.visible = cfg.start_with_windows

    def _on_start_with_windows_toggle(e: Any) -> None:
        start_with_windows_elevated_row.visible = bool(start_with_windows.data)
        on_change(e)

    start_with_windows = theme.wyrm_switch(
        cfg.start_with_windows, on_change=_on_start_with_windows_toggle
    )

    volume_step_value = theme.value_text(f"{cfg.volume_step}%")
    volume_step = theme.wyrm_slider(cfg.volume_step, 1, 20, 19)

    def _on_volume_change(e: Any) -> None:
        volume_step_value.value = f"{int(volume_step.value or 0)}%"
        on_change(e)

    volume_step.on_change = _on_volume_change

    hud_time_value = theme.value_text(f"{cfg.hud_display_time}s")
    hud_time = theme.wyrm_slider(cfg.hud_display_time, 1, 10, 9)

    def _on_hud_time_change(e: Any) -> None:
        hud_time_value.value = f"{int(hud_time.value or 0)}s"
        on_change(e)

    hud_time.on_change = _on_hud_time_change

    def _slider_row(label: str, value_text: ft.Text, slider: ft.Slider) -> ft.Container:
        return theme.row(
            ft.Column(
                [
                    ft.Row(
                        [theme.body(label), value_text],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    slider,
                ],
                spacing=2,
                tight=True,
            )
        )

    content = ft.Column(
        [
            theme.panel_title("Geral"),
            theme.panel_subtitle("comportamento básico do aplicativo"),
            theme.group_label("inicialização"),
            theme.row(
                ft.Row(
                    [
                        theme.row_text("Iniciar com o Windows", "HKCU\\...\\Run"),
                        start_with_windows,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            ),
            start_with_windows_elevated_row,
            theme.group_label("reprodução"),
            _slider_row("Passo do volume", volume_step_value, volume_step),
            _slider_row("Tempo do HUD", hud_time_value, hud_time),
        ],
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    content.data = {
        "volume_step": volume_step,
        "hud_display_time": hud_time,
        "start_with_windows": start_with_windows,
        "start_with_windows_elevated": start_with_windows_elevated,
    }

    return content
