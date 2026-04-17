import ctypes
import os
import logging
import sys
import threading
import queue
from ctypes import wintypes
from typing import Any, Optional, Callable

logger = logging.getLogger(__name__)

# --- Win32 API Centralization ---
user32 = ctypes.windll.user32 if os.name == "nt" else None

if user32:
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    user32.EnumDisplayMonitors.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.LPARAM,
    ]
    user32.EnumDisplayMonitors.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = wintypes.LONG
    user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    user32.SetWindowLongW.restype = wintypes.LONG

# --- Win32 Structures ---


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


# --- Window Styling Constants ---
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_BORDER = 0x00800000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_APPWINDOW = 0x00040000
SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1


def apply_window_stealth(window_title: str) -> None:
    """Arranca barra de título, bordas e transforma em ToolWindow via Win32."""
    if os_name() != "nt":
        return

    try:
        hwnd = user32.FindWindowW(None, window_title)
        if not hwnd:
            return

        # Retira decorações básicas
        style = user32.GetWindowLongW(hwnd, -16)
        new_style = style & ~WS_CAPTION & ~WS_THICKFRAME & ~WS_BORDER
        user32.SetWindowLongW(hwnd, -16, new_style)

        # Configura ToolWindow e Topmost
        ex_style = user32.GetWindowLongW(hwnd, -20)
        new_ex_style = (ex_style | WS_EX_TOOLWINDOW | WS_EX_TOPMOST) & ~WS_EX_APPWINDOW
        user32.SetWindowLongW(hwnd, -20, new_ex_style)

        # Força atualização da janela
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0,
            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER,
        )
    except Exception as e:
        logger.warning(f"Win32: Falha ao aplicar stealth em '{window_title}': {e}")


def force_topmost(window_title: str) -> None:
    """Reforça agressivamente o estado Always-on-Top da janela."""
    if os_name() != "nt":
        return

    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, window_title)
        if not hwnd:
            return

        ctypes.windll.user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE
            | SWP_NOSIZE
            | SWP_NOACTIVATE
            | SWP_NOOWNERZORDER
            | SWP_SHOWWINDOW,
        )
    except Exception as e:
        logger.warning(f"Win32: Falha ao forçar topmost em '{window_title}': {e}")


def is_desktop_locked() -> bool:
    """Verifica se a sessão do Windows está bloqueada."""
    if os_name() != "nt":
        return False

    DESKTOP_SWITCHDESKTOP = 0x0100
    user32 = ctypes.windll.user32

    # Setup types
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.SwitchDesktop.argtypes = [wintypes.HANDLE]
    user32.SwitchDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL

    desktop_handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not desktop_handle:
        return True  # Se não consegue abrir, assumimos que está bloqueado ou sem acesso

    try:
        return not bool(user32.SwitchDesktop(desktop_handle))
    finally:
        user32.CloseDesktop(desktop_handle)


def os_name() -> str:
    return os.name


def get_monitors_info() -> list[dict[str, Any]]:
    """Enumera monitores e retorna lista bruta de áreas (Win32)."""
    if os_name() != "nt":
        return []

    monitors = []
    user32 = ctypes.windll.user32

    monitor_enum_proc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        wintypes.HANDLE,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def _callback(hMonitor, _hdc, _rect, _lparam):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if user32.GetMonitorInfoW(hMonitor, ctypes.byref(info)):
            monitors.append(
                {
                    "index": len(monitors),
                    "monitor_rect": (
                        info.rcMonitor.left,
                        info.rcMonitor.top,
                        info.rcMonitor.right,
                        info.rcMonitor.bottom,
                    ),
                    "work_rect": (
                        info.rcWork.left,
                        info.rcWork.top,
                        info.rcWork.right,
                        info.rcWork.bottom,
                    ),
                }
            )
        return True

    callback = monitor_enum_proc(_callback)
    user32.EnumDisplayMonitors(0, 0, callback, 0)
    return monitors


def is_process_elevated() -> bool:
    """Retorna se o processo atual esta em modo administrador no Windows."""
    if os_name() != "nt":
        return True

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin_if_needed(argv: list[str]) -> bool:
    """
    Relanca o processo atual com privilegios de administrador quando necessario.
    Retorna True quando o relaunch foi iniciado e o processo atual deve encerrar.
    """
    if os_name() != "nt" or is_process_elevated():
        return False

    if "--no-admin-relaunch" in argv:
        return False

    try:
        executable = os.path.abspath(argv[0])
        if executable.lower().endswith(".py"):
            executable = os.path.abspath(sys.executable)
            args = " ".join([f'"{arg}"' for arg in argv])
        else:
            args = " ".join([f'"{arg}"' for arg in argv[1:]])

        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            args,
            None,
            1,
        )
        return int(rc) > 32
    except Exception as e:
        logger.warning("Falha ao solicitar elevacao de privilegios: %s", e)
        return False


