import json
import os
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.core import autostart
from src.core.config import AppConfig, ConfigManager
from src.ui import theme
from src.ui.settings import save_settings_from_controls


def test_config_defaults(config_manager: ConfigManager) -> None:
    cfg = config_manager.load()
    assert cfg.volume_step == 5
    assert cfg.websocket_port == 8974
    assert "play_pause" in cfg.hotkeys


def test_config_save_load(config_manager: ConfigManager, temp_config_file: str) -> None:
    cfg = config_manager.load()
    cfg.volume_step = 10
    cfg.websocket_port = 9000
    res = config_manager.save(cfg)
    assert res is True

    # Reload from file
    new_cfg = config_manager.load()
    assert new_cfg.volume_step == 10
    assert new_cfg.websocket_port == 9000


def test_config_filtered_data(config_manager: ConfigManager) -> None:
    # Simulate junk in JSON
    with open(config_manager.config_path, "w", encoding="utf-8") as f:
        json.dump({"volume_step": 7, "invalid_key": "junk"}, f)

    cfg = config_manager.load()
    assert cfg.volume_step == 7
    assert not hasattr(cfg, "invalid_key")


# ==============================================================================
# EST-02.1 & EST-02.1.1: Testes de Persistência Atômica do settings.json
# ==============================================================================


def test_atomic_save_normal(config_manager: ConfigManager) -> None:
    """Cenário A: Salvamento normal cria arquivo válido, formatado e com retorno True."""
    cfg = AppConfig(volume_step=8, hud_display_time=4)
    res = config_manager.save(cfg)

    assert res is True
    assert config_manager.config.volume_step == 8

    # Valida conteúdo físico no disco
    with open(config_manager.config_path, encoding="utf-8") as f:
        raw_text = f.read()

    # Confirma indentação de 4 espaços
    assert '    "volume_step": 8' in raw_text

    # Confirma JSON válido
    data = json.loads(raw_text)
    assert data["volume_step"] == 8
    assert data["hud_display_time"] == 4


def test_atomic_save_replace_existing(config_manager: ConfigManager) -> None:
    """Cenário B: Substituição atômica de configuração pré-existente sem arquivos residuais."""
    cfg_initial = AppConfig(volume_step=5, log_level="INFO")
    assert config_manager.save(cfg_initial) is True

    cfg_updated = AppConfig(volume_step=15, log_level="DEBUG")
    assert config_manager.save(cfg_updated) is True

    # Verifica persistência e atualização do estado em memória
    assert config_manager.config.volume_step == 15
    assert config_manager.config.log_level == "DEBUG"

    # Confirma ausência de arquivos temporários (.tmp.) no diretório
    parent_dir = Path(config_manager.config_path).parent
    tmp_files = list(parent_dir.glob(".*.tmp.*"))
    assert tmp_files == [], f"Arquivos temporários residuais encontrados: {tmp_files}"


