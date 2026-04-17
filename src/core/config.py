import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    """Estrutura de dados para as configurações do aplicativo."""

    volume_step: int = 5
    hud_display_time: int = 3
    websocket_port: int = 8975
    hud_monitor: int = 0
    hud_position: str = "bottom_right"
    log_level: str = "INFO"
    log_file: str = "wyrmplayer.log"
    # Atalhos Globais
    hotkeys: dict[str, str] = field(
        default_factory=lambda: {
            "play_pause": "alt gr+p",
            "next_track": "alt gr+right",
            "previous_track": "alt gr+left",
            "volume_up": "alt gr+up",
            "volume_down": "alt gr+down",
            "mute": "alt gr+m",
        }
    )
    # Gatilhos para o HUD aparecer
    triggers: dict[str, bool] = field(
        default_factory=lambda: {
            "volume": True,
            "metadata": True,
            "playback": True,
        }
    )


class ConfigManager:
    """Gerenciador de persistência para configurações em JSON."""

    def __init__(self, config_file: str = "settings.json") -> None:
        if os.path.isabs(config_file):
            self.config_path = config_file
        else:
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).resolve().parent
            else:
                base_dir = Path(__file__).resolve().parents[2]
            self.config_path = str((base_dir / config_file).resolve())
        self.config = self.load()

    def reload(self) -> AppConfig:
        """Força o recarregamento do arquivo e atualiza o estado interno."""
        return self.load()

    def load(self) -> AppConfig:
        """Carrega configurações do arquivo ou cria padrões se não existir."""
        if not os.path.exists(self.config_path):
            logger.info(f"Arquivo de configurações não encontrado em {self.config_path}. Criando padrões...")
            default_cfg = AppConfig()
            self.config = default_cfg
            self.save()
            return default_cfg

        try:
            with open(self.config_path, encoding="utf-8") as f:
                data = json.load(f)
                allowed_keys = {f.name for f in dataclass_fields(AppConfig)}
                filtered_data = {k: v for k, v in data.items() if k in allowed_keys}
                new_cfg = AppConfig(**filtered_data)
                self.config = new_cfg
                return new_cfg
        except Exception as e:
            logger.error(f"Erro ao carregar configurações de {self.config_path}: {e}")
            return AppConfig()

    def save(self, config: AppConfig | None = None) -> None:
        """Salva o estado atual das configurações no arquivo JSON."""
        if config:
            self.config = config

        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self.config), f, indent=4, ensure_ascii=False)
            logger.info(f"Configurações salvas em {self.config_path}")
        except Exception as e:
            logger.error(f"Erro ao salvar configurações: {e}")

    def get_all(self) -> AppConfig:
        return self.config
