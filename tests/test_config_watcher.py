import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.config import AppConfig, ConfigManager
from src.core.state import AppState
from src.services import config_watcher as config_watcher_module
from src.services.config_watcher import ConfigWatcher


@pytest.fixture
def settings_file(tmp_path: Path) -> str:
    path = tmp_path / "settings.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


@pytest.fixture
def config_manager(settings_file: str) -> MagicMock:
    mock = MagicMock()
    mock.config_path = settings_file
    mock.load.return_value = AppConfig(websocket_port=8974, log_level="INFO", log_file="a.log")
    return mock


@pytest.fixture
def watcher(config_manager: MagicMock) -> ConfigWatcher:
    hotkeys = MagicMock()
    hotkeys.is_input_desktop_accessible.return_value = True
    hud = MagicMock()

    return ConfigWatcher(
        hotkeys=hotkeys,
        hud=hud,
        state=AppState(),
        config=config_manager,
        on_websocket_port_change=AsyncMock(),
        on_spotify_integration_change=MagicMock(),
    )


def _touch_settings_file(watcher: ConfigWatcher) -> None:
    """Garante um mtime maior que o registrado em last_mtime e no histórico de falhas."""
    current = os.path.getmtime(watcher.settings_path)
    ref_time = max(
        current,
        watcher.last_mtime or 0.0,
        getattr(watcher, "_last_failed_mtime", 0.0) or 0.0,
    )
    new_time = ref_time + 1.0
    os.utime(watcher.settings_path, (new_time, new_time))


async def test_websocket_port_change_triggers_callback(
    watcher: ConfigWatcher, config_manager: MagicMock
) -> None:
    new_cfg = AppConfig(websocket_port=9000, log_level="INFO", log_file="a.log")
    config_manager.reload.return_value = new_cfg
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    watcher.on_websocket_port_change.assert_awaited_once_with(9000)
    assert watcher.last_websocket_port == 9000


async def test_no_websocket_port_change_skips_callback(
    watcher: ConfigWatcher, config_manager: MagicMock
) -> None:
    config_manager.reload.return_value = AppConfig(
        websocket_port=8974, log_level="INFO", log_file="a.log"
    )
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    watcher.on_websocket_port_change.assert_not_awaited()


