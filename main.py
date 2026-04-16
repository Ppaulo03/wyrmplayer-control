import asyncio
import atexit
import ctypes
import logging
import os
import sys
import tempfile
import time
from typing import Awaitable, Callable

import flet as ft

from src.core.config import ConfigManager
from src.core.hotkeys import HotkeyManager
from src.core.state import AppState
from src.core.websocket import MusicWebSocketServer
from src.services.player_controller import PlayerController
from src.ui.hud import MusicHUD
from src.ui.settings import main as settings_main
from src.ui.tray import SystemTrayManager

# Configuração global de logging
# Carrega configuração para determinar nível e arquivo de log
cfg_manager = ConfigManager()
app_cfg = cfg_manager.load()
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"


def resolve_log_file_path(log_file_name: str) -> str:
    """Resolve the configured log file against the app directory."""
    if os.path.isabs(log_file_name):
        return log_file_name

    base_dir = os.path.dirname(
        sys.executable if getattr(sys, "frozen", False) else __file__
    )
    return os.path.abspath(os.path.join(base_dir, log_file_name))


def apply_logging_configuration(level_name: str, log_file_name: str) -> str:
    """Apply level and file target to the root logger."""
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    log_file_path = resolve_log_file_path(log_file_name)
    root_logger = logging.getLogger()

    root_logger.setLevel(level)
    for handler in list(root_logger.handlers):
        if isinstance(handler, logging.FileHandler):
            root_logger.removeHandler(handler)
            handler.close()
        else:
            handler.setLevel(level)

    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(file_handler)

    for handler in root_logger.handlers:
        handler.setLevel(level)

    return log_file_path


log_level = getattr(logging, app_cfg.log_level.upper(), logging.INFO)
log_file_path = resolve_log_file_path(
    app_cfg.log_file if hasattr(app_cfg, "log_file") else "wyrmplayer.log"
)
logging.basicConfig(
    level=log_level,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler(log_file_path, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)
logger.info(f"Log file initialized at {log_file_path} with level {app_cfg.log_level}")


async def watch_config_changes(
    hotkeys: HotkeyManager,
    hud: MusicHUD,
    state: AppState,
    exit_event: asyncio.Event,
    on_websocket_port_change: Callable[[int], Awaitable[None]],
) -> None:
    """Observa settings.json e recarrega atalhos automaticamente quando houver mudança."""
    settings_path = "settings.json"
    last_mtime: float | None = None
    last_hotkeys = state.config.load().hotkeys.copy()
    last_hud_layout = (
        state.config.load().hud_monitor,
        state.config.load().hud_position,
    )
    last_log_level = state.config.load().log_level
    last_log_file = state.config.load().log_file
    last_websocket_port = state.config.load().websocket_port

    # Debounce de lock/unlock para evitar resets repetidos por oscilacao do desktop.
    raw_input_desktop_accessible = hotkeys.is_input_desktop_accessible()
    stable_input_desktop_accessible = raw_input_desktop_accessible
    stable_state_samples = 1
    stable_samples_required = 3
    lock_observed = False
    last_unlock_rebind_at = 0.0
    unlock_rebind_cooldown_seconds = 8.0

    if os.path.exists(settings_path):
        try:
            last_mtime = os.path.getmtime(settings_path)
        except OSError:
            last_mtime = None

    while not exit_event.is_set():
        await asyncio.sleep(0.4)

        current_input_desktop_accessible = hotkeys.is_input_desktop_accessible()
        if current_input_desktop_accessible == raw_input_desktop_accessible:
            stable_state_samples += 1
        else:
            raw_input_desktop_accessible = current_input_desktop_accessible
            stable_state_samples = 1

        # So considera mudanca real quando o estado se mantem por algumas amostras.
        if (
            stable_state_samples >= stable_samples_required
            and stable_input_desktop_accessible != raw_input_desktop_accessible
        ):
            stable_input_desktop_accessible = raw_input_desktop_accessible

            if not stable_input_desktop_accessible:
                lock_observed = True
            elif lock_observed:
                now = time.monotonic()
                if now - last_unlock_rebind_at >= unlock_rebind_cooldown_seconds:
                    hotkeys.setup(force_backend_reset=True)
                    last_unlock_rebind_at = now
                    logger.info(
                        "Sessao desbloqueada: hotkeys re-registradas automaticamente."
                    )
                lock_observed = False

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
            if cfg.log_level != last_log_level or cfg.log_file != last_log_file:
                last_log_level = cfg.log_level
                last_log_file = cfg.log_file
                log_file_path = apply_logging_configuration(cfg.log_level, cfg.log_file)
                logger.info(
                    "Config alterada: logging atualizado para nível %s e arquivo %s.",
                    cfg.log_level,
                    log_file_path,
                )
            if cfg.websocket_port != last_websocket_port:
                last_websocket_port = cfg.websocket_port
                logger.info(
                    "Config alterada: porta do WebSocket atualizada para %s.",
                    cfg.websocket_port,
                )
                await on_websocket_port_change(cfg.websocket_port)
            current_hud_layout = (cfg.hud_monitor, cfg.hud_position)
            if current_hud_layout != last_hud_layout:
                last_hud_layout = current_hud_layout
                logger.info("Config alterada: HUD reposicionado e exibido novamente.")
                await hud.show_hud(display_time=cfg.hud_display_time)
            else:
                hud.apply_layout()
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
    runtime_cfg = state.config.load()

    # 1. Inicializa o servidor WebSocket (Porta 8975)
    server = MusicWebSocketServer(state, port=runtime_cfg.websocket_port)

    # 2. Inicializa a lógica de controle do player
    controller = PlayerController(state, server)

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
        controller.set_server(server)
        server_task = asyncio.create_task(server.start())

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
        on_open_settings=lambda: None,  # O tray já faz o Popen internamente
        on_reload_hotkeys=signal_reload,
    )
    tray.start()

    # 5. Inicializa o HUD visual
    hud = MusicHUD(state)
    await hud.main(page)

    # 6. Inicia o servidor WebSocket
    logger.info("Iniciando Servidor WebSocket em background...")
    server_task = asyncio.create_task(server.start())
    config_watch_task = asyncio.create_task(
        watch_config_changes(
            hotkeys,
            hud,
            state,
            exit_event,
            restart_websocket_server,
        )
    )

    # Aguarda o sinal de saída do Tray ou cancelamento
    try:
        await exit_event.wait()
        logger.info("Encerrando tarefas...")
    except asyncio.CancelledError:
        pass
    finally:
        server_task.cancel()
        config_watch_task.cancel()
        hotkeys.stop()
        tray.stop()
        logger.info("Controlador encerrado com sucesso.")
        os._exit(0)


