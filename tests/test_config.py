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
        res = config_manager.save(AppConfig(volume_step=(idx + 1) * 10))
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


# ==============================================================================
# EST-02.2B.1: Validação de Configurações e Compatibilidade Retroativa
# ==============================================================================


@pytest.mark.parametrize(
    ("payload", "field_name"),
    [
        ({"volume_step": "abc"}, "volume_step"),
        ({"volume_step": None}, "volume_step"),
        ({"volume_step": [5]}, "volume_step"),
        ({"volume_step": 5.5}, "volume_step"),
        ({"hud_display_time": "3"}, "hud_display_time"),
        ({"hud_display_time": None}, "hud_display_time"),
        ({"hud_display_time": 3.0}, "hud_display_time"),
        ({"websocket_port": "8974"}, "websocket_port"),
        ({"websocket_port": None}, "websocket_port"),
        ({"websocket_port": [8974]}, "websocket_port"),
        ({"hud_monitor": "0"}, "hud_monitor"),
        ({"hud_monitor": None}, "hud_monitor"),
        ({"hud_monitor": []}, "hud_monitor"),
        ({"hud_position": 123}, "hud_position"),
        ({"hud_position": None}, "hud_position"),
        ({"hud_position": []}, "hud_position"),
        ({"log_level": 123}, "log_level"),
        ({"log_level": None}, "log_level"),
        ({"log_file": 123}, "log_file"),
        ({"log_file": None}, "log_file"),
        ({"spotify_integration": "false"}, "spotify_integration"),
        ({"spotify_integration": 0}, "spotify_integration"),
        ({"spotify_integration": None}, "spotify_integration"),
        ({"start_with_windows": "true"}, "start_with_windows"),
        ({"start_with_windows": 1}, "start_with_windows"),
        ({"start_with_windows": None}, "start_with_windows"),
        ({"start_with_windows_elevated": "true"}, "start_with_windows_elevated"),
        ({"start_with_windows_elevated": 1}, "start_with_windows_elevated"),
        ({"start_with_windows_elevated": None}, "start_with_windows_elevated"),
        ({"hotkeys": []}, "hotkeys"),
        ({"hotkeys": "string"}, "hotkeys"),
        ({"hotkeys": None}, "hotkeys"),
        ({"triggers": []}, "triggers"),
        ({"triggers": "string"}, "triggers"),
        ({"triggers": None}, "triggers"),
    ],
)
def test_validation_rejects_incompatible_field_types(
    tmp_path: Path, payload: dict[str, Any], field_name: str
) -> None:
    """CFG-B02: Rejeita tipos incompatíveis nos campos conhecidos de AppConfig."""
    cfg_path = tmp_path / "settings.json"
    raw_content = json.dumps(payload)
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is False
    assert mgr.last_error_category == "invalid_content"
    # Não sobrescreve o arquivo com erro
    assert cfg_path.read_text(encoding="utf-8") == raw_content


@pytest.mark.parametrize(
    ("payload", "is_valid"),
    [
        # volume_step: 1 a 100
        ({"volume_step": 0}, False),
        ({"volume_step": -5}, False),
        ({"volume_step": 1}, True),
        ({"volume_step": 5}, True),
        ({"volume_step": 100}, True),
        ({"volume_step": 101}, False),
        # hud_display_time: 1 a 60
        ({"hud_display_time": 0}, False),
        ({"hud_display_time": -1}, False),
        ({"hud_display_time": 1}, True),
        ({"hud_display_time": 30}, True),
        ({"hud_display_time": 60}, True),
        ({"hud_display_time": 61}, False),
        # websocket_port: 1 a 65535
        ({"websocket_port": 0}, False),
        ({"websocket_port": -80}, False),
        ({"websocket_port": 1}, True),
        ({"websocket_port": 8974}, True),
        ({"websocket_port": 65535}, True),
        ({"websocket_port": 65536}, False),
        # hud_monitor: >= 0
        ({"hud_monitor": -1}, False),
        ({"hud_monitor": 0}, True),
        ({"hud_monitor": 3}, True),
        # hud_position: presets conhecidos
        ({"hud_position": "bottom_right"}, True),
        ({"hud_position": "top_left"}, True),
        ({"hud_position": "center"}, True),
        ({"hud_position": "floating"}, False),
        ({"hud_position": ""}, False),
        # log_level: níveis padrão Python (case-insensitive)
        ({"log_level": "DEBUG"}, True),
        ({"log_level": "info"}, True),
        ({"log_level": "WARNING"}, True),
        ({"log_level": "ERROR"}, True),
        ({"log_level": "CRITICAL"}, True),
        ({"log_level": "VERBOSE"}, False),
        ({"log_level": ""}, False),
        # log_file: string não vazia
        ({"log_file": "wyrmplayer.log"}, True),
        ({"log_file": "logs/test.log"}, True),
        ({"log_file": ""}, False),
        ({"log_file": "   "}, False),
    ],
)
def test_validation_values_and_boundaries(
    tmp_path: Path, payload: dict[str, Any], is_valid: bool
) -> None:
    """CFG-B03: Valida intervalos numéricos e valores válidos para strings/presets."""
    cfg_path = tmp_path / "settings.json"
    raw_content = json.dumps(payload)
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is is_valid
    if is_valid:
        assert mgr.last_error_category is None
    else:
        assert mgr.last_error_category == "invalid_content"
        assert cfg_path.read_text(encoding="utf-8") == raw_content


@pytest.mark.parametrize(
    "field_name",
    ["volume_step", "hud_display_time", "websocket_port", "hud_monitor"],
)
@pytest.mark.parametrize("bool_val", [True, False])
def test_validation_booleans_not_accepted_as_integers(
    tmp_path: Path, field_name: str, bool_val: bool
) -> None:
    """CFG-B02: Garante que booleanos não sejam aceitos em campos inteiros."""
    cfg_path = tmp_path / "settings.json"
    raw_content = json.dumps({field_name: bool_val})
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is False
    assert mgr.last_error_category == "invalid_content"
    assert cfg_path.read_text(encoding="utf-8") == raw_content


