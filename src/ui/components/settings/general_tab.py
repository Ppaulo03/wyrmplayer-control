import webbrowser
from collections.abc import Callable
from typing import Any

import flet as ft

from src.core.config import AppConfig
from src.services import spotify_setup

SPICETIFY_WEBSITE = "https://spicetify.app"


def _spotify_status_message(cfg: AppConfig) -> tuple[str, str]:
    """Roda o diagnóstico (somente leitura) e traduz o resultado numa mensagem curta."""
    status = spotify_setup.check_status(cfg.websocket_port)

    if status.spotify_is_microsoft_store:
        return (
            "O Spotify instalado é a versão da Microsoft Store, incompatível com o "
            "Spicetify. Desinstale-a e instale a versão oficial em spotify.com/download.",
            ft.Colors.RED_300,
        )

    if status.spicetify_path is None:
        return (
            f"Spicetify não encontrado. Instale em {SPICETIFY_WEBSITE}, reinicie o "
            "WyrmPlayerControl e clique em 'Configurar Spotify' na tray.",
            ft.Colors.ORANGE_300,
        )

    if not status.port_matches:
        return (
            f"Porta do WebSocket incompatível: a extensão do Spotify exige a porta "
            f"{spotify_setup.WEBNOWPLAYING_PORT} fixa (atual: {cfg.websocket_port}).",
            ft.Colors.RED_300,
        )

    if not status.extension_enabled:
        return (
            "Spicetify encontrado. A extensão será registrada automaticamente ao "
            "reiniciar o WyrmPlayerControl.",
            ft.Colors.AMBER_300,
        )

    return (
        "Tudo pronto! Se ainda não aplicou, clique em 'Configurar Spotify' na tray "
        "(isso reinicia o Spotify).",
        ft.Colors.GREEN_300,
    )


def general_tab(cfg: AppConfig, on_change: Callable[[Any], Any]) -> ft.Control:
    """Aba de Ajustes Gerais."""

    # Controles
    volume_step = ft.Slider(
        min=1, max=20, divisions=19, label="{value}%", value=cfg.volume_step, on_change=on_change
    )
    hud_time = ft.Slider(
        min=1,
        max=10,
        divisions=9,
        label="{value}s",
        value=cfg.hud_display_time,
        on_change=on_change,
    )

    log_level = ft.Dropdown(
        label="Nível de log",
        value=(cfg.log_level or "INFO").upper(),
        options=[
            ft.DropdownOption(key=k, text=k)
            for k in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        ],
        expand=True,
    )
    log_level.on_select = on_change

    log_file = ft.TextField(
        label="Arquivo de log",
        value=cfg.log_file,
        hint_text="wyrmplayer.log",
        dense=True,
        expand=True,
        on_change=on_change,
    )

    websocket_port = ft.TextField(
        label="Porta do WebSocket",
        value=str(cfg.websocket_port),
        hint_text="8974",
        dense=True,
        expand=True,
        keyboard_type=ft.KeyboardType.NUMBER,
        on_submit=on_change,
        on_blur=on_change,
    )

    spotify_status = ft.Text(
        size=12,
        color=ft.Colors.WHITE60,
        visible=False,
    )

    def _refresh_spotify_status() -> None:
        if not spotify_integration.value:
            spotify_status.visible = False
            return

        message, color = _spotify_status_message(cfg)
        spotify_status.value = message
        spotify_status.color = color
        spotify_status.visible = True

    def _on_spotify_toggle(e: Any) -> None:
        _refresh_spotify_status()
        on_change(e)

    spotify_integration = ft.Switch(
        label="Integração com Spotify (Spicetify)",
        value=cfg.spotify_integration,
        on_change=_on_spotify_toggle,
    )
    _refresh_spotify_status()

    start_with_windows_elevated = ft.Switch(
        label="Iniciar elevado (garante atalhos sobre janelas/jogos elevados)",
        value=cfg.start_with_windows_elevated,
        on_change=on_change,
        visible=cfg.start_with_windows,
    )
    start_with_windows_hint = ft.Text(
        "Por padrão, inicia sem pedir confirmação de administrador (UAC) a cada "
        "login. Se os atalhos não funcionarem sobre um jogo específico que também "
        "roda elevado, ative a opção abaixo.",
        size=12,
        color=ft.Colors.WHITE60,
        visible=cfg.start_with_windows,
    )

    def _on_start_with_windows_toggle(e: Any) -> None:
        start_with_windows_elevated.visible = start_with_windows.value
        start_with_windows_hint.visible = start_with_windows.value
        on_change(e)

    start_with_windows = ft.Switch(
        label="Iniciar com o Windows",
        value=cfg.start_with_windows,
        on_change=_on_start_with_windows_toggle,
    )

    card = ft.Container(
        padding=16,
        border_radius=14,
        bgcolor="#0D1422",
        border=ft.border.all(1, "#23314A"),
        content=ft.Column(
            [
                ft.Text("Ajustes Gerais", size=24, weight=ft.FontWeight.BOLD),
                start_with_windows,
                start_with_windows_hint,
                start_with_windows_elevated,
                ft.Text("Passo do volume (%)", size=13, color=ft.Colors.WHITE70),
                volume_step,
                ft.Text("Tempo do HUD (segundos)", size=13, color=ft.Colors.WHITE70),
                hud_time,
                ft.Text("Nível dos logs salvos", size=13, color=ft.Colors.WHITE70),
                log_level,
                ft.Text("Nome do arquivo de log", size=13, color=ft.Colors.WHITE70),
                log_file,
                ft.Text("Porta do WebSocket", size=13, color=ft.Colors.WHITE70),
                websocket_port,
                ft.Text(
                    "Como configurar o Spotify: 1) instale o Spicetify; 2) ative esta opção "
                    "e reinicie o WyrmPlayerControl; 3) clique em 'Configurar Spotify' na "
                    "system tray para aplicar.",
                    size=13,
                    color=ft.Colors.WHITE70,
                ),
                ft.TextButton(
                    "Abrir spicetify.app",
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=lambda e: webbrowser.open(SPICETIFY_WEBSITE),
                ),
                spotify_integration,
                spotify_status,
            ],
            spacing=10,
            tight=True,
        ),
    )

    # Armazenamos referências para extração de dados no save
    card.data = {
        "volume_step": volume_step,
        "hud_display_time": hud_time,
        "log_level": log_level,
        "log_file": log_file,
        "websocket_port": websocket_port,
        "spotify_integration": spotify_integration,
        "start_with_windows": start_with_windows,
        "start_with_windows_elevated": start_with_windows_elevated,
    }

    return card
