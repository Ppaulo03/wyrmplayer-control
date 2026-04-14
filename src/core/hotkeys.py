import logging

import keyboard

from src.services.player_controller import PlayerController

logger = logging.getLogger(__name__)


class HotkeyManager:
    """Gerencia a configuração e registro de atalhos de teclado."""

    def __init__(self, controller: PlayerController) -> None:
        self.controller = controller

    def setup(self) -> None:
        """Registra os atalhos padrões do sistema usando Alt Gr."""
        hotkey_map = {
            "alt gr+p": self.controller.play_pause,
            "alt gr+right": self.controller.next_track,
            "alt gr+left": self.controller.previous_track,
            "alt gr+up": lambda: self.controller.adjust_volume(5),
            "alt gr+down": lambda: self.controller.adjust_volume(-5),
            "alt gr+m": self.controller.toggle_mute,
        }

        for hk, callback in hotkey_map.items():
            keyboard.add_hotkey(hk, callback)
            logger.info(f"Hotkey configurada: {hk}")

        logger.info("Sistema de Hotkeys inicializado com sucesso.")