async def test_log_config_change_applies_new_configuration(
    watcher: ConfigWatcher, config_manager: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply_logging_mock = MagicMock()
    monkeypatch.setattr(config_watcher_module, "apply_logging_configuration", apply_logging_mock)

    new_cfg = AppConfig(websocket_port=8974, log_level="DEBUG", log_file="b.log")
    config_manager.reload.return_value = new_cfg
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    apply_logging_mock.assert_called_once_with("DEBUG", "b.log")
    assert watcher.last_log_level == "DEBUG"
    assert watcher.last_log_file == "b.log"


# ==============================================================================
# EST-02.1.1 R03: Integração Real com ConfigWatcher e persistência atômica
# ==============================================================================


async def test_atomic_save_integration_with_config_watcher(tmp_path: Path) -> None:
    """R03: ConfigWatcher detecta substituição atômica por os.replace() sem mocks de detecção."""
    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    # Salva configuração inicial real
    initial_cfg = AppConfig(websocket_port=8974, log_level="INFO", log_file="init.log")
    assert real_config_manager.save(initial_cfg) is True

    # Inicializa watcher real usando o ConfigManager real e o mecanismo nativo de mtime
    mock_hotkeys = MagicMock()
    mock_hotkeys.is_input_desktop_accessible.return_value = True
    mock_hud = MagicMock()
    on_ws_change = AsyncMock()
    on_spotify_change = MagicMock()

    watcher = ConfigWatcher(
        hotkeys=mock_hotkeys,
        hud=mock_hud,
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=on_spotify_change,
    )

    initial_mtime = watcher.last_mtime
    assert initial_mtime is not None

    # Pequena pausa temporal para assegurar avanço de timestamp no filesystem
    await asyncio.sleep(0.05)

    # Executa salvamento atômico real com novo websocket_port
    updated_cfg = AppConfig(websocket_port=9200, log_level="DEBUG", log_file="updated.log")
    save_ok = real_config_manager.save(updated_cfg)
    assert save_ok is True

    # Executa a checagem real do ConfigWatcher
    await watcher._check_config_file()

    # Confirma que a substituição atômica foi identificada pelo mtime
    assert watcher.last_mtime is not None
    assert watcher.last_mtime > initial_mtime

    # Confirma que o callback de mudança foi acionado com o novo valor
    on_ws_change.assert_awaited_once_with(9200)
    assert watcher.last_websocket_port == 9200
    assert watcher.last_log_level == "DEBUG"

    # Confirma que a configuração recarregada corresponde aos valores persistidos
    assert real_config_manager.config.websocket_port == 9200
    assert real_config_manager.load().websocket_port == 9200


async def test_config_watcher_ignores_invalid_json_and_recovers_when_fixed(
    tmp_path: Path,
) -> None:
    """CFG-04: ConfigWatcher ignora JSON inválido, não reprocessa em loop
    e recupera quando corrigido.
    """
    import json

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974, log_level="INFO", log_file="init.log")
    assert real_config_manager.save(initial_cfg) is True

    mock_hotkeys = MagicMock()
    mock_hotkeys.is_input_desktop_accessible.return_value = True
    mock_hud = MagicMock()
    on_ws_change = AsyncMock()
    on_spotify_change = MagicMock()

    watcher = ConfigWatcher(
        hotkeys=mock_hotkeys,
        hud=mock_hud,
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=on_spotify_change,
    )

    initial_mtime = watcher.last_mtime
    assert initial_mtime is not None

    # 1. Corrompe o settings.json com JSON malformado
    settings_path.write_text("{\ninvalid_json: [", encoding="utf-8")
    _touch_settings_file(watcher)
    corrupted_mtime = os.path.getmtime(watcher.settings_path)

    # Executa a verificação
    await watcher._check_config_file()

    # Confirma que last_mtime foi atualizado para evitar loop infinito a cada 500ms
    assert watcher.last_mtime == corrupted_mtime

    # Confirma que nenhuma alteração foi propagada aos componentes
    on_ws_change.assert_not_awaited()
    assert watcher.last_websocket_port == 8974
    mock_hotkeys.setup.assert_not_called()

    # Executa novamente sem alterar o arquivo: deve ser no-op
    await watcher._check_config_file()
    on_ws_change.assert_not_awaited()

    # 2. Corrige o arquivo com novo websocket_port e log_level
    valid_new_json = json.dumps({"websocket_port": 9300, "log_level": "DEBUG"})
    settings_path.write_text(valid_new_json, encoding="utf-8")
    _touch_settings_file(watcher)

    # Executa a verificação
    await watcher._check_config_file()

    # Confirma que recuperou e aplicou a nova configuração
    on_ws_change.assert_awaited_once_with(9300)
    assert watcher.last_websocket_port == 9300
    assert watcher.last_log_level == "DEBUG"
    assert real_config_manager.config.websocket_port == 9300


# ==============================================================================
# EST-02.2A.1 R02: Recuperação de Falhas Transitórias e Política de Erros
# ==============================================================================


