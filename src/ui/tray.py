import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pystray
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class SystemTrayManager:
    """Gerencia o ícone da bandeja e o menu de contexto."""

    def __init__(
        self,
        on_exit_callback: Callable[[], Any],
        on_open_settings: Callable[[], Any],
        on_reload_hotkeys: Callable[[], Any],
        on_configure_spotify: Callable[[], Any],
        is_spotify_integration_enabled: Callable[[], bool],
    ) -> None:
        self.on_exit_callback = on_exit_callback
        self.on_open_settings = on_open_settings
        self.on_reload_hotkeys = on_reload_hotkeys
        self.on_configure_spotify = on_configure_spotify
        self.is_spotify_integration_enabled = is_spotify_integration_enabled
        self.icon: pystray.Icon | None = None
        self._configuring_spotify = threading.Lock()

    def _open_settings(self) -> None:
        """Abre a tela de configurações em um processo separado."""
        if self.on_open_settings is not None:
            self.on_open_settings()

        try:
            # Em build, abre o próprio executável em modo de configurações.
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "--settings"])
            else:
                subprocess.Popen([sys.executable, "-m", "src.ui.settings"])
            logger.info("Janela de configurações iniciada.")
        except Exception as e:
            logger.error(f"Erro ao abrir configurações: {e}")

    def _reload_hotkeys(self) -> None:
        """Solicita ao HotkeyManager que recarregue os atalhos."""
        logger.info("Solicitando recarregamento de atalhos...")
        if self.on_reload_hotkeys is not None:
            self.on_reload_hotkeys()

    def _configure_spotify(self) -> None:
        """
        Dispara a checagem/aplicação da integração com Spotify (Spicetify).

        Roda numa thread própria: o callback do menu do pystray executa na MESMA
        thread que bombeia as mensagens da tray, então qualquer chamada bloqueante
        aqui (subprocess, MessageBoxW) travaria o ícone inteiro até terminar.
        """
        if self.on_configure_spotify is None:
            return

        if not self._configuring_spotify.acquire(blocking=False):
            logger.info("Spotify setup: já em andamento, ignorando clique adicional.")
            return

        logger.info("Solicitando configuração da integração com Spotify...")

        def _run() -> None:
            try:
                self.on_configure_spotify()
            finally:
                self._configuring_spotify.release()

        threading.Thread(target=_run, daemon=True).start()

    def _create_placeholder_icon(self) -> Image.Image:
        """Cria um ícone simples para a bandeja."""
        width = 64
        height = 64
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        dc = ImageDraw.Draw(image)

        # Desenha um círculo âmbar (cor do nosso tema)
        dc.ellipse((8, 8, 56, 56), fill=(255, 191, 0), outline=(255, 255, 255))

        # Pequena nota musical simbólica (um ponto e uma haste)
        dc.rectangle((30, 20, 35, 45), fill=(0, 0, 0))
        dc.ellipse((20, 40, 35, 50), fill=(0, 0, 0))

        return image

    def _resolve_asset_path(self, relative_path: str) -> Path:
        """Resolve paths both in source mode and PyInstaller frozen mode."""
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            base_dir = Path(cast(str, sys._MEIPASS))
        else:
            base_dir = Path(__file__).resolve().parents[2]
        return base_dir / relative_path

    def _load_tray_icon(self) -> Image.Image:
        """Load tray icon from assets, fallback to generated placeholder."""
        icon_path = self._resolve_asset_path(os.path.join("assets", "tray.ico"))
        try:
            return Image.open(icon_path)
        except Exception as e:
            logger.warning(f"Nao foi possivel carregar icone da tray em {icon_path}: {e}")
            return self._create_placeholder_icon()

    def _run_icon(self) -> None:
        """Executa o ícone da bandeja (bloqueante na thread)."""
        menu = pystray.Menu(
            pystray.MenuItem("Music Controller", lambda: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Configurações", lambda icon, item: self._open_settings()),
            pystray.MenuItem("Recarregar Atalhos", lambda icon, item: self._reload_hotkeys()),
            pystray.MenuItem(
                "Configurar Spotify",
                lambda icon, item: self._configure_spotify(),
                visible=lambda item: self.is_spotify_integration_enabled(),
            ),
            pystray.MenuItem("Sair", self._on_exit_click),
        )

        self.icon = pystray.Icon(
            "wyrmplayer_controller",
            self._load_tray_icon(),
            title="WyrmPlayer Controller",
            menu=menu,
        )
        self.icon.run()

    def _on_exit_click(self, icon: Any, item: Any) -> None:
        """Chamado quando o usuário clica em Sair."""
        logger.info("Solicitação de saída via System Tray.")
        if self.icon is not None:
            self.icon.stop()
        if self.on_exit_callback is not None:
            self.on_exit_callback()

    def start(self) -> None:
        """Inicia o ícone da bandeja em uma thread separada."""
        tray_thread = threading.Thread(target=self._run_icon, daemon=True)
        tray_thread.start()
        logger.info("System Tray inicializado.")

    def stop(self) -> None:
        """Interrompe o ícone da bandeja, se estiver ativo."""
        if self.icon is not None:
            self.icon.stop()

    def refresh_menu(self) -> None:
        """Força a releitura das propriedades dinâmicas do menu (ex.: visibilidade)."""
        if self.icon is not None:
            self.icon.update_menu()
