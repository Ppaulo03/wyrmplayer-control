import pytest
from unittest.mock import MagicMock
from src.core.state import AppState
from src.services.player_controller import PlayerController
from src.core.config import AppConfig

class MockMessenger:
    def __init__(self):
        self.commands = []
    
    def enqueue_command(self, command: str) -> None:
        self.commands.append(command)

@pytest.fixture
def state():
    return AppState()

@pytest.fixture
def messenger():
    return MockMessenger()

@pytest.fixture
def config_manager():
    mock = MagicMock()
    mock.load.return_value = AppConfig()
    return mock

@pytest.fixture
def controller(state, config_manager, messenger):
    return PlayerController(state, config_manager, messenger)

def test_play_pause_command(controller, messenger):
    controller.play_pause()
    assert "playPause" in messenger.commands

def test_next_track_command(controller, messenger):
    controller.next_track()
    assert "next" in messenger.commands

def test_previous_track_command(controller, messenger):
    controller.previous_track()
    assert "previous" in messenger.commands

def test_adjust_volume_up(controller, messenger, config_manager, state):
    state.metadata = state.metadata.__class__(volume=50) # Set initial volume
    
    # Mock config step to 5
    config_manager.load.return_value = AppConfig(volume_step=10)
    
    controller.adjust_volume(1) # Up one step
    assert "setVolume 60" in messenger.commands

def test_toggle_mute(controller, messenger, state):
    state.metadata = state.metadata.__class__(volume=40)
    state.is_muted = False
    
    controller.toggle_mute()
    assert state.is_muted is True
    assert "setVolume 0" in messenger.commands
    
    controller.toggle_mute()
    assert state.is_muted is False
    assert "setVolume 40" in messenger.commands