@pytest.mark.parametrize(
    ("hotkey_payload", "is_valid"),
    [
        ({"play_pause": 123}, False),
        ({"volume_up": None}, False),
        ({"volume_down": True}, False),
        ({"mute": ["alt", "m"]}, False),
        ({"play_pause": "alt gr+p"}, True),
        ({"play_pause": ""}, True),  # String vazia permitida para desativar
        ({"volume_up": "ctrl+alt+up"}, True),
    ],
)
def test_validation_hotkeys_structures_and_values(
    tmp_path: Path, hotkey_payload: dict[str, Any], is_valid: bool
) -> None:
    """CFG-B04: Valida tipos dos valores do dicionário de hotkeys."""
    cfg_path = tmp_path / "settings.json"
    raw_content = json.dumps({"hotkeys": hotkey_payload})
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is is_valid
    if not is_valid:
        assert mgr.last_error_category == "invalid_content"
        assert cfg_path.read_text(encoding="utf-8") == raw_content


def test_validation_hotkeys_partial_preserves_only_configured_actions(tmp_path: Path) -> None:
    """R01: Configurações parciais de hotkeys preservam apenas ações configuradas sem defaults."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(
        json.dumps({"hotkeys": {"play_pause": "ctrl+p", "mute": ""}}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.hotkeys["play_pause"] == "ctrl+p"
    assert mgr.config.hotkeys["mute"] == ""  # desativado
    # Ações ausentes NÃO devem ser introduzidas automaticamente
    assert "next_track" not in mgr.config.hotkeys
    assert "previous_track" not in mgr.config.hotkeys
    assert "volume_up" not in mgr.config.hotkeys
    assert "volume_down" not in mgr.config.hotkeys
    assert mgr.config.hotkeys == {"play_pause": "ctrl+p", "mute": ""}


@pytest.mark.parametrize(
    ("trigger_payload", "is_valid"),
    [
        ({"volume": "sim"}, False),
        ({"metadata": 1}, False),
        ({"playback": None}, False),
        ({"volume": False}, True),
        ({"volume": True, "metadata": False, "playback": True}, True),
    ],
)
def test_validation_triggers_structures_and_values(
    tmp_path: Path, trigger_payload: dict[str, Any], is_valid: bool
) -> None:
    """CFG-B05: Valida integridade e tipos dos valores de triggers."""
    cfg_path = tmp_path / "settings.json"
    raw_content = json.dumps({"triggers": trigger_payload})
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is is_valid
    if not is_valid:
        assert mgr.last_error_category == "invalid_content"
        assert cfg_path.read_text(encoding="utf-8") == raw_content


def test_validation_triggers_partial_fill_with_defaults(tmp_path: Path) -> None:
    """CFG-B05: Preserva trigger especificado e preenche ausentes com padrões."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"triggers": {"volume": False}}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.triggers["volume"] is False
    assert mgr.config.triggers["metadata"] is True
    assert mgr.config.triggers["playback"] is True


