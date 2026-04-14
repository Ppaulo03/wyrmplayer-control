import asyncio
import ctypes
import logging
import os
import sys

import flet as ft

from src.core.hotkeys import HotkeyManager
from src.core.state import AppState
from src.core.websocket import MusicWebSocketServer
from src.services.player_controller import PlayerController
from src.ui.hud import MusicHUD
from src.ui.tray import SystemTrayManager

# Configuração global de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def watch_config_changes(hotkeys: HotkeyManager, state: AppState, exit_event: asyncio.Event) -> None:
    """Observa settings.json e recarrega atalhos automaticamente quando houver mudança."""
    settings_path = "settings.json"
    last_mtime: float | None = None
    last_hotkeys = state.config.load().hotkeys.copy()

    if os.path.exists(settings_path):
        try:
            last_mtime = os.path.getmtime(settings_path)
        except OSError:
            last_mtime = None

    while not exit_event.is_set():
        await asyncio.sleep(0.4)

        if not os.path.exists(settings_path):
            continue

        try:
            current_mtime = os.path.getmtime(settings_path)
        except OSError:
            continue

        if last_mtime is not None and current_mtime <= last_mtime:
            continue

        last_mtime = current_mtime

        try:
            cfg = state.config.load()
            if cfg.hotkeys != last_hotkeys:
                hotkeys.setup()
                last_hotkeys = cfg.hotkeys.copy()
                logger.info("Config alterada: hotkeys recarregadas automaticamente.")
        except Exception as e:
            logger.error(f"Falha ao recarregar configuração dinâmica: {e}")


async def app_main(page: ft.Page) -> None:
    """
    Orquestrador principal rodando dentro da sessão do Flet.
    Gerencia o Tray, WebSocket, Hotkeys e o HUD.
    """
    state = AppState()
    loop = asyncio.get_running_loop()
    exit_event = asyncio.Event()

    # 1. Inicializa o servidor WebSocket (Porta 8975)
    server = MusicWebSocketServer(state, port=8975)

    # 2. Inicializa a lógica de controle do player
    controller = PlayerController(state, server)


    # 3. Configura o gerenciador de atalhos globais
    hotkeys = HotkeyManager(controller)
    hotkeys.setup()

    # 4. Configura os gatilhos da Tray
    def signal_exit():
        logger.info("Sinal de saída recebido pelo Tray.")
        loop.call_soon_threadsafe(exit_event.set)

    def signal_reload():
        # Recarrega hotkeys (que por sua vez recarrega o config.json)
        hotkeys.setup()

    tray = SystemTrayManager(
        on_exit_callback=signal_exit,
        on_open_settings=lambda: None, # O tray já faz o Popen internamente
        on_reload_hotkeys=signal_reload
    )
    tray.start()

    # 5. Inicializa o HUD visual
    hud = MusicHUD(state)
    await hud.main(page)

    # 6. Inicia o servidor WebSocket
    logger.info("Iniciando Servidor WebSocket em background...")
    server_task = asyncio.create_task(server.start())
    config_watch_task = asyncio.create_task(watch_config_changes(hotkeys, state, exit_event))


    # Aguarda o sinal de saída do Tray ou cancelamento
    try:
        await exit_event.wait()
        logger.info("Encerrando tarefas...")
    except asyncio.CancelledError:
        pass
    finally:
        server_task.cancel()
        config_watch_task.cancel()
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