async def test_transient_read_failure_preserves_state_and_retries_without_new_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R02 - Teste 1 e 2:
    Teste 1: Falha temporária de leitura não aplica nova configuração e preserva anterior.
    Teste 2: Restabelecimento de acesso sem alteração de timestamp/conteúdo recupera
    e aplica a nova configuração com sucesso.
    """
    import json
    from typing import Any

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974, log_level="INFO", log_file="init.log")
    assert real_config_manager.save(initial_cfg) is True

    mock_hotkeys = MagicMock()
    mock_hotkeys.is_input_desktop_accessible.return_value = True
    mock_hud = MagicMock()
    on_ws_change = AsyncMock()
    on_spotify_change = MagicMock()

    watcher = ConfigWatcher(
        hotkeys=mock_hotkeys,
        hud=mock_hud,
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=on_spotify_change,
    )

    # 1. Modifica o arquivo para uma nova configuração válida
    new_cfg_data = json.dumps({"websocket_port": 9500, "log_level": "DEBUG"})
    settings_path.write_text(new_cfg_data, encoding="utf-8")
    _touch_settings_file(watcher)
    target_mtime = os.path.getmtime(watcher.settings_path)

    # 2. Simula falha temporária de I/O (PermissionError ao abrir para leitura)
    original_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == str(settings_path) and ("r" in args or kwargs.get("mode", "r") == "r"):
            raise PermissionError("Bloqueio de arquivo temporário simulado")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    # Executa a verificação sob falha temporária (Teste 1)
    await watcher._check_config_file()

    # Confirma que a nova configuração NÃO foi aplicada
    on_ws_change.assert_not_awaited()
    assert watcher.last_websocket_port == 8974
    assert real_config_manager.config.websocket_port == 8974
    assert real_config_manager.last_load_succeeded is False
    assert real_config_manager.last_error_category == "transient_io"

    # Confirma que last_mtime NÃO foi avançado para target_mtime, permitindo retry
    assert watcher.last_mtime != target_mtime

    # 3. Restabelece o acesso ao arquivo (Teste 2: Recuperação sem novo timestamp)
    monkeypatch.undo()

    # Confirma que timestamp e conteúdo permanecem idênticos
    assert os.path.getmtime(watcher.settings_path) == target_mtime
    assert settings_path.read_text(encoding="utf-8") == new_cfg_data

    # Executa novamente a checagem com o MESMO timestamp
    await watcher._check_config_file()

    # Confirma que a nova configuração foi carregada e aplicada
    on_ws_change.assert_awaited_once_with(9500)
    assert watcher.last_websocket_port == 9500
    assert watcher.last_log_level == "DEBUG"
    assert real_config_manager.config.websocket_port == 9500
    assert real_config_manager.last_load_succeeded is True
    assert real_config_manager.last_error_category is None
    assert watcher.last_mtime == target_mtime


async def test_invalid_json_does_not_trigger_repeated_retries(tmp_path: Path) -> None:
    """R02 - Teste 3: JSON inválido não provoca retries desnecessários a cada verificação;
    recupera normalmente quando corrigido.
    """
    import json

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974, log_level="INFO", log_file="init.log")
    assert real_config_manager.save(initial_cfg) is True

    mock_hotkeys = MagicMock()
    mock_hotkeys.is_input_desktop_accessible.return_value = True
    mock_hud = MagicMock()
    on_ws_change = AsyncMock()
    on_spotify_change = MagicMock()

    watcher = ConfigWatcher(
        hotkeys=mock_hotkeys,
        hud=mock_hud,
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=on_spotify_change,
    )

    # 1. Escreve JSON malformado
    settings_path.write_text("{\nmalformed_json_content", encoding="utf-8")
    _touch_settings_file(watcher)
    bad_mtime = os.path.getmtime(watcher.settings_path)

    # Executa 1ª verificação: detecta e rejeita
    await watcher._check_config_file()
    assert watcher.last_mtime == bad_mtime
    on_ws_change.assert_not_awaited()

    # Espiona reload() para confirmar que verificações subsequentes não reprocessam o arquivo
    reload_call_count = 0
    orig_reload = real_config_manager.reload

    def counting_reload() -> AppConfig:
        nonlocal reload_call_count
        reload_call_count += 1
        return orig_reload()

    real_config_manager.reload = counting_reload  # type: ignore[method-assign]

    # Executa 3 checagens adicionais sem alterar o arquivo
    await watcher._check_config_file()
    await watcher._check_config_file()
    await watcher._check_config_file()

    # Confirma que nenhuma leitura/reload adicional foi executada
    assert reload_call_count == 0
    on_ws_change.assert_not_awaited()

    # 2. Corrige o arquivo
    valid_cfg_data = json.dumps({"websocket_port": 9600, "log_level": "DEBUG"})
    settings_path.write_text(valid_cfg_data, encoding="utf-8")
    _touch_settings_file(watcher)

    # Executa a verificação: recupera com sucesso
    await watcher._check_config_file()
    assert reload_call_count == 1
    on_ws_change.assert_awaited_once_with(9600)
    assert watcher.last_websocket_port == 9600


async def test_transient_failure_and_recovery_lifecycle_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R02 - Teste 4: Ciclo completo de falha transitória seguida de recuperação.
    Verifica que:
    - Watcher permanece operacional.
    - Estado interno não fica inconsistente.
    - O arquivo não é regravado indevidamente.
    - Não existem callbacks duplicados.
    - As atualizações são aplicadas somente após carregamento válido.
    """
    import json
    from typing import Any

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(
        websocket_port=8974,
        hud_monitor=0,
        hud_position="bottom_right",
        log_level="INFO",
    )
    assert real_config_manager.save(initial_cfg) is True

    mock_hotkeys = MagicMock()
    mock_hotkeys.is_input_desktop_accessible.return_value = True
    mock_hud = MagicMock()
    mock_hud.show_hud = AsyncMock()
    on_ws_change = AsyncMock()
    on_spotify_change = MagicMock()

    watcher = ConfigWatcher(
        hotkeys=mock_hotkeys,
        hud=mock_hud,
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=on_spotify_change,
    )

    # Nova configuração válida alterando websocket_port e hud_position
    new_cfg_content = json.dumps(
        {
            "websocket_port": 9700,
            "hud_monitor": 1,
            "hud_position": "top_left",
            "log_level": "DEBUG",
        }
    )
    settings_path.write_text(new_cfg_content, encoding="utf-8")
    _touch_settings_file(watcher)
    target_mtime = os.path.getmtime(watcher.settings_path)

    # Simula erro de I/O transitório (OSError)
    original_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == str(settings_path) and ("r" in args or kwargs.get("mode", "r") == "r"):
            raise OSError("Erro temporário de I/O simulado")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    # 1. Checagem durante a falha
    await watcher._check_config_file()

    # Confirma que nada foi aplicado
    on_ws_change.assert_not_awaited()
    mock_hud.apply_layout.assert_not_called()
    assert watcher.last_websocket_port == 8974
    assert watcher.last_hud_layout == (0, "bottom_right")
    # Arquivo original deve permanecer exatamente igual ao gravado
    assert settings_path.read_text(encoding="utf-8") == new_cfg_content

    # 2. Restabelece acesso e executa checagem
    monkeypatch.undo()
    await watcher._check_config_file()

    # Confirma aplicação exata e ausência de callbacks duplicados
    on_ws_change.assert_awaited_once_with(9700)
    assert on_ws_change.await_count == 1
    mock_hud.apply_layout.assert_called()
    mock_hud.show_hud.assert_awaited_once()

    assert watcher.last_websocket_port == 9700
    assert watcher.last_hud_layout == (1, "top_left")
    assert watcher.last_log_level == "DEBUG"
    assert watcher.last_mtime == target_mtime

    # Arquivo não foi regravado indevidamente
    assert settings_path.read_text(encoding="utf-8") == new_cfg_content


