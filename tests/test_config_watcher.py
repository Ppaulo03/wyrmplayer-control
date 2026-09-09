import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.config import AppConfig
from src.core.state import AppState
from src.services import config_watcher as config_watcher_module
from src.services.config_watcher import ConfigWatcher


@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


@pytest.fixture
def config_manager(settings_file):
    mock = MagicMock()
    mock.config_path = settings_file
    mock.load.return_value = AppConfig(websocket_port=8974, log_level="INFO", log_file="a.log")
    return mock


@pytest.fixture
def watcher(config_manager):
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


async def test_websocket_port_change_triggers_callback(watcher, config_manager):
    new_cfg = AppConfig(websocket_port=9000, log_level="INFO", log_file="a.log")
    config_manager.reload.return_value = new_cfg
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    watcher.on_websocket_port_change.assert_awaited_once_with(9000)
    assert watcher.last_websocket_port == 9000


async def test_no_websocket_port_change_skips_callback(watcher, config_manager):
    config_manager.reload.return_value = AppConfig(
        websocket_port=8974, log_level="INFO", log_file="a.log"
    )
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    watcher.on_websocket_port_change.assert_not_awaited()


async def test_log_config_change_applies_new_configuration(watcher, config_manager, monkeypatch):
    apply_logging_mock = MagicMock()
    monkeypatch.setattr(config_watcher_module, "apply_logging_configuration", apply_logging_mock)

    new_cfg = AppConfig(websocket_port=8974, log_level="DEBUG", log_file="b.log")
    config_manager.reload.return_value = new_cfg
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    apply_logging_mock.assert_called_once_with("DEBUG", "b.log")
    assert watcher.last_log_level == "DEBUG"
    assert watcher.last_log_file == "b.log"
