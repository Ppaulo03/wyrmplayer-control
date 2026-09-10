import os
import sys
from pathlib import Path
from typing import Any, cast

import flet as ft

from src.core import autostart
from src.core.config import AppConfig, ConfigManager
from src.ui import theme
from src.ui.components.settings.advanced_tab import advanced_tab
from src.ui.components.settings.general_tab import general_tab
from src.ui.components.settings.hotkeys_tab import hotkeys_tab
from src.ui.components.settings.integrations_tab import integrations_tab
from src.ui.components.settings.layout_tab import layout_tab

_SECTIONS = [
    ("geral", "Geral"),
    ("atalhos", "Atalhos"),
    ("exibicao", "Exibição"),
    ("integracoes", "Integrações"),
    ("avancado", "Avançado"),
]


def main(page: ft.Page) -> None:
    # --- Setup Inicial ---
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(cast(str, sys._MEIPASS))
    else:
        base_dir = Path(__file__).resolve().parents[2]

    settings_icon = str(base_dir / os.path.join("assets", "icon.ico"))
    assets_dir = base_dir / "assets"

    page.title = "Configurações - WyrmPlayer Control"
    page.window.icon = settings_icon
    page.window.width, page.window.height = 720, 620
    page.window.resizable = True
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = theme.VOID
    page.padding = 0
    page.fonts = {name: str(assets_dir / path) for name, path in theme.PAGE_FONTS.items()}
    page.theme = ft.Theme(font_family=theme.FONT_BODY)

    config_manager = ConfigManager()
    cfg = config_manager.load()

    status_text = theme.mono("salvamento automático ativo", size=10.5, color=theme.POSITIVE)
    status_dot = theme.diamond(5, theme.POSITIVE)

    # --- Lógica de Salvamento ---
    def save_settings() -> None:
        try:
            g_data = getattr(tab_gen, "data", {})
            h_data = getattr(tab_hot, "data", {})
            l_data = getattr(tab_lay, "data", {})
            i_data = getattr(tab_int, "data", {})
            a_data = getattr(tab_adv, "data", {})

            new_cfg = AppConfig(
                volume_step=int(g_data["volume_step"].value or 5),
                hud_display_time=int(g_data["hud_display_time"].value or 3),
                websocket_port=int(a_data["websocket_port"].value or 8974),
                log_level=str(a_data["log_level"].value or "INFO").upper(),
                log_file=(a_data["log_file"].value or "wyrmplayer.log").strip(),
                spotify_integration=bool(i_data["spotify_integration"].data),
                start_with_windows=bool(g_data["start_with_windows"].data),
                start_with_windows_elevated=bool(g_data["start_with_windows_elevated"].data),
                hud_monitor=int(l_data["hud_monitor"].value or 0),
                hud_position=l_data["hud_position"].value or "bottom_right",
                hotkeys={k: (v.value or "").strip() for k, v in h_data.items()},
                triggers={
                    "volume": bool(l_data["volume"].data),
                    "metadata": bool(l_data["metadata"].data),
                    "playback": bool(l_data["playback"].data),
                },
            )
            config_manager.save(new_cfg)
            autostart.sync(new_cfg.start_with_windows, elevated=new_cfg.start_with_windows_elevated)
            status_text.value = "salvamento automático ativo"
            status_text.color = theme.POSITIVE
            status_dot.bgcolor = theme.POSITIVE
        except Exception as e:
            status_text.value = f"erro ao salvar: {e}"
            status_text.color = theme.DANGER
            status_dot.bgcolor = theme.DANGER
            page.update()

    def on_ui_change(e: Any) -> None:
        save_settings()
        page.update()

    # --- Instanciação das seções ---
    tab_gen = general_tab(cfg, on_ui_change)
    tab_hot = hotkeys_tab(cfg, save_settings, status_text)
    tab_lay = layout_tab(cfg, on_ui_change)
    tab_int = integrations_tab(cfg, on_ui_change)
    tab_adv = advanced_tab(cfg, on_ui_change)

    panels: dict[str, ft.Control] = {
        "geral": tab_gen,
        "atalhos": tab_hot,
        "exibicao": tab_lay,
        "integracoes": tab_int,
        "avancado": tab_adv,
    }

    content_area = ft.Container(content=panels["geral"], padding=26, expand=True)

    nav_items: dict[str, ft.Container] = {}

    def _select_section(key: str) -> None:
        for item_key, item in nav_items.items():
            active = item_key == key
            item.bgcolor = theme.PLATE_2 if active else None
            item.border = ft.Border.only(
                left=ft.BorderSide(2, theme.ACCENT if active else "transparent")
            )
            text_ctl = cast(ft.Text, item.content)
            text_ctl.color = theme.ACCENT if active else theme.DIM
        content_area.content = panels[key]
        page.update()

    def _build_nav_item(key: str, label: str) -> ft.Container:
        item = ft.Container(
            content=theme.mono(label.lower(), size=12.5, color=theme.DIM),
            padding=ft.Padding.symmetric(horizontal=16, vertical=9),
            on_click=lambda e, k=key: _select_section(k),
        )
        nav_items[key] = item
        return item

    nav = ft.Container(
        width=176,
        bgcolor=theme.PLATE_1,
        border=ft.Border.only(right=ft.BorderSide(1, theme.HAIRLINE)),
        content=ft.Column(
            [
                ft.Container(
                    content=ft.Row(
                        [theme.diamond(8), theme.mono("wyrmplayer", size=12, color=theme.INK)],
                        spacing=9,
                    ),
                    padding=ft.Padding.only(left=16, right=16, top=16, bottom=14),
                    border=ft.Border.only(bottom=ft.BorderSide(1, theme.HAIRLINE)),
                ),
                ft.Column([_build_nav_item(key, label) for key, label in _SECTIONS], spacing=0),
            ],
            spacing=0,
        ),
        expand=True,
    )

    status_bar = ft.Container(
        content=ft.Row(
            [
                ft.Row([status_dot, status_text], spacing=7),
                theme.mono("settings.json"),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=theme.PLATE_1,
        border=ft.Border.only(top=ft.BorderSide(1, theme.HAIRLINE)),
        padding=ft.Padding.symmetric(horizontal=14, vertical=8),
    )

    page.add(
        ft.Column(
            [
                ft.Row([nav, content_area], spacing=0, expand=True),
                status_bar,
            ],
            spacing=0,
            expand=True,
        )
    )

    _select_section("geral")


if __name__ == "__main__":
    ft.run(main=main)