async def test_initial_retry_exhaustion_schedules_cooldown_without_abandoning_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EST-02.2A.2 - Teste 1: Esgotar as tentativas iniciais entra em cooldown
    e agenda a próxima tentativa, sem marcar o arquivo como processado.
    """
    import json
    import time
    from typing import Any

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974)
    assert real_config_manager.save(initial_cfg) is True

    fake_now = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_now)

    watcher = ConfigWatcher(
        hotkeys=MagicMock(),
        hud=MagicMock(),
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=AsyncMock(),
        on_spotify_integration_change=MagicMock(),
        max_transient_retries=3,
        retry_cooldown_seconds=30.0,
    )

    settings_path.write_text(json.dumps({"websocket_port": 9800}), encoding="utf-8")
    _touch_settings_file(watcher)
    failing_mtime = os.path.getmtime(watcher.settings_path)

    # Simula erro de I/O transitório contínuo
    original_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == str(settings_path) and ("r" in args or kwargs.get("mode", "r") == "r"):
            raise PermissionError("Acesso negado contínuo")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    # Executa as 3 tentativas iniciais
    await watcher._check_config_file()  # tentativa 1
    assert watcher._transient_retry_count == 1
    assert watcher.last_mtime != failing_mtime

    await watcher._check_config_file()  # tentativa 2
    assert watcher._transient_retry_count == 2
    assert watcher.last_mtime != failing_mtime

    await watcher._check_config_file()  # tentativa 3 (atinge max_transient_retries)
    assert watcher._transient_retry_count == 3
    # Crucial: o arquivo NÃO foi considerado aplicado (last_mtime NÃO é atualizado)
    assert watcher.last_mtime != failing_mtime
    # Próxima tentativa agendada para fake_now + 30.0
    assert watcher._next_retry_time == fake_now + 30.0
    assert real_config_manager.config.websocket_port == 8974


async def test_recovery_after_cooldown_without_new_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EST-02.2A.2 - Teste 2 (Principal): Após esgotar tentativas iniciais e entrar
    em cooldown, restaurar o acesso e avançar o tempo permite recuperação completa
    sem nova modificação no arquivo.
    """
    import json
    import time
    from typing import Any

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974)
    assert real_config_manager.save(initial_cfg) is True

    fake_now = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_now)

    on_ws_change = AsyncMock()
    watcher = ConfigWatcher(
        hotkeys=MagicMock(),
        hud=MagicMock(),
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=MagicMock(),
        max_transient_retries=3,
        retry_cooldown_seconds=30.0,
    )

    valid_new_json = json.dumps({"websocket_port": 9800})
    settings_path.write_text(valid_new_json, encoding="utf-8")
    _touch_settings_file(watcher)
    target_mtime = os.path.getmtime(watcher.settings_path)

    # 1. Simula erro de I/O transitório durante as tentativas iniciais
    original_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == str(settings_path) and ("r" in args or kwargs.get("mode", "r") == "r"):
            raise PermissionError("Arquivo bloqueado temporariamente")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    for _ in range(3):
        await watcher._check_config_file()

    assert watcher._transient_retry_count == 3
    assert watcher.last_mtime != target_mtime
    on_ws_change.assert_not_awaited()

    # 2. Durante o cooldown, checagens sem avanço de tempo não executam reload
    await watcher._check_config_file()
    on_ws_change.assert_not_awaited()

    # 3. Restaura o acesso ao arquivo (sem tocar no arquivo, mesmo timestamp)
    monkeypatch.setattr("builtins.open", original_open)
    assert os.path.getmtime(watcher.settings_path) == target_mtime
    assert settings_path.read_text(encoding="utf-8") == valid_new_json

    # 4. Avança o tempo simulado para além do cooldown (fake_now + 35.0 > 1030.0)
    fake_now = 1035.0

    # 5. Executa novamente _check_config_file()
    await watcher._check_config_file()

    # 6 e 7. Confirmar que a leitura ocorreu e nova configuração foi aplicada
    on_ws_change.assert_awaited_once_with(9800)
    assert watcher.last_websocket_port == 9800
    assert real_config_manager.config.websocket_port == 9800

    # 8. Confirmar que o estado de retry foi limpo
    assert watcher._transient_retry_count == 0
    assert watcher._last_failed_mtime is None
    assert watcher._next_retry_time == 0.0

    # 9. Confirmar que o timestamp foi reconhecido como processado
    assert watcher.last_mtime == target_mtime

    # 10. Confirmar que verificações adicionais não geram callbacks duplicados
    await watcher._check_config_file()
    assert on_ws_change.await_count == 1