class LowLevelKeyboardHook:
    """
    Low-level keyboard hook using SetWindowsHookEx with WH_KEYBOARD_LL.
    Captures keyboard input at system level, working in fullscreen games.
    """

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101

    # KBDLLHOOKSTRUCT layout
    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_void_p),
        ]

    # Class-level hook procedure to prevent garbage collection
    _hook_proc = None

    def __init__(self) -> None:
        self._hook_id: wintypes.HHOOK | None = None
        self._callbacks: dict[tuple[int, int], Callable[[], None]] = (
            {}
        )  # (vk, mods) -> callback
        self._lock = threading.Lock()

    def _key_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        """Keyboard hook procedure called for every keyboard event."""
        user32 = ctypes.windll.user32

        try:
            if nCode >= 0 and (wParam == self.WM_KEYDOWN or wParam == self.WM_KEYUP):
                # Cast lParam to KBDLLHOOKSTRUCT pointer
                kb_struct = ctypes.cast(
                    lParam, ctypes.POINTER(self._KBDLLHOOKSTRUCT)
                ).contents
                vk_code = kb_struct.vkCode
                flags = kb_struct.flags

                # Bit 7 of flags indicates key up (0x80 = key up, 0x00 = key down)
                is_injected = bool(flags & 0x10)

                # Ignore injected keys (to avoid loops)
                if not is_injected and wParam == self.WM_KEYDOWN:
                    self._check_hotkey(vk_code, user32)

        except Exception as e:
            logger.error(f"Error in keyboard hook: {e}")

        return user32.CallNextHookEx(self._hook_id, nCode, wParam, lParam)

    def _check_hotkey(self, vk: int, user32) -> None:
        """Check if current key combination matches a registered hotkey."""
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_ALT = 0x0001
        MOD_WIN = 0x0008

        # Get current modifier state using GetAsyncKeyState
        mods = 0

        # Check for Ctrl (both left and right)
        if (user32.GetAsyncKeyState(0xA2) & 0x8000) or (
            user32.GetAsyncKeyState(0xA3) & 0x8000
        ):
            mods |= MOD_CONTROL

        # Check for Shift (both left and right)
        if (user32.GetAsyncKeyState(0xA0) & 0x8000) or (
            user32.GetAsyncKeyState(0xA1) & 0x8000
        ):
            mods |= MOD_SHIFT

        # Check for Alt (both left and right)
        if (user32.GetAsyncKeyState(0xA4) & 0x8000) or (
            user32.GetAsyncKeyState(0xA5) & 0x8000
        ):
            mods |= MOD_ALT

        # Check for Windows key (left and right)
        if (user32.GetAsyncKeyState(0x5B) & 0x8000) or (
            user32.GetAsyncKeyState(0x5C) & 0x8000
        ):
            mods |= MOD_WIN

        with self._lock:
            # Try exact match first
            if (vk, mods) in self._callbacks:
                try:
                    logger.debug(f"Executing hotkey: vk={vk}, mods={mods}")
                    self._callbacks[(vk, mods)]()
                except Exception as e:
                    logger.error(f"Error executing hotkey callback: {e}")

    def register_hotkey(self, mods: int, vk: int, callback: Callable[[], None]) -> bool:
        """Register a hotkey with the hook."""
        with self._lock:
            self._callbacks[(vk, mods)] = callback
        logger.debug(f"Registered hotkey: vk={vk}, mods={mods}")
        return True

    def unregister_hotkey(self, mods: int, vk: int) -> bool:
        """Unregister a hotkey."""
        with self._lock:
            self._callbacks.pop((vk, mods), None)
        return True

    def unregister_all(self) -> None:
        """Clear all registered hotkeys."""
        with self._lock:
            self._callbacks.clear()

    def start(self) -> bool:
        """Start the keyboard hook."""
        if os.name != "nt":
            return False

        if self._hook_id:
            return True  # Already started

        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            # Create hook procedure - store as class variable to prevent garbage collection
            if LowLevelKeyboardHook._hook_proc is None:
                LowLevelKeyboardHook._hook_proc = ctypes.WINFUNCTYPE(
                    ctypes.c_int, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
                )(self._key_proc)

            # Get module handle for current module (use None for system-wide hook)
            mod_handle = kernel32.GetModuleHandleW(None)

            # Install hook - for WH_KEYBOARD_LL, module handle is ignored but we pass it anyway
            self._hook_id = user32.SetWindowsHookExW(
                self.WH_KEYBOARD_LL,
                LowLevelKeyboardHook._hook_proc,
                mod_handle,
                0,  # Thread ID (0 = all threads)
            )

            if not self._hook_id:
                error_code = ctypes.get_last_error()
                logger.error(
                    f"Failed to install low-level keyboard hook (error code: {error_code})"
                )
                return False

            logger.info(
                f"Low-level keyboard hook installed successfully (hook_id={self._hook_id})"
            )
            return True

        except Exception as e:
            logger.error(f"Exception installing keyboard hook: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return False

    def stop(self) -> None:
        """Stop the keyboard hook."""
        try:
            if self._hook_id:
                user32 = ctypes.windll.user32
                result = user32.UnhookWindowsHookEx(self._hook_id)
                if result:
                    logger.info("Low-level keyboard hook stopped")
                else:
                    error_code = ctypes.get_last_error()
                    logger.error(
                        f"Failed to unhook keyboard hook (error code: {error_code})"
                    )
                self._hook_id = None
        except Exception as e:
            logger.error(f"Error stopping keyboard hook: {e}")
        finally:
            with self._lock:
                self._callbacks.clear()
