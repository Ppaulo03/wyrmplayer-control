import asyncio
import logging
import os
import sys

# Garante que a raiz do projeto esteja no sys.path para suportar os imports 'src.*'
# mesmo quando o main.py é executado de dentro da pasta src.
root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if root_path not in sys.path:
    sys.path.insert(0, root_path)

import flet as ft

from src.core.config import ConfigManager
from src.core.hotkeys import HotkeyManager
from src.core.state import AppState
from src.core.websocket import MusicWebSocketServer
from src.infrastructure import win32
from src.services.player_controller import PlayerController
from src.ui.hud import MusicHUD
from src.ui.settings import main as settings_main
from src.ui.tray import SystemTrayManager

# Evolução Arquitetural
from src.core.logging_config import setup_initial_logging
from src.core.single_instance import SingleInstance
from src.services.config_watcher import ConfigWatcher

# Inicialização do Gerenciador de Configuração
cfg_manager = ConfigManager()
app_cfg = cfg_manager.load()

# Configuração de Logging
log_file_path = setup_initial_logging(
    app_cfg.log_level,
    app_cfg.log_file if hasattr(app_cfg, "log_file") else "wyrmplayer.log",
)
logger = logging.getLogger(__name__)
logger.info(f"Log file initialized at {log_file_path} with level {app_cfg.log_level}")

# Gerenciador de Instância Única
single_instance = SingleInstance()


async def app_main(page: ft.Page) -> None:
    """
    Orquestrador principal rodando dentro da sessão do Flet.
    Gerencia o Tray, WebSocket, Hotkeys e o HUD.
    """
    state = AppState()
    loop = asyncio.get_running_loop()
    exit_event = asyncio.Event()
    runtime_cfg = cfg_manager.load()

    # 1. Inicializa o servidor WebSocket (Porta 8975)
    server = MusicWebSocketServer(state, port=runtime_cfg.websocket_port)

    # 2. Inicializa a lógica de controle do player
    controller = PlayerController(state, cfg_manager, server, loop=loop)

    server_task: asyncio.Task[None] | None = None

    async def restart_websocket_server(new_port: int) -> None:
        nonlocal server, server_task

        logger.info("Reiniciando servidor WebSocket na porta %s...", new_port)
        if server_task is not None:
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("Falha ao encerrar WebSocket anterior: %s", e)

        server = MusicWebSocketServer(state, port=new_port)
        controller.set_messenger(server)
        server_task = asyncio.create_task(server.start())

    # 3. Configura o gerenciador de atalhos globais
    hotkeys = HotkeyManager(controller, cfg_manager)
    hotkeys.setup()

    # 4. Configura os gatilhos da Tray
    def signal_exit() -> None:
        logger.info("Sinal de saída recebido pelo Tray.")
        loop.call_soon_threadsafe(exit_event.set)

    def signal_reload() -> None:
        hotkeys.setup()

    tray = SystemTrayManager(
        on_exit_callback=signal_exit,
        on_open_settings=lambda: None,
        on_reload_hotkeys=signal_reload,
    )
    tray.start()

    # 5. Inicializa o HUD visual
    hud = MusicHUD(state, cfg_manager)
    await hud.main(page)

    # 6. Inicializa o Monitor de Configurações
    watcher = ConfigWatcher(
        hotkeys=hotkeys,
        hud=hud,
        state=state,
        config=cfg_manager,
        on_websocket_port_change=restart_websocket_server,
    )

    # 7. Inicia tarefas em background
    logger.info("Iniciando tarefas de background...")
    server_task = asyncio.create_task(server.start())
    config_watch_task = asyncio.create_task(watcher.start(exit_event))

    # Aguarda o sinal de saída do Tray ou cancelamento do loop
    try:
        await exit_event.wait()
        logger.info("Sinal de saída recebido. Encerrando tarefas...")
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Erro inesperado no loop principal: {e}")
    finally:
        # Cancelamento das tarefas em background
        for task in [server_task, config_watch_task]:
            if task and not task.done():
                task.cancel()

        # Liberação de recursos de sistema
        hotkeys.stop()
        tray.stop()

        logger.info("Controlador encerrado com sucesso.")
        single_instance.unlock()
        os._exit(0)


if __name__ == "__main__":
    if "--settings" in sys.argv:
        ft.run(main=settings_main)
        sys.exit(0)

    if win32.relaunch_as_admin_if_needed(sys.argv):
        sys.exit(0)

    if not single_instance.lock():
        logger.warning("Another instance is already running. Exiting.")
        sys.exit(1)

    try:
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