async def test_prolonged_failure_continues_periodic_retries_across_multiple_intervals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EST-02.2A.2 - Teste 3: Indisponibilidade persistente do arquivo continua
    sendo retentada periodicamente após cada período de cooldown sem abandono.
    """
    import json
    import time
    from typing import Any

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974)
    assert real_config_manager.save(initial_cfg) is True

    fake_now = 2000.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_now)

    on_ws_change = AsyncMock()
    watcher = ConfigWatcher(
        hotkeys=MagicMock(),
        hud=MagicMock(),
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=MagicMock(),
        max_transient_retries=2,
        retry_cooldown_seconds=30.0,
    )

    settings_path.write_text(json.dumps({"websocket_port": 9850}), encoding="utf-8")
    _touch_settings_file(watcher)
    target_mtime = os.path.getmtime(watcher.settings_path)

    reload_call_count = 0
    original_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal reload_call_count
        if str(file) == str(settings_path) and ("r" in args or kwargs.get("mode", "r") == "r"):
            reload_call_count += 1
            raise OSError("Falha persistente de disco")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    # 2 tentativas iniciais
    await watcher._check_config_file()
    await watcher._check_config_file()
    assert reload_call_count == 2
    assert watcher._transient_retry_count == 2
    assert watcher._next_retry_time == 2030.0

    # Durante o 1º cooldown (t=2010), não deve tentar ler
    fake_now = 2010.0
    await watcher._check_config_file()
    assert reload_call_count == 2

    # Expira o 1º cooldown (t=2031): deve tentar novamente
    fake_now = 2031.0
    await watcher._check_config_file()
    assert reload_call_count == 3
    assert watcher._transient_retry_count == 3
    # Agenda o 2º cooldown
    assert watcher._next_retry_time == 2061.0

    # Durante o 2º cooldown (t=2050), não deve tentar ler
    fake_now = 2050.0
    await watcher._check_config_file()
    assert reload_call_count == 3

    # Expira o 2º cooldown (t=2062): deve tentar novamente
    fake_now = 2062.0
    await watcher._check_config_file()
    assert reload_call_count == 4
    assert watcher._transient_retry_count == 4
    assert watcher._next_retry_time == 2092.0

    # O arquivo continua elegível (não foi abandonado)
    assert watcher.last_mtime != target_mtime
    on_ws_change.assert_not_awaited()

    # Agora o acesso é restabelecido no 3º intervalo
    monkeypatch.setattr("builtins.open", original_open)
    fake_now = 2095.0
    await watcher._check_config_file()

    # Recuperação com sucesso
    on_ws_change.assert_awaited_once_with(9850)
    assert watcher.last_mtime == target_mtime
    assert watcher._transient_retry_count == 0
    assert watcher._next_retry_time == 0.0


async def test_new_file_modification_during_cooldown_bypasses_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EST-02.2A.2 - Teste 4: Uma nova alteração no arquivo durante o período
    de cooldown descarta o cooldown da versão anterior e é processada imediatamente.
    """
    import json
    import time
    from typing import Any

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974)
    assert real_config_manager.save(initial_cfg) is True

    fake_now = 3000.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_now)

    on_ws_change = AsyncMock()
    watcher = ConfigWatcher(
        hotkeys=MagicMock(),
        hud=MagicMock(),
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=MagicMock(),
        max_transient_retries=2,
        retry_cooldown_seconds=30.0,
    )

    # 1. Modifica para v1
    settings_path.write_text(json.dumps({"websocket_port": 9100}), encoding="utf-8")
    _touch_settings_file(watcher)
    v1_mtime = os.path.getmtime(watcher.settings_path)

    original_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == str(settings_path) and ("r" in args or kwargs.get("mode", "r") == "r"):
            raise PermissionError("Bloqueado na v1")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    # Esgota tentativas iniciais da v1 e entra em cooldown
    await watcher._check_config_file()
    await watcher._check_config_file()
    assert watcher._transient_retry_count == 2
    assert watcher._next_retry_time == 3030.0
    assert watcher._last_failed_mtime == v1_mtime

    # 2. Restaurar permissão e escrever v2 durante o cooldown (fake_now ainda é 3005.0 < 3030.0)
    fake_now = 3005.0
    monkeypatch.setattr("builtins.open", original_open)

    settings_path.write_text(json.dumps({"websocket_port": 9200}), encoding="utf-8")
    _touch_settings_file(watcher)
    v2_mtime = os.path.getmtime(watcher.settings_path)
    assert v2_mtime > v1_mtime

    # 3. Executa verificação: a nova versão deve ser processada IMEDIATAMENTE,
    # ignorando o cooldown pendente da v1
    await watcher._check_config_file()

    on_ws_change.assert_awaited_once_with(9200)
    assert watcher.last_websocket_port == 9200
    assert watcher.last_mtime == v2_mtime
    assert watcher._transient_retry_count == 0
    assert watcher._next_retry_time == 0.0


