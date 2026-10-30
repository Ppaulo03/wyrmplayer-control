import json
import logging
import os
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ERROR_CATEGORY_INVALID_CONTENT = "invalid_content"
ERROR_CATEGORY_TRANSIENT_IO = "transient_io"


class ConfigValidationError(ValueError):
    """Exceção levantada quando um arquivo de configuração contém dados inválidos."""


VALID_HUD_POSITIONS: frozenset[str] = frozenset(
    {
        "bottom_right",
        "bottom_left",
        "top_right",
        "top_left",
        "top_center",
        "bottom_center",
        "center",
    }
)

VALID_LOG_LEVELS: frozenset[str] = frozenset(
    {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }
)

DEFAULT_HOTKEYS: dict[str, str] = {
    "play_pause": "alt gr+p",
    "next_track": "alt gr+right",
    "previous_track": "alt gr+left",
    "volume_up": "alt gr+up",
    "volume_down": "alt gr+down",
    "mute": "alt gr+m",
}

DEFAULT_TRIGGERS: dict[str, bool] = {
    "volume": True,
    "metadata": True,
    "playback": True,
}


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
    hotkeys: dict[str, str] = field(default_factory=lambda: DEFAULT_HOTKEYS.copy())
    # Gatilhos para o HUD aparecer
    triggers: dict[str, bool] = field(default_factory=lambda: DEFAULT_TRIGGERS.copy())