def test_validation_backward_compatibility_sparse_fields(tmp_path: Path) -> None:
    """CFG-B06: Arquivo antigo parcialmente preenchido é preservado e complementado."""
    cfg_path = tmp_path / "settings.json"
    sparse_data = {"volume_step": 10}
    cfg_path.write_text(json.dumps(sparse_data), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 10
    # Todos os outros campos receberam os padrões
    assert mgr.config.hud_display_time == 3
    assert mgr.config.websocket_port == 8974
    assert mgr.config.hud_position == "bottom_right"
    assert mgr.config.log_level == "INFO"
    assert mgr.config.spotify_integration is False
    assert mgr.config.hotkeys["play_pause"] == "alt gr+p"
    assert mgr.config.triggers["volume"] is True

    # O arquivo em disco NÃO foi sobrescrito automaticamente
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == sparse_data


def test_validation_unknown_fields_preserved_on_disk(tmp_path: Path) -> None:
    """CFG-B06: Campos desconhecidos são ignorados em memória e preservados no disco."""
    cfg_path = tmp_path / "settings.json"
    future_data = {
        "volume_step": 12,
        "future_option": "custom_value",
        "nested_unknown": {"foo": "bar"},
    }
    cfg_path.write_text(json.dumps(future_data), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 12
    assert not hasattr(mgr.config, "future_option")
    # O arquivo físico preservou integralmente os campos desconhecidos
    assert json.loads(cfg_path.read_text(encoding="utf-8")) == future_data


def test_validation_preserves_last_valid_state_and_recovers_when_fixed(tmp_path: Path) -> None:
    """CFG-B07: Preserva a última configuração válida na presença de valor inválido e recupera."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(
        json.dumps({"volume_step": 15, "websocket_port": 9123}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 15
    assert mgr.config.websocket_port == 9123

    # 1. Modifica o arquivo com um tipo de campo inválido
    invalid_content = json.dumps({"volume_step": "cem"})
    cfg_path.write_text(invalid_content, encoding="utf-8")

    reloaded = mgr.reload()
    assert mgr.last_load_succeeded is False
    assert mgr.last_error_category == "invalid_content"
    # Estado anterior permanece em memória
    assert reloaded.volume_step == 15
    assert reloaded.websocket_port == 9123
    assert mgr.config.volume_step == 15
    assert mgr.config.websocket_port == 9123
    # Arquivo no disco continua intacto
    assert cfg_path.read_text(encoding="utf-8") == invalid_content

    # 2. Corrige o arquivo com novo valor válido
    cfg_path.write_text(json.dumps({"volume_step": 20}), encoding="utf-8")
    recovered = mgr.reload()
    assert mgr.last_load_succeeded is True
    assert mgr.last_error_category is None
    assert recovered.volume_step == 20
    assert mgr.config.volume_step == 20


def test_validation_hotkeys_empty_dict_preserved(tmp_path: Path) -> None:
    """R01: Dicionário explicitamente vazio em hotkeys é preservado sem defaults."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"hotkeys": {}}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.hotkeys == {}


def test_validation_hotkeys_single_action_preserved(tmp_path: Path) -> None:
    """R01: Dicionário contendo uma única ação preserva somente ela."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"hotkeys": {"play_pause": "ctrl+p"}}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.hotkeys == {"play_pause": "ctrl+p"}


def test_validation_hotkeys_multiple_actions_preserved(tmp_path: Path) -> None:
    """R01: Dicionário contendo algumas ações preserva somente as fornecidas."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(
        json.dumps({"hotkeys": {"volume_up": "alt+up", "volume_down": "alt+down"}}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.hotkeys == {"volume_up": "alt+up", "volume_down": "alt+down"}


def test_validation_hotkeys_explicitly_disabled_by_empty_string(tmp_path: Path) -> None:
    """R01: Ação explicitamente desativada por string vazia permanece desativada."""
    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"hotkeys": {"play_pause": ""}}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.hotkeys == {"play_pause": ""}


def test_validation_hotkeys_collision_with_default_does_not_inject_default(tmp_path: Path) -> None:
    """R01: Atalho personalizado igual ao padrão de outra ação não injeta a ação padrão."""
    cfg_path = tmp_path / "settings.json"
    # play_pause configurado com 'alt gr+m', que é o padrão de 'mute'
    cfg_path.write_text(json.dumps({"hotkeys": {"play_pause": "alt gr+m"}}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.hotkeys == {"play_pause": "alt gr+m"}
    assert "mute" not in mgr.config.hotkeys


def test_validation_hotkeys_full_configuration_preserved(tmp_path: Path) -> None:
    """R01: Configuração completa com todos os atalhos fornecidos é preservada."""
    cfg_path = tmp_path / "settings.json"
    full_hotkeys = {
        "play_pause": "ctrl+1",
        "next_track": "ctrl+2",
        "previous_track": "ctrl+3",
        "volume_up": "ctrl+4",
        "volume_down": "ctrl+5",
        "mute": "ctrl+6",
    }
    cfg_path.write_text(json.dumps({"hotkeys": full_hotkeys}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.config.hotkeys == full_hotkeys


def test_validation_hotkeys_and_triggers_instances_independence(tmp_path: Path) -> None:
    """R01: Instâncias de AppConfig e ConfigManager possuem dicionários independentes."""
    cfg1 = AppConfig()
    cfg2 = AppConfig()
    assert cfg1.hotkeys is not cfg2.hotkeys
    assert cfg1.triggers is not cfg2.triggers

    cfg1.hotkeys["play_pause"] = "modified"
    assert cfg2.hotkeys["play_pause"] != "modified"

    cfg1.triggers["volume"] = False
    assert cfg2.triggers["volume"] is True


def test_hotkey_manager_integration_collision_does_not_override_custom_hotkey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R01: HotkeyManager.setup com atalho 'alt gr+m' em play_pause não registra mute."""
    from src.core.hotkeys import HotkeyManager

    cfg_path = tmp_path / "settings.json"
    cfg_path.write_text(json.dumps({"hotkeys": {"play_pause": "alt gr+m"}}), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    mock_controller = MagicMock()

    hk_mgr = HotkeyManager(controller=mock_controller, config=mgr)

    # Captura o mapa passado para _setup_low_level_keyboard_hotkeys
    captured_maps: list[dict[str, Any]] = []

    def fake_setup_kb(hotkey_map: dict[str, Any]) -> bool:
        captured_maps.append(hotkey_map)
        return True

    monkeypatch.setattr(hk_mgr, "_setup_low_level_keyboard_hotkeys", fake_setup_kb)
    monkeypatch.setattr(hk_mgr, "_clear_keyboard_hotkeys", lambda: True)
    monkeypatch.setattr(hk_mgr, "_clear_native_hotkeys", lambda: True)

    hk_mgr.setup()

    assert len(captured_maps) == 1
    hotkey_map = captured_maps[0]
    assert list(hotkey_map.keys()) == ["alt gr+m"]

    # Invoca o callback do atalho 'alt gr+m' e valida que é play_pause, não toggle_mute
    hotkey_map["alt gr+m"]()
    mock_controller.play_pause.assert_called_once()
    mock_controller.toggle_mute.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"hotkeys": {"future_action": "ctrl+k"}},
        {"hotkeys": {"future_action": 123}},
        {"hotkeys": {"future_action": {"sequence": ["ctrl", "alt", "k"]}}},
        {"hotkeys": {"future_action": None}},
        {"hotkeys": {"future_action": [1, 2, 3]}},
    ],
)
def test_validation_ignores_unknown_internal_hotkeys_entries(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    """R02: Ações desconhecidas em hotkeys são ignoradas sem rejeitar a configuração."""
    cfg_path = tmp_path / "settings.json"
    raw_content = json.dumps(payload)
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.last_error_category is None
    # Ação desconhecida foi ignorada na representação em memória
    assert "future_action" not in mgr.config.hotkeys
    assert mgr.config.hotkeys == {}
    # Arquivo no disco permaneceu intacto
    assert cfg_path.read_text(encoding="utf-8") == raw_content


@pytest.mark.parametrize(
    "payload",
    [
        {"triggers": {"future_event": True}},
        {"triggers": {"future_event": "enabled"}},
        {"triggers": {"future_event": 123}},
        {"triggers": {"future_event": None}},
        {"triggers": {"future_event": {"nested": "value"}}},
    ],
)
def test_validation_ignores_unknown_internal_triggers_entries(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    """R02: Eventos desconhecidos em triggers são ignorados sem rejeitar a configuração."""
    cfg_path = tmp_path / "settings.json"
    raw_content = json.dumps(payload)
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.last_error_category is None
    assert "future_event" not in mgr.config.triggers
    # Triggers conhecidos mantêm seus defaults
    assert mgr.config.triggers["volume"] is True
    assert mgr.config.triggers["metadata"] is True
    assert mgr.config.triggers["playback"] is True
    assert cfg_path.read_text(encoding="utf-8") == raw_content


def test_validation_document_with_valid_known_and_unknown_internal_entries(
    tmp_path: Path,
) -> None:
    """R02: Documento com campos conhecidos válidos e entradas internas desconhecidas é aceito."""
    cfg_path = tmp_path / "settings.json"
    complex_data = {
        "websocket_port": 9000,
        "volume_step": 10,
        "hotkeys": {
            "play_pause": "ctrl+p",
            "unknown_hotkey": 123,
            "another_unknown": {"seq": ["ctrl", "k"]},
        },
        "triggers": {
            "volume": False,
            "future_trigger": "enabled",
            "another_trigger": 999,
        },
        "future_root_option": "custom",
    }
    raw_content = json.dumps(complex_data)
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is True
    assert mgr.last_error_category is None
    assert mgr.config.websocket_port == 9000
    assert mgr.config.volume_step == 10
    assert mgr.config.hotkeys == {"play_pause": "ctrl+p"}
    assert mgr.config.triggers == {
        "volume": False,
        "metadata": True,
        "playback": True,
    }
    assert cfg_path.read_text(encoding="utf-8") == raw_content


@pytest.mark.parametrize(
    "payload",
    [
        {"hotkeys": {"play_pause": 123}},
        {"hotkeys": {"volume_up": None}},
        {"hotkeys": {"next_track": True}},
        {"hotkeys": {"mute": ["alt", "m"]}},
        {"triggers": {"volume": "enabled"}},
        {"triggers": {"metadata": 1}},
        {"triggers": {"playback": None}},
        {"triggers": {"volume": [True]}},
    ],
)
def test_validation_known_internal_entries_still_rejected_when_invalid(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    """R02: Entradas conhecidas continuam sendo estritamente validadas e rejeitadas se inválidas."""
    cfg_path = tmp_path / "settings.json"
    raw_content = json.dumps(payload)
    cfg_path.write_text(raw_content, encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_path))
    assert mgr.last_load_succeeded is False
    assert mgr.last_error_category == "invalid_content"
    assert cfg_path.read_text(encoding="utf-8") == raw_content


# ==============================================================================
# EST-02.2B.2: Consolidação da Validação e Preservação das Configurações
# ==============================================================================


# ------------------------------------------------------------------------------
# Grupo A: Validação de save()
# ------------------------------------------------------------------------------


def test_save_valid_config_succeeds(config_manager: ConfigManager) -> None:
    """CFG-B2-01: Configuração válida é persistida com sucesso e atualiza estado em memória."""
    valid_cfg = AppConfig(
        volume_step=15,
        hud_display_time=5,
        websocket_port=9000,
        hud_monitor=1,
        hud_position="top_right",
        log_level="DEBUG",
        log_file="custom.log",
        spotify_integration=True,
        start_with_windows=True,
        start_with_windows_elevated=True,
        hotkeys={"play_pause": "ctrl+shift+p"},
        triggers={"volume": False, "metadata": True, "playback": True},
    )
    assert config_manager.save(valid_cfg) is True
    assert config_manager.config.volume_step == 15
    assert config_manager.config.websocket_port == 9000
    assert config_manager.config.hotkeys == {"play_pause": "ctrl+shift+p"}


@pytest.mark.parametrize(
    ("attr_name", "invalid_value"),
    [
        ("volume_step", "dez"),
        ("volume_step", True),
        ("volume_step", 15.5),
        ("hud_display_time", "cinco"),
        ("hud_display_time", False),
        ("websocket_port", "8974"),
        ("websocket_port", True),
        ("hud_monitor", "zero"),
        ("hud_position", 123),
        ("log_level", 10),
        ("log_file", 999),
        ("spotify_integration", "sim"),
        ("start_with_windows", 1),
        ("start_with_windows_elevated", "true"),
        ("hotkeys", "ctrl+p"),
        ("triggers", True),
    ],
)
def test_save_invalid_types_rejected(
    config_manager: ConfigManager, attr_name: str, invalid_value: Any
) -> None:
    """CFG-B2-01: save() rejeita tipos incompatíveis nos campos conhecidos."""
    cfg = AppConfig()
    setattr(cfg, attr_name, invalid_value)

    res = config_manager.save(cfg)
    assert res is False


@pytest.mark.parametrize(
    ("attr_name", "out_of_range_value"),
    [
        ("volume_step", -10),
        ("volume_step", 0),
        ("volume_step", 101),
        ("hud_display_time", 0),
        ("hud_display_time", 61),
        ("websocket_port", 0),
        ("websocket_port", 65536),
        ("hud_monitor", -1),
        ("hud_position", "middle_center"),
        ("log_level", "VERBOSE"),
        ("log_file", ""),
        ("log_file", "   "),
    ],
)
def test_save_out_of_range_rejected(
    config_manager: ConfigManager, attr_name: str, out_of_range_value: Any
) -> None:
    """CFG-B2-01: save() rejeita valores fora dos intervalos ou conjuntos permitidos."""
    cfg = AppConfig()
    setattr(cfg, attr_name, out_of_range_value)

    res = config_manager.save(cfg)
    assert res is False


@pytest.mark.parametrize(
    "invalid_hotkeys",
    [
        {"play_pause": 123},
        {"next_track": None},
        {"volume_up": True},
        {"volume_down": ["ctrl", "down"]},
    ],
)
def test_save_invalid_hotkeys_rejected(
    config_manager: ConfigManager, invalid_hotkeys: dict[str, Any]
) -> None:
    """CFG-B2-01: save() rejeita atalhos conhecidos com valores não-string."""
    cfg = AppConfig(hotkeys=invalid_hotkeys)  # type: ignore[arg-type]
    assert config_manager.save(cfg) is False


@pytest.mark.parametrize(
    "invalid_triggers",
    [
        {"volume": "enabled"},
        {"metadata": 1},
        {"playback": None},
        {"volume": [True]},
    ],
)
def test_save_invalid_triggers_rejected(
    config_manager: ConfigManager, invalid_triggers: dict[str, Any]
) -> None:
    """CFG-B2-01: save() rejeita triggers conhecidos com valores não-booleanos."""
    cfg = AppConfig(triggers=invalid_triggers)  # type: ignore[arg-type]
    assert config_manager.save(cfg) is False


def test_save_non_dataclass_rejected(config_manager: ConfigManager) -> None:
    """CFG-B2-01: save() rejeita objetos que não são instâncias de AppConfig."""
    assert config_manager.save("invalido") is False  # type: ignore[arg-type]
    assert config_manager.save({"volume_step": 10}) is False  # type: ignore[arg-type]
    assert config_manager.save(12345) is False  # type: ignore[arg-type]


def test_save_recovery_after_validation_failure(config_manager: ConfigManager) -> None:
    """CFG-B2-01: Permite tentativa posterior com dados corrigidos após falha de validação."""
    invalid_cfg = AppConfig(volume_step=-50)
    assert config_manager.save(invalid_cfg) is False

    valid_cfg = AppConfig(volume_step=20)
    assert config_manager.save(valid_cfg) is True
    assert config_manager.config.volume_step == 20


# ------------------------------------------------------------------------------
# Grupo B: Integridade e Estado
# ------------------------------------------------------------------------------


def test_save_validation_failure_preserves_disk_file(config_manager: ConfigManager) -> None:
    """CFG-B2-02: Falha de validação em save() não modifica o arquivo válido em disco."""
    initial_cfg = AppConfig(volume_step=10, websocket_port=9000)
    assert config_manager.save(initial_cfg) is True

    with open(config_manager.config_path, "rb") as f:
        disk_bytes_before = f.read()

    invalid_cfg = AppConfig(volume_step=999)
    res = config_manager.save(invalid_cfg)
    assert res is False

    with open(config_manager.config_path, "rb") as f:
        disk_bytes_after = f.read()

    assert disk_bytes_after == disk_bytes_before


def test_save_validation_failure_does_not_mutate_self_config(
    config_manager: ConfigManager,
) -> None:
    """CFG-B2-02: save(new_invalid_config) não atualiza a referência nem o estado de self.config."""
    initial_cfg = AppConfig(volume_step=12)
    assert config_manager.save(initial_cfg) is True
    assert config_manager.config.volume_step == 12

    invalid_cfg = AppConfig(volume_step=-99)
    res = config_manager.save(invalid_cfg)
    assert res is False

    # self.config preservou a configuração válida anterior
    assert config_manager.config.volume_step == 12
    assert config_manager.config is not invalid_cfg


def test_save_validation_failure_leaves_no_temporary_files(
    config_manager: ConfigManager,
) -> None:
    """CFG-B2-02: Falha de validação aborta antes da criação de arquivos temporários."""
    parent_dir = Path(config_manager.config_path).parent

    invalid_cfg = AppConfig(websocket_port=99999)
    res = config_manager.save(invalid_cfg)
    assert res is False

    tmp_files = list(parent_dir.glob(".*.tmp.*"))
    assert tmp_files == []


def test_save_normalization_consistency(config_manager: ConfigManager) -> None:
    """CFG-B2-01: Normalização de campos (ex.: log_level) é consistente em memória e disco."""
    cfg = AppConfig(log_level="debug")
    assert config_manager.save(cfg) is True

    # Em memória
    assert config_manager.config.log_level == "DEBUG"

    # Em disco
    with open(config_manager.config_path, encoding="utf-8") as f:
        disk_data = json.load(f)
    assert disk_data["log_level"] == "DEBUG"


# ------------------------------------------------------------------------------
# Grupo C: Compatibilidade de Hotkeys, Triggers e Campos Desconhecidos
# ------------------------------------------------------------------------------


def test_save_preserves_empty_hotkeys(config_manager: ConfigManager) -> None:
    """CFG-B2-03: hotkeys explicitamente vazio permanece vazio após save() e reload()."""
    cfg = AppConfig(hotkeys={})
    assert config_manager.save(cfg) is True
    assert config_manager.config.hotkeys == {}

    reloaded = config_manager.reload()
    assert reloaded.hotkeys == {}


def test_save_preserves_partial_hotkeys(config_manager: ConfigManager) -> None:
    """CFG-B2-03: hotkeys parcial preserva ações configuradas sem ativar ausentes."""
    cfg = AppConfig(hotkeys={"play_pause": "ctrl+p"})
    assert config_manager.save(cfg) is True
    assert config_manager.config.hotkeys == {"play_pause": "ctrl+p"}

    with open(config_manager.config_path, encoding="utf-8") as f:
        disk_data = json.load(f)
    assert disk_data["hotkeys"] == {"play_pause": "ctrl+p"}

    reloaded = config_manager.reload()
    assert reloaded.hotkeys == {"play_pause": "ctrl+p"}


def test_save_preserves_explicitly_disabled_hotkey(config_manager: ConfigManager) -> None:
    """CFG-B2-03: Atalho configurado como vazio string ("") representa desativação explícita."""
    cfg = AppConfig(hotkeys={"play_pause": "", "mute": "alt gr+m"})
    assert config_manager.save(cfg) is True
    assert config_manager.config.hotkeys == {"play_pause": "", "mute": "alt gr+m"}

    reloaded = config_manager.reload()
    assert reloaded.hotkeys == {"play_pause": "", "mute": "alt gr+m"}


def test_save_preserves_partial_triggers(config_manager: ConfigManager) -> None:
    """CFG-B2-03: Triggers parciais mantêm opções explícitas e completam com defaults."""
    cfg = AppConfig(triggers={"volume": False})
    assert config_manager.save(cfg) is True

    expected = {"volume": False, "metadata": True, "playback": True}
    assert config_manager.config.triggers == expected

    reloaded = config_manager.reload()
    assert reloaded.triggers == expected


def test_save_investigation_unknown_fields_dropped_on_resave(tmp_path: Path) -> None:
    """CFG-B2-04: Confirma que campos desconhecidos são descartados no save completo."""
    cfg_file = tmp_path / "settings.json"
    raw_json = {
        "volume_step": 10,
        "future_option": {"enabled": True},
        "hotkeys": {
            "play_pause": "ctrl+p",
            "future_action": {"sequence": ["ctrl", "alt", "k"]},
        },
    }
    cfg_file.write_text(json.dumps(raw_json), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_file))
    assert mgr.last_load_succeeded is True
    assert mgr.config.volume_step == 10
    assert mgr.config.hotkeys == {"play_pause": "ctrl+p"}
    assert not hasattr(mgr.config, "future_option")

    # Modifica preferência e salva
    new_cfg = AppConfig(volume_step=15, hotkeys=mgr.config.hotkeys)
    assert mgr.save(new_cfg) is True

    # Verifica o que foi gravado em disco
    saved_data = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved_data["volume_step"] == 15
    assert saved_data["hotkeys"] == {"play_pause": "ctrl+p"}
    # Campos desconhecidos são descartados conforme schema estrito
    assert "future_option" not in saved_data
    assert "future_action" not in saved_data["hotkeys"]


# ------------------------------------------------------------------------------
# Grupo D: Interface e Preservação de Hotkeys Parciais
# ------------------------------------------------------------------------------


class _DummyControl:
    """Controle genérico para simular abas e componentes da interface Flet."""

    def __init__(self, value: Any = None, data: Any = None) -> None:
        self.value = value
        self.data = data


class _DummyTab:
    """Tab simulado contendo dicionário de controles em .data."""

    def __init__(self, data_dict: dict[str, Any]) -> None:
        self.data = data_dict


def test_ui_save_settings_from_controls_validation_failure(
    config_manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG-B2-05: Falha de validação na UI comunica erro no status e não aciona autostart.sync."""
    sync_mock = MagicMock()
    monkeypatch.setattr(autostart, "sync", sync_mock)

    tab_gen = _DummyTab(
        {
            "volume_step": _DummyControl(value="-50"),  # Inválido
            "hud_display_time": _DummyControl(value="3"),
            "start_with_windows": _DummyControl(data=True),
            "start_with_windows_elevated": _DummyControl(data=False),
        }
    )
    tab_hot = _DummyTab({"play_pause": _DummyControl(value="ctrl+p", data=True)})
    tab_lay = _DummyTab(
        {
            "hud_monitor": _DummyControl(value="0"),
            "hud_position": _DummyControl(value="bottom_right"),
            "volume": _DummyControl(data=True),
            "metadata": _DummyControl(data=True),
            "playback": _DummyControl(data=True),
        }
    )
    tab_int = _DummyTab({"spotify_integration": _DummyControl(data=False)})
    tab_adv = _DummyTab(
        {
            "websocket_port": _DummyControl(value="8974"),
            "log_level": _DummyControl(value="INFO"),
            "log_file": _DummyControl(value="wyrmplayer.log"),
        }
    )

    status_text = theme.mono("aguardando")
    status_dot = theme.diamond(5, theme.POSITIVE)

    res = save_settings_from_controls(
        config_manager=config_manager,
        tab_gen=tab_gen,
        tab_hot=tab_hot,
        tab_lay=tab_lay,
        tab_int=tab_int,
        tab_adv=tab_adv,
        status_text=status_text,
        status_dot=status_dot,
        page=None,
    )

    assert res is False
    assert status_text.color == theme.DANGER
    assert status_dot.bgcolor == theme.DANGER
    assert "erro" in str(status_text.value).lower()
    sync_mock.assert_not_called()


def test_ui_save_settings_from_controls_success(
    config_manager: ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG-B2-05: Salvamento válido atualiza status para positivo e sincroniza autostart."""
    sync_mock = MagicMock()
    monkeypatch.setattr(autostart, "sync", sync_mock)

    tab_gen = _DummyTab(
        {
            "volume_step": _DummyControl(value="15"),
            "hud_display_time": _DummyControl(value="4"),
            "start_with_windows": _DummyControl(data=True),
            "start_with_windows_elevated": _DummyControl(data=False),
        }
    )
    tab_hot = _DummyTab({"play_pause": _DummyControl(value="ctrl+p", data=True)})
    tab_lay = _DummyTab(
        {
            "hud_monitor": _DummyControl(value="0"),
            "hud_position": _DummyControl(value="bottom_right"),
            "volume": _DummyControl(data=True),
            "metadata": _DummyControl(data=True),
            "playback": _DummyControl(data=True),
        }
    )
    tab_int = _DummyTab({"spotify_integration": _DummyControl(data=False)})
    tab_adv = _DummyTab(
        {
            "websocket_port": _DummyControl(value="8974"),
            "log_level": _DummyControl(value="INFO"),
            "log_file": _DummyControl(value="wyrmplayer.log"),
        }
    )

    status_text = theme.mono("aguardando")
    status_dot = theme.diamond(5, theme.DANGER)

    res = save_settings_from_controls(
        config_manager=config_manager,
        tab_gen=tab_gen,
        tab_hot=tab_hot,
        tab_lay=tab_lay,
        tab_int=tab_int,
        tab_adv=tab_adv,
        status_text=status_text,
        status_dot=status_dot,
        page=None,
    )

    assert res is True
    assert status_text.color == theme.POSITIVE
    assert status_dot.bgcolor == theme.POSITIVE
    assert "ativo" in str(status_text.value).lower()
    sync_mock.assert_called_once_with(True, elevated=False)


def test_ui_preserves_partial_hotkeys_when_editing_other_setting(tmp_path: Path) -> None:
    """CFG-B2-03: Alterar volume_step preserva hotkeys parciais sem ativar ausentes."""
    from src.ui.components.settings.advanced_tab import advanced_tab
    from src.ui.components.settings.general_tab import general_tab
    from src.ui.components.settings.hotkeys_tab import hotkeys_tab
    from src.ui.components.settings.integrations_tab import integrations_tab
    from src.ui.components.settings.layout_tab import layout_tab

    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text(
        json.dumps({"volume_step": 10, "hotkeys": {"play_pause": "ctrl+p"}}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_file))
    initial_cfg = mgr.load()
    assert initial_cfg.volume_step == 10
    assert initial_cfg.hotkeys == {"play_pause": "ctrl+p"}

    status_text = theme.mono("")

    def dummy_cb() -> None:
        pass

    tab_gen = general_tab(initial_cfg, lambda e: None)
    tab_hot = hotkeys_tab(initial_cfg, dummy_cb, status_text)
    tab_lay = layout_tab(initial_cfg, lambda e: None)
    tab_int = integrations_tab(initial_cfg, lambda e: None, None)
    tab_adv = advanced_tab(initial_cfg, lambda e: None)

    # Simula usuário alterando volume_step de 10 para 15
    g_data = getattr(tab_gen, "data", {})
    g_data["volume_step"].value = 15

    # Executa salvamento real dos controles
    saved = save_settings_from_controls(
        config_manager=mgr,
        tab_gen=tab_gen,
        tab_hot=tab_hot,
        tab_lay=tab_lay,
        tab_int=tab_int,
        tab_adv=tab_adv,
        status_text=status_text,
        status_dot=theme.diamond(5, theme.POSITIVE),
        page=None,
    )
    assert saved is True

    # Verifica o arquivo persistido no disco
    saved_json = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved_json["volume_step"] == 15
    # Hotkeys continuam exatamente parciais {"play_pause": "ctrl+p"},
    # nenhuma chave vazia ou default foi adicionada!
    assert saved_json["hotkeys"] == {"play_pause": "ctrl+p"}


def test_ui_preserves_empty_hotkeys_when_editing_other_setting(tmp_path: Path) -> None:
    """CFG-B2-03: hotkeys vazio permanece vazio após editar volume_step."""
    from src.ui.components.settings.advanced_tab import advanced_tab
    from src.ui.components.settings.general_tab import general_tab
    from src.ui.components.settings.hotkeys_tab import hotkeys_tab
    from src.ui.components.settings.integrations_tab import integrations_tab
    from src.ui.components.settings.layout_tab import layout_tab

    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text(
        json.dumps({"volume_step": 10, "hotkeys": {}}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_file))
    initial_cfg = mgr.load()
    assert initial_cfg.hotkeys == {}

    status_text = theme.mono("")
    tab_gen = general_tab(initial_cfg, lambda e: None)
    tab_hot = hotkeys_tab(initial_cfg, lambda: None, status_text)
    tab_lay = layout_tab(initial_cfg, lambda e: None)
    tab_int = integrations_tab(initial_cfg, lambda e: None, None)
    tab_adv = advanced_tab(initial_cfg, lambda e: None)

    g_data = getattr(tab_gen, "data", {})
    g_data["volume_step"].value = 25

    saved = save_settings_from_controls(
        config_manager=mgr,
        tab_gen=tab_gen,
        tab_hot=tab_hot,
        tab_lay=tab_lay,
        tab_int=tab_int,
        tab_adv=tab_adv,
        status_text=status_text,
        status_dot=theme.diamond(5, theme.POSITIVE),
        page=None,
    )
    assert saved is True

    saved_json = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved_json["volume_step"] == 25
    assert saved_json["hotkeys"] == {}


def test_ui_records_new_hotkey_alongside_existing(tmp_path: Path) -> None:
    """CFG-B2-03: Cenário - Gravar nova ação inclui apenas ela junto às já configuradas."""
    from src.ui.components.settings.advanced_tab import advanced_tab
    from src.ui.components.settings.general_tab import general_tab
    from src.ui.components.settings.hotkeys_tab import hotkeys_tab
    from src.ui.components.settings.integrations_tab import integrations_tab
    from src.ui.components.settings.layout_tab import layout_tab

    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text(
        json.dumps({"volume_step": 10, "hotkeys": {"play_pause": "ctrl+p"}}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_file))
    initial_cfg = mgr.load()

    status_text = theme.mono("")
    tab_gen = general_tab(initial_cfg, lambda e: None)
    tab_hot = hotkeys_tab(initial_cfg, lambda: None, status_text)
    tab_lay = layout_tab(initial_cfg, lambda e: None)
    tab_int = integrations_tab(initial_cfg, lambda e: None, None)
    tab_adv = advanced_tab(initial_cfg, lambda e: None)

    # Simula gravação de atalho para next_track
    h_fields = getattr(tab_hot, "data", {})
    h_fields["next_track"].value = "ctrl+right"
    h_fields["next_track"].data = True

    saved = save_settings_from_controls(
        config_manager=mgr,
        tab_gen=tab_gen,
        tab_hot=tab_hot,
        tab_lay=tab_lay,
        tab_int=tab_int,
        tab_adv=tab_adv,
        status_text=status_text,
        status_dot=theme.diamond(5, theme.POSITIVE),
        page=None,
    )
    assert saved is True

    saved_json = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved_json["hotkeys"] == {"play_pause": "ctrl+p", "next_track": "ctrl+right"}


def test_ui_clearing_hotkey_marks_it_explicitly_disabled(tmp_path: Path) -> None:
    """CFG-B2-03: Cenário - Limpar atalho existente define como desativado explicitamente ("")."""
    from src.ui.components.settings.advanced_tab import advanced_tab
    from src.ui.components.settings.general_tab import general_tab
    from src.ui.components.settings.hotkeys_tab import hotkeys_tab
    from src.ui.components.settings.integrations_tab import integrations_tab
    from src.ui.components.settings.layout_tab import layout_tab

    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text(
        json.dumps({"volume_step": 10, "hotkeys": {"play_pause": "ctrl+p", "mute": "alt gr+m"}}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_file))
    initial_cfg = mgr.load()

    status_text = theme.mono("")
    tab_gen = general_tab(initial_cfg, lambda e: None)
    tab_hot = hotkeys_tab(initial_cfg, lambda: None, status_text)
    tab_lay = layout_tab(initial_cfg, lambda e: None)
    tab_int = integrations_tab(initial_cfg, lambda e: None, None)
    tab_adv = advanced_tab(initial_cfg, lambda e: None)

    # Simula limpeza de play_pause
    h_fields = getattr(tab_hot, "data", {})
    h_fields["play_pause"].value = ""
    h_fields["play_pause"].data = True

    saved = save_settings_from_controls(
        config_manager=mgr,
        tab_gen=tab_gen,
        tab_hot=tab_hot,
        tab_lay=tab_lay,
        tab_int=tab_int,
        tab_adv=tab_adv,
        status_text=status_text,
        status_dot=theme.diamond(5, theme.POSITIVE),
        page=None,
    )
    assert saved is True

    saved_json = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert saved_json["hotkeys"] == {"play_pause": "", "mute": "alt gr+m"}


# ------------------------------------------------------------------------------
# Grupo E: Round-Trip Completo de Configurações
# ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "initial_payload", "mutation_fn", "expected_verify_fn"),
    [
        (
            "Caso A: Configuração completa",
            {
                "volume_step": 10,
                "hud_display_time": 4,
                "websocket_port": 9100,
                "hud_monitor": 2,
                "hud_position": "top_left",
                "log_level": "WARNING",
                "log_file": "test.log",
                "spotify_integration": True,
                "start_with_windows": True,
                "start_with_windows_elevated": True,
                "hotkeys": {
                    "play_pause": "ctrl+1",
                    "next_track": "ctrl+2",
                    "previous_track": "ctrl+3",
                    "volume_up": "ctrl+4",
                    "volume_down": "ctrl+5",
                    "mute": "ctrl+6",
                },
                "triggers": {"volume": False, "metadata": True, "playback": False},
            },
            lambda cfg: setattr(cfg, "volume_step", 20),
            lambda loaded: (
                loaded.volume_step == 20
                and loaded.hud_position == "top_left"
                and loaded.hotkeys["play_pause"] == "ctrl+1"
                and loaded.triggers["playback"] is False
            ),
        ),
        (
            "Caso B: Configuração antiga (apenas volume_step e websocket_port)",
            {"volume_step": 8, "websocket_port": 8980},
            lambda cfg: setattr(cfg, "volume_step", 12),
            lambda loaded: (
                loaded.volume_step == 12
                and loaded.websocket_port == 8980
                and loaded.log_level == "INFO"
            ),
        ),
        (
            "Caso C: Hotkeys explicitamente vazias",
            {"volume_step": 10, "hotkeys": {}},
            lambda cfg: setattr(cfg, "volume_step", 15),
            lambda loaded: loaded.volume_step == 15 and loaded.hotkeys == {},
        ),
        (
            "Caso D: Hotkeys parciais",
            {"volume_step": 10, "hotkeys": {"play_pause": "ctrl+alt+p"}},
            lambda cfg: setattr(cfg, "volume_step", 18),
            lambda loaded: (
                loaded.volume_step == 18 and loaded.hotkeys == {"play_pause": "ctrl+alt+p"}
            ),
        ),
        (
            "Caso E: Hotkeys desativadas por string vazia",
            {"volume_step": 10, "hotkeys": {"play_pause": "", "mute": ""}},
            lambda cfg: setattr(cfg, "volume_step", 16),
            lambda loaded: (
                loaded.volume_step == 16 and loaded.hotkeys == {"play_pause": "", "mute": ""}
            ),
        ),
        (
            "Caso F: Triggers parciais",
            {"triggers": {"volume": False}},
            lambda cfg: setattr(cfg, "hud_display_time", 6),
            lambda loaded: (
                loaded.hud_display_time == 6
                and loaded.triggers == {"volume": False, "metadata": True, "playback": True}
            ),
        ),
        (
            "Caso G: Campos desconhecidos (tolerados no load)",
            {
                "volume_step": 10,
                "unknown_legacy_opt": "custom",
                "hotkeys": {"play_pause": "ctrl+p"},
            },
            lambda cfg: setattr(cfg, "volume_step", 22),
            lambda loaded: loaded.volume_step == 22 and loaded.hotkeys == {"play_pause": "ctrl+p"},
        ),
    ],
)
def test_roundtrip_scenarios(
    tmp_path: Path,
    description: str,
    initial_payload: dict[str, Any],
    mutation_fn: Any,
    expected_verify_fn: Any,
) -> None:
    """CFG-B2-07: Round-trip completo carrega, modifica, salva, reinstancia e recarrega."""
    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text(json.dumps(initial_payload), encoding="utf-8")

    mgr1 = ConfigManager(config_file=str(cfg_file))
    assert mgr1.last_load_succeeded is True
    cfg = mgr1.config

    # Aplica mutação
    mutation_fn(cfg)
    assert mgr1.save(cfg) is True

    # Reinstancia em um segundo ConfigManager isolado
    mgr2 = ConfigManager(config_file=str(cfg_file))
    assert mgr2.last_load_succeeded is True
    loaded = mgr2.config

    assert expected_verify_fn(loaded), f"Falha na verificação de roundtrip para: {description}"


def test_roundtrip_case_h_invalid_values_not_persisted(tmp_path: Path) -> None:
    """CFG-B2-07: Caso H - Valores inválidos são rejeitados e não substituem o arquivo."""
    cfg_file = tmp_path / "settings.json"
    initial_content = {"volume_step": 10, "websocket_port": 8974}
    cfg_file.write_text(json.dumps(initial_content), encoding="utf-8")

    mgr = ConfigManager(config_file=str(cfg_file))
    assert mgr.last_load_succeeded is True

    # Tenta salvar configuração inválida
    invalid_candidate = AppConfig(volume_step=-100)
    assert mgr.save(invalid_candidate) is False

    # Novo ConfigManager recarrega o arquivo original íntegro
    mgr2 = ConfigManager(config_file=str(cfg_file))
    assert mgr2.config.volume_step == 10


def test_integration_hotkey_manager_does_not_register_unconfigured_hotkeys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG-B2-03: HotkeyManager real não registra atalhos ausentes de hotkeys parciais."""
    from src.core.hotkeys import HotkeyManager
    from tests.test_hotkeys import FakeKeyboard

    cfg_file = tmp_path / "settings.json"
    cfg_file.write_text(
        json.dumps({"hotkeys": {"play_pause": "ctrl+p"}}),
        encoding="utf-8",
    )

    mgr = ConfigManager(config_file=str(cfg_file))
    assert mgr.config.hotkeys == {"play_pause": "ctrl+p"}

    fake_kb = FakeKeyboard()
    monkeypatch.setattr("src.core.hotkeys.keyboard.add_hotkey", fake_kb.add_hotkey)
    monkeypatch.setattr("src.core.hotkeys.keyboard.remove_hotkey", fake_kb.remove_hotkey)

    controller_mock = MagicMock()
    hk_mgr = HotkeyManager(controller=controller_mock, config=mgr)
    hk_mgr.setup()

    registered_shortcuts = [hk for hk, _ in fake_kb.registered_hotkeys.values()]
    # play_pause e suas variantes foram registradas
    assert any("p" in s for s in registered_shortcuts)
    # Ações não configuradas (next_track, mute, volume_up) NÃO foram registradas
    assert not any("right" in s for s in registered_shortcuts)
    assert not any("left" in s for s in registered_shortcuts)
    assert not any("up" in s for s in registered_shortcuts)
    assert not any("down" in s for s in registered_shortcuts)
    assert not any("m" in s for s in registered_shortcuts)
