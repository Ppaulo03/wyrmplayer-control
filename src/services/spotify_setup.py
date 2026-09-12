import configparser
import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.infrastructure import win32

logger = logging.getLogger(__name__)

WEBNOWPLAYING_EXTENSION = "webnowplaying.js"
# Porta fixa esperada pela extensão webnowplaying.js do Spicetify (não configurável nela).
WEBNOWPLAYING_PORT = 8974
# Prefixo do pacote da versão Microsoft Store do Spotify — incompatível com o Spicetify.
SPOTIFY_STORE_PACKAGE_PREFIX = "SpotifyAB.SpotifyMusic_"

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _clean_output_line(raw: str) -> str:
    """Remove códigos ANSI de cor/cursor do texto de progresso do Spicetify."""
    return _ANSI_ESCAPE_RE.sub("", raw).strip()


@dataclass(frozen=True)
class SpotifySetupStatus:
    """Resultado do diagnóstico (somente leitura) da integração com Spotify via Spicetify."""

    spotify_installed: bool
    spotify_is_microsoft_store: bool
    spicetify_path: Path | None
    extension_enabled: bool
    extension_file_present: bool
    port_matches: bool

    @property
    def spicetify_installed(self) -> bool:
        return self.spicetify_path is not None

    @property
    def ready(self) -> bool:
        """Tudo pronto: Spicetify instalado, extensão habilitada e com arquivo, porta ok."""
        return (
            not self.spotify_is_microsoft_store
            and self.spicetify_installed
            and self.extension_enabled
            and self.extension_file_present
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


def get_spicetify_extensions_dirs(spicetify_path: Path | None) -> list[Path]:
    """
    Resolve as pastas onde o Spicetify pode carregar extensões.

    A pasta "de verdade" (onde as extensões que vêm junto com o Spicetify e
    as instaladas via Marketplace realmente ficam) é ao lado do próprio
    executável — normalmente %localappdata%\\spicetify\\Extensions, não
    %appdata%\\spicetify\\Extensions (essa é só onde o config-xpui.ini mora).
    Verificado na prática: webnowplaying.js estava em
    %localappdata%\\spicetify\\Extensions e funcionando, enquanto
    %appdata%\\spicetify\\Extensions estava vazia. Ainda checamos %appdata%
    como fallback, caso alguma instalação use esse layout.
    """
    dirs = []
    if spicetify_path is not None:
        dirs.append(spicetify_path.parent / "Extensions")

    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(Path(appdata) / "spicetify" / "Extensions")

    return dirs


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


def is_extension_file_present(spicetify_path: Path | None) -> bool:
    """
    Verifica se o arquivo webnowplaying.js existe de fato numa das pastas de
    extensões do Spicetify (ver get_spicetify_extensions_dirs).

    Registrar o nome em [AdditionalOptions] extensions (is_extension_enabled)
    não significa que o arquivo existe. Sem o arquivo, o Spotify "aplica" sem
    erro mas a extensão nunca carrega e nunca conecta no WebSocket.
    """
    return any(
        (extensions_dir / WEBNOWPLAYING_EXTENSION).is_file()
        for extensions_dir in get_spicetify_extensions_dirs(spicetify_path)
    )


def check_status(websocket_port: int) -> SpotifySetupStatus:
    """Roda o diagnóstico completo. Somente leitura, sem efeitos colaterais."""
    spicetify_path = find_spicetify_executable()
    config_path = get_spicetify_config_path()

    return SpotifySetupStatus(
        spotify_installed=find_spotify_executable() is not None,
        spotify_is_microsoft_store=is_spotify_microsoft_store(),
        spicetify_path=spicetify_path,
        extension_enabled=is_extension_enabled(config_path),
        extension_file_present=is_extension_file_present(spicetify_path),
        port_matches=websocket_port == WEBNOWPLAYING_PORT,
    )


def _run_streaming(
    command: list[str], *, timeout: float, on_output: Callable[[str], None]
) -> subprocess.CompletedProcess[str]:
    """
    Roda um comando lendo a saída incrementalmente, char a char, chamando
    `on_output` a cada linha "lógica" (cortada em '\\r' ou '\\n').

    Precisa ser char-a-char em vez de iterar por linha (`for line in stdout`):
    barras de progresso como a do Spicetify atualizam a mesma linha via '\\r'
    sem nunca emitir '\\n' até terminar — iterar por linha ficaria muda até o
    comando inteiro acabar, exatamente o problema que isso resolve.
    """
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None

    all_output: list[str] = []
    buffer = ""
    deadline = time.monotonic() + timeout

    try:
        while True:
            ch = proc.stdout.read(1)
            if ch:
                all_output.append(ch)
                if ch in ("\r", "\n"):
                    cleaned = _clean_output_line(buffer)
                    if cleaned:
                        on_output(cleaned)
                    buffer = ""
                else:
                    buffer += ch
            elif proc.poll() is not None:
                break

            if time.monotonic() > deadline:
                proc.kill()
                raise subprocess.TimeoutExpired(command, timeout)

        cleaned = _clean_output_line(buffer)
        if cleaned:
            on_output(cleaned)
        proc.wait(timeout=max(0.0, deadline - time.monotonic()))
    finally:
        proc.stdout.close()

    return subprocess.CompletedProcess(
        command, proc.returncode if proc.returncode is not None else 1, "".join(all_output), ""
    )


def _run_spicetify(
    command: list[str],
    *,
    avoid_admin: bool,
    timeout: float,
    on_output: Callable[[str], None] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Roda um comando do Spicetify, sem privilégios administrativos de verdade quando
    `avoid_admin` é True (via `win32.run_command_unelevated`, verificado rodando em
    nível de integridade Médio de verdade, não apenas ignorando o aviso).

    O token criado por logon S4U (usado pela tarefa agendada) faz o Spicetify
    detectar erroneamente elevação mesmo rodando em nível Médio — por isso,
    quando `avoid_admin` é True, também passamos `--bypass-admin`. Isso só é
    seguro aqui porque a execução já é genuinamente não-elevada; nunca passe
    `--bypass-admin` num comando que roda realmente elevado.

    `on_output`, quando informado, é chamado com cada linha nova de saída
    conforme ela chega — útil pra UI mostrar progresso ao vivo em vez de ficar
    muda até o comando terminar. Só funciona no caminho não-elevado; no
    caminho da tarefa agendada a saída só fica disponível no final.
    """
    if avoid_admin:
        if on_output:
            on_output("rodando sem privilégios administrativos (tarefa agendada)... aguarde")
        # A tarefa agendada adiciona latência (agendar, despachar, rodar) além do
        # tempo do próprio comando — folga extra evita falso timeout.
        return win32.run_command_unelevated([*command, "--bypass-admin"], timeout=timeout + 30)

    if on_output is None:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)

    return _run_streaming(command, timeout=timeout, on_output=on_output)


def configure_extension(
    spicetify_path: Path,
    *,
    avoid_admin: bool = False,
    on_output: Callable[[str], None] | None = None,
) -> bool:
    """
    Registra webnowplaying.js no config do Spicetify. Não reinicia o Spotify.

    O Spicetify se recusa a rodar enquanto elevado (risco de o Spotify, que roda
    como usuário normal, ficar com tela em branco por não acessar arquivos
    modificados por um processo admin). Passe `avoid_admin=True` quando o processo
    atual estiver elevado — o comando roda de fato sem privilégios administrativos
    via `win32.run_command_unelevated`.
    """
    command = [str(spicetify_path), "config", "extensions", WEBNOWPLAYING_EXTENSION]

    try:
        result = _run_spicetify(command, avoid_admin=avoid_admin, timeout=30, on_output=on_output)
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
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


# 'apply'/'backup apply' fazem o patch de centenas de arquivos do Spotify — já
# observamos isso levar 70-90s numa máquina comum, então um timeout de 60s
# (usado antes) matava o processo no meio e derrubava tudo como falha
# silenciosa. Generoso o bastante pra sobrar folga em máquinas mais lentas.
_APPLY_TIMEOUT_SECONDS = 240.0

# Trecho comum às mensagens de erro do Spicetify que pedem pra rodar
# 'spicetify backup apply' antes de aplicar — tanto "You haven't backed up"
# (nunca fez backup) quanto "Spotify version and backup version are
# mismatched" (Spotify se atualizou sozinho) terminam sugerindo esse mesmo
# comando, então casar nele é mais robusto que casar o texto exato de cada
# variante (que pode mudar entre versões do Spicetify).
_NEEDS_BACKUP_APPLY_MARKER = "backup apply"

# O Spicetify se recusa a aplicar quando a própria versão dele está
# desatualizada em relação ao client do Spotify instalado ("Spotify version
# mismatch with Spicetify") — o próprio Spicetify sugere rodar
# 'spicetify update' nesse caso.
_VERSION_MISMATCH_MARKER = "version mismatch with spicetify"


def _close_spotify_processes(timeout: float = 10.0) -> None:
    """
    Força o fechamento de todos os processos Spotify.exe (cliente principal +
    processos helper do Chromium que ele usa) e espera até sumirem da lista de
    processos (ou o timeout).

    'spicetify backup apply' pode falhar com "The process cannot access the
    file because it is being used by another process" ao limpar o backup
    quando um processo do Spotify ainda está com um arquivo aberto na pasta
    extraída/patcheada — geralmente porque a tentativa anterior de
    'spicetify apply' já tinha começado a fechar/reabrir o Spotify e não deu
    tempo de soltar os handles antes do retry. Rodar isso antes do retry
    garante uma base limpa.
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "Spotify.exe", "/T"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Spotify setup: falha ao tentar fechar o Spotify: %s", e)
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            still_running = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Spotify.exe"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return

        if "Spotify.exe" not in still_running.stdout:
            return
        time.sleep(0.3)

    logger.warning(
        "Spotify setup: processo(s) Spotify.exe continuam de pé após %.0fs de espera; "
        "seguindo mesmo assim.",
        timeout,
    )


def _run_apply_command(
    command: list[str],
    *,
    avoid_admin: bool,
    on_output: Callable[[str], None] | None,
    label: str,
) -> subprocess.CompletedProcess[str] | None:
    """Roda um comando de apply/update do Spicetify; devolve None se a execução em si falhar."""
    try:
        return _run_spicetify(
            command, avoid_admin=avoid_admin, timeout=_APPLY_TIMEOUT_SECONDS, on_output=on_output
        )
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        logger.error("Spotify setup: falha ao rodar '%s': %s", label, e)
        return None


def _update_spicetify_proactively(
    spicetify_path: Path, *, avoid_admin: bool, on_output: Callable[[str], None] | None
) -> None:
    """
    Roda 'spicetify update' incondicionalmente, antes de tentar aplicar.

    'spicetify update' já é rápido e seguro quando não há nada pra atualizar
    (só reporta "up-to-date" e sai) — então não vale a pena tentar adivinhar
    se há uma versão nova disponível antes de chamar, nem depender só de
    reagir ao erro "version mismatch" durante o apply: esse erro só aparece em
    certos cenários (ex.: patch de custom app/Marketplace), então um apply que
    só mexe na lista de extensões pode "ter sucesso" sem nunca disparar esse
    reativo, deixando o Spicetify desatualizado silenciosamente mesmo assim.
    Falha aqui não impede a tentativa de aplicar em seguida — o retry reativo
    abaixo ainda cobre esse caso.
    """
    if on_output:
        on_output("checando se há atualização do spicetify...")
    result = _run_apply_command(
        [str(spicetify_path), "update"],
        avoid_admin=avoid_admin,
        on_output=on_output,
        label="spicetify update",
    )
    if result is None or result.returncode != 0:
        logger.warning(
            "Spotify setup: checagem proativa de 'spicetify update' falhou ou não pôde "
            "confirmar sucesso; seguindo mesmo assim (o apply ainda tenta se recuperar "
            "reativamente se a versão desatualizada causar erro)."
        )
        return
    logger.info("Spotify setup: checagem proativa de atualização do Spicetify concluída.")


def apply_changes(
    spicetify_path: Path,
    *,
    avoid_admin: bool = False,
    on_output: Callable[[str], None] | None = None,
) -> bool:
    """
    Roda 'spicetify apply'. Reinicia o cliente do Spotify para aplicar as mudanças.

    Antes de tentar, roda 'spicetify update' proativamente (ver
    _update_spicetify_proactively) — não depende só de reagir a um erro
    específico. Além disso, o Spicetify recusa aplicar em pelo menos mais um
    cenário conhecido, do qual também tentamos nos recuperar automaticamente
    em vez de exigir que o usuário rode comandos manualmente no terminal: sem
    backup (nunca fez, ou o Spotify se atualizou sozinho e o backup ficou
    desincronizado) → roda 'spicetify backup apply'.

    Ver nota sobre `avoid_admin` e `on_output` em `configure_extension`.
    """
    _update_spicetify_proactively(spicetify_path, avoid_admin=avoid_admin, on_output=on_output)

    result = _run_apply_command(
        [str(spicetify_path), "apply"],
        avoid_admin=avoid_admin,
        on_output=on_output,
        label="spicetify apply",
    )
    if result is None:
        return False

    if result.returncode == 0:
        logger.info("Spotify setup: 'spicetify apply' concluído com sucesso.")
        return True

    # Até duas recuperações em sequência: o Spicetify pode reclamar de
    # desatualização E de backup ausente na mesma tentativa (a atualização
    # pode invalidar um backup que já existia), então depois de corrigir uma
    # causa reavaliamos a próxima falha em vez de assumir que só uma se aplica.
    for _ in range(2):
        stdout_lower = result.stdout.lower()

        if _VERSION_MISMATCH_MARKER in stdout_lower:
            logger.warning(
                "Spotify setup: Spicetify desatualizado pra versão do Spotify instalado. "
                "Rodando 'spicetify update' e tentando de novo..."
            )
            if on_output:
                on_output("atualizando o spicetify...")
            update_result = _run_apply_command(
                [str(spicetify_path), "update"],
                avoid_admin=avoid_admin,
                on_output=on_output,
                label="spicetify update",
            )
            if update_result is None or update_result.returncode != 0:
                logger.error("Spotify setup: 'spicetify update' falhou; desistindo.")
                return False

        elif _NEEDS_BACKUP_APPLY_MARKER in stdout_lower:
            logger.warning(
                "Spotify setup: Spicetify pediu 'spicetify backup apply' antes de aplicar "
                "(sem backup ainda, ou Spotify atualizado sozinho). Rodando esse comando "
                "e tentando de novo..."
            )
            if on_output:
                on_output("preparando novo backup do spicetify...")
            _close_spotify_processes()
            backup_result = _run_apply_command(
                [str(spicetify_path), "backup", "apply"],
                avoid_admin=avoid_admin,
                on_output=on_output,
                label="spicetify backup apply",
            )
            if backup_result is None:
                return False
            if backup_result.returncode == 0:
                logger.info("Spotify setup: 'spicetify backup apply' concluído com sucesso.")
                return True
            logger.error(
                "Spotify setup: 'spicetify backup apply' retornou código %s. "
                "stdout: %s | stderr: %s",
                backup_result.returncode,
                backup_result.stdout.strip(),
                backup_result.stderr.strip(),
            )
            return False

        else:
            break

        result = _run_apply_command(
            [str(spicetify_path), "apply"],
            avoid_admin=avoid_admin,
            on_output=on_output,
            label="spicetify apply",
        )
        if result is None:
            return False
        if result.returncode == 0:
            logger.info("Spotify setup: 'spicetify apply' concluído com sucesso.")
            return True

    logger.error(
        "Spotify setup: 'spicetify apply' retornou código %s. stdout: %s | stderr: %s",
        result.returncode,
        result.stdout.strip(),
        result.stderr.strip(),
    )
    return False


def run_interactive_setup(
    websocket_port: int, on_progress: Callable[[str], None] | None = None
) -> None:
    """
    Fluxo completo e interativo de configuração do Spicetify: checa status,
    registra a extensão se preciso, pede confirmação nativa (MessageBoxW) antes
    de reiniciar o Spotify (`spicetify apply`) e informa o resultado.

    Usa diálogos nativos do Win32, então é seguro chamar tanto do processo
    principal (clique na tray) quanto do processo da janela de Configurações
    (ex.: ao ativar o toggle de integração) — nenhum dos dois depende de estado
    do processo chamador. Bloqueante: sempre rode numa thread separada da
    thread de UI/mensagens (tray, Flet), nunca diretamente nela.

    `on_progress`, quando informado, é chamado com cada linha de progresso do
    Spicetify conforme ela chega (ex.: "Patching files [126/252]") — evita que
    a UI fique muda entre o clique e o diálogo final de sucesso/falha, que sem
    isso pode levar bem mais de um minuto (patch de centenas de arquivos).
    """
    cfg_status = check_status(websocket_port)

    if cfg_status.spotify_is_microsoft_store:
        win32.info_dialog(
            "Spotify",
            "O Spotify instalado é a versão da Microsoft Store, que não é compatível "
            "com o Spicetify. Desinstale-a e instale a versão oficial em "
            "https://www.spotify.com/download para usar a integração.",
        )
        return

    if cfg_status.spicetify_path is None:
        win32.info_dialog(
            "Spotify",
            "Spicetify não foi encontrado. Instale-o manualmente em "
            "https://spicetify.app e tente novamente.",
        )
        return

    if not cfg_status.port_matches:
        win32.info_dialog(
            "Spotify",
            f"A porta configurada ({websocket_port}) não é a esperada pela extensão "
            f"do Spicetify ({WEBNOWPLAYING_PORT}). Ajuste 'websocket_port' "
            "em settings.json e reinicie o app antes de continuar.",
        )
        return

    if not cfg_status.extension_file_present:
        # Não bloqueia: aplicar (e possivelmente atualizar o Spicetify, ver
        # apply_changes) continua útil mesmo sem o arquivo da extensão ainda —
        # inclusive é um pré-requisito pra o Marketplace funcionar direito, que
        # é de onde o usuário normalmente instala essa extensão. Só avisa.
        logger.warning(
            "Spotify setup: o arquivo '%s' não está instalado na pasta de extensões do "
            "Spicetify — mesmo assim, seguindo com a configuração/aplicação (útil, ex., "
            "pra atualizar o Spicetify). O Spotify não vai conectar até o arquivo ser "
            "instalado (ex.: via Spicetify Marketplace, procurando por 'WebNowPlaying').",
            WEBNOWPLAYING_EXTENSION,
        )

    avoid_admin = win32.is_process_elevated()
    if avoid_admin:
        logger.info(
            "Spotify setup: WyrmPlayerControl está rodando como administrador; o Spicetify "
            "será rodado sem privilégios administrativos (tarefa agendada temporária)."
        )

    if not cfg_status.extension_enabled and not configure_extension(
        cfg_status.spicetify_path, avoid_admin=avoid_admin, on_output=on_progress
    ):
        win32.info_dialog("Spotify", "Falha ao configurar a extensão. Veja o log para detalhes.")
        return

    proceed = win32.confirm_dialog(
        "Spotify",
        "Isso vai reiniciar o cliente do Spotify para aplicar a integração. Continuar?",
        warning=True,
    )
    if not proceed:
        logger.info("Spotify setup: usuário cancelou a aplicação (spicetify apply).")
        win32.info_dialog(
            "Spotify",
            "Configuração cancelada. A extensão já está registrada no Spicetify; "
            "aplique quando quiser clicando novamente em 'Configurar Spotify' na tray.",
        )
        return

    if not apply_changes(cfg_status.spicetify_path, avoid_admin=avoid_admin, on_output=on_progress):
        win32.info_dialog("Spotify", "Falha ao aplicar as mudanças. Veja o log para detalhes.")
        return

    if not cfg_status.extension_file_present:
        win32.info_dialog(
            "Spotify",
            "Aplicado com sucesso, mas o Spotify ainda não vai conectar: falta instalar "
            f"o arquivo '{WEBNOWPLAYING_EXTENSION}' na pasta de extensões do Spicetify "
            "(ex.: via Spicetify Marketplace, procurando por 'WebNowPlaying'). Depois de "
            "instalar, clique em 'Configurar Spotify' de novo.",
        )
        return

    win32.info_dialog("Spotify", "Integração aplicada com sucesso.")


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

    if not status.extension_file_present:
        logger.warning(
            "Spotify setup: o arquivo '%s' não foi encontrado em nenhuma pasta de "
            "extensões do Spicetify (ver get_spicetify_extensions_dirs). Ele normalmente "
            "vem junto com a instalação do Spicetify — se sumiu, tente reinstalar o "
            "Spicetify ou instalar a extensão manualmente (ex.: via Spicetify Marketplace) "
            "antes de aplicar, senão o Spotify nunca vai conectar mesmo com tudo o resto certo.",
            WEBNOWPLAYING_EXTENSION,
        )

    if status.extension_enabled:
        logger.info(
            "Spotify setup: extensão %s já habilitada no Spicetify.", WEBNOWPLAYING_EXTENSION
        )
        return status

    avoid_admin = win32.is_process_elevated()
    if avoid_admin:
        logger.info(
            "Spotify setup: WyrmPlayerControl está rodando como administrador; o Spicetify "
            "será configurado sem privilégios administrativos (tarefa agendada temporária)."
        )

    if configure_extension(status.spicetify_path, avoid_admin=avoid_admin):
        logger.info(
            "Spotify setup: extensão configurada, mas ainda não aplicada. Use o item "
            "'Configurar Spotify' na system tray para aplicar (isso reinicia o Spotify)."
        )
        return check_status(websocket_port)

    return status
