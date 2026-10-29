import threading
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core.config import AppConfig, ConfigManager
from src.core.hotkeys import HotkeyManager
from src.services.player_controller import PlayerController


class FakeKeyboard:
    """Simulador em memória da biblioteca keyboard para testes determinísticos."""

    def __init__(self) -> None:
        self.hotkeys: dict[int, tuple[str, Callable[[], None]]] = {}
        self._next_id = 1
        self.fail_all = False
        self.fail_on_shortcuts: set[str] = set()

    def add_hotkey(
        self,
        hotkey: str,
        callback: Callable[[], None],
        suppress: bool = False,
    ) -> int:
        if self.fail_all or hotkey in self.fail_on_shortcuts:
            raise RuntimeError(f"Simulando erro de registro para variante: {hotkey}")
        hid = self._next_id
        self._next_id += 1
        self.hotkeys[hid] = (hotkey, callback)
        return hid

    def remove_hotkey(self, handler_or_hotkey: Any) -> None:
        if isinstance(handler_or_hotkey, int):
            self.hotkeys.pop(handler_or_hotkey, None)
        else:
            to_remove = [k for k, (hk, _) in self.hotkeys.items() if hk == handler_or_hotkey]
            for k in to_remove:
                self.hotkeys.pop(k, None)

    def trigger(self, hotkey_str: str) -> None:
        """Simula o pressionamento de uma hotkey pelo usuário."""
        for _, (hk, cb) in list(self.hotkeys.items()):
            if hk == hotkey_str:
                cb()


@pytest.fixture
def fake_keyboard(monkeypatch: pytest.MonkeyPatch) -> FakeKeyboard:
    fake = FakeKeyboard()
    monkeypatch.setattr("src.core.hotkeys.keyboard.add_hotkey", fake.add_hotkey)
    monkeypatch.setattr("src.core.hotkeys.keyboard.remove_hotkey", fake.remove_hotkey)
    return fake


@pytest.fixture
def fake_controller() -> MagicMock:
    controller = MagicMock(spec=PlayerController)
    return controller


@pytest.fixture
def hotkey_manager(fake_controller: MagicMock, config_manager: ConfigManager) -> HotkeyManager:
    return HotkeyManager(controller=fake_controller, config=config_manager)