def _validate_and_build_config(data: dict[str, Any]) -> AppConfig:
    """Valida tipos, estruturas e valores de um dicionário e constrói um AppConfig seguro.

    Aplica compatibilidade retroativa preenchendo campos ausentes com seus valores padrão,
    mas rejeita campos conhecidos com tipos ou valores incompatíveis com o aplicativo.
    Campos desconhecidos na raiz ou em dicionários internos são ignorados na representação
    em memória, preservando compatibilidade com versões futuras sem sobrescrever o disco.
    """
    # 1. volume_step
    volume_step = 5
    if "volume_step" in data:
        val = data["volume_step"]
        if not isinstance(val, int) or isinstance(val, bool):
            raise ConfigValidationError(
                f"Campo 'volume_step' deve ser um número inteiro, obtido {type(val).__name__}."
            )
        if not (1 <= val <= 100):
            raise ConfigValidationError(
                f"Campo 'volume_step' deve estar entre 1 e 100, obtido {val}."
            )
        volume_step = val

    # 2. hud_display_time
    hud_display_time = 3
    if "hud_display_time" in data:
        val = data["hud_display_time"]
        if not isinstance(val, int) or isinstance(val, bool):
            raise ConfigValidationError(
                f"Campo 'hud_display_time' deve ser um número inteiro, obtido {type(val).__name__}."
            )
        if not (1 <= val <= 60):
            raise ConfigValidationError(
                f"Campo 'hud_display_time' deve estar entre 1 e 60 segundos, obtido {val}."
            )
        hud_display_time = val

    # 3. websocket_port
    websocket_port = 8974
    if "websocket_port" in data:
        val = data["websocket_port"]
        if not isinstance(val, int) or isinstance(val, bool):
            raise ConfigValidationError(
                f"Campo 'websocket_port' deve ser um número inteiro, obtido {type(val).__name__}."
            )
        if not (1 <= val <= 65535):
            raise ConfigValidationError(
                f"Campo 'websocket_port' deve ser uma porta TCP válida (1 a 65535), obtido {val}."
            )
        websocket_port = val

    # 4. hud_monitor
    hud_monitor = 0
    if "hud_monitor" in data:
        val = data["hud_monitor"]
        if not isinstance(val, int) or isinstance(val, bool):
            raise ConfigValidationError(
                f"Campo 'hud_monitor' deve ser um número inteiro, obtido {type(val).__name__}."
            )
        if val < 0:
            raise ConfigValidationError(f"Campo 'hud_monitor' não pode ser negativo, obtido {val}.")
        hud_monitor = val

    # 5. hud_position
    hud_position = "bottom_right"
    if "hud_position" in data:
        val = data["hud_position"]
        if not isinstance(val, str):
            raise ConfigValidationError(
                f"Campo 'hud_position' deve ser uma string, obtido {type(val).__name__}."
            )
        if val not in VALID_HUD_POSITIONS:
            raise ConfigValidationError(
                f"Posição de HUD '{val}' desconhecida. Valores permitidos: "
                f"{sorted(VALID_HUD_POSITIONS)}."
            )
        hud_position = val

    # 6. log_level
    log_level = "INFO"
    if "log_level" in data:
        val = data["log_level"]
        if not isinstance(val, str):
            raise ConfigValidationError(
                f"Campo 'log_level' deve ser uma string, obtido {type(val).__name__}."
            )
        upper_val = val.upper()
        if upper_val not in VALID_LOG_LEVELS:
            raise ConfigValidationError(
                f"Nível de log '{val}' inválido. Níveis permitidos: {sorted(VALID_LOG_LEVELS)}."
            )
        log_level = upper_val

    # 7. log_file
    log_file = "wyrmplayer.log"
    if "log_file" in data:
        val = data["log_file"]
        if not isinstance(val, str):
            raise ConfigValidationError(
                f"Campo 'log_file' deve ser uma string, obtido {type(val).__name__}."
            )
        if not val.strip():
            raise ConfigValidationError(
                "Campo 'log_file' não pode ser vazio ou conter apenas espaços."
            )
        log_file = val

    # 8. spotify_integration
    spotify_integration = False
    if "spotify_integration" in data:
        val = data["spotify_integration"]
        if not isinstance(val, bool):
            raise ConfigValidationError(
                f"Campo 'spotify_integration' deve ser um booleano, obtido {type(val).__name__}."
            )
        spotify_integration = val

    # 9. start_with_windows
    start_with_windows = False
    if "start_with_windows" in data:
        val = data["start_with_windows"]
        if not isinstance(val, bool):
            raise ConfigValidationError(
                f"Campo 'start_with_windows' deve ser um booleano, obtido {type(val).__name__}."
            )
        start_with_windows = val

    # 10. start_with_windows_elevated
    start_with_windows_elevated = False
    if "start_with_windows_elevated" in data:
        val = data["start_with_windows_elevated"]
        if not isinstance(val, bool):
            raise ConfigValidationError(
                "Campo 'start_with_windows_elevated' deve ser um booleano, "
                f"obtido {type(val).__name__}."
            )
        start_with_windows_elevated = val

    # 11. hotkeys
    # R01: Se 'hotkeys' não estiver no JSON, utiliza DEFAULT_HOTKEYS. Se estiver presente
    # (mesmo parcial ou vazio), preserva estritamente a seleção do usuário sem introduzir
    # ações não configuradas. Entradas desconhecidas são ignoradas (R02).
    if "hotkeys" in data:
        raw_hotkeys = data["hotkeys"]
        if not isinstance(raw_hotkeys, dict):
            raise ConfigValidationError(
                "Campo 'hotkeys' deve ser um objeto JSON (dict), "
                f"obtido {type(raw_hotkeys).__name__}."
            )
        hotkeys: dict[str, str] = {}
        for k, v in raw_hotkeys.items():
            if not isinstance(k, str):
                raise ConfigValidationError(
                    f"Chave de atalho deve ser uma string, obtido {type(k).__name__}."
                )
            if k in DEFAULT_HOTKEYS:
                if not isinstance(v, str):
                    raise ConfigValidationError(
                        f"Atalho para a ação '{k}' deve ser uma string, obtido {type(v).__name__}."
                    )
                hotkeys[k] = v
            # Chaves desconhecidas em hotkeys são ignoradas na representação em memória
    else:
        hotkeys = DEFAULT_HOTKEYS.copy()

    # 12. triggers
    # Triggers preenche ausentes com defaults (necessário para UI do HUD), mas preserva
    # valores configurados e ignora chaves desconhecidas (R02).
    triggers = DEFAULT_TRIGGERS.copy()
    if "triggers" in data:
        raw_triggers = data["triggers"]
        if not isinstance(raw_triggers, dict):
            raise ConfigValidationError(
                "Campo 'triggers' deve ser um objeto JSON (dict), "
                f"obtido {type(raw_triggers).__name__}."
            )
        for k, v in raw_triggers.items():
            if not isinstance(k, str):
                raise ConfigValidationError(
                    f"Chave de gatilho deve ser uma string, obtido {type(k).__name__}."
                )
            if k in DEFAULT_TRIGGERS:
                if not isinstance(v, bool):
                    raise ConfigValidationError(
                        f"Gatilho '{k}' deve ser um booleano (true/false), "
                        f"obtido {type(v).__name__}."
                    )
                triggers[k] = v
            # Chaves desconhecidas em triggers são ignoradas na representação em memória

    return AppConfig(
        volume_step=volume_step,
        hud_display_time=hud_display_time,
        websocket_port=websocket_port,
        hud_monitor=hud_monitor,
        hud_position=hud_position,
        log_level=log_level,
        log_file=log_file,
        spotify_integration=spotify_integration,
        start_with_windows=start_with_windows,
        start_with_windows_elevated=start_with_windows_elevated,
        hotkeys=hotkeys,
        triggers=triggers,
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

                new_cfg = _validate_and_build_config(data)
                self.config = new_cfg
                self.last_load_succeeded = True
                self.last_error_category = None
                return new_cfg

            except ConfigValidationError as err:
                logger.error(
                    "Configuração com valores ou tipos inválidos em %s: %s. "
                    "O arquivo original não foi modificado.",
                    self.config_path,
                    err,
                )
                return self._handle_load_failure(ERROR_CATEGORY_INVALID_CONTENT)

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

        Antes de qualquer operação em disco, valida os tipos, intervalos e estruturas
        da configuração candidata através de _validate_and_build_config. Caso a validação
        falhe, a persistência é abortada imediatamente, retornando False, sem criar
        arquivos temporários, sem alterar o arquivo original em disco e sem modificar
        o estado confirmado em self.config.

        Garante persistência atômica através da escrita em arquivo temporário no
        mesmo sistema de arquivos, seguida de flush, fsync e substituição via
        os.replace. Em caso de falha de gravação ou serialização, o arquivo
        original permanece intacto e self.config não é alterado prematuramente.

        A operação completa é protegida por self._lock, garantindo que salvamentos
        concorrentes não produzam divergência entre memória e disco.

        Limitações:
            1. Mutação de estado compartilhado:
               Se o chamador mutar diretamente atributos da instância compartilhada
               em self.config (ex.: config_manager.config.volume_step = -10) antes de
               invocar save(), tais alterações já residem no objeto em memória. Caso
               a validação rejeite os novos valores, o arquivo em disco permanece
               inalterado, mas o objeto em memória terá os valores mutados. Para
               garantir isolamento estrito entre memória e disco, recomenda-se
               instanciar uma nova configuração candidata e passar para save(new_config).
            2. Campos desconhecidos:
               AppConfig define estritamente os campos conhecidos da aplicação. Campos
               não reconhecidos presentes no arquivo original são ignorados durante
               o carregamento e serão descartados caso o arquivo seja regravado por
               save(), preservando o schema estrito da aplicação.

        Returns:
            bool: True se a validação, persistência e atualização foram bem-sucedidas;
                  False caso tenha ocorrido qualquer erro de validação, serialização,
                  criação de diretório, escrita ou substituição.
        """
        with self._lock:
            target_config = config if config is not None else self.config

            if not isinstance(target_config, AppConfig):
                logger.error(
                    "Tentativa de salvar configuração de tipo inválido em %s: "
                    "esperava AppConfig, obtido %s.",
                    self.config_path,
                    type(target_config).__name__,
                )
                return False

            try:
                raw_data = asdict(target_config)
                validated_config = _validate_and_build_config(raw_data)
            except ConfigValidationError as err:
                logger.error(
                    "Configuração com valores ou tipos inválidos rejeitada antes de salvar em %s: "
                    "%s. O arquivo original não foi modificado.",
                    self.config_path,
                    err,
                )
                return False
            except Exception as err:
                logger.error(
                    "Erro inesperado ao validar configurações antes de salvar em %s: %s. "
                    "O arquivo original não foi modificado.",
                    self.config_path,
                    err,
                )
                return False

            try:
                payload = json.dumps(asdict(validated_config), indent=4, ensure_ascii=False)
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
                self.config = validated_config

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
