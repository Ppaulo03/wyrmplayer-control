import ctypes
import os
import tempfile
import logging
import atexit
from typing import Optional

logger = logging.getLogger(__name__)

class SingleInstance:
    """
    Manager to ensure only one instance of the application is running.
    Uses Win32 Mutex on Windows and a fallback lockfile mechanism.
    """
    def __init__(self, app_name: str = "WyrmPlayerControl"):
        self.app_name = app_name
        self.lock_file_path = os.path.join(tempfile.gettempdir(), f"{app_name}.lock")
        self.lock_file_handle: Optional[int] = None
        self.mutex_handle: Optional[int] = None

    def _is_process_running(self, pid: int) -> bool:
        if pid <= 0:
            return False

        if os.name == "nt":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            process_handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid,
            )
            if not process_handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not ctypes.windll.kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(process_handle)

        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def lock(self) -> bool:
        """Attempt to acquire the single instance lock."""
        if os.name != "nt":
            return True

        ERROR_ALREADY_EXISTS = 183
        mutex_name = f"Global\\{self.app_name}Singleton"
        self.mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        
        if self.mutex_handle:
            if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
                return False
            atexit.register(self.unlock)
            return True

        logger.warning("Falha ao criar mutex de instancia unica; usando lockfile legado.")
        return self._lock_file_fallback()

    def _lock_file_fallback(self) -> bool:
        try:
            self.lock_file_handle = os.open(self.lock_file_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            existing_pid = -1
            try:
                with open(self.lock_file_path, encoding="utf-8") as lock_file:
                    raw_pid = lock_file.read().strip()
                if raw_pid:
                    existing_pid = int(raw_pid)
            except (OSError, ValueError):
                existing_pid = -1

            if existing_pid > 0 and self._is_process_running(existing_pid):
                return False

            try:
                os.remove(self.lock_file_path)
            except OSError:
                return False

            try:
                self.lock_file_handle = os.open(self.lock_file_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            except FileExistsError:
                return False

        os.write(self.lock_file_handle, str(os.getpid()).encode("utf-8"))
        os.fsync(self.lock_file_handle)
        atexit.register(self.unlock)
        return True

    def unlock(self) -> None:
        """Release the acquired locks."""
        if os.name == "nt" and self.mutex_handle:
            ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
            self.mutex_handle = None

        if self.lock_file_handle:
            try:
                os.close(self.lock_file_handle)
            finally:
                self.lock_file_handle = None
            try:
                os.remove(self.lock_file_path)
            except OSError:
                pass