def test_atomic_save_failure_during_write_preserves_original(
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cenário C: Falha durante escrita mantém arquivo original íntegro e descarta temporário."""
    cfg_initial = AppConfig(volume_step=5, log_level="INFO")
    assert config_manager.save(cfg_initial) is True

    with open(config_manager.config_path, encoding="utf-8") as f:
        original_content = f.read()

    # Simula falha durante fsync ao gravar o temporário
    monkeypatch.setattr(os, "fsync", MagicMock(side_effect=OSError("Simulated disk error")))

    cfg_new = AppConfig(volume_step=20, log_level="ERROR")
    res = config_manager.save(cfg_new)

    # 1. Falha é comunicada ao consumidor
    assert res is False

    # 2. Estado em memória NÃO foi sobrescrito prematuramente
    assert config_manager.config.volume_step == 5
    assert config_manager.config.log_level == "INFO"

    # 3. Arquivo original em disco permaneceu 100% íntegro e inalterado
    with open(config_manager.config_path, encoding="utf-8") as f:
        current_content = f.read()
    assert current_content == original_content

    # 4. Nenhum arquivo temporário residual foi abandonado no diretório
    parent_dir = Path(config_manager.config_path).parent
    tmp_files = list(parent_dir.glob(".*.tmp.*"))
    assert tmp_files == []


def test_atomic_save_failure_during_replace_preserves_original(
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cenário D: Falha em os.replace mantém o arquivo original e remove o temporário."""
    cfg_initial = AppConfig(volume_step=5, log_level="INFO")
    assert config_manager.save(cfg_initial) is True

    with open(config_manager.config_path, encoding="utf-8") as f:
        original_content = f.read()

    # Simula falha na operação atômica de replace (ex.: permissão negada no filesystem)
    monkeypatch.setattr(os, "replace", MagicMock(side_effect=OSError("Access denied on replace")))

    cfg_new = AppConfig(volume_step=25, log_level="DEBUG")
    res = config_manager.save(cfg_new)

    # Falha comunicada
    assert res is False

    # Estado em memória permanece inalterado
    assert config_manager.config.volume_step == 5

    # Arquivo original no disco continua idêntico
    with open(config_manager.config_path, encoding="utf-8") as f:
        current_content = f.read()
    assert current_content == original_content

    # Temporário gerado antes do replace foi limpo pelo bloco finally
    parent_dir = Path(config_manager.config_path).parent
    tmp_files = list(parent_dir.glob(".*.tmp.*"))
    assert tmp_files == []


def test_atomic_save_serialization_failure_does_not_truncate(
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cenário E: Falha na serialização JSON não trunca nem toca no arquivo existente."""
    cfg_initial = AppConfig(volume_step=5)
    assert config_manager.save(cfg_initial) is True

    with open(config_manager.config_path, encoding="utf-8") as f:
        original_content = f.read()

    # Simula falha na geração de JSON
    monkeypatch.setattr(
        json, "dumps", MagicMock(side_effect=TypeError("Object of type X is not JSON serializable"))
    )

    cfg_new = AppConfig(volume_step=30)
    res = config_manager.save(cfg_new)

    assert res is False
    assert config_manager.config.volume_step == 5

    # Arquivo original intacto
    with open(config_manager.config_path, encoding="utf-8") as f:
        assert f.read() == original_content


def test_atomic_save_multiple_consecutive_saves(config_manager: ConfigManager) -> None:
    """Cenário F: Múltiplos salvamentos consecutivos mantêm consistência e sem resíduos."""
    parent_dir = Path(config_manager.config_path).parent

    for step in range(1, 21):
        cfg = AppConfig(volume_step=step)
        res = config_manager.save(cfg)
        assert res is True
        assert config_manager.config.volume_step == step

        # Confirma ausência de lixo em cada iteração
        assert list(parent_dir.glob(".*.tmp.*")) == []

    # Confirma que o disco contém a última versão salva
    loaded = config_manager.load()
    assert loaded.volume_step == 20


def test_atomic_save_unicode_support(config_manager: ConfigManager) -> None:
    """Cenário G: Preservação de caracteres Unicode em UTF-8 sem escapes ASCII desnecessários."""
    unicode_log_file = "wyrm_música_dragão_龍_🎵.log"
    unicode_shortcut = "ctrl+alt+ç"

    cfg = AppConfig(
        log_file=unicode_log_file,
        hotkeys={"play_pause": unicode_shortcut},
    )
    res = config_manager.save(cfg)
    assert res is True

    # Lê o arquivo diretamente em disco como texto UTF-8
    with open(config_manager.config_path, encoding="utf-8") as f:
        raw_text = f.read()

    # ensure_ascii=False deve manter os caracteres legíveis sem \uXXXX
    assert unicode_log_file in raw_text
    assert unicode_shortcut in raw_text

    # Reload confirma integridade dos dados
    loaded = config_manager.load()
    assert loaded.log_file == unicode_log_file
    assert loaded.hotkeys["play_pause"] == unicode_shortcut


def test_atomic_save_recovery_after_failure(
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cenário H: Recuperação funcional após uma tentativa anterior de salvamento com falha."""
    cfg_initial = AppConfig(volume_step=5)
    assert config_manager.save(cfg_initial) is True

    # 1. Tentativa falha
    monkeypatch.setattr(os, "replace", MagicMock(side_effect=OSError("Temporary lock failure")))
    cfg_failed = AppConfig(volume_step=99)
    assert config_manager.save(cfg_failed) is False
    assert config_manager.config.volume_step == 5

    # 2. Condição de erro é resolvida
    monkeypatch.undo()

    # 3. Próxima tentativa tem sucesso e atualiza o estado
    cfg_recovered = AppConfig(volume_step=12)
    assert config_manager.save(cfg_recovered) is True
    assert config_manager.config.volume_step == 12

    loaded = config_manager.load()
    assert loaded.volume_step == 12


# ==============================================================================
# EST-02.1.1 R01: Sincronização de Salvamentos Concorrentes
# ==============================================================================


def test_atomic_save_concurrent_calls_preserve_consistency(
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R01: Duas threads não intercalam replace e atualização de self.config."""
    # Configuração inicial
    assert config_manager.save(AppConfig(volume_step=1)) is True

    t1_in_critical = threading.Event()
    t2_attempted = threading.Event()
    t1_proceed = threading.Event()

    original_replace = os.replace
    replace_count = 0
    replace_lock = threading.Lock()

    def instrumented_replace(src: str, dst: str) -> None:
        nonlocal replace_count
        with replace_lock:
            replace_count += 1
            current = replace_count

        if current == 1:
            # Thread 1 alcançou a substituição atômica dentro do lock
            t1_in_critical.set()
            # Aguarda Thread 2 tentar executar save() concorrente
            t2_attempted.wait(timeout=2.0)
            t1_proceed.wait(timeout=2.0)

        original_replace(src, dst)

    monkeypatch.setattr(os, "replace", instrumented_replace)

    results: dict[str, bool] = {}

    def thread1_target() -> None:
        results["t1"] = config_manager.save(AppConfig(volume_step=10))

    def thread2_target() -> None:
        # Aguarda Thread 1 estar executando
        t1_in_critical.wait(timeout=2.0)
        t2_attempted.set()
        # Thread 2 tenta save() enquanto Thread 1 ainda segura o lock
        results["t2"] = config_manager.save(AppConfig(volume_step=20))

    t1 = threading.Thread(target=thread1_target)
    t2 = threading.Thread(target=thread2_target)

    t1.start()
    t2.start()

    # Confirma que T1 entrou na seção protegida e T2 disparou
    assert t1_in_critical.wait(timeout=2.0) is True
    assert t2_attempted.wait(timeout=2.0) is True

    # Libera T1 para concluir seu ciclo completo
    t1_proceed.set()

    t1.join(timeout=3.0)
    t2.join(timeout=3.0)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert results.get("t1") is True
    assert results.get("t2") is True

    # Como as operações foram serializadas pelo lock, o estado final em memória
    # e em disco devem ser rigorosamente coerentes com a última operação concluída
    final_mem = config_manager.config.volume_step
    with open(config_manager.config_path, encoding="utf-8") as f:
        disk_data = json.load(f)
    final_disk = disk_data["volume_step"]

    assert final_mem == final_disk
    # Não há resíduos temporários
    parent_dir = Path(config_manager.config_path).parent
    assert list(parent_dir.glob(".*.tmp.*")) == []


def test_atomic_save_multiple_threads_barrier(config_manager: ConfigManager) -> None:
    """R01: Múltiplas threads concorrentes serializadas por lock sem corrupção e sem deadlocks."""
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    results: list[bool] = [False] * thread_count

    def worker(idx: int) -> None:
        barrier.wait(timeout=2.0)
        res = config_manager.save(AppConfig(volume_step=idx * 10))
        results[idx] = res

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=3.0)
        assert not t.is_alive()

    # Todas as threads conseguiram salvar
    assert all(results)

    # Estado em memória e disco perfeitamente sincronizados
    with open(config_manager.config_path, encoding="utf-8") as f:
        disk_cfg = json.load(f)
    assert config_manager.config.volume_step == disk_cfg["volume_step"]


# ==============================================================================
# EST-02.1.1 R02: Tratamento de Falhas na Criação do Diretório
# ==============================================================================


def test_atomic_save_makedirs_failure_handled_gracefully(
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R02: Falha em os.makedirs retorna False sem exceção, preservando arquivo e estado."""
    cfg_initial = AppConfig(volume_step=5)
    assert config_manager.save(cfg_initial) is True

    with open(config_manager.config_path, encoding="utf-8") as f:
        original_content = f.read()

    # Simula falha de permissão ao criar o diretório
    monkeypatch.setattr(
        os, "makedirs", MagicMock(side_effect=PermissionError("Acesso negado ao diretório"))
    )

    cfg_new = AppConfig(volume_step=99)
    # A chamada NÃO deve propagar PermissionError
    res = config_manager.save(cfg_new)

    # 1. Retorna False conforme contrato
    assert res is False

    # 2. Estado interno permanece inalterado
    assert config_manager.config.volume_step == 5

    # 3. Arquivo em disco permanece intacto
    with open(config_manager.config_path, encoding="utf-8") as f:
        assert f.read() == original_content

    # 4. Nenhum temporário criado ou residual
    parent_dir = Path(config_manager.config_path).parent
    assert list(parent_dir.glob(".*.tmp.*")) == []

    # 5. Recuperação: remove a falha e confirma que o próximo save funciona normalmente
    monkeypatch.undo()
    assert config_manager.save(AppConfig(volume_step=14)) is True
    assert config_manager.config.volume_step == 14
    loaded = config_manager.load()
    assert loaded.volume_step == 14


# ==============================================================================
# EST-02.1.1 R04: Teste Real do Consumidor da Interface (save_settings_from_controls)
# ==============================================================================


class _FakeField:
    def __init__(self, value: Any) -> None:
        self.value = value


class _FakeSwitch:
    def __init__(self, data: Any) -> None:
        self.data = data


class _FakeTab:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


def test_ui_settings_consumer_handles_save_failure_and_success(
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R04: Testa diretamente o fluxo de produção de save_settings_from_controls."""
    status_text = theme.mono("salvamento automático ativo", size=10.5, color=theme.POSITIVE)
    status_dot = theme.diamond(5, theme.POSITIVE)
    page_mock = MagicMock()

    sync_calls: list[tuple[bool, bool]] = []

    def mock_sync(enabled: bool, elevated: bool = False) -> None:
        sync_calls.append((enabled, elevated))

    monkeypatch.setattr(autostart, "sync", mock_sync)

    tab_gen = _FakeTab(
        {
            "volume_step": _FakeField("10"),
            "hud_display_time": _FakeField("3"),
            "start_with_windows": _FakeSwitch(True),
            "start_with_windows_elevated": _FakeSwitch(False),
        }
    )
    tab_hot = _FakeTab({"play_pause": _FakeField("alt gr+p")})
    tab_lay = _FakeTab(
        {
            "hud_monitor": _FakeField("0"),
            "hud_position": _FakeField("bottom_right"),
            "volume": _FakeSwitch(True),
            "metadata": _FakeSwitch(True),
            "playback": _FakeSwitch(True),
        }
    )
    tab_int = _FakeTab({"spotify_integration": _FakeSwitch(False)})
    tab_adv = _FakeTab(
        {
            "websocket_port": _FakeField("8974"),
            "log_level": _FakeField("INFO"),
            "log_file": _FakeField("wyrmplayer.log"),
        }
    )

    real_replace = os.replace
    should_fail_replace = True

    def conditional_replace(src: str, dst: str) -> None:
        if should_fail_replace:
            raise OSError("Falha no disco simulada")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", conditional_replace)

    # 1. Executa a função real de produção com falha simulada no save
    res_fail = save_settings_from_controls(
        config_manager,
        tab_gen,
        tab_hot,
        tab_lay,
        tab_int,
        tab_adv,
        status_text,
        status_dot,
        page_mock,
    )

    assert res_fail is False
    # Interface indica erro
    assert status_text.value == "erro ao salvar configurações"
    assert status_text.color == theme.DANGER
    assert status_dot.bgcolor == theme.DANGER
    # Efeito colateral de autostart NÃO foi aplicado
    assert len(sync_calls) == 0

    # 2. Executa a função real de produção com sucesso
    should_fail_replace = False
    res_ok = save_settings_from_controls(
        config_manager,
        tab_gen,
        tab_hot,
        tab_lay,
        tab_int,
        tab_adv,
        status_text,
        status_dot,
        page_mock,
    )

    assert res_ok is True
    # Interface indica sucesso
    assert status_text.value == "salvamento automático ativo"
    assert status_text.color == theme.POSITIVE
    assert status_dot.bgcolor == theme.POSITIVE
    # Efeito colateral foi devidamente aplicado com os valores dos controles
    assert sync_calls == [(True, False)]


# ==============================================================================
# EST-02.2A: Testes de Carregamento Seguro e Recuperação de Configurações
# ==============================================================================


def test_load_nonexistent_file_initializes_and_persists_defaults(tmp_path: Path) -> None:
    """CFG-01: Arquivo inexistente cria e persiste padrões com segurança."""
    cfg_path = tmp_path / "sub" / "settings.json"
    assert not cfg_path.exists()

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 5
    assert mgr.config.websocket_port == 8974

    # Confirma que o arquivo foi realmente criado no disco
    assert cfg_path.exists()
    with open(cfg_path, encoding="utf-8") as f:
        saved_data = json.load(f)
    assert saved_data["volume_step"] == 5
    assert saved_data["websocket_port"] == 8974


def test_load_valid_existing_file_preserves_values(tmp_path: Path) -> None:
    """CFG-01: Arquivo existente com JSON válido carrega com sucesso."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(
        json.dumps({"volume_step": 12, "websocket_port": 9123}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 12
    assert mgr.config.websocket_port == 9123


def test_load_malformed_json_preserves_file_and_memory_without_overwrite(tmp_path: Path) -> None:
    """CFG-01 e CFG-05: JSON malformado não sobrescreve o arquivo no disco."""
    cfg_path = tmp_path / "settings.json"
    malformed_content = '{\n  "volume_step": 12,\n  "unclosed: '
    cfg_path.write_text(malformed_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is False
    # Retorna defaults em memória por ser a primeira inicialização
    assert mgr.config.volume_step == 5
    assert mgr.config.websocket_port == 8974

    # O arquivo original no disco DEVE permanecer inalterado!
    assert cfg_path.read_text(encoding="utf-8") == malformed_content


@pytest.mark.parametrize(
    "raw_json",
    [
        "[]",
        "null",
        '"string"',
        "123",
        "true",
    ],
)
def test_load_invalid_root_structures(tmp_path: Path, raw_json: str) -> None:
    """CFG-03: Estrutura raiz diferente de dict é rejeitada sem sobrescrever o disco."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(raw_json, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is False
    assert mgr.config.volume_step == 5
    assert cfg_path.read_text(encoding="utf-8") == raw_json


def test_reload_malformed_file_preserves_last_valid_configuration(tmp_path: Path) -> None:
    """CFG-02: Falha no reload mantém a última configuração válida em memória."""
    cfg_path = tmp_path / "settings.json"
    initial_content = json.dumps({"volume_step": 18, "websocket_port": 9500})
    cfg_path.write_text(initial_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 18
    assert mgr.config.websocket_port == 9500

    # Corrompe o arquivo no disco
    corrupted_content = "{ corrupted json : true "
    cfg_path.write_text(corrupted_content, encoding="utf-8")

    reloaded_cfg = mgr.reload()
    assert mgr.last_load_succeeded is False
    # Garante que os valores anteriores válidos em memória foram preservados
    assert reloaded_cfg.volume_step == 18
    assert reloaded_cfg.websocket_port == 9500
    assert mgr.config.volume_step == 18
    assert mgr.config.websocket_port == 9500

    # Garante que o arquivo corrompido não foi sobrescrito
    assert cfg_path.read_text(encoding="utf-8") == corrupted_content


def test_recovery_after_fixing_malformed_file(tmp_path: Path) -> None:
    """CFG-02 e CFG-04: Recuperação transparente após o arquivo ser corrigido manualmente."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"volume_step": 10}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 10

    # 1. Arquivo se corrompe
    cfg_path.write_text("{bad", encoding="utf-8")
    mgr.reload()
    assert mgr.last_load_succeeded is False
    assert mgr.config.volume_step == 10

    # 2. Usuário/processo corrige o arquivo
    cfg_path.write_text(json.dumps({"volume_step": 20}), encoding="utf-8")
    recovered_cfg = mgr.reload()
    assert mgr.last_load_succeeded is True
    assert recovered_cfg.volume_step == 20
    assert mgr.config.volume_step == 20


def test_load_io_or_permission_error_preserves_state_and_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG-05: Erro de I/O (PermissionError / OSError) é tratado sem crash e sem sobrescrita."""
    cfg_path = tmp_path / "settings.json"
    valid_content = json.dumps({"volume_step": 14})
    cfg_path.write_text(valid_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 14

    # Simula PermissionError ao tentar ler o arquivo
    original_open = open

    def failing_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        if str(file) == str(cfg_path) and ("r" in args or kwargs.get("mode", "r") == "r"):
            raise PermissionError("Acesso negado simulado")
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)

    cfg = mgr.reload()
    assert mgr.last_load_succeeded is False
    assert cfg.volume_step == 14
    assert mgr.config.volume_step == 14

    # Remove o monkeypatch e verifica integridade do arquivo
    monkeypatch.undo()
    assert cfg_path.read_text(encoding="utf-8") == valid_content


def test_load_unicode_decode_error_preserves_state_and_file(tmp_path: Path) -> None:
    """CFG-05: Arquivo com bytes inválidos para UTF-8 é tratado sem sobrescrita."""
    cfg_path = tmp_path / "settings.json"
    # Escreve bytes inválidos em UTF-8
    cfg_path.write_bytes(b'{"key": "\xff\xfe\xfa\x80"}')

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is False
    assert mgr.config.volume_step == 5  # default em memória

    # O arquivo em disco NÃO pode ter sido substituído por AppConfig padrão
    assert cfg_path.read_bytes() == b'{"key": "\xff\xfe\xfa\x80"}'


# ==============================================================================
# EST-02.2A.1 R01: Preservação de Estado na Falha de Recriação
# ==============================================================================


def test_first_initialization_successful(tmp_path: Path) -> None:
    """R01 - Teste 1: Primeira inicialização sem arquivo cria padrões e persiste com sucesso."""
    cfg_path = tmp_path / "settings.json"
    assert not cfg_path.exists()

    mgr = ConfigManager(config_file=str(cfg_path))

    assert mgr.last_load_succeeded is True
    assert mgr.last_error_category is None
    assert mgr.config.volume_step == 5
    assert mgr.config.websocket_port == 8974
    assert cfg_path.exists()

    # Confirma que o arquivo gravado possui conteúdo JSON válido dos padrões
    saved_data = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert saved_data["volume_step"] == 5
    assert saved_data["websocket_port"] == 8974


def test_first_initialization_save_failure_uses_in_memory_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R01 - Teste 2: Primeira inicialização com falha de escrita usa padrões em memória,
    registra falha de persistência e não deixa arquivo corrompido ou parcial.
    """
    cfg_path = tmp_path / "settings.json"
    assert not cfg_path.exists()

    def failing_replace(src: Any, dst: Any) -> None:
        raise PermissionError("Permissão negada ao salvar na inicialização")

    monkeypatch.setattr(os, "replace", failing_replace)

    mgr = ConfigManager(config_file=str(cfg_path))

    assert mgr.last_load_succeeded is False
    assert mgr.last_error_category == "transient_io"
    assert mgr.config.volume_step == 5
    assert mgr.config.websocket_port == 8974
    assert not cfg_path.exists()

    # Confirma ausência de arquivos temporários (.tmp.)
    tmp_files = list(tmp_path.glob(".*.tmp.*"))
    assert tmp_files == []


def test_reload_missing_file_save_failure_preserves_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R01 - Teste 3: Quando arquivo desaparece e recriação falha, preserva a
    configuração anterior em memória e define last_load_succeeded = False.
    """
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"volume_step": 25, "websocket_port": 9100}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 25
    assert mgr.config.websocket_port == 9100
    prev_config_obj = mgr.config

    # O arquivo desaparece externamente
    cfg_path.unlink()
    assert not cfg_path.exists()

    # Simula falha ao recriar o arquivo via save()
    def failing_replace(src: Any, dst: Any) -> None:
        raise PermissionError("Falha simulada na recriação do settings.json")

    monkeypatch.setattr(os, "replace", failing_replace)

    reloaded_cfg = mgr.reload()

    # Preserva configuração anterior e sua identidade de objeto
    assert mgr.last_load_succeeded is False
    assert mgr.last_error_category == "transient_io"
    assert mgr.config is prev_config_obj
    assert reloaded_cfg is prev_config_obj
    assert mgr.config.volume_step == 25
    assert mgr.config.websocket_port == 9100
    assert not cfg_path.exists()


def test_reload_missing_file_recovery_after_restoring_write_conditions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R01 - Teste 4: Após falha de recriação, quando as condições de escrita são restabelecidas,
    a recriação é concluída e last_load_succeeded se torna True.
    """
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"volume_step": 30}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 30

    # 1. Arquivo desaparece e tentativa de recriar falha
    cfg_path.unlink()

    def failing_replace(src: Any, dst: Any) -> None:
        raise PermissionError("Permissão negada temporária")

    monkeypatch.setattr(os, "replace", failing_replace)

    mgr.reload()
    assert mgr.last_load_succeeded is False
    assert mgr.config.volume_step == 30

    # 2. Restabelece condições de escrita
    monkeypatch.undo()

    # Nova tentativa de carregamento/recriação
    recovered_cfg = mgr.reload()
    assert mgr.last_load_succeeded is True
    assert mgr.last_error_category is None
    assert cfg_path.exists()
    assert recovered_cfg.volume_step == 5  # Recreação padrão persistida
    assert mgr.config.volume_step == 5