def test_initial_setup(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """Verifica se a inicialização normal registra as hotkeys e ativa o backend keyboard."""
    hotkey_manager.setup()

    assert hotkey_manager.active_backend == "keyboard"
    # 6 atalhos default, cada um gerando variantes via expand_shortcut_variants (total 18)
    assert len(fake_keyboard.hotkeys) == 18
    assert hotkey_manager.registered_keyboard_count == 18

    # Simula acionamento de uma variante de play_pause
    fake_keyboard.trigger("alt gr+p")
    fake_controller.play_pause.assert_called_once()


def test_consecutive_setup_calls_prevent_duplication(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """Verifica que duas chamadas consecutivas de setup() não acumulam registros nem callbacks."""
    hotkey_manager.setup()
    first_count = len(fake_keyboard.hotkeys)
    assert first_count == 18

    # Segunda chamada consecutiva (recarregamento sem force_backend_reset)
    hotkey_manager.setup(force_backend_reset=False)
    second_count = len(fake_keyboard.hotkeys)

    # Não deve duplicar (18 -> 18, e NÃO 18 -> 36)
    assert second_count == 18
    assert hotkey_manager.registered_keyboard_count == 18

    # Acionar a tecla deve executar a ação exatamente uma vez
    fake_keyboard.trigger("alt gr+p")
    assert fake_controller.play_pause.call_count == 1


def test_twenty_consecutive_reloads(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    fake_controller: MagicMock,
) -> None:
    """Verifica que 20 recarregamentos sucessivos permanecem estritamente idempotentes."""
    for _ in range(20):
        hotkey_manager.setup()

    assert len(fake_keyboard.hotkeys) == 18
    assert hotkey_manager.registered_keyboard_count == 18

    fake_keyboard.trigger("alt gr+up")
    assert fake_controller.adjust_volume.call_count == 1
    fake_controller.adjust_volume.assert_called_with(1)


def test_hotkey_configuration_change(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    config_manager: ConfigManager,
    fake_controller: MagicMock,
) -> None:
    """Verifica que alterar as combinações remove as antigas e registra as novas."""
    hotkey_manager.setup()
    assert "alt gr+p" in [hk for hk, _ in fake_keyboard.hotkeys.values()]

    # Altera as configurações para um atalho diferente
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

    # Recarrega as configurações
    hotkey_manager.setup()

    # O atalho antigo não deve mais existir
    registered_keys = [hk for hk, _ in fake_keyboard.hotkeys.values()]
    assert "alt gr+p" not in registered_keys
    assert "ctrl+shift+p" in registered_keys

    # Acionar atalho antigo não deve disparar callback
    fake_keyboard.trigger("alt gr+p")
    assert fake_controller.play_pause.call_count == 0

    # Acionar novo atalho deve disparar
    fake_keyboard.trigger("ctrl+shift+p")
    assert fake_controller.play_pause.call_count == 1


def test_partial_failure_rollback(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que uma falha parcial durante o registro faz rollback sem deixar órfãos."""
    # Impede fallback nativo para focar no comportamento do backend keyboard
    monkeypatch.setattr(hotkey_manager, "_setup_native_hotkeys", lambda _: False)

    # Configura falha geral para simular erro no meio do registro
    fake_keyboard.fail_all = True

    hotkey_manager.setup()

    # Rollback deve garantir que nenhum handler fique registrado no fake_keyboard
    assert len(fake_keyboard.hotkeys) == 0
    assert hotkey_manager.registered_keyboard_count == 0
    assert hotkey_manager.active_backend is None


def test_fallback_to_native_backend(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica fallback para o backend nativo sem manter órfãos no backend keyboard."""
    fake_keyboard.fail_all = True
    native_setup_called = False

    def mock_setup_native(hotkey_map: dict[str, Callable[[], None]]) -> bool:
        nonlocal native_setup_called
        native_setup_called = True
        return True

    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_setup_native_hotkeys", mock_setup_native)

    hotkey_manager.setup()

    assert native_setup_called is True
    assert hotkey_manager.active_backend == "native"
    # O backend keyboard não deve ter nenhum handler residual
    assert len(fake_keyboard.hotkeys) == 0
    assert hotkey_manager.registered_keyboard_count == 0


def test_switch_from_native_to_keyboard_cleans_native(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que a transição de volta para o keyboard limpa os registros nativos."""
    clear_native_called = False

    def mock_clear_native() -> bool:
        nonlocal clear_native_called
        clear_native_called = True
        return True

    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    monkeypatch.setattr(hotkey_manager, "_clear_native_hotkeys", mock_clear_native)

    # Primeiro força estado simulando que estava no nativo
    hotkey_manager._active_backend = "native"

    # Agora setup é chamado e o backend keyboard funciona
    hotkey_manager.setup()

    assert clear_native_called is True
    assert hotkey_manager.active_backend == "keyboard"
    assert len(fake_keyboard.hotkeys) == 18


def test_stop_releases_all_resources_safely(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que stop() libera todos os registros e pode ser chamado repetidamente."""
    send_cmd_mock = MagicMock(return_value=True)
    monkeypatch.setattr(hotkey_manager, "_send_native_command", send_cmd_mock)
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    hotkey_manager.setup()
    assert len(fake_keyboard.hotkeys) == 18

    # Primeira chamada de stop()
    hotkey_manager.stop()
    assert len(fake_keyboard.hotkeys) == 0
    assert hotkey_manager.registered_keyboard_count == 0
    assert hotkey_manager.active_backend is None

    # Chamadas subsequentes devem ser idempotentes e seguras
    hotkey_manager.stop()
    hotkey_manager.stop()
    assert len(fake_keyboard.hotkeys) == 0


def test_concurrent_setup_calls(
    hotkey_manager: HotkeyManager,
    fake_keyboard: FakeKeyboard,
) -> None:
    """Verifica que chamadas concorrentes a setup() não causam inconsistências nem duplicações."""
    threads: list[threading.Thread] = []
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        hotkey_manager.setup()

    for _ in range(10):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # No final, o número de registros deve ser exatamente 18 (e não 180)
    assert len(fake_keyboard.hotkeys) == 18
    assert hotkey_manager.registered_keyboard_count == 18
    assert hotkey_manager.active_backend == "keyboard"


def test_native_backend_commands_and_rollback(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que o backend nativo executa rollback caso o comando de batch falhe."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)

    # Simula que a thread nativa está ativa
    mock_thread = MagicMock()
    mock_thread.is_alive.return_value = True
    hotkey_manager._native_thread = mock_thread

    commands_sent: list[tuple[str, Any]] = []

    def mock_send(cmd: str, payload: Any = None) -> bool:
        commands_sent.append((cmd, payload))
        if cmd == "register_batch":
            return False  # Simula falha no registro em batch
        return True

    monkeypatch.setattr(hotkey_manager, "_send_native_command", mock_send)

    hotkey_map = {"alt gr+p": lambda: None}
    success = hotkey_manager._setup_native_hotkeys(hotkey_map)

    assert success is False
    # Deve ter enviado 'clear' antes de registrar, e 'clear' no rollback pós-falha
    cmd_names = [cmd for cmd, _ in commands_sent]
    assert cmd_names == ["clear", "register_batch", "clear"]


def test_native_commands_noop_when_thread_not_alive(
    hotkey_manager: HotkeyManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifica que 'stop' e 'clear' não iniciam thread quando ela está inativa."""
    monkeypatch.setattr(hotkey_manager, "_is_windows", True)
    ensure_mock = MagicMock(return_value=True)
    monkeypatch.setattr(hotkey_manager, "_ensure_native_thread", ensure_mock)

    assert hotkey_manager._native_thread is None

    # Enviar 'stop' com thread inativa
    res_stop = hotkey_manager._send_native_command("stop")
    assert res_stop is True
    ensure_mock.assert_not_called()

    # Enviar 'clear' com thread inativa
    res_clear = hotkey_manager._send_native_command("clear")
    assert res_clear is True
    ensure_mock.assert_not_called()


def test_parse_shortcut_native(hotkey_manager: HotkeyManager) -> None:
    """Verifica a conversão de atalhos textuais para modificadores WinAPI e Virtual Keys."""
    # alt gr+p -> MOD_CONTROL | MOD_ALT | MOD_NOREPEAT (0x0002 | 0x0001 | 0x4000 = 0x4003)
    parsed = hotkey_manager._parse_shortcut_native("alt gr+p")
    assert parsed is not None
    mods, vk = parsed
    assert mods == 0x4003
    assert vk == ord("P")

    # ctrl+shift+left
    parsed_arrow = hotkey_manager._parse_shortcut_native("ctrl+shift+left")
    assert parsed_arrow is not None
    mods_arrow, vk_arrow = parsed_arrow
    assert mods_arrow == (0x0002 | 0x0004 | 0x4000)
    assert vk_arrow == 0x25

    # Atalho inválido ou vazio
    assert hotkey_manager._parse_shortcut_native("") is None
    assert hotkey_manager._parse_shortcut_native("unknown_key_xyz") is None


def test_safe_hotkey_callback_handles_exception(
    hotkey_manager: HotkeyManager,
) -> None:
    """Verifica que _safe_hotkey_callback captura exceções sem derrubar o processo."""

    def broken_callback() -> None:
        raise ValueError("Simulando falha de reprodução")

    safe_cb = hotkey_manager._safe_hotkey_callback("teste", broken_callback)
    # Não deve levantar exceção
    safe_cb()
