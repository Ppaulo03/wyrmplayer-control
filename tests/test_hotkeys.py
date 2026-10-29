import threading
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from src.core.config import ConfigManager
from src.core.hotkeys import HotkeyManager
from src.services.player_controller import PlayerController


class FakeRemovalHandler:
    """Objeto chamável que representa o handler de remoção retornado por add_hotkey."""

    def __init__(self, hid: int, shortcut: str, fake_keyboard: "FakeKeyboard") -> None:
        self._fake_hid = hid
        self._fake_shortcut = shortcut
        self._fake_keyboard = fake_keyboard

    def __call__(self) -> None:
        self._fake_keyboard.remove_hotkey(self._fake_hid)


class FakeKeyboard:
    """Simulador em memória que reproduz o contrato real da biblioteca keyboard."""

    def __init__(self) -> None:
        self.registered_hotkeys: dict[int, tuple[str, Callable[[], None]]] = {}
        self._next_id = 1
        self.fail_register_shortcuts: set[str] = set()
        self.fail_all_register = False
        self.fail_remove_handlers: set[int] = set()
        self.fail_all_remove = False

    def add_hotkey(
        self,
        hotkey: str,
        callback: Callable[[], None],
        suppress: bool = False,
    ) -> FakeRemovalHandler:
        if self.fail_all_register or hotkey in self.fail_register_shortcuts:
            raise RuntimeError(f"Simulando erro de registro para variante: {hotkey}")

        hid = self._next_id
        self._next_id += 1
        self.registered_hotkeys[hid] = (hotkey, callback)
        return FakeRemovalHandler(hid, hotkey, self)

    def remove_hotkey(self, handler_or_hotkey: Any) -> None:
        if self.fail_all_remove:
            raise RuntimeError("Simulando falha total de remoção no backend keyboard")

        hid = getattr(handler_or_hotkey, "_fake_hid", None)
        if hid is not None:
            if hid in self.fail_remove_handlers:
                raise RuntimeError(f"Simulando falha de remoção para handler {hid}")
            self.registered_hotkeys.pop(hid, None)
            return

        if isinstance(handler_or_hotkey, int):
            if handler_or_hotkey in self.fail_remove_handlers:
                raise RuntimeError(f"Falha de remoção handler ID {handler_or_hotkey}")
            self.registered_hotkeys.pop(handler_or_hotkey, None)
            return

        if isinstance(handler_or_hotkey, str):
            to_remove = [
                k for k, (hk, _) in self.registered_hotkeys.items() if hk == handler_or_hotkey
            ]
            for k in to_remove:
                if k in self.fail_remove_handlers:
                    raise RuntimeError(f"Falha de remoção hotkey {handler_or_hotkey}")
                self.registered_hotkeys.pop(k, None)

    def trigger(self, hotkey_str: str) -> None:
        """Simula o acionamento de um atalho."""
        for _, (hk, cb) in list(self.registered_hotkeys.items()):
            if hk == hotkey_str:
                cb()


class FakeNativeBackend:
    """Simulador do backend nativo que rastreia estado de thread e comandos WinAPI."""

    def __init__(self) -> None:
        self.active_hotkeys: dict[int, tuple[int, int, str, Callable[[], None]]] = {}
        self.is_thread_alive = True
        self.fail_clear = False
        self.fail_register = False
        self.fail_stop = False
        self.simulate_timeout = False
        self.refuse_to_stop = False
        self._next_id = 1
        self.commands_received: list[str] = []

    def handle_command(self, cmd: str, payload: Any = None) -> bool:
        self.commands_received.append(cmd)
        if self.simulate_timeout:
            return False

        if cmd == "clear":
            if self.fail_clear:
                return False
            self.active_hotkeys.clear()
            return True
        elif cmd == "register_batch":
            if self.fail_register:
                return False
            registrations = cast(list[tuple[int, int, str, Callable[[], None]]], payload)
            for mods, vk, name, cb in registrations:
                hid = self._next_id
                self._next_id += 1
                self.active_hotkeys[hid] = (mods, vk, name, cb)
            return len(registrations) > 0
        elif cmd == "stop":
            if self.fail_stop:
                return False
            self.active_hotkeys.clear()
            if not self.refuse_to_stop:
                self.is_thread_alive = False
            return True
        return False


@pytest.fixture
def fake_keyboard(monkeypatch: pytest.MonkeyPatch) -> FakeKeyboard:
    fake = FakeKeyboard()
    monkeypatch.setattr("src.core.hotkeys.keyboard.add_hotkey", fake.add_hotkey)
    monkeypatch.setattr("src.core.hotkeys.keyboard.remove_hotkey", fake.remove_hotkey)
    return fake


@pytest.fixture
def fake_controller() -> MagicMock:
    return MagicMock(spec=PlayerController)


