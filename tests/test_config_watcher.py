import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.config import AppConfig, ConfigManager
from src.core.state import AppState
from src.services import config_watcher as config_watcher_module
from src.services.config_watcher import ConfigWatcher


@pytest.fixture
def settings_file(tmp_path: Path) -> str:
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


@pytest.fixture
def config_manager(settings_file: str) -> MagicMock:
    mock = MagicMock()
    mock.config_path = settings_file
    mock.load.return_value = AppConfig(websocket_port=8974, log_level="INFO", log_file="a.log")
    return mock


@pytest.fixture
def watcher(config_manager: MagicMock) -> ConfigWatcher:
    hotkeys = MagicMock()
    hotkeys.is_input_desktop_accessible.return_value = True
    hud = MagicMock()

    return ConfigWatcher(
        hotkeys=hotkeys,
        hud=hud,
        state=AppState(),
        config=config_manager,
        on_websocket_port_change=AsyncMock(),
        on_spotify_integration_change=MagicMock(),
    )


def _touch_settings_file(watcher: ConfigWatcher) -> None:
    """Garante um mtime maior que o registrado em last_mtime."""
    current = os.path.getmtime(watcher.settings_path)
    new_time = current + 1
    os.utime(watcher.settings_path, (new_time, new_time))


async def test_websocket_port_change_triggers_callback(
    watcher: ConfigWatcher, config_manager: MagicMock
) -> None:
    new_cfg = AppConfig(websocket_port=9000, log_level="INFO", log_file="a.log")
    config_manager.reload.return_value = new_cfg
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    watcher.on_websocket_port_change.assert_awaited_once_with(9000)
    assert watcher.last_websocket_port == 9000


async def test_no_websocket_port_change_skips_callback(
    watcher: ConfigWatcher, config_manager: MagicMock
) -> None:
    config_manager.reload.return_value = AppConfig(
        websocket_port=8974, log_level="INFO", log_file="a.log"
    )
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    watcher.on_websocket_port_change.assert_not_awaited()


async def test_log_config_change_applies_new_configuration(
    watcher: ConfigWatcher, config_manager: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply_logging_mock = MagicMock()
    monkeypatch.setattr(config_watcher_module, "apply_logging_configuration", apply_logging_mock)

    new_cfg = AppConfig(websocket_port=8974, log_level="DEBUG", log_file="b.log")
    config_manager.reload.return_value = new_cfg
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    apply_logging_mock.assert_called_once_with("DEBUG", "b.log")
    assert watcher.last_log_level == "DEBUG"
    assert watcher.last_log_file == "b.log"


# ==============================================================================
# EST-02.1.1 R03: Integração Real com ConfigWatcher e persistência atômica
# ==============================================================================


async def test_atomic_save_integration_with_config_watcher(tmp_path: Path) -> None:
    """R03: ConfigWatcher detecta substituição atômica por os.replace() sem mocks de detecção."""
    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    # Salva configuração inicial real
    initial_cfg = AppConfig(websocket_port=8974, log_level="INFO", log_file="init.log")
    assert real_config_manager.save(initial_cfg) is True

    # Inicializa watcher real usando o ConfigManager real e o mecanismo nativo de mtime
    mock_hotkeys = MagicMock()
    mock_hotkeys.is_input_desktop_accessible.return_value = True
    mock_hud = MagicMock()
    on_ws_change = AsyncMock()
    on_spotify_change = MagicMock()

    watcher = ConfigWatcher(
        hotkeys=mock_hotkeys,
        hud=mock_hud,
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=on_spotify_change,
    )

    initial_mtime = watcher.last_mtime
    assert initial_mtime is not None

    # Pequena pausa temporal para assegurar avanço de timestamp no filesystem
    await asyncio.sleep(0.05)

    # Executa salvamento atômico real com novo websocket_port
    updated_cfg = AppConfig(websocket_port=9200, log_level="DEBUG", log_file="updated.log")
    save_ok = real_config_manager.save(updated_cfg)
    assert save_ok is True

    # Executa a checagem real do ConfigWatcher
    await watcher._check_config_file()

    # Confirma que a substituição atômica foi identificada pelo mtime
    assert watcher.last_mtime is not None
    assert watcher.last_mtime > initial_mtime

    # Confirma que o callback de mudança foi acionado com o novo valor
    on_ws_change.assert_awaited_once_with(9200)
    assert watcher.last_websocket_port == 9200
    assert watcher.last_log_level == "DEBUG"

    # Confirma que a configuração recarregada corresponde aos valores persistidos
    assert real_config_manager.config.websocket_port == 9200
    assert real_config_manager.load().websocket_port == 9200
