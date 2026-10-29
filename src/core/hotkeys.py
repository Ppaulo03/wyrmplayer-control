import ctypes
import logging
import os
import queue
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from typing import Any, cast

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
        self._user32 = ctypes.WinDLL("user32", use_last_error=True) if self._is_windows else None

        self._lock = threading.RLock()
        self._active_backend: str | None = None
        self._keyboard_remove_handlers: list[Callable[..., Any]] = []

        # Native hotkey support (fallback for Windows)
        self._native_thread: threading.Thread | None = None
        self._native_ready = threading.Event()
        self._native_stop = threading.Event()
        self._native_commands: queue.Queue[tuple[str, Any, threading.Event, dict[str, Any]]] = (
            queue.Queue()
        )
        self._native_callbacks: dict[int, Callable[[], None]] = {}
        self._next_hotkey_id = 1

    @property
    def active_backend(self) -> str | None:
        """Retorna o backend de hotkeys atualmente ativo ('keyboard', 'native' ou None)."""
        with self._lock:
            if self._active_backend == "keyboard" and not self._keyboard_remove_handlers:
                return None
            if self._active_backend == "native":
                if not self._native_callbacks or not (
                    self._native_thread and self._native_thread.is_alive()
                ):
                    return None
            return self._active_backend

    @property
    def registered_keyboard_count(self) -> int:
        """Retorna a quantidade de handlers registrados no backend keyboard."""
        with self._lock:
            return len(self._keyboard_remove_handlers)

    @property
    def registered_native_count(self) -> int:
        """Retorna a quantidade de atalhos registrados no backend nativo."""
        with self._lock:
            return len(self._native_callbacks)

    @property
    def is_native_thread_alive(self) -> bool:
        """Retorna se a thread nativa está atualmente viva."""
        with self._lock:
            return bool(self._native_thread and self._native_thread.is_alive())

    # Métodos delegados para keyboard_utils

    def _safe_hotkey_callback(self, name: str, callback: Callable[[], None]) -> Callable[[], None]:
        """Protege callback de hotkey para facilitar diagnóstico em runtime."""

        def wrapped() -> None:
            logger.info("Hotkey acionada: %s", name)
            try:
                callback()
            except Exception as e:
                logger.exception("Erro ao executar hotkey '%s': %s", name, e)

        return wrapped

    def _clear_keyboard_hotkeys(self) -> bool:
        """Remove os atalhos registrados pelo aplicativo na biblioteca keyboard.

        Retorna True se todos os handlers foram removidos com sucesso, ou False
        se algum handler falhou na remoção (mantendo as referências para tentativa posterior).
        """
        failed_handlers: list[Callable[..., Any]] = []
        for remove_fn in self._keyboard_remove_handlers:
            try:
                keyboard.remove_hotkey(remove_fn)
            except Exception as e:
                logger.error("Erro ao remover hotkey do backend keyboard: %s", e)
                failed_handlers.append(remove_fn)

        self._keyboard_remove_handlers = failed_handlers
        if failed_handlers:
            logger.warning(
                "Limpeza incompleta no backend keyboard: %d handler(s) retido(s) para recuperação.",
                len(failed_handlers),
            )
            return False

        if self._active_backend == "keyboard":
            self._active_backend = None

        return True

    def _rollback_keyboard_handlers(self, handlers: list[Callable[..., Any]]) -> bool:
        """Executa rollback de handlers registrados durante tentativa falha de registro.

        Retorna True se todos os handlers foram removidos com sucesso.
        Se algum falhar, ele é incorporado a _keyboard_remove_handlers para rastreamento.
        """
        unremoved: list[Callable[..., Any]] = []
        for handler in handlers:
            try:
                keyboard.remove_hotkey(handler)
            except Exception as e:
                logger.error("Erro no rollback de handler do keyboard: %s", e)
                unremoved.append(handler)

        if unremoved:
            self._keyboard_remove_handlers.extend(unremoved)
            return False

        return True

    def _clear_native_hotkeys(self) -> bool:
        """Remove os atalhos registrados no backend nativo sem derrubar a thread.

        Retorna True se confirmado que nenhum atalho nativo está ativo.
        Se a thread nativa estiver comprovadamente encerrada, reconcilia o estado
        removendo referências obsoletas que já foram liberadas pelo SO.
        """
        if not self._is_windows:
            return True

        # Se a thread não existe ou está comprovadamente encerrada:
        if self._native_thread is None or not self._native_thread.is_alive():
            if self._native_callbacks:
                logger.info(
                    "Thread nativa encerrada detectada; reconciliando %d registro(s) obsoleto(s).",
                    len(self._native_callbacks),
                )
                self._native_callbacks.clear()
            self._native_thread = None
            if self._active_backend == "native":
                self._active_backend = None
            return True

        # Thread ativa: exige confirmação explícita de desregistro via WinAPI
        ok = self._send_native_command("clear")
        if not ok:
            logger.warning("Falha ou timeout ao desregistrar hotkeys nativas na thread ativa.")
            return False

        if self._active_backend == "native":
            self._active_backend = None

        return True

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
            ",": 0xBC,  # VK_OEM_COMMA
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

        # Se a referência anterior é de uma thread morta, reconcilia antes de criar nova
        if self._native_thread is not None and not self._native_thread.is_alive():
            self._native_callbacks.clear()
            self._native_thread = None

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
                            failed_ids: list[int] = []
                            for hotkey_id in list(self._native_callbacks.keys()):
                                ok = bool(self._user32.UnregisterHotKey(None, hotkey_id))
                                if ok:
                                    self._native_callbacks.pop(hotkey_id, None)
                                else:
                                    failed_ids.append(hotkey_id)
                                    logger.error(
                                        "Erro ao desregistrar hotkey nativa ID %d (codigo=%s).",
                                        hotkey_id,
                                        ctypes.get_last_error(),
                                    )
                            result["ok"] = len(failed_ids) == 0 and len(self._native_callbacks) == 0
                        elif command == "register_batch":
                            registrations = cast(
                                list[tuple[int, int, str, Callable[[], None]]], payload
                            )
                            registered_count = 0
                            for mods, vk, callback_name, callback in registrations:
                                hotkey_id = self._next_hotkey_id
                                self._next_hotkey_id += 1

                                ok = bool(self._user32.RegisterHotKey(None, hotkey_id, mods, vk))
                                if not ok:
                                    logger.error(
                                        "Erro ao registrar hotkey nativa '%s' (codigo=%s).",
                                        callback_name,
                                        ctypes.get_last_error(),
                                    )
                                    continue

                                self._native_callbacks[hotkey_id] = callback
                                registered_count += 1
                                logger.info("Hotkey configurada (nativa): %s", callback_name)

                            result["ok"] = registered_count > 0
                        elif command == "stop":
                            failed_ids = []
                            for hotkey_id in list(self._native_callbacks.keys()):
                                ok = bool(self._user32.UnregisterHotKey(None, hotkey_id))
                                if ok:
                                    self._native_callbacks.pop(hotkey_id, None)
                                else:
                                    failed_ids.append(hotkey_id)
                                    logger.error(
                                        "Erro ao desregistrar hotkey ID %d no stop (codigo=%s).",
                                        hotkey_id,
                                        ctypes.get_last_error(),
                                    )
                            result["ok"] = len(failed_ids) == 0 and len(self._native_callbacks) == 0
                            if result["ok"]:
                                self._native_stop.set()
                        else:
                            result["ok"] = False
                    except Exception as e:
                        result["ok"] = False
                        result["error"] = str(e)
                    finally:
                        done_event.set()

                while self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_HOTKEY:
                        cb: Callable[[], None] | None = self._native_callbacks.get(int(msg.wParam))
                        if cb is not None:
                            cb()

                time.sleep(0.01)

        self._native_thread = threading.Thread(target=native_loop, daemon=True)
        self._native_thread.start()
        self._native_ready.wait(timeout=1.0)

        return bool(self._native_thread and self._native_thread.is_alive())

    def _send_native_command(self, command: str, payload: Any = None) -> bool:
        if command in ("stop", "clear") and not (
            self._native_thread and self._native_thread.is_alive()
        ):
            return True

        if not self._ensure_native_thread():
            return False

        done_event = threading.Event()
        result: dict[str, Any] = {}
        self._native_commands.put((command, payload, done_event, result))
        if not done_event.wait(timeout=2.0):
            logger.error("Timeout aguardando processamento do comando nativo '%s'.", command)
            return False
        return bool(result.get("ok", False))

    def _setup_low_level_keyboard_hotkeys(self, hotkey_map: dict[str, Callable[[], None]]) -> bool:
        """Registra hotkeys usando biblioteca keyboard com suporte a fullscreen.

        Aplica política de funcionamento parcial controlado (R01): se um atalho
        falhar, os demais atalhos funcionais permanecem registrados.
        O backend continua operacional desde que possua registros úteis.
        """
        if not hotkey_map:
            return False

        new_handlers: list[Callable[..., Any]] = []
        registered_by_shortcut: dict[str, list[str]] = {}
        failed_variants: list[tuple[str, str]] = []
        unsupported_shortcuts: list[str] = []

        try:
            for shortcut, callback in hotkey_map.items():
                safe_callback = self._safe_hotkey_callback(shortcut, callback)
                variants = keyboard_utils.expand_shortcut_variants(shortcut)
                registered_variants: list[str] = []

                for variant in variants:
                    try:
                        # suppress=False allows keys to pass through to the game
                        # This is the key to fullscreen game support
                        handler = keyboard.add_hotkey(variant, safe_callback, suppress=False)
                        new_handlers.append(handler)
                        registered_variants.append(variant)
                        logger.info("Hotkey configurada (fullscreen compatible): %s", variant)
                    except Exception as e:
                        logger.warning(
                            "Falha ao registrar variante '%s' do atalho '%s': %s",
                            variant,
                            shortcut,
                            e,
                        )
                        failed_variants.append((shortcut, variant))

                if registered_variants:
                    registered_by_shortcut[shortcut] = registered_variants
                else:
                    unsupported_shortcuts.append(shortcut)
                    logger.warning(
                        "Nenhuma variante funcional registrada para o atalho '%s'.",
                        shortcut,
                    )

            # R01: O backend continua operacional desde que possua registros úteis
            if new_handlers and registered_by_shortcut:
                self._keyboard_remove_handlers.extend(new_handlers)
                if unsupported_shortcuts or failed_variants:
                    logger.warning(
                        "Sistema de Hotkeys configurado com sucesso parcial no backend keyboard: "
                        "%d/%d atalhos ativos, %d atalho(s) não suportado(s) (%s).",
                        len(registered_by_shortcut),
                        len(hotkey_map),
                        len(unsupported_shortcuts),
                        ", ".join(unsupported_shortcuts) if unsupported_shortcuts else "nenhum",
                    )
                else:
                    logger.info(
                        "Sistema de Hotkeys configurado integralmente (fullscreen compatible)."
                    )
                return True

            # Falha total: nenhum atalho conseguiu registrar variantes úteis
            logger.warning(
                "Nenhum atalho pôde ser registrado no backend keyboard. Executando rollback."
            )
            rollback_ok = self._rollback_keyboard_handlers(new_handlers)
            if not rollback_ok:
                logger.error(
                    "Rollback do backend keyboard falhou; handlers retidos para evitar conflito."
                )
            return False

        except Exception as e:
            logger.exception("Exceção inesperada no registro de hotkeys do keyboard: %s", e)
            self._rollback_keyboard_handlers(new_handlers)
            return False

    def _setup_native_hotkeys(self, hotkey_map: dict[str, Callable[[], None]]) -> bool:
        """Registra hotkeys globais no backend nativo do Windows (fallback)."""
        if not self._is_windows or not hotkey_map:
            return False

        if not self._clear_native_hotkeys():
            logger.warning(
                "Não foi possível confirmar limpeza nativa antes de registrar novo lote."
            )
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
                "Hotkeys não suportadas no backend nativo: %s",
                ", ".join(unsupported),
            )

        if not registrations:
            return False

        ok = self._send_native_command("register_batch", registrations)
        if ok:
            logger.info("Sistema de Hotkeys configurado com sucesso (backend nativo).")
            return True

        # Rollback se falhar
        logger.warning("Falha no registro do lote nativo; executando rollback.")
        self._clear_native_hotkeys()
        return False

    def setup(self, force_backend_reset: bool = False) -> None:
        """Registra os atalhos baseados na configuração persistente."""
        with self._lock:
            # 1. Carrega e valida configurações ANTES de remover registros existentes (R02)
            try:
                cfg = self.config.load()
                if not hasattr(cfg, "hotkeys") or not isinstance(cfg.hotkeys, dict):
                    raise ValueError(
                        f"Configuração de hotkeys inválida: {type(getattr(cfg, 'hotkeys', None))}"
                    )

                hk = cfg.hotkeys
                hotkey_map = {
                    hk.get("play_pause", ""): self.controller.play_pause,
                    hk.get("next_track", ""): self.controller.next_track,
                    hk.get("previous_track", ""): self.controller.previous_track,
                    hk.get("volume_up", ""): lambda: self.controller.adjust_volume(1),
                    hk.get("volume_down", ""): lambda: self.controller.adjust_volume(-1),
                    hk.get("mute", ""): self.controller.toggle_mute,
                }
                hotkey_map = {shortcut: cb for shortcut, cb in hotkey_map.items() if shortcut}
            except Exception as e:
                logger.error(
                    "Falha ao carregar ou validar configurações de hotkeys: %s. "
                    "Mantendo registros atuais.",
                    e,
                )
                return

            # 2. Somente agora inicia a substituição dos registros existentes
            if force_backend_reset:
                if not self._stop_unlocked():
                    logger.warning(
                        "Reset forçado de backend não foi concluído com sucesso; "
                        "abortando reconfiguração para evitar registros conflitantes."
                    )
                    return
            else:
                kb_clean = self._clear_keyboard_hotkeys()
                native_clean = self._clear_native_hotkeys()
                if not kb_clean or not native_clean:
                    logger.warning(
                        "Limpeza prévia de hotkeys inconclusiva (kb=%s, native=%s); "
                        "abortando nova configuração para evitar duplicatas ou conflitos.",
                        kb_clean,
                        native_clean,
                    )
                    return
                self._active_backend = None

            if not hotkey_map:
                logger.info("Nenhuma hotkey configurada.")
                self._active_backend = None
                return

            # 3. Tenta backend keyboard com suporte a fullscreen (suppress=False)
            if self._setup_low_level_keyboard_hotkeys(hotkey_map):
                self._active_backend = "keyboard"
                return

            # 4. Fallback: backend nativo do Windows (não funciona em fullscreen)
            if self._is_windows:
                # O fallback SÓ pode acontecer se o keyboard estiver comprovadamente limpo!
                if not self._clear_keyboard_hotkeys():
                    logger.error(
                        "Backend keyboard possui handlers pendentes após tentativa falha; "
                        "bloqueando fallback nativo para evitar ativação conflitante."
                    )
                    return

                if self._setup_native_hotkeys(hotkey_map):
                    self._active_backend = "native"
                    return

            self._active_backend = None
            logger.warning("Falha ao registrar hotkeys em todos os backends disponíveis.")

    def stop(self) -> bool:
        """Libera registros de hotkeys no encerramento do app."""
        with self._lock:
            return self._stop_unlocked()

    def _stop_unlocked(self) -> bool:
        """Libera registros internamente (deve ser chamado sob self._lock)."""
        native_ok = True
        if self._is_windows:
            if self._native_thread and self._native_thread.is_alive():
                cmd_ok = self._send_native_command("stop")
                self._native_thread.join(timeout=1.0)
                if self._native_thread.is_alive():
                    logger.error(
                        "Thread nativa ainda está ativa após timeout de join; mantendo referência."
                    )
                    native_ok = False
                elif not cmd_ok:
                    logger.error("Comando 'stop' nativo falhou ao desregistrar hotkeys.")
                    native_ok = False
                else:
                    self._native_thread = None
                    self._native_callbacks.clear()
            else:
                self._native_thread = None
                self._native_callbacks.clear()

        keyboard_ok = self._clear_keyboard_hotkeys()

        if native_ok and keyboard_ok:
            self._active_backend = None
            return True

        logger.warning(
            "Encerramento de hotkeys incompleto (keyboard_ok=%s, native_ok=%s).",
            keyboard_ok,
            native_ok,
        )
        return False

    def is_input_desktop_accessible(self) -> bool:
        """Retorna se o desktop de entrada atual esta acessivel (sessao desbloqueada)."""
        return not win32.is_desktop_locked()