@pytest.fixture
def hotkey_manager(fake_controller: MagicMock, config_manager: ConfigManager) -> HotkeyManager:
    return HotkeyManager(controller=fake_controller, config=config_manager)


# ==============================================================================
# EST-01-R01: Limpeza dos handlers do keyboard
# ==============================================================================


def test_r01_successful_keyboard_cleanup(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """1. Remoção bem-sucedida de todos os handlers."""
    hotkey_manager.setup()
    assert hotkey_manager.registered_keyboard_count == 18
    assert len(fake_keyboard.registered_hotkeys) == 18

    assert hotkey_manager._clear_keyboard_hotkeys() is True
    assert hotkey_manager.registered_keyboard_count == 0
    assert len(fake_keyboard.registered_hotkeys) == 0


def test_r01_keyboard_cleanup_with_exception_preserves_reference(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """2 e 3. Remoção com exceção preserva referências cuja remoção não foi confirmada."""
    hotkey_manager.setup()
    # Simula falha ao remover os handlers com IDs ímpares (9 falhas de 18)
    for hid in range(1, 19, 2):
        fake_keyboard.fail_remove_handlers.add(hid)

    # Limpeza deve falhar e retornar False
    success = hotkey_manager._clear_keyboard_hotkeys()
    assert success is False

    # Os 9 handlers que falharam continuam armazenados
    assert hotkey_manager.registered_keyboard_count == 9
    assert len(fake_keyboard.registered_hotkeys) == 9


def test_r01_reconfiguration_blocked_after_cleanup_failure(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """4. Tentativa de reconfiguração é abortada enquanto limpeza anterior for inconclusiva."""
    hotkey_manager.setup()
    # Simula falha total ao remover
    fake_keyboard.fail_all_remove = True

    # Chama setup() novamente: deve tentar limpar, falhar e não registrar duplicatas
    hotkey_manager.setup()

    assert hotkey_manager.registered_keyboard_count == 18
    assert len(fake_keyboard.registered_hotkeys) == 18


def test_r01_recovery_after_removal_succeeds(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """5. Recuperação: quando a remoção volta a funcionar, a limpeza é concluída."""
    hotkey_manager.setup()
    fake_keyboard.fail_all_remove = True

    # Primeira tentativa falha
    assert hotkey_manager._clear_keyboard_hotkeys() is False
    assert hotkey_manager.registered_keyboard_count == 18

    # Falha é resolvida
    fake_keyboard.fail_all_remove = False

    # Segunda tentativa limpa com sucesso
    assert hotkey_manager._clear_keyboard_hotkeys() is True
    assert hotkey_manager.registered_keyboard_count == 0
    assert len(fake_keyboard.registered_hotkeys) == 0


def test_r01_no_duplicate_callbacks_after_reloads(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """6. Confirmação de que callbacks nunca disparam em duplicidade."""
    hotkey_manager.setup()
    hotkey_manager.setup()
    hotkey_manager.setup()

    fake_keyboard.trigger("alt gr+p")
    assert fake_controller.play_pause.call_count == 1


# ==============================================================================
# EST-01-R02: Impedir ativação conflitante de backends
# ==============================================================================


def test_r02_normal_transition_from_native_to_keyboard(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1. Transição normal de native para keyboard após limpeza confirmada."""
    clear_called = False

    def mock_clear() -> bool:
        nonlocal clear_called
        clear_called = True
        return True

    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_clear_native_hotkeys", mock_clear)

    # Força estado anterior como nativo
    hotkey_manager._active_backend = "native"

    hotkey_manager.setup()

    assert clear_called is True
    assert hotkey_manager.active_backend == "keyboard"
    assert len(fake_keyboard.registered_hotkeys) == 18


def test_r02_clear_failure_blocks_keyboard_activation(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2. Falha no comando clear nativo impede ativação do backend keyboard."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_clear_native_hotkeys", lambda: False)

    hotkey_manager._active_backend = "native"
    hotkey_manager.setup()

    # Backend keyboard NÃO pode ter sido ativado
    assert hotkey_manager.active_backend is None or hotkey_manager.active_backend == "native"
    assert len(fake_keyboard.registered_hotkeys) == 0


def test_r02_timeout_in_native_cleanup_blocks_keyboard(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3. Timeout na limpeza nativa bloqueia a ativação de novo backend."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    def mock_send_cmd(cmd: str, payload: Any = None) -> bool:
        return False

    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    hotkey_manager._native_thread = mock_thread
    monkeypatch.setattr(hotkey_manager, "_send_native_command", mock_send_cmd)

    hotkey_manager.setup()

    assert len(fake_keyboard.registered_hotkeys) == 0


def test_r02_confirmation_without_unregistration_fails(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4. Confirmação sem desregistro bem-sucedido na WinAPI é tratada como falha."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    fake_user32 = MagicMock()
    fake_user32.UnregisterHotKey.return_value = 0
    fake_user32.PeekMessageW.return_value = 0
    monkeypatch.setattr(hotkey_manager, "_user32", fake_user32)

    hotkey_manager._native_callbacks[100] = lambda: None

    def mock_send(cmd: str, payload: Any = None) -> bool:
        return False

    monkeypatch.setattr(hotkey_manager, "_send_native_command", mock_send)
    assert hotkey_manager._clear_native_hotkeys() is False


def test_r02_no_backend_activated_after_failed_cleanup(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5. Verificação de que nenhum outro backend é ativado após falha de limpeza."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_clear_native_hotkeys", lambda: False)

    hotkey_manager.setup()

    assert hotkey_manager.active_backend != "keyboard"
    assert len(fake_keyboard.registered_hotkeys) == 0


def test_r02_recovery_after_subsequent_successful_cleanup(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6. Recuperação após uma limpeza que posteriormente tem sucesso."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    cleanup_ok = False

    def mock_clear() -> bool:
        return cleanup_ok

    monkeypatch.setattr(hotkey_manager, "_clear_native_hotkeys", mock_clear)

    # 1ª tentativa: falha
    hotkey_manager.setup()
    assert len(fake_keyboard.registered_hotkeys) == 0

    # Condição é resolvida
    cleanup_ok = True

    # 2ª tentativa: recupera e ativa keyboard
    hotkey_manager.setup()
    assert hotkey_manager.active_backend == "keyboard"
    assert len(fake_keyboard.registered_hotkeys) == 18


# ==============================================================================
# EST-01-R03: Encerramento de threads nativas
# ==============================================================================


def test_r03_normal_thread_termination(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1. Encerramento normal da thread nativa."""
    fake_native = FakeNativeBackend()
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_send_native_command", fake_native.handle_command)

    mock_thread = MagicMock()
    mock_thread.is_alive.side_effect = lambda: fake_native.is_thread_alive
    hotkey_manager._native_thread = mock_thread

    res = hotkey_manager.stop()
    assert res is True
    assert hotkey_manager.is_native_thread_alive is False
    assert hotkey_manager._native_thread is None


def test_r03_stop_command_failure_preserves_thread_reference(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2. Falha no comando stop mantém a referência e reporta falha."""
    fake_native = FakeNativeBackend()
    fake_native.fail_stop = True
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_send_native_command", fake_native.handle_command)

    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    hotkey_manager._native_thread = mock_thread

    res = hotkey_manager.stop()
    assert res is False
    assert hotkey_manager._native_thread is not None


def test_r03_thread_alive_after_timeout_preserves_reference(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 e 4. Thread que permanece viva após o timeout preserva a referência."""
    fake_native = FakeNativeBackend()
    fake_native.refuse_to_stop = True
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_send_native_command", fake_native.handle_command)

    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    hotkey_manager._native_thread = mock_thread

    res = hotkey_manager.stop()
    assert res is False
    assert hotkey_manager._native_thread is mock_thread
    assert hotkey_manager.is_native_thread_alive is True


def test_r03_prevent_spawning_second_thread_while_first_alive(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5. Impede criação de uma segunda thread enquanto a anterior estiver ativa."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    hotkey_manager._native_thread = mock_thread

    assert hotkey_manager._ensure_native_thread() is True
    assert hotkey_manager._native_thread is mock_thread


def test_r03_recovery_and_successful_stop_after_prior_failure(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6. Encerramento bem-sucedido após uma falha anterior."""
    fake_native = FakeNativeBackend()
    fake_native.refuse_to_stop = True
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_send_native_command", fake_native.handle_command)

    mock_thread = MagicMock()
    mock_thread.is_alive.side_effect = lambda: fake_native.is_thread_alive
    hotkey_manager._native_thread = mock_thread

    # 1ª tentativa: falha
    assert hotkey_manager.stop() is False
    assert hotkey_manager._native_thread is not None

    # Thread finalmente encerra
    fake_native.refuse_to_stop = False

    # 2ª tentativa: tem sucesso
    assert hotkey_manager.stop() is True
    assert hotkey_manager._native_thread is None


def test_r03_repeated_stop_calls_are_safe(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """7. Chamadas repetidas de stop() são seguras e idempotentes."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    assert hotkey_manager.stop() is True
    assert hotkey_manager.stop() is True
    assert hotkey_manager.stop() is True


# ==============================================================================
# EST-01-R04: Política de registro parcial (best-effort)
# ==============================================================================


def test_r04_best_effort_variant_tolerance(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """1. Falha de variante alternativa não invalida o atalho se outra variante funcionar."""
    fake_keyboard.fail_register_shortcuts.add("right alt+p")

    hotkey_manager.setup()

    assert hotkey_manager.active_backend == "keyboard"
    assert hotkey_manager.registered_keyboard_count == 17
    assert len(fake_keyboard.registered_hotkeys) == 17

    fake_keyboard.trigger("alt gr+p")
    cast(MagicMock, fake_controller.play_pause).assert_called_once()


def test_r04_total_shortcut_failure_triggers_rollback(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2. Se todas as variantes de um atalho falharem, executa rollback de tudo."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", False)

    fake_keyboard.fail_register_shortcuts.update(["alt gr+p", "ctrl+alt+p", "right alt+p"])

    hotkey_manager.setup()

    assert hotkey_manager.active_backend is None
    assert hotkey_manager.registered_keyboard_count == 0
    assert len(fake_keyboard.registered_hotkeys) == 0


def test_r04_rollback_failure_blocks_conflicting_fallback(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3. Se o rollback do keyboard falhar, bloqueia fallback nativo para evitar conflito."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    fake_keyboard.fail_register_shortcuts.update(["alt gr+p", "ctrl+alt+p", "right alt+p"])
    fake_keyboard.fail_all_remove = True

    native_setup_called = False

    def mock_native_setup(m: Any) -> bool:
        nonlocal native_setup_called
        native_setup_called = True
        return True

    monkeypatch.setattr(hotkey_manager, "_setup_native_hotkeys", mock_native_setup)

    hotkey_manager.setup()

    assert native_setup_called is False
    assert hotkey_manager.active_backend is None
    assert hotkey_manager.registered_keyboard_count > 0


# ==============================================================================
# EST-01-R06: Concorrência e thread-safety
# ==============================================================================


def test_r06_concurrent_setup_with_timeouts_and_exception_handling(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """Valida setup() concorrente com barreiras sincronizadas e detecção de exceções."""
    threads: list[threading.Thread] = []
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5.0)
            hotkey_manager.setup()
        except Exception as e:
            errors.append(e)

    for _ in range(thread_count):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive(), "Thread bloqueada durante teste de concorrência"

    assert len(errors) == 0, f"Exceções em threads de setup: {errors}"
    assert len(fake_keyboard.registered_hotkeys) == 18
    assert hotkey_manager.registered_keyboard_count == 18
    assert hotkey_manager.active_backend == "keyboard"


def test_r06_concurrent_setup_and_stop_mixed(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """Valida chamadas simultâneas de setup() e stop() sem deadlocks nem exceções."""
    threads: list[threading.Thread] = []
    thread_count = 10
    barrier = threading.Barrier(thread_count)
    errors: list[Exception] = []

    def setup_worker() -> None:
        try:
            barrier.wait(timeout=5.0)
            hotkey_manager.setup()
        except Exception as e:
            errors.append(e)

    def stop_worker() -> None:
        try:
            barrier.wait(timeout=5.0)
            hotkey_manager.stop()
        except Exception as e:
            errors.append(e)

    for i in range(thread_count):
        target = setup_worker if i % 2 == 0 else stop_worker
        t = threading.Thread(target=target)
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive(), "Thread bloqueada durante concorrência setup/stop"

    assert len(errors) == 0, f"Exceções ocorridas na concorrência: {errors}"

    if hotkey_manager.active_backend == "keyboard":
        assert len(fake_keyboard.registered_hotkeys) == 18
        assert hotkey_manager.registered_keyboard_count == 18
    else:
        assert hotkey_manager.active_backend is None
        assert len(fake_keyboard.registered_hotkeys) == 0
        assert hotkey_manager.registered_keyboard_count == 0


def test_parse_shortcut_native(hotkey_manager: HotkeyManager) -> None:
    """Verifica a conversão de atalhos textuais para modificadores WinAPI e Virtual Keys."""
    parsed = hotkey_manager._parse_shortcut_native("alt gr+p")
    assert parsed is not None
    mods, vk = parsed
    assert mods == 0x4003
    assert vk == ord("P")

    parsed_arrow = hotkey_manager._parse_shortcut_native("ctrl+shift+left")
    assert parsed_arrow is not None
    mods_arrow, vk_arrow = parsed_arrow
    assert mods_arrow == (0x0002 | 0x0004 | 0x4000)
    assert vk_arrow == 0x25

    assert hotkey_manager._parse_shortcut_native("") is None
    assert hotkey_manager._parse_shortcut_native("unknown_key_xyz") is None


def test_safe_hotkey_callback_handles_exception(
    hotkey_manager: HotkeyManager,
) -> None:
    """Verifica que _safe_hotkey_callback captura exceções sem derrubar o processo."""

    def broken_callback() -> None:
        raise ValueError("Simulando falha de reprodução")

    safe_cb = hotkey_manager._safe_hotkey_callback("teste", broken_callback)
    safe_cb()
