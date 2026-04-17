import asyncio
import logging
from typing import Optional

from src.core.config import ConfigManager
from src.core.state import AppState, StateCategory
from src.domain.protocols import IMessenger

logger = logging.getLogger(__name__)


class PlayerController:
    """
    Lógica de processamento de comandos de mídia.
    Agora desacoplada da implementação específica do transporte via IMessenger.
    """

    def __init__(
        self,
        state: AppState,
        config: ConfigManager,
        messenger: IMessenger,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.state = state
        self.config = config
        self.messenger = messenger
        self._loop = loop

    def set_messenger(self, messenger: IMessenger) -> None:
        """Atualiza a instância de comunicação usada para enviar comandos."""
        self.messenger = messenger

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Define o loop de eventos para notificações assíncronas."""
        self._loop = loop

    def _notify_ui(self, category: StateCategory = StateCategory.ALL) -> None:
        """Notifica a UI de forma segura entre threads usando o loop configurado."""
        if self._loop:
            self._loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.state.notify(category=category))
            )
        else:
            logger.warning("PlayerController: Tentativa de notificar UI sem loop de eventos configurado.")

    def play_pause(self) -> None:
        """Alterna entre reprodução e pausa."""
        self.messenger.enqueue_command("playPause")

    def next_track(self) -> None:
        """Pula para a próxima música."""
        self.messenger.enqueue_command("next")

    def previous_track(self) -> None:
        """Volta para a música anterior."""
        self.messenger.enqueue_command("previous")

    def toggle_mute(self) -> None:
        """Alterna o estado de mute salvando o volume anterior."""
        if not self.state.is_muted:
            # Muta: salva volume se for maior que 0 e zera no player
            if self.state.metadata.volume > 0:
                self.state.last_non_zero_volume = self.state.metadata.volume

            self.messenger.enqueue_command("setVolume 0")
            self.state.is_muted = True
            logger.info(f"mute ativado (Volume salvo: {self.state.last_non_zero_volume}%)")
        else:
            # Desmuta: restaura o último volume conhecido
            self.messenger.enqueue_command(f"setVolume {self.state.last_non_zero_volume}")
            self.state.is_muted = False
            logger.info(f"mute desativado (Volume restaurado: {self.state.last_non_zero_volume}%)")

        self._notify_ui(category=StateCategory.PLAYBACK)

    def adjust_volume(self, delta: int) -> None:
        """Ajusta o volume usando o 'passo' configurado pelo usuário."""
        cfg = self.config.load()
        step = cfg.volume_step

        # O delta vindo do atalho (ex: +1 ou -1) multiplicado pelo passo configurado
        actual_delta = step if delta > 0 else -step

        if self.state.is_muted:
            base_volume = self.state.last_non_zero_volume
            self.state.is_muted = False
        else:
            base_volume = self.state.metadata.volume

        new_volume = max(0, min(100, base_volume + actual_delta))

        self.messenger.enqueue_command(f"setVolume {new_volume}")
        self._notify_ui(category=StateCategory.VOLUME)
