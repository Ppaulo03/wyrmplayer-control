import json
from src.core.config import ConfigManager

def test_config_defaults(config_manager: ConfigManager) -> None:
    cfg = config_manager.load()
    assert cfg.volume_step == 5
    assert cfg.websocket_port == 8975
    assert "play_pause" in cfg.hotkeys

def test_config_save_load(config_manager: ConfigManager, temp_config_file: str) -> None:
    cfg = config_manager.load()
    cfg.volume_step = 10
    cfg.websocket_port = 9000
    config_manager.save(cfg)
    
    # Reload from file
    new_cfg = config_manager.load()
    assert new_cfg.volume_step == 10
    assert new_cfg.websocket_port == 9000

def test_config_filtered_data(config_manager: ConfigManager) -> None:
    # Simulate junk in JSON
    with open(config_manager.config_path, "w") as f:
        json.dump({"volume_step": 7, "invalid_key": "junk"}, f)
    
    cfg = config_manager.load()
    assert cfg.volume_step == 7
    assert not hasattr(cfg, "invalid_key")
