import json
import logging
import os
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from dataclasses import fields as dataclass_fields
from pathlib import Path

logger = logging.getLogger(__name__)

ERROR_CATEGORY_INVALID_CONTENT = "invalid_content"
ERROR_CATEGORY_TRANSIENT_IO = "transient_io"


@dataclass
class AppConfig:
    """Estrutura de dados para as configurações do aplicativo."""

    volume_step: int = 5
    hud_display_time: int = 3
    # 8974 é a porta padrão do protocolo WebNowPlaying, usada tanto pela extensão
    # de navegador (YouTube Music) quanto pela extensão webnowplaying.js do
    # Spicetify (Spotify) — que não permite configurar outra porta.
    websocket_port: int = 8974
    hud_monitor: int = 0
    hud_position: str = "bottom_right"
    log_level: str = "INFO"
    log_file: str = "wyrmplayer.log"
    # Habilita a checagem/configuração automática da integração com Spotify (Spicetify) no startup.
    spotify_integration: bool = False
    # Registra (ou remove) o app em HKCU\...\Run para iniciar junto com o Windows.
    start_with_windows: bool = False
    # Se True, inicia elevado (mostra confirmação do UAC a cada login) para garantir
    # os atalhos globais sobre janelas/jogos que também rodam elevados.
    start_with_windows_elevated: bool = False
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
    """Gerenciador de persistência para configurações em JSON.

    Thread-safety:
        Todas as operações sobre o estado e persistência (save, load, reload)
        são protegidas por um RLock reentrante por instância (self._lock).
        Em ambientes multi-thread, chamadas concorrentes a save() são serializadas,
        impedindo inconsistências entre o estado em memória (self.config) e o arquivo
        em disco (settings.json).

    Limitações multi-processo:
        O lock protege acessos concorrentes dentro do mesmo processo. Se múltiplos
        processos externos acessarem o arquivo simultaneamente, a operação de escrita
        atômica (os.replace) garante integridade do arquivo JSON, mas a sincronização
        de estado em memória é escopada à instância local.
    """

    def __init__(self, config_file: str = "settings.json") -> None:
        self._lock = threading.RLock()
        self.last_load_succeeded: bool = False
        self.last_error_category: str | None = None
        if os.path.isabs(config_file):
            self.config_path = config_file
        else:
            if getattr(sys, "frozen", False):
                base_dir = Path(sys.executable).resolve().parent
            else:
                base_dir = Path(__file__).resolve().parents[2]
            self.config_path = str((base_dir / config_file).resolve())
        self.config: AppConfig = self.load()

    def reload(self) -> AppConfig:
        """Força o recarregamento do arquivo e atualiza o estado interno."""
        with self._lock:
            return self.load()

    def load(self) -> AppConfig:
        """Carrega configurações do arquivo ou cria padrões se não existir.

        Distingue arquivo inexistente (criando padrões em disco) de arquivo
        inválido/ilegível (mantendo o arquivo original intacto e preservando
        a última configuração válida em memória).
        """
        with self._lock:
            if not os.path.exists(self.config_path):
                logger.info(
                    "Arquivo de configurações não encontrado em %s. Criando padrões...",
                    self.config_path,
                )
                default_cfg = AppConfig()
                has_previous = hasattr(self, "config") and self.config is not None
                save_ok = self.save(default_cfg)
                self.last_load_succeeded = save_ok
                if save_ok:
                    self.last_error_category = None
                    return self.config

                # Falha ao recriar / persistir arquivo inexistente
                self.last_error_category = ERROR_CATEGORY_TRANSIENT_IO
                if has_previous:
                    logger.warning(
                        "Falha ao recriar arquivo de configurações %s. "
                        "Mantendo a última configuração válida em memória.",
                        self.config_path,
                    )
                    return self.config
                else:
                    logger.warning(
                        "Falha ao persistir configurações padrão em %s na inicialização. "
                        "Utilizando configurações padrão transitórias em memória.",
                        self.config_path,
                    )
                    self.config = default_cfg
                    return default_cfg

            try:
                with open(self.config_path, encoding="utf-8") as f:
                    data = json.load(f)

                if not isinstance(data, dict):
                    logger.error(
                        "Estrutura raiz inválida no arquivo de configurações %s: "
                        "esperado objeto JSON (dict), obtido %s. "
                        "O arquivo original não foi modificado.",
                        self.config_path,
                        type(data).__name__,
                    )
                    return self._handle_load_failure(ERROR_CATEGORY_INVALID_CONTENT)

                allowed_keys = {f.name for f in dataclass_fields(AppConfig)}
                filtered_data = {k: v for k, v in data.items() if k in allowed_keys}
                new_cfg = AppConfig(**filtered_data)
                self.config = new_cfg
                self.last_load_succeeded = True
                self.last_error_category = None
                return new_cfg

            except json.JSONDecodeError as err:
                logger.error(
                    "JSON malformado no arquivo de configurações %s (linha %d, coluna %d): %s. "
                    "O arquivo original não foi modificado.",
                    self.config_path,
                    err.lineno,
                    err.colno,
                    err.msg,
                )
                return self._handle_load_failure(ERROR_CATEGORY_INVALID_CONTENT)

            except UnicodeDecodeError as err:
                logger.error(
                    "Erro de decodificação UTF-8 no arquivo de configurações %s: %s. "
                    "O arquivo original não foi modificado.",
                    self.config_path,
                    err,
                )
                return self._handle_load_failure(ERROR_CATEGORY_INVALID_CONTENT)

            except (PermissionError, OSError) as err:
                logger.error(
                    "Falha de I/O ao ler arquivo de configurações %s: %s. "
                    "O arquivo original não foi modificado.",
                    self.config_path,
                    err,
                )
                return self._handle_load_failure(ERROR_CATEGORY_TRANSIENT_IO)

            except Exception as err:
                logger.error(
                    "Erro inesperado ao carregar configurações de %s: %s. "
                    "O arquivo original não foi modificado.",
                    self.config_path,
                    err,
                )
                return self._handle_load_failure(ERROR_CATEGORY_TRANSIENT_IO)

    def _handle_load_failure(
        self, error_category: str = ERROR_CATEGORY_INVALID_CONTENT
    ) -> AppConfig:
        """Trata falha de carregamento sem sobrescrever o arquivo em disco."""
        self.last_load_succeeded = False
        self.last_error_category = error_category
        if hasattr(self, "config") and self.config is not None:
            logger.warning(
                "Mantendo a última configuração válida em memória para %s.",
                self.config_path,
            )
            return self.config

        logger.warning(
            "Nenhuma configuração válida prévia em memória; "
            "utilizando configurações padrão em memória sem sobrescrever %s.",
            self.config_path,
        )
        fallback_cfg = AppConfig()
        self.config = fallback_cfg
        return fallback_cfg

    def save(self, config: AppConfig | None = None) -> bool:
        """Salva o estado das configurações no arquivo JSON de forma atômica e thread-safe.

        Garante persistência atômica através da escrita em arquivo temporário no
        mesmo sistema de arquivos, seguida de flush, fsync e substituição via
        os.replace. Em caso de falha de gravação ou serialização, o arquivo
        original permanece intacto e self.config não é alterado prematuramente.

        A operação completa é protegida por self._lock, garantindo que salvamentos
        concorrentes não produzam divergência entre memória e disco.

        Limitações:
            Se o chamador mutar diretamente atributos da instância compartilhada
            em self.config (ex.: config_manager.config.volume_step = 10) antes de
            invocar save(), tais alterações já residem no objeto em memória. Para
            garantir isolamento estrito entre memória e disco em caso de falha,
            recomenda-se passar uma nova instância para save(new_config).

        Returns:
            bool: True se a persistência e atualização foram bem-sucedidas;
                  False caso tenha ocorrido qualquer erro de serialização,
                  criação de diretório, escrita ou substituição.
        """
        with self._lock:
            target_config = config if config is not None else self.config

            try:
                payload = json.dumps(asdict(target_config), indent=4, ensure_ascii=False)
            except Exception as e:
                logger.error("Erro ao serializar configurações para JSON: %s", e)
                return False

            temp_path: str | None = None

            try:
                target_dir = os.path.dirname(os.path.abspath(self.config_path))
                os.makedirs(target_dir, exist_ok=True)

                prefix = f".{Path(self.config_path).name}.tmp."
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=target_dir,
                    prefix=prefix,
                    delete=False,
                ) as f:
                    temp_path = f.name
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())

                # Substituição atômica no mesmo filesystem
                os.replace(temp_path, self.config_path)
                temp_path = None

                # Atualiza o estado em memória apenas após o sucesso confirmado em disco
                if config is not None:
                    self.config = config

                logger.info("Configurações salvas em %s", self.config_path)
                return True
            except Exception as e:
                logger.error("Erro ao salvar configurações em %s: %s", self.config_path, e)
                return False
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError as err:
                        logger.warning(
                            "Falha ao remover arquivo temporário de configuração '%s': %s",
                            temp_path,
                            err,
                        )

    def get_all(self) -> AppConfig:
        with self._lock:
            return self.config
