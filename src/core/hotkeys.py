import ctypes
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, cast, Optional

import keyboard

from src.core.config import ConfigManager
from src.core.utils import keyboard as keyboard_utils
from src.infrastructure import win32
from src.services.player_controller import PlayerController

logger = logging.getLogger(__name__)


class HotkeyManager:
    """Gerencia a configuração e registro de atalhos de teclado."""

    def __init__(self, controller: PlayerController, config: ConfigManager) -> None:
        self.controller = controller
        self.config = config
        self._is_windows = os.name == "nt"
        self._user32 = ctypes.windll.user32 if self._is_windows else None

        # Native hotkey support (fallback for Windows)
        self._native_thread: threading.Thread | None = None
        self._native_ready = threading.Event()
        self._native_stop = threading.Event()
        self._native_commands: queue.Queue[
            tuple[str, Any, threading.Event, dict[str, Any]]
        ] = queue.Queue()
        self._native_callbacks: dict[int, Callable[[], None]] = {}
        self._next_hotkey_id = 1

    # Métodos delegados para keyboard_utils

    def _safe_hotkey_callback(
        self, name: str, callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Protege callback de hotkey para facilitar diagnóstico em runtime."""

        def wrapped() -> None:
            logger.info("Hotkey acionada: %s", name)
            try:
                callback()
            except Exception as e:
                logger.exception("Erro ao executar hotkey '%s': %s", name, e)

        return wrapped

    def _clear_keyboard_hotkeys(self) -> None:
        """Limpa hotkeys do backend keyboard com fallback para versões antigas."""
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            keyboard.unhook_all()

    def _parse_shortcut_native(self, shortcut: str) -> tuple[int, int] | None:
        """Converte combinação textual para (mods, virtual_key) do WinAPI."""
        lowered = shortcut.lower().strip()
        if not lowered:
            return None

        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_WIN = 0x0008
        MOD_NOREPEAT = 0x4000

        vk_map = {
            "left": 0x25,
            "up": 0x26,
            "right": 0x27,
            "down": 0x28,
            "space": 0x20,
        }

        mods = 0
        key_token = ""
        for token in [part.strip() for part in lowered.split("+") if part.strip()]:
            if token in {"ctrl", "control"}:
                mods |= MOD_CONTROL
            elif token == "shift":
                mods |= MOD_SHIFT
            elif token == "alt":
                mods |= MOD_ALT
            elif token in {"win", "windows", "cmd"}:
                mods |= MOD_WIN
            elif token in {"alt gr", "altgr", "right alt", "ralt"}:
                mods |= MOD_CONTROL | MOD_ALT
            else:
                key_token = token

        if not key_token:
            return None

        if key_token in vk_map:
            vk = vk_map[key_token]
        elif len(key_token) == 1 and key_token.isalpha():
            vk = ord(key_token.upper())
        elif len(key_token) == 1 and key_token.isdigit():
            vk = ord(key_token)
        else:
            return None

        return mods | MOD_NOREPEAT, vk

    def _ensure_native_thread(self) -> bool:
        if not self._is_windows or self._user32 is None:
            return False

        if self._native_thread and self._native_thread.is_alive():
            return True

        self._native_ready.clear()
        self._native_stop.clear()

        def native_loop() -> None:
            assert self._user32 is not None

            msg = wintypes.MSG()
            PM_REMOVE = 0x0001
            WM_HOTKEY = 0x0312

            # Garante queue de mensagens para a thread.
            self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE)
            self._native_ready.set()

            while not self._native_stop.is_set():
                # Processa comandos de registro/desregistro na MESMA thread.
                while True:
                    try:
                        item = self._native_commands.get_nowait()
                        command, payload, done_event, result = item
                    except queue.Empty:
                        break

                    try:
                        if command == "clear":
                            for hotkey_id in list(self._native_callbacks.keys()):
                                self._user32.UnregisterHotKey(None, hotkey_id)
                                self._native_callbacks.pop(hotkey_id, None)
                            result["ok"] = True
                        elif command == "register_batch":
                            registrations = cast(
                                list[tuple[int, int, str, Callable[[], None]]], payload
                            )
                            registered_count = 0
                            for mods, vk, callback_name, callback in registrations:
                                hotkey_id = self._next_hotkey_id
                                self._next_hotkey_id += 1

                                ok = bool(
                                    self._user32.RegisterHotKey(
                                        None, hotkey_id, mods, vk
                                    )
                                )
                                if not ok:
                                    logger.error(
                                        "Erro ao registrar hotkey nativa '%s' (codigo=%s).",
                                        callback_name,
                                        ctypes.get_last_error(),
                                    )
                                    continue

                                self._native_callbacks[hotkey_id] = callback
                                registered_count += 1
                                logger.info(
                                    "Hotkey configurada (nativa): %s", callback_name
                                )

                            result["ok"] = registered_count > 0
                        elif command == "stop":
                            for hotkey_id in list(self._native_callbacks.keys()):
                                self._user32.UnregisterHotKey(None, hotkey_id)
                                self._native_callbacks.pop(hotkey_id, None)
                            result["ok"] = True
                            self._native_stop.set()
                        else:
                            result["ok"] = False
                    except Exception as e:
                        result["ok"] = False
                        result["error"] = str(e)
                    finally:
                        done_event.set()

                while self._user32.PeekMessageW(
                    ctypes.byref(msg), None, 0, 0, PM_REMOVE
                ):
                    if msg.message == WM_HOTKEY:
                        cb: Callable[[], None] | None = self._native_callbacks.get(
                            int(msg.wParam)
                        )
                        if cb is not None:
                            cb()

                time.sleep(0.01)

        self._native_thread = threading.Thread(target=native_loop, daemon=True)
        self._native_thread.start()
        self._native_ready.wait(timeout=1.0)

        return bool(self._native_thread and self._native_thread.is_alive())

    def _send_native_command(self, command: str, payload: Any = None) -> bool:
        if not self._ensure_native_thread():
            return False

        done_event = threading.Event()
        result: dict[str, Any] = {}
        self._native_commands.put((command, payload, done_event, result))
        done_event.wait(timeout=2.0)
        return bool(result.get("ok", False))

    def _setup_low_level_keyboard_hotkeys(
        self, hotkey_map: dict[str, Callable[[], None]]
    ) -> bool:
        """Registra hotkeys usando biblioteca keyboard com suporte a fullscreen."""
        # Use the keyboard library with suppress=False to allow input to pass through
        # This will work in fullscreen games
        unsupported: list[str] = []

        for shortcut, callback in hotkey_map.items():
            safe_callback = self._safe_hotkey_callback(shortcut, callback)
            for variant in keyboard_utils.expand_shortcut_variants(shortcut):
                try:
                    # suppress=False allows keys to pass through to the game
                    # This is the key to fullscreen game support
                    keyboard.add_hotkey(variant, safe_callback, suppress=False)
                    logger.info(
                        "Hotkey configurada (fullscreen compatible): %s", variant
                    )
                except Exception as e:
                    logger.error("Erro ao registrar hotkey '%s': %s", variant, e)
                    unsupported.append(shortcut)

        if unsupported:
            logger.warning(
                "Hotkeys não suportadas: %s",
                ", ".join(set(unsupported)),
            )

        ok = len(hotkey_map) > 0 and len(set(unsupported)) < len(hotkey_map)
        if ok:
            logger.info(
                "Sistema de Hotkeys configurado com sucesso (fullscreen compatible)."
            )

        return ok

    def _setup_native_hotkeys(self, hotkey_map: dict[str, Callable[[], None]]) -> bool:
        """Registra hotkeys globais no backend nativo do Windows (fallback)."""
        if not self._is_windows:
            return False

        if not self._send_native_command("clear"):
            return False

        registrations: list[tuple[int, int, str, Callable[[], None]]] = []
        unsupported: list[str] = []

        for shortcut, callback in hotkey_map.items():
            parsed = self._parse_shortcut_native(shortcut)
            if not parsed:
                unsupported.append(shortcut)
                continue

            mods, vk = parsed
            registrations.append(
                (mods, vk, shortcut, self._safe_hotkey_callback(shortcut, callback))
            )

        if unsupported:
            logger.warning(
                "Hotkeys nao suportadas no backend nativo: %s",
                ", ".join(unsupported),
            )

        ok = self._send_native_command("register_batch", registrations)
        if ok:
            logger.info("Sistema de Hotkeys configurado com sucesso (backend nativo).")

        return ok

    def setup(self, force_backend_reset: bool = False) -> None:
        """Registra os atalhos baseados na configuração persistente."""
        cfg = self.config.load()
        hk = cfg.hotkeys

        hotkey_map = {
            hk["play_pause"]: self.controller.play_pause,
            hk["next_track"]: self.controller.next_track,
            hk["previous_track"]: self.controller.previous_track,
            hk["volume_up"]: lambda: self.controller.adjust_volume(1),
            hk["volume_down"]: lambda: self.controller.adjust_volume(-1),
            hk["mute"]: self.controller.toggle_mute,
        }
        hotkey_map = {shortcut: cb for shortcut, cb in hotkey_map.items() if shortcut}

        if force_backend_reset:
            self.stop()

        # Use keyboard library with fullscreen game support (suppress=False)
        if self._setup_low_level_keyboard_hotkeys(hotkey_map):
            return

        # Fallback: native Windows hotkeys (doesn't work in fullscreen)
        if self._is_windows:
            if self._setup_native_hotkeys(hotkey_map):
                return

        logger.info("Sistema de Hotkeys configurado com sucesso.")

    def stop(self) -> None:
        """Libera registros de hotkeys no encerramento do app."""
        # Stop native hotkeys
        if self._is_windows:
            self._send_native_command("stop")
            self._native_thread = None
            self._native_callbacks.clear()

        # Stop keyboard library hotkeys
        self._clear_keyboard_hotkeys()

    def is_input_desktop_accessible(self) -> bool:
        """Retorna se o desktop de entrada atual esta acessivel (sessao desbloqueada)."""
        return not win32.is_desktop_locked()
