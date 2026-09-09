import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable

from src.core.config import ConfigManager
from src.core.hotkeys import HotkeyManager
from src.core.logging_config import apply_logging_configuration
from src.core.state import AppState
from src.ui.hud import MusicHUD

logger = logging.getLogger(__name__)


class ConfigWatcher:
    """
    Service that monitors settings.json for changes and updates application components.
    Also handles re-binding hotkeys on Windows session unlock events.
    """

    def __init__(
        self,
        hotkeys: HotkeyManager,
        hud: MusicHUD,
        state: AppState,
        config: ConfigManager,
        on_websocket_port_change: Callable[[int], Awaitable[None]],
        on_spotify_integration_change: Callable[[], None],
    ):
        self.hotkeys = hotkeys
        self.hud = hud
        self.state = state
        self.config = config
        self.on_websocket_port_change = on_websocket_port_change
        self.on_spotify_integration_change = on_spotify_integration_change
        self.settings_path = config.config_path

        # Internal state
        self.last_mtime: float | None = None
        current_cfg = self.config.load()
        self.last_hotkeys = current_cfg.hotkeys.copy()
        self.last_hud_layout = (current_cfg.hud_monitor, current_cfg.hud_position)
        self.last_log_level = current_cfg.log_level
        self.last_log_file = current_cfg.log_file
        self.last_websocket_port = current_cfg.websocket_port
        self.last_spotify_integration = current_cfg.spotify_integration

        # Debounce/Windows Session state
        self.raw_input_desktop_accessible = hotkeys.is_input_desktop_accessible()
        self.stable_input_desktop_accessible = self.raw_input_desktop_accessible
        self.stable_state_samples = 1
        self.stable_samples_required = 3
        self.lock_observed = False
        self.last_unlock_rebind_at = 0.0
        self.unlock_rebind_cooldown_seconds = 8.0

        if os.path.exists(self.settings_path):
            try:
                self.last_mtime = os.path.getmtime(self.settings_path)
            except OSError:
                self.last_mtime = None

    async def start(self, exit_event: asyncio.Event) -> None:
        """Runs the monitoring loop until exit_event is set."""
        logger.info(f"Monitor de configurações iniciado para: {self.settings_path}")
        while not exit_event.is_set():
            try:
                await asyncio.sleep(0.5)
                self._check_session_unlock()
                await self._check_config_file()
            except Exception as e:
                logger.error(f"Erro no loop do Monitor: {e}")
                await asyncio.sleep(2)  # Evita spam se algo quebrar

    def _check_session_unlock(self) -> None:
        """Detects if Windows session was unlocked to re-register hotkeys."""
        current_input_desktop_accessible = self.hotkeys.is_input_desktop_accessible()
        if current_input_desktop_accessible == self.raw_input_desktop_accessible:
            self.stable_state_samples += 1
        else:
            self.raw_input_desktop_accessible = current_input_desktop_accessible
            self.stable_state_samples = 1

        if (
            self.stable_state_samples >= self.stable_samples_required
            and self.stable_input_desktop_accessible != self.raw_input_desktop_accessible
        ):
            self.stable_input_desktop_accessible = self.raw_input_desktop_accessible

            if not self.stable_input_desktop_accessible:
                self.lock_observed = True
            elif self.lock_observed:
                now = time.monotonic()
                if now - self.last_unlock_rebind_at >= self.unlock_rebind_cooldown_seconds:
                    self.hotkeys.setup(force_backend_reset=True)
                    self.last_unlock_rebind_at = now
                    logger.info("Sessao desbloqueada: hotkeys re-registradas automaticamente.")
                self.lock_observed = False

    async def _check_config_file(self) -> None:
        """Checks for metadata changes in settings.json and applies them."""
        if not os.path.exists(self.settings_path):
            return

        try:
            current_mtime = os.path.getmtime(self.settings_path)
        except OSError:
            return

        if self.last_mtime is not None and current_mtime <= self.last_mtime:
            return

        # Sincronização e detecção real
        try:
            old_layout = self.last_hud_layout
            cfg = self.config.reload()
            # Atualiza mtime apos leitura confirmada
            self.last_mtime = os.path.getmtime(self.settings_path)

            logger.info(f"Mudança de arquivo detectada! mtime: {self.last_mtime}. Recarregando...")

            # Hotkeys change
            if cfg.hotkeys != self.last_hotkeys:
                self.hotkeys.setup()
                self.last_hotkeys = cfg.hotkeys.copy()
                logger.info("Hotkeys recarregadas.")

            # HUD change
            current_hud_layout = (cfg.hud_monitor, cfg.hud_position)
            if current_hud_layout != old_layout:
                self.last_hud_layout = current_hud_layout
                logger.info(
                    "HUD alterado: Monitor %s, Posição %s. Forçando exibição.",
                    cfg.hud_monitor,
                    cfg.hud_position,
                )
                self.hud.apply_layout()
                await self.hud.show_hud(display_time=cfg.hud_display_time)
            else:
                self.hud.apply_layout()

            # Porta do WebSocket
            if cfg.websocket_port != self.last_websocket_port:
                new_port = cfg.websocket_port
                self.last_websocket_port = new_port
                await self.on_websocket_port_change(new_port)
                logger.info("Porta do WebSocket alterada para %s.", new_port)

            # Nível/arquivo de log
            if cfg.log_level != self.last_log_level or cfg.log_file != self.last_log_file:
                self.last_log_level = cfg.log_level
                self.last_log_file = cfg.log_file
                apply_logging_configuration(cfg.log_level, cfg.log_file)
                logger.info(
                    "Logging reconfigurado: nível=%s, arquivo=%s.", cfg.log_level, cfg.log_file
                )

            # Integração com Spotify (só afeta visibilidade do item na tray)
            if cfg.spotify_integration != self.last_spotify_integration:
                self.last_spotify_integration = cfg.spotify_integration
                self.on_spotify_integration_change()
                logger.info(
                    "Integração com Spotify %s.",
                    "ativada" if cfg.spotify_integration else "desativada",
                )

        except Exception as e:
            logger.error(f"Falha ao processar mudança de configuração: {e}")
