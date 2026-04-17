import os
import sys
from pathlib import Path
from typing import Any, cast

import flet as ft

from src.core.config import AppConfig, ConfigManager
from src.ui.components.settings.general_tab import general_tab
from src.ui.components.settings.hotkeys_tab import hotkeys_tab
from src.ui.components.settings.layout_tab import layout_tab


def main(page: ft.Page) -> None:
    # --- Setup Inicial ---
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(cast(str, sys._MEIPASS))
    else:
        base_dir = Path(__file__).resolve().parents[2]

    settings_icon = str(base_dir / os.path.join("assets", "icon.ico"))

    page.title = "Configurações - WyrmPlayer Control"
    page.window.icon = settings_icon
    page.window.width, page.window.height = 540, 720
    page.window.resizable = True
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#070B12"
    page.padding = 18
    page.scroll = ft.ScrollMode.AUTO

    config_manager = ConfigManager()
    cfg = config_manager.load()

    autosave_status = ft.Text("Salvamento automático ativo", size=12, color=ft.Colors.GREEN_300)

    # --- Lógica de Salvamento ---
    def save_settings() -> None:
        try:
            # Extrai dados dos componentes via atributo .data
            g_data = getattr(tab_gen, "data", {})
            h_data = getattr(tab_hot, "data", {})
            l_data = getattr(tab_lay, "data", {})

            new_cfg = AppConfig(
                volume_step=int(g_data["volume_step"].value or 5),
                hud_display_time=int(g_data["hud_display_time"].value or 3),
                websocket_port=int(g_data["websocket_port"].value or 8975),
                log_level=str(g_data["log_level"].value or "INFO").upper(),
                log_file=(g_data["log_file"].value or "wyrmplayer.log").strip(),
                hud_monitor=int(l_data["hud_monitor"].value or 0),
                hud_position=l_data["hud_position"].value or "bottom_right",
                hotkeys={k: (v.value or "").strip() for k, v in h_data.items()},
                triggers={
                    "volume": bool(l_data["volume"].value),
                    "metadata": bool(l_data["metadata"].value),
                    "playback": bool(l_data["playback"].value),
                },
            )
            config_manager.save(new_cfg)
        except Exception as e:
            autosave_status.value = f"Erro ao salvar: {e}"
            autosave_status.color = ft.Colors.RED_400
            page.update()

    def on_ui_change(e: Any) -> None:
        save_settings()
        page.update()

    # --- Instanciação dos Componentes ---
    tab_gen = general_tab(cfg, on_ui_change)
    tab_hot = hotkeys_tab(cfg, save_settings, autosave_status)
    tab_lay = layout_tab(cfg, on_ui_change)

    # --- Estrutura de Abas (Flet 0.84.0) ---
    tabs_control = ft.Tabs(
        length=3,
        selected_index=0,
        content=ft.Column(
            [
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="Geral", icon=ft.Icons.TUNE),
                        ft.Tab(label="Atalhos", icon=ft.Icons.KEYBOARD),
                        ft.Tab(label="Exibição", icon=ft.Icons.VISIBILITY),
                    ]
                ),
                ft.TabBarView(
                    controls=[
                        ft.Column([tab_gen], scroll=ft.ScrollMode.AUTO),
                        ft.Column([tab_hot], scroll=ft.ScrollMode.AUTO),
                        tab_lay,
                    ],
                    expand=True,
                ),
            ],
            expand=True,
        ),
    )

    page.add(
        ft.Column(
            [
                tabs_control,
                ft.Row(
                    [ft.Icon(ft.Icons.CLOUD_DONE, size=16, color=ft.Colors.GREEN_300), autosave_status],
                    spacing=8,
                ),
            ],
            spacing=12,
            expand=True,
        )
    )


if __name__ == "__main__":
    ft.run(main=main)
