import ctypes
import threading
from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from src.core.config import AppConfig, ConfigManager
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


class FakeUser32:
    """Simulador de user32 para testar a thread nativa sem depender do desktop real."""

    def __init__(self) -> None:
        self.registered_hotkeys: dict[int, tuple[int, int]] = {}
        self.unregister_calls: list[int] = []
        self.fail_unregister_ids: set[int] = set()
        self.fail_all_unregister = False
        self.fail_register = False

    def PeekMessageW(self, msg: Any, hwnd: Any, min_val: int, max_val: int, remove: int) -> int:
        return 0

    def RegisterHotKey(self, hwnd: Any, hid: int, mods: int, vk: int) -> int:
        if self.fail_register:
            return 0
        self.registered_hotkeys[hid] = (mods, vk)
        return 1

    def UnregisterHotKey(self, hwnd: Any, hid: int) -> int:
        self.unregister_calls.append(hid)
        if self.fail_all_unregister or hid in self.fail_unregister_ids:
            return 0  # Falha na WinAPI
        self.registered_hotkeys.pop(hid, None)
        return 1  # Sucesso na WinAPI


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
# R01: Preservar hotkeys funcionais (funcionamento parcial controlado)
# ==============================================================================


def test_r01_all_shortcuts_registered_success(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """1. Todos os atalhos registrados normalmente com sucesso integral."""
    hotkey_manager.setup()

    assert hotkey_manager.active_backend == "keyboard"
    assert hotkey_manager.registered_keyboard_count == 18
    assert len(fake_keyboard.registered_hotkeys) == 18

    fake_keyboard.trigger("alt gr+p")
    cast(MagicMock, fake_controller.play_pause).assert_called_once()


def test_r01_one_shortcut_fails_others_remain_functional(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """2. Um atalho falha e os demais atalhos continuam operacionais no keyboard."""
    # Faz com que todas as 3 variantes de 'mute' (alt gr+m) falhem no registro
    fake_keyboard.fail_register_shortcuts.update(["alt gr+m", "ctrl+alt+m", "right alt+m"])

    hotkey_manager.setup()

    # O backend keyboard DEVE continuar ativo com os 5 atalhos funcionais (15 variantes)
    assert hotkey_manager.active_backend == "keyboard"
    assert hotkey_manager.registered_keyboard_count == 15
    assert len(fake_keyboard.registered_hotkeys) == 15

    # play_pause continua funcionando perfeitamente!
    fake_keyboard.trigger("alt gr+p")
    cast(MagicMock, fake_controller.play_pause).assert_called_once()


def test_r01_variant_fails_but_equivalent_variant_works(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """3. Uma variante de AltGr falha, mas a variante alternativa equivalente funciona."""
    # Apenas 'right alt+p' falha; 'alt gr+p' e 'ctrl+alt+p' funcionam
    fake_keyboard.fail_register_shortcuts.add("right alt+p")

    hotkey_manager.setup()

    assert hotkey_manager.active_backend == "keyboard"
    assert hotkey_manager.registered_keyboard_count == 17
    assert len(fake_keyboard.registered_hotkeys) == 17

    fake_keyboard.trigger("alt gr+p")
    cast(MagicMock, fake_controller.play_pause).assert_called_once()


def test_r01_all_shortcuts_fail_triggers_rollback(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """4. Quando todos os atalhos falham, executa rollback total do backend keyboard."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", False)
    fake_keyboard.fail_all_register = True

    hotkey_manager.setup()

    assert hotkey_manager.active_backend is None
    assert hotkey_manager.registered_keyboard_count == 0
    assert len(fake_keyboard.registered_hotkeys) == 0


def test_r01_fallback_to_winapi_when_keyboard_totally_fails(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5. Fallback para WinAPI ocorre apenas quando o keyboard falhar totalmente."""
    fake_keyboard.fail_all_register = True
    native_called = False

    def mock_native(m: Any) -> bool:
        nonlocal native_called
        native_called = True
        hotkey_manager._native_callbacks[1] = lambda: None
        return True

    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_setup_native_hotkeys", mock_native)

    hotkey_manager.setup()

    assert native_called is True
    assert hotkey_manager.active_backend == "native"
    assert len(fake_keyboard.registered_hotkeys) == 0


def test_r01_rollback_failure_blocks_insecure_transition(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """6. Falha no rollback de atalhos do keyboard impede ativação conflitante da WinAPI."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    # 1º atalho registra normalmente; no 2º atalho ocorre exceção que aborta o backend
    call_count = 0

    def faulty_expand(shortcut: str) -> list[str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ["alt gr+p"]
        raise RuntimeError("Falha crítica inesperada no subsistema de atalhos")

    monkeypatch.setattr("src.core.hotkeys.keyboard_utils.expand_shortcut_variants", faulty_expand)
    fake_keyboard.fail_all_remove = True

    native_called = False

    def mock_native(m: Any) -> bool:
        nonlocal native_called
        native_called = True
        return True

    monkeypatch.setattr(hotkey_manager, "_setup_native_hotkeys", mock_native)

    hotkey_manager.setup()

    # O fallback nativo NÃO pode ser acionado enquanto o rollback do keyboard falhar
    assert native_called is False
    assert hotkey_manager.active_backend is None
    assert hotkey_manager.registered_keyboard_count > 0


# ==============================================================================
# R02: Validar configurações antes da limpeza
# ==============================================================================


def test_r02_config_read_failure_preserves_existing_hotkeys(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    config_manager: ConfigManager,
    fake_controller: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falha de leitura no config durante reload preserva atalhos que já estavam funcionando."""
    hotkey_manager.setup()
    assert hotkey_manager.registered_keyboard_count == 18

    # Simula erro de leitura/corrupção no load()
    monkeypatch.setattr(
        config_manager, "load", MagicMock(side_effect=RuntimeError("Arquivo corrompido"))
    )

    # Recarrega configurações
    hotkey_manager.setup()

    # Os atalhos anteriores DEVEM continuar intactos e funcionais!
    assert hotkey_manager.active_backend == "keyboard"
    assert hotkey_manager.registered_keyboard_count == 18
    assert len(fake_keyboard.registered_hotkeys) == 18

    fake_keyboard.trigger("alt gr+p")
    cast(MagicMock, fake_controller.play_pause).assert_called_once()


def test_r02_invalid_config_structure_preserves_existing_hotkeys(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuração com estrutura inválida preserva atalhos anteriores."""
    hotkey_manager.setup()
    assert hotkey_manager.registered_keyboard_count == 18

    # Configuração inválida onde hotkeys não é dicionário
    bad_cfg = MagicMock()
    bad_cfg.hotkeys = "invalido_nao_dict"
    monkeypatch.setattr(config_manager, "load", MagicMock(return_value=bad_cfg))

    hotkey_manager.setup()

    assert hotkey_manager.active_backend == "keyboard"
    assert hotkey_manager.registered_keyboard_count == 18


def test_r02_recovery_and_shortcut_change_after_valid_config(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    config_manager: ConfigManager,
    fake_controller: MagicMock,
) -> None:
    """Recuperação após leitura válida e alteração efetiva de combinações."""
    hotkey_manager.setup()

    # Salva nova configuração com atalho diferente
    new_cfg = AppConfig(
        hotkeys={
            "play_pause": "ctrl+shift+p",
            "next_track": "",
            "previous_track": "",
            "volume_up": "",
            "volume_down": "",
            "mute": "",
        }
    )
    config_manager.save(new_cfg)

    hotkey_manager.setup()

    registered_keys = [hk for hk, _ in fake_keyboard.registered_hotkeys.values()]
    assert "alt gr+p" not in registered_keys
    assert "ctrl+shift+p" in registered_keys

    # Atalho antigo não dispara
    fake_keyboard.trigger("alt gr+p")
    assert cast(MagicMock, fake_controller.play_pause).call_count == 0

    # Novo atalho dispara
    fake_keyboard.trigger("ctrl+shift+p")
    assert cast(MagicMock, fake_controller.play_pause).call_count == 1


# ==============================================================================
# R03: Recuperação de thread nativa encerrada
# ==============================================================================


def test_r03_native_thread_alive_timeout_preserves_reference(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thread nativa que permanece viva após timeout de stop preserva referência."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    hotkey_manager._native_thread = mock_thread

    monkeypatch.setattr(hotkey_manager, "_send_native_command", lambda cmd, p=None: True)

    res = hotkey_manager.stop()
    assert res is False
    assert hotkey_manager._native_thread is mock_thread
    assert hotkey_manager.is_native_thread_alive is True


def test_r03_dead_native_thread_reconciles_callbacks_and_cleans_up(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Thread nativa comprovadamente morta tem seus callbacks residuais reconciliados."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    # Simula thread que morreu inesperadamente
    dead_thread = MagicMock()
    dead_thread.is_alive.return_value = False
    hotkey_manager._native_thread = dead_thread
    hotkey_manager._native_callbacks[1] = lambda: None
    hotkey_manager._active_backend = "native"

    # _clear_native_hotkeys deve detectar que a thread está morta, limpar callbacks e retornar True
    res = hotkey_manager._clear_native_hotkeys()

    assert res is True
    assert len(hotkey_manager._native_callbacks) == 0
    assert hotkey_manager._native_thread is None
    assert hotkey_manager.active_backend is None


def test_r03_reconfiguration_succeeds_after_thread_termination(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Após encerramento de thread nativa anterior, nova configuração tem sucesso."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    dead_thread = MagicMock()
    dead_thread.is_alive.return_value = False
    hotkey_manager._native_thread = dead_thread
    hotkey_manager._native_callbacks[1] = lambda: None

    hotkey_manager.setup()

    assert hotkey_manager.active_backend == "keyboard"
    assert hotkey_manager.registered_keyboard_count == 18
    assert len(hotkey_manager._native_callbacks) == 0


def test_r03_no_second_thread_while_first_alive(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garantia de que duas threads nativas não ficam ativas simultaneamente."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    live_thread = MagicMock()
    live_thread.is_alive.return_value = True
    hotkey_manager._native_thread = live_thread

    assert hotkey_manager._ensure_native_thread() is True
    assert hotkey_manager._native_thread is live_thread


# ==============================================================================
# R04: Testes reais de WinAPI e UnregisterHotKey
# ==============================================================================


def test_r04_real_unregister_hotkey_failure_propagation(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercita o native_loop real com FakeUser32 comprovando a detecção de erro na WinAPI."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    fake_user32 = FakeUser32()
    monkeypatch.setattr(hotkey_manager, "_user32", fake_user32)

    # Inicia a thread nativa real
    assert hotkey_manager._ensure_native_thread() is True

    # Registra uma hotkey através do loop real
    reg_ok = hotkey_manager._send_native_command(
        "register_batch", [(0x4003, 0x50, "alt gr+p", lambda: None)]
    )
    assert reg_ok is True
    assert len(hotkey_manager._native_callbacks) == 1
    hotkey_id = list(hotkey_manager._native_callbacks.keys())[0]

    # Simula falha na WinAPI para o comando UnregisterHotKey
    fake_user32.fail_all_unregister = True

    # Envia o comando clear real para a fila da thread
    clear_ok = hotkey_manager._send_native_command("clear")

    # UnregisterHotKey FOI chamado pela thread nativa
    assert hotkey_id in fake_user32.unregister_calls
    # O comando clear reportou FALHA
    assert clear_ok is False
    # O registro NÃO foi descartado prematuramente
    assert hotkey_id in hotkey_manager._native_callbacks

    # Recuperação: WinAPI volta a responder com sucesso
    fake_user32.fail_all_unregister = False
    recovery_ok = hotkey_manager._send_native_command("clear")

    assert recovery_ok is True
    assert len(hotkey_manager._native_callbacks) == 0

    # Finaliza a thread nativa
    hotkey_manager._send_native_command("stop")


# ==============================================================================
# R05: Consistência de active_backend
# ==============================================================================


def test_r05_active_backend_consistency_on_empty_handlers(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """active_backend não reporta backend quando não há registros ativos."""
    hotkey_manager.setup()
    assert hotkey_manager.active_backend == "keyboard"

    # Limpeza remove todos os handlers
    hotkey_manager._clear_keyboard_hotkeys()
    assert hotkey_manager.registered_keyboard_count == 0
    assert hotkey_manager.active_backend is None


def test_r05_active_backend_on_empty_hotkey_map(
    hotkey_manager: HotkeyManager,
    config_manager: ConfigManager,
) -> None:
    """Configuração sem nenhum atalho define active_backend como None."""
    empty_cfg = AppConfig(
        hotkeys={
            "play_pause": "",
            "next_track": "",
            "previous_track": "",
            "volume_up": "",
            "volume_down": "",
            "mute": "",
        }
    )
    config_manager.save(empty_cfg)

    hotkey_manager.setup()
    assert hotkey_manager.active_backend is None


# ==============================================================================
# R06: Diagnóstico da WinAPI (ctypes use_last_error)
# ==============================================================================


def test_r06_winapi_last_error_diagnostic(
    hotkey_manager: HotkeyManager,
) -> None:
    """Verifica que o carregamento de user32 no Windows utiliza use_last_error=True."""
    if not hotkey_manager._is_windows:
        pytest.skip("Teste exclusivo de ambiente Windows")

    assert hotkey_manager._user32 is not None

    # Chamada propositalmente inválida de UnregisterHotKey para ID inexistente
    res = hotkey_manager._user32.UnregisterHotKey(None, 987654)
    # WinAPI retorna 0 em caso de erro
    assert res == 0
    err = ctypes.get_last_error()
    # ERROR_HOTKEY_NOT_REGISTERED = 1419
    assert err != 0


# ==============================================================================
# Regressões gerais e concorrência
# ==============================================================================


def test_twenty_consecutive_reloads_idempotent(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """20 recarregamentos sucessivos permanecem estritamente idempotentes."""
    for _ in range(20):
        hotkey_manager.setup()

    assert hotkey_manager.registered_keyboard_count == 18
    assert len(fake_keyboard.registered_hotkeys) == 18

    fake_keyboard.trigger("alt gr+p")
    cast(MagicMock, fake_controller.play_pause).assert_called_once()


def test_concurrent_setup_with_timeouts_and_exception_handling(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """Valida setup() concorrente com barreiras e sem exceções."""
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
        assert not t.is_alive(), "Thread bloqueada durante concorrência"

    assert len(errors) == 0, f"Exceções em threads: {errors}"
    assert len(fake_keyboard.registered_hotkeys) == 18
    assert hotkey_manager.registered_keyboard_count == 18
    assert hotkey_manager.active_backend == "keyboard"


def test_concurrent_setup_and_stop_mixed(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """Valida concorrência entre setup() e stop() sem deadlocks."""
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
        assert not t.is_alive(), "Thread bloqueada"

    assert len(errors) == 0, f"Exceções: {errors}"

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