async def test_invalid_content_does_not_trigger_continuous_retries(tmp_path: Path) -> None:
    """EST-02.2A.2 - Teste 5: JSON malformado não gera retries contínuos periódicos;
    recupera normalmente quando uma versão válida substitui o conteúdo inválido.
    """
    import json

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974)
    assert real_config_manager.save(initial_cfg) is True

    on_ws_change = AsyncMock()
    watcher = ConfigWatcher(
        hotkeys=MagicMock(),
        hud=MagicMock(),
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=MagicMock(),
    )

    # Escreve JSON malformado
    settings_path.write_text("{\ninvalid_json", encoding="utf-8")
    _touch_settings_file(watcher)
    bad_mtime = os.path.getmtime(watcher.settings_path)

    # 1ª verificação: rejeita e atualiza last_mtime
    await watcher._check_config_file()
    assert watcher.last_mtime == bad_mtime
    on_ws_change.assert_not_awaited()

    # Espiona reload() para confirmar que checagens seguintes não tentam reload
    reload_count = 0
    orig_reload = real_config_manager.reload

    def counting_reload() -> AppConfig:
        nonlocal reload_count
        reload_count += 1
        return orig_reload()

    real_config_manager.reload = counting_reload  # type: ignore[method-assign]

    # Checagens adicionais: devem ser no-ops
    await watcher._check_config_file()
    await watcher._check_config_file()
    assert reload_count == 0

    # Corrige com configuração válida
    settings_path.write_text(json.dumps({"websocket_port": 9300}), encoding="utf-8")
    _touch_settings_file(watcher)

    await watcher._check_config_file()
    assert reload_count == 1
    on_ws_change.assert_awaited_once_with(9300)
    assert watcher.last_websocket_port == 9300