_lock_file_handle = None
_lock_file_path = os.path.join(tempfile.gettempdir(), "WyrmPlayerControl.lock")
_singleton_mutex_handle = None


def _is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False

    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259

        process_handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            pid,
        )
        if not process_handle:
            return False

        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                process_handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(process_handle)

    try:
        os.kill(pid, 0)
    except OSError:
        return False

    return True


def ensure_single_instance() -> bool:
    """Ensure only one Windows instance runs at a time."""
    global _lock_file_handle, _singleton_mutex_handle

    if os.name != "nt":
        return True

    ERROR_ALREADY_EXISTS = 183
    mutex_name = "Global\\WyrmPlayerControlSingleton"
    mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
    if mutex_handle:
        _singleton_mutex_handle = mutex_handle
        if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            return False
        return True

    # Fallback legado para casos raros de falha no mutex.
    logger.warning("Falha ao criar mutex de instancia unica; usando lockfile legado.")

    try:
        _lock_file_handle = os.open(_lock_file_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        existing_pid = -1

        try:
            with open(_lock_file_path, "r", encoding="utf-8") as lock_file:
                raw_pid = lock_file.read().strip()
            if raw_pid:
                existing_pid = int(raw_pid)
        except (OSError, ValueError):
            existing_pid = -1

        if existing_pid > 0 and _is_process_running(existing_pid):
            return False

        try:
            os.remove(_lock_file_path)
        except OSError:
            return False

        try:
            _lock_file_handle = os.open(
                _lock_file_path, os.O_CREAT | os.O_EXCL | os.O_RDWR
            )
        except FileExistsError:
            return False

    os.write(_lock_file_handle, str(os.getpid()).encode("utf-8"))
    os.fsync(_lock_file_handle)
    return True


def _release_lock() -> None:
    """Release the file lock on exit."""
    global _lock_file_handle, _singleton_mutex_handle

    if os.name == "nt" and _singleton_mutex_handle:
        ctypes.windll.kernel32.CloseHandle(_singleton_mutex_handle)
        _singleton_mutex_handle = None

    if not _lock_file_handle:
        return

    try:
        os.close(_lock_file_handle)
    finally:
        _lock_file_handle = None

    try:
        os.remove(_lock_file_path)
    except OSError:
        pass


atexit.register(_release_lock)

if __name__ == "__main__":
    if "--settings" in sys.argv:
        ft.run(main=settings_main)
        sys.exit(0)

    if not ensure_single_instance():
        logger.warning("Another instance is already running. Exiting.")
        sys.exit(1)
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
