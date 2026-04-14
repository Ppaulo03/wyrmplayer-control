import asyncio
import ctypes
import logging
import sys

try:
    # Força a sensibilidade ao DPI do Windows antes de qualquer renderização
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

import flet as ft

from src.core.hotkeys import HotkeyManager
from src.core.state import AppState
from src.core.websocket import MusicWebSocketServer
from src.services.player_controller import PlayerController
from src.ui.hud import MusicHUD
from src.ui.tray import MusicTray

# Configuração global de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def app_main(page: ft.Page) -> None:
    """
    Orquestrador principal rodando dentro da sessão do Flet.
    Gerencia o Tray, WebSocket, Hotkeys e o HUD.
    """
    state = AppState()
    loop = asyncio.get_running_loop()
    exit_event = asyncio.Event()

    # 1. Configura a saída via Tray
    def signal_exit():
        logger.info("Sinal de saída recebido pelo Tray.")
        loop.call_soon_threadsafe(exit_event.set)

    tray = MusicTray(on_exit_callback=signal_exit)
    tray.start()

    # 2. Inicializa o servidor WebSocket (Porta 8975)
    server = MusicWebSocketServer(state, port=8975)

    # 3. Inicializa a lógica de controle do player
    controller = PlayerController(state, server)

    # 4. Configura o gerenciador de atalhos globais
    hotkeys = HotkeyManager(controller)
    hotkeys.setup()

    # 5. Inicializa o HUD visual
    hud = MusicHUD(state)
    await hud.main(page)

    # 6. Inicia o servidor WebSocket
    logger.info("Iniciando Servidor WebSocket em background...")
    server_task = asyncio.create_task(server.start())

    # Aguarda o sinal de saída do Tray ou cancelamento
    try:
        await exit_event.wait()
        logger.info("Encerrando tarefas...")
    except asyncio.CancelledError:
        pass
    finally:
        server_task.cancel()
        logger.info("Controlador encerrado com sucesso.")
        sys.exit(0)


if __name__ == "__main__":
    try:
        # FLET_APP_HIDDEN mantém o processo sem janela na Taskbar
        ft.run(
            main=app_main,
            view=ft.AppView.FLET_APP_HIDDEN,
        )
    except KeyboardInterrupt:
        logger.info("Interrompido.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Erro fatal: {e}")
        sys.exit(1)
