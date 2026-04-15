import asyncio
import logging

from src.core.state import AppState
from src.core.websocket import MusicWebSocketServer

logger = logging.getLogger(__name__)


class PlayerController:
    """Lógica de processamento de comandos de mídia."""

    def __init__(self, state: AppState, server: MusicWebSocketServer) -> None:
        self.state = state
        self.server = server

    def set_server(self, server: MusicWebSocketServer) -> None:
        """Atualiza a instância de WebSocket usada para enviar comandos."""
        self.server = server

    def _notify_ui(self) -> None:
        """Notifica a UI de forma segura entre threads."""
        if self.server._loop:
            self.server._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.state.notify())
            )

    def play_pause(self) -> None:
        """Alterna entre reprodução e pausa."""
        self.server.enqueue_command("playPause")

    def next_track(self) -> None:
        """Pula para a próxima música."""
        self.server.enqueue_command("next")

    def previous_track(self) -> None:
        """Volta para a música anterior."""
        self.server.enqueue_command("previous")

    def toggle_mute(self) -> None:
        """Alterna o estado de mute salvando o volume anterior."""
        if not self.state.is_muted:
            # Muta: salva volume se for maior que 0 e zera no player
            if self.state.metadata.volume > 0:
                self.state.last_non_zero_volume = self.state.metadata.volume

            self.server.enqueue_command("setVolume 0")
            self.state.is_muted = True
            logger.info(
                f"mute ativado (Volume salvo: {self.state.last_non_zero_volume}%)"
            )
        else:
            # Desmuta: restaura o último volume conhecido
            self.server.enqueue_command(f"setVolume {self.state.last_non_zero_volume}")
            self.state.is_muted = False
            logger.info(
                f"mute desativado (Volume restaurado: {self.state.last_non_zero_volume}%)"
            )

        self._notify_ui()

    def adjust_volume(self, delta: int) -> None:
        """Ajusta o volume usando o 'passo' configurado pelo usuário."""
        cfg = self.state.config.load()  # Recarrega para pegar mudanças recentes
        step = cfg.volume_step

        # O delta vindo do atalho (ex: +1 ou -1) multiplicado pelo passo configurado
        actual_delta = step if delta > 0 else -step

        if self.state.is_muted:
            base_volume = self.state.last_non_zero_volume
            self.state.is_muted = False
        else:
            base_volume = self.state.metadata.volume

        new_volume = max(0, min(100, base_volume + actual_delta))

        self.server.enqueue_command(f"setVolume {new_volume}")
        self._notify_ui()
