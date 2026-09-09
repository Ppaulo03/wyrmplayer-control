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


def get_startup_command() -> str:
    """
    Resolve a linha de comando usada para iniciar o app junto com o Windows.

    Em build (PyInstaller), aponta para o próprio executável. Em modo dev, usa
    pythonw.exe (sem console) rodando src/main.py.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    python_executable = _find_pythonw(Path(sys.executable))
    main_path = Path(__file__).resolve().parents[2] / "src" / "main.py"
    return f'"{python_executable}" "{main_path}" --no-admin-relaunch'


def is_enabled() -> bool:
    """Verifica se o app já está registrado para iniciar com o Windows."""
    if os.name != "nt":
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH) as key:
            winreg.QueryValueEx(key, REGISTRY_VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.warning("Autostart: falha ao ler registro: %s", e)
        return False


def enable() -> bool:
    """Registra o app em HKCU\\...\\Run. Retorna True se aplicado com sucesso."""
    if os.name != "nt":
        return False

    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REGISTRY_KEY_PATH) as key:
            winreg.SetValueEx(key, REGISTRY_VALUE_NAME, 0, winreg.REG_SZ, get_startup_command())
        logger.info("Autostart: registrado para iniciar com o Windows.")
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


def sync(enabled: bool) -> None:
    """Alinha o registro do Windows ao valor desejado, sem escrever à toa."""
    if os.name != "nt":
        return

    if enabled == is_enabled():
        return

    if enabled:
        enable()
    else:
        disable()