async def test_no_duplicate_callbacks_after_successful_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EST-02.2A.2 - Teste 6: Após recuperação com sucesso, verificações adicionais
    subsequentes não geram chamadas duplicadas aos callbacks.
    """
    import json
    import time
    from typing import Any

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(websocket_port=8974)
    assert real_config_manager.save(initial_cfg) is True

    fake_now = 4000.0
    monkeypatch.setattr(time, "monotonic", lambda: fake_now)

    on_ws_change = AsyncMock()
    watcher = ConfigWatcher(
        hotkeys=MagicMock(),
        hud=MagicMock(),
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=MagicMock(),
        max_transient_retries=1,
        retry_cooldown_seconds=10.0,
    )

    settings_path.write_text(json.dumps({"websocket_port": 9400}), encoding="utf-8")
    _touch_settings_file(watcher)

    original_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == str(settings_path) and ("r" in args or kwargs.get("mode", "r") == "r"):
            raise PermissionError("Falha temporária")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    # Falha na primeira tentativa
    await watcher._check_config_file()
    assert on_ws_change.await_count == 0

    # Restabelece acesso e avança cooldown
    monkeypatch.setattr("builtins.open", original_open)
    fake_now = 4015.0

    # Recupera com sucesso
    await watcher._check_config_file()
    assert on_ws_change.await_count == 1
    on_ws_change.assert_awaited_with(9400)

    # Executa 5 checagens adicionais sem alterar o arquivo
    for _ in range(5):
        await watcher._check_config_file()

    # Confirma que nenhuma chamada duplicada ocorreu
    assert on_ws_change.await_count == 1


# ==============================================================================
# EST-02.2B.1: Validação de Configurações no ConfigWatcher
# ==============================================================================


async def test_config_watcher_blocks_invalid_field_types_and_recovers_when_fixed(
    tmp_path: Path,
) -> None:
    """CFG-B07: ConfigWatcher bloqueia aplicação de campos conhecidos inválidos
    e aplica com sucesso após correção do arquivo.
    """
    import json

    settings_path = tmp_path / "settings.json"
    real_config_manager = ConfigManager(config_file=str(settings_path))

    initial_cfg = AppConfig(
        websocket_port=8974,
        hud_monitor=0,
        hud_position="bottom_right",
        spotify_integration=False,
    )
    assert real_config_manager.save(initial_cfg) is True

    mock_hotkeys = MagicMock()
    mock_hotkeys.is_input_desktop_accessible.return_value = True
    mock_hud = MagicMock()
    mock_hud.show_hud = AsyncMock()
    on_ws_change = AsyncMock()
    on_spotify_change = MagicMock()

    watcher = ConfigWatcher(
        hotkeys=mock_hotkeys,
        hud=mock_hud,
        state=AppState(),
        config=real_config_manager,
        on_websocket_port_change=on_ws_change,
        on_spotify_integration_change=on_spotify_change,
    )

    # 1. Modifica o arquivo com campos com tipos e valores inválidos
    invalid_data = {
        "volume_step": "cem",
        "websocket_port": 999999,
        "hud_position": "invalid_preset",
        "spotify_integration": "sim",
        "hotkeys": {"play_pause": 123},
    }
    settings_path.write_text(json.dumps(invalid_data), encoding="utf-8")
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    # Confirma que NENHUM componente recebeu dados inválidos
    mock_hotkeys.setup.assert_not_called()
    mock_hud.apply_layout.assert_not_called()
    on_ws_change.assert_not_awaited()
    on_spotify_change.assert_not_called()
    assert watcher.last_websocket_port == 8974
    assert watcher.last_hud_layout == (0, "bottom_right")
    assert watcher.last_spotify_integration is False
    assert real_config_manager.last_load_succeeded is False
    assert real_config_manager.last_error_category == "invalid_content"

    # 2. Corrige o arquivo com dados válidos
    valid_data = {
        "websocket_port": 9300,
        "hud_monitor": 0,
        "hud_position": "top_left",
        "spotify_integration": True,
    }
    settings_path.write_text(json.dumps(valid_data), encoding="utf-8")
    _touch_settings_file(watcher)

    await watcher._check_config_file()

    # Confirma que os novos parâmetros foram devidamente aplicados
    assert real_config_manager.last_load_succeeded is True
    assert real_config_manager.last_error_category is None
    on_ws_change.assert_awaited_once_with(9300)
    on_spotify_change.assert_called_once()
    mock_hud.apply_layout.assert_called()
    assert watcher.last_websocket_port == 9300
    assert watcher.last_hud_layout == (0, "top_left")
    assert watcher.last_spotify_integration is True
