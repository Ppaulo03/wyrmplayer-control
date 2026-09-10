import webbrowser
from collections.abc import Callable
from typing import Any

import flet as ft

from src.core.config import AppConfig
from src.services import spotify_setup
from src.ui import theme

SPICETIFY_WEBSITE = "https://spicetify.app"


def _spotify_status(cfg: AppConfig) -> tuple[str, bool]:
    """Roda o diagnóstico (somente leitura) e traduz o resultado numa mensagem curta + estado."""
    status = spotify_setup.check_status(cfg.websocket_port)

    if status.spotify_is_microsoft_store:
        return (
            "spotify instalado é a versão da microsoft store, incompatível com o spicetify",
            False,
        )

    if status.spicetify_path is None:
        return (f"spicetify não encontrado — instale em {SPICETIFY_WEBSITE}", False)

    if not status.port_matches:
        return (
            f"porta incompatível: extensão exige {spotify_setup.WEBNOWPLAYING_PORT} fixa",
            False,
        )

    if not status.extension_enabled:
        return ("spicetify encontrado — extensão será registrada ao reiniciar", False)

    return ("extensão registrada · aplique na tray pra reiniciar o spotify", True)


def integrations_tab(cfg: AppConfig, on_change: Callable[[Any], Any]) -> ft.Control:
    """Seção Integrações: fontes de reprodução externas (Spotify via Spicetify)."""

    hint_text = theme.mono("", size=11.5, color=theme.DIM)
    hint_text.expand = True
    status_chip_holder = ft.Row([], spacing=8)
    hint_row = theme.row(
        ft.Row(
            [status_chip_holder, hint_text],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
    )
    hint_row.visible = cfg.spotify_integration

    def _refresh_status() -> None:
        message, ok = _spotify_status(cfg)
        status_chip_holder.controls = [theme.status_chip("pronto" if ok else "atenção", ok=ok)]
        hint_text.value = message

    def _on_toggle(e: Any) -> None:
        enabled = bool(spotify_integration.data)
        hint_row.visible = enabled
        if enabled:
            _refresh_status()
        on_change(e)

    spotify_integration = theme.wyrm_switch(cfg.spotify_integration, on_change=_on_toggle)
    if cfg.spotify_integration:
        _refresh_status()

    steps = theme.row(
        ft.Column(
            [
                ft.Row(
                    [
                        theme.mono("1", size=12, color=theme.DIM_2),
                        theme.mono("instalar o spicetify.app"),
                    ]
                ),
                ft.Row(
                    [
                        theme.mono("2", size=12, color=theme.DIM_2),
                        theme.mono("ativar esta opção e reiniciar"),
                    ]
                ),
                ft.Row(
                    [
                        theme.mono("3", size=12, color=theme.DIM_2),
                        theme.mono("aplicar pelo menu da tray"),
                    ]
                ),
            ],
            spacing=4,
            tight=True,
        )
    )

    content = ft.Column(
        [
            theme.panel_title("Integrações"),
            theme.panel_subtitle("fontes de reprodução externas"),
            theme.group_label("spotify"),
            theme.row(
                ft.Row(
                    [
                        theme.row_text("Integração com Spicetify", "webnowplaying.js"),
                        spotify_integration,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                )
            ),
            hint_row,
            steps,
            ft.Container(
                content=ft.TextButton(
                    "abrir spicetify.app",
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=lambda e: webbrowser.open(SPICETIFY_WEBSITE),
                ),
                padding=ft.Padding.only(top=8),
            ),
        ],
        spacing=0,
        tight=True,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )

    content.data = {"spotify_integration": spotify_integration}

    return content
