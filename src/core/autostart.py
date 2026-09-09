import logging
import os
import sys
import winreg
from pathlib import Path

logger = logging.getLogger(__name__)

REGISTRY_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
REGISTRY_VALUE_NAME = "WyrmPlayerControl"


def _find_pythonw(python_executable: Path) -> Path:
    """Prefere pythonw.exe (sem console) ao lado do interpretador atual, se existir."""
    candidate = python_executable.with_name("pythonw.exe")
    return candidate if candidate.exists() else python_executable


def get_startup_command(elevated: bool = False) -> str:
    """
    Resolve a linha de comando usada para iniciar o app junto com o Windows.

    Em build (PyInstaller), aponta para o próprio executável. Em modo dev, usa
    pythonw.exe (sem console) rodando src/main.py.

    Por padrão (`elevated=False`) inclui `--no-admin-relaunch`, para não pedir
    confirmação de administrador (UAC) a cada login — trade-off: os atalhos globais
    podem não funcionar sobre janelas/jogos que já rodam elevados, até o app ser
    reaberto manualmente. Com `elevated=True`, o app eleva normalmente ao iniciar
    (mostra o prompt do UAC a cada login), garantindo os atalhos nesse cenário.
    """
    if getattr(sys, "frozen", False):
        base_command = f'"{sys.executable}"'
        return base_command if elevated else f"{base_command} --no-admin-relaunch"

    python_executable = _find_pythonw(Path(sys.executable))
    main_path = Path(__file__).resolve().parents[2] / "src" / "main.py"
    base_command = f'"{python_executable}" "{main_path}"'
    return base_command if elevated else f"{base_command} --no-admin-relaunch"


def get_registered_command() -> str | None:
    """Lê o comando atualmente registrado em HKCU\\...\\Run, se houver."""
    if os.name != "nt":
        return None

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, REGISTRY_VALUE_NAME)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError as e:
        logger.warning("Autostart: falha ao ler registro: %s", e)
        return None


def is_enabled() -> bool:
    """Verifica se o app já está registrado para iniciar com o Windows."""
    return get_registered_command() is not None


def enable(elevated: bool = False) -> bool:
    """Registra o app em HKCU\\...\\Run. Retorna True se aplicado com sucesso."""
    if os.name != "nt":
        return False

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH) as key:
            winreg.SetValueEx(
                key, REGISTRY_VALUE_NAME, 0, winreg.REG_SZ, get_startup_command(elevated)
            )
        logger.info("Autostart: registrado para iniciar com o Windows (elevado=%s).", elevated)
        return True
    except OSError as e:
        logger.error("Autostart: falha ao registrar: %s", e)
        return False


def disable() -> bool:
    """Remove o registro de autostart, se existir. Retorna True se aplicado com sucesso."""
    if os.name != "nt":
        return False

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, REGISTRY_VALUE_NAME)
        logger.info("Autostart: removido do início com o Windows.")
        return True
    except FileNotFoundError:
        return True
    except OSError as e:
        logger.error("Autostart: falha ao remover: %s", e)
        return False


def sync(enabled: bool, elevated: bool = False) -> None:
    """Alinha o registro do Windows ao valor desejado, sem escrever à toa."""
    if os.name != "nt":
        return

    if not enabled:
        if is_enabled():
            disable()
        return

    if get_registered_command() != get_startup_command(elevated):
        enable(elevated)
