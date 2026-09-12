import logging
import threading
import webbrowser
from collections.abc import Callable
from typing import Any

import flet as ft

from src.core.config import AppConfig
from src.services import spotify_setup
from src.ui import theme

logger = logging.getLogger(__name__)

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

    if not status.extension_file_present:
        return (
            "arquivo webnowplaying.js não encontrado — normalmente vem com o spicetify; "
            "tente reinstalá-lo ou buscar 'webnowplaying' no marketplace",
            False,
        )

    return ("extensão registrada e ativa", True)


def integrations_tab(cfg: AppConfig, on_change: Callable[[Any], Any], page: ft.Page) -> ft.Control:
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

    progress_text = theme.mono("", size=11, color=theme.DIM)
    progress_text.expand = True
    progress_row = theme.row(
        ft.Row(
            [ft.ProgressRing(width=12, height=12, stroke_width=2), progress_text],
            spacing=9,
        )
    )
    progress_row.visible = False

    _configuring_lock = threading.Lock()

    def _refresh_status() -> None:
        message, ok = _spotify_status(cfg)
        status_chip_holder.controls = [theme.status_chip("pronto" if ok else "atenção", ok=ok)]
        hint_text.value = message

    def _safe_page_update() -> None:
        # Chamado de uma thread de background: uma falha aqui (ex.: conexão
        # com o cliente Flet momentaneamente instável) não pode ficar muda —
        # sem o log, o sintoma vira "a barra de progresso trava pra sempre"
        # sem nenhum rastro de por quê.
        try:
            page.update()
        except Exception as e:
            logger.warning("Integrações: falha ao atualizar a UI (page.update): %s", e)

    def _on_progress(line: str) -> None:
        # Chamado da thread de background (ver _trigger_interactive_setup) a
        # cada linha nova de saída do Spicetify — sem isso a janela fica muda
        # do clique até o diálogo final, que pode levar bem mais de um minuto
        # (patch de centenas de arquivos).
        progress_text.value = line
        _safe_page_update()

    def _run_interactive_setup() -> None:
        # Evita duas execuções concorrentes (ex.: usuário ativa o toggle e
        # clica em "Configurar Spotify" logo em seguida) — mesma proteção que
        # a tray já tem pro mesmo fluxo.
        if not _configuring_lock.acquire(blocking=False):
            return
        try:
            progress_row.visible = True
            progress_text.value = "iniciando..."
            configure_button.disabled = True
            _safe_page_update()
            spotify_setup.run_interactive_setup(cfg.websocket_port, on_progress=_on_progress)
            _refresh_status()
        finally:
            progress_row.visible = False
            progress_text.value = ""
            configure_button.disabled = False
            _configuring_lock.release()
            _safe_page_update()

    def _trigger_interactive_setup() -> None:
        # Chamadas bloqueantes (subprocess, MessageBoxW) travariam a janela de
        # Configurações se rodassem direto no callback do Flet.
        threading.Thread(target=_run_interactive_setup, daemon=True).start()

    def _on_toggle(e: Any) -> None:
        enabled = bool(spotify_integration.data)
        hint_row.visible = enabled
        configure_button.visible = enabled
        if enabled:
            _refresh_status()
        on_change(e)

        if enabled:
            # Configura e pergunta (diálogo nativo) se pode reiniciar o Spotify
            # já aqui, em vez de deixar o usuário ter que descobrir sozinho que
            # precisa ir no menu da tray depois.
            _trigger_interactive_setup()

    spotify_integration = theme.wyrm_switch(cfg.spotify_integration, on_change=_on_toggle)
    if cfg.spotify_integration:
        _refresh_status()

    configure_button = ft.TextButton(
        "configurar spotify",
        icon=ft.Icons.SYNC,
        on_click=lambda e: _trigger_interactive_setup(),
    )
    configure_button.visible = cfg.spotify_integration

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
                        theme.mono("ativar esta opção"),
                    ]
                ),
                ft.Row(
                    [
                        theme.mono("3", size=12, color=theme.DIM_2),
                        theme.mono("confirmar o reinício do spotify quando perguntado"),
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
            progress_row,
            steps,
            ft.Container(
                content=ft.Row(
                    [
                        configure_button,
                        ft.TextButton(
                            "abrir spicetify.app",
                            icon=ft.Icons.OPEN_IN_NEW,
                            on_click=lambda e: webbrowser.open(SPICETIFY_WEBSITE),
                        ),
                    ],
                    spacing=0,
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
