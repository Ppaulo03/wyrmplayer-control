import pytest
import os
import tempfile
from typing import Generator
from src.core.config import ConfigManager
from src.core.state import AppState

@pytest.fixture
def temp_config_file() -> Generator[str, None, None]:
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)

@pytest.fixture
def config_manager(temp_config_file: str) -> ConfigManager:
    return ConfigManager(config_file=temp_config_file)

@pytest.fixture
def app_state() -> AppState:
    """Fixture de estado limpa para a nova arquitetura."""
    return AppState()
