import configparser
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

WEBNOWPLAYING_EXTENSION = "webnowplaying.js"
# Porta fixa esperada pela extensão webnowplaying.js do Spicetify (não configurável nela).
WEBNOWPLAYING_PORT = 8974
# Prefixo do pacote da versão Microsoft Store do Spotify — incompatível com o Spicetify.
SPOTIFY_STORE_PACKAGE_PREFIX = "SpotifyAB.SpotifyMusic_"


@dataclass(frozen=True)
class SpotifySetupStatus:
    """Resultado do diagnóstico (somente leitura) da integração com Spotify via Spicetify."""

    spotify_installed: bool
    spotify_is_microsoft_store: bool
    spicetify_path: Path | None
    extension_enabled: bool
    port_matches: bool

    @property
    def spicetify_installed(self) -> bool:
        return self.spicetify_path is not None

    @property
    def ready(self) -> bool:
        """Tudo pronto: Spicetify instalado, extensão habilitada e porta correta."""
        return (
            not self.spotify_is_microsoft_store
            and self.spicetify_installed
            and self.extension_enabled
            and self.port_matches
        )


def find_spotify_executable() -> Path | None:
    """Localiza o executável do cliente desktop do Spotify, se instalado."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None

    candidate = Path(appdata) / "Spotify" / "Spotify.exe"
    return candidate if candidate.exists() else None


def is_spotify_microsoft_store() -> bool:
    """
    Detecta se o Spotify instalado é a versão da Microsoft Store.

    O Spicetify não é compatível com essa versão (o pacote roda em sandbox e não
    pode ser modificado da mesma forma que a instalação padrão via spotify.com).
    """
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return False

    packages_dir = Path(local_appdata) / "Packages"
    if not packages_dir.exists():
        return False

    return any(packages_dir.glob(f"{SPOTIFY_STORE_PACKAGE_PREFIX}*"))


def find_spicetify_executable() -> Path | None:
    """Localiza o executável do Spicetify CLI (via PATH ou instalação padrão)."""
    which_result = shutil.which("spicetify")
    if which_result:
        return Path(which_result)

    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidate = Path(local_appdata) / "spicetify" / "spicetify.exe"
        if candidate.exists():
            return candidate

    return None


def get_spicetify_config_path() -> Path | None:
    """Resolve o caminho do config-xpui.ini do Spicetify."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None

    return Path(appdata) / "spicetify" / "config-xpui.ini"


def is_extension_enabled(config_path: Path | None) -> bool:
    """Verifica se webnowplaying.js já está listada em [AdditionalOptions] extensions."""
    if config_path is None or not config_path.exists():
        return False

    parser = configparser.ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8")
    except (OSError, configparser.Error) as e:
        logger.warning("Spotify setup: falha ao ler %s: %s", config_path, e)
        return False

    extensions_value = parser.get("AdditionalOptions", "extensions", fallback="")
    enabled = [item.strip() for item in extensions_value.split("|") if item.strip()]
    return WEBNOWPLAYING_EXTENSION in enabled


def check_status(websocket_port: int) -> SpotifySetupStatus:
    """Roda o diagnóstico completo. Somente leitura, sem efeitos colaterais."""
    spicetify_path = find_spicetify_executable()
    config_path = get_spicetify_config_path()

    return SpotifySetupStatus(
        spotify_installed=find_spotify_executable() is not None,
        spotify_is_microsoft_store=is_spotify_microsoft_store(),
        spicetify_path=spicetify_path,
        extension_enabled=is_extension_enabled(config_path),
        port_matches=websocket_port == WEBNOWPLAYING_PORT,
    )


def configure_extension(spicetify_path: Path) -> bool:
    """Registra webnowplaying.js no config do Spicetify. Não reinicia o Spotify."""
    try:
        result = subprocess.run(
            [str(spicetify_path), "config", "extensions", WEBNOWPLAYING_EXTENSION],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Spotify setup: falha ao rodar 'spicetify config extensions': %s", e)
        return False

    if result.returncode != 0:
        logger.error(
            "Spotify setup: 'spicetify config extensions' retornou código %s. "
            "stdout: %s | stderr: %s",
            result.returncode,
            result.stdout.strip(),
            result.stderr.strip(),
        )
        return False

    logger.info("Spotify setup: extensão %s registrada no Spicetify.", WEBNOWPLAYING_EXTENSION)
    return True


def apply_changes(spicetify_path: Path) -> bool:
    """Roda 'spicetify apply'. Reinicia o cliente do Spotify para aplicar as mudanças."""
    try:
        result = subprocess.run(
            [str(spicetify_path), "apply"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.error("Spotify setup: falha ao rodar 'spicetify apply': %s", e)
        return False

    if result.returncode != 0:
        logger.error(
            "Spotify setup: 'spicetify apply' retornou código %s. stdout: %s | stderr: %s",
            result.returncode,
            result.stdout.strip(),
            result.stderr.strip(),
        )
        return False

    logger.info("Spotify setup: 'spicetify apply' concluído com sucesso.")
    return True


def run_startup_check(websocket_port: int) -> SpotifySetupStatus:
    """
    Diagnóstico executado no startup quando 'spotify_integration' está ativa.

    Só registra a extensão no config do Spicetify (efeito colateral seguro, sem
    reiniciar o Spotify). Aplicar de fato (`spicetify apply`) exige confirmação
    explícita do usuário via tray — nunca acontece automaticamente aqui.
    """
    status = check_status(websocket_port)

    if status.spotify_is_microsoft_store:
        logger.warning(
            "Spotify setup: o Spotify instalado é a versão da Microsoft Store, que não é "
            "compatível com o Spicetify. Desinstale-a e instale a versão oficial em "
            "https://www.spotify.com/download para usar a integração."
        )
        return status

    if not status.port_matches:
        logger.warning(
            "Spotify setup: websocket_port=%s, mas a extensão do Spicetify espera a porta "
            "%s fixa (não configurável). Ajuste 'websocket_port' em settings.json.",
            websocket_port,
            WEBNOWPLAYING_PORT,
        )

    if status.spicetify_path is None:
        logger.info(
            "Spotify setup: Spicetify não encontrado. Instale manualmente em "
            "https://spicetify.app para habilitar a integração com Spotify."
        )
        return status

    if status.extension_enabled:
        logger.info(
            "Spotify setup: extensão %s já habilitada no Spicetify.", WEBNOWPLAYING_EXTENSION
        )
        return status

    if configure_extension(status.spicetify_path):
        logger.info(
            "Spotify setup: extensão configurada, mas ainda não aplicada. Use o item "
            "'Configurar Spotify' na system tray para aplicar (isso reinicia o Spotify)."
        )
        return check_status(websocket_port)

    return status
