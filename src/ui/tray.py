import logging
import threading
from typing import Any, Callable

import pystray
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)


class MusicTray:
    """Gerencia o ícone da bandeja do sistema (System Tray)."""

    def __init__(self, on_exit_callback: Callable[[], Any]) -> None:
        self.on_exit_callback = on_exit_callback
        self.icon: Optional[pystray.Icon] = None

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

    def _run_icon(self) -> None:
        """Executa o ícone da bandeja (bloqueante na thread)."""
        menu = pystray.Menu(
            pystray.MenuItem("Music Controller", lambda: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", self._on_exit_click),
        )
        
        self.icon = pystray.Icon(
            "music_controller",
            self._create_placeholder_icon(),
            title="Music Controller",
            menu=menu,
        )
        self.icon.run()

    def _on_exit_click(self, icon: pystray.Icon, item: Any) -> None:
        """Chamado quando o usuário clica em Sair."""
        logger.info("Solicitação de saída via System Tray.")
        if self.icon:
            self.icon.stop()
        if self.on_exit_callback:
            self.on_exit_callback()

    def start(self) -> None:
        """Inicia o ícone da bandeja em uma thread separada."""
        tray_thread = threading.Thread(target=self._run_icon, daemon=True)
        tray_thread.start()
        logger.info("System Tray inicializado.")
