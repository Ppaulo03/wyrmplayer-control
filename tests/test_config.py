import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core import autostart
from src.core.config import AppConfig, ConfigManager
from src.ui import theme


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
# EST-02.1: Testes de Persistência Atômica do settings.json
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


def test_ui_settings_consumer_handles_save_failure_and_success(
    config_manager: ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cenário I: Interface reflete erro sem falsos sucessos e sem efeitos colaterais."""
    # Simula componentes de interface da tela de configurações
    status_text = theme.mono("salvamento automático ativo", size=10.5, color=theme.POSITIVE)
    status_dot = theme.diamond(5, theme.POSITIVE)
    page_mock = MagicMock()

    sync_calls: list[tuple[bool, bool]] = []

    def mock_sync(enabled: bool, elevated: bool = False) -> None:
        sync_calls.append((enabled, elevated))

    monkeypatch.setattr(autostart, "sync", mock_sync)

    # Função representativa do save_settings em src/ui/settings.py
    def simulate_save_settings(target_cfg: AppConfig) -> bool:
        saved = config_manager.save(target_cfg)
        if not saved:
            status_text.value = "erro ao salvar configurações"
            status_text.color = theme.DANGER
            status_dot.bgcolor = theme.DANGER
            page_mock.update()
            return False

        autostart.sync(
            target_cfg.start_with_windows, elevated=target_cfg.start_with_windows_elevated
        )
        status_text.value = "salvamento automático ativo"
        status_text.color = theme.POSITIVE
        status_dot.bgcolor = theme.POSITIVE
        page_mock.update()
        return True

    real_replace = os.replace
    should_fail_replace = True

    def conditional_replace(src: str, dst: str) -> None:
        if should_fail_replace:
            raise OSError("Falha no disco")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", conditional_replace)

    # 1. Simula falha na gravação
    bad_cfg = AppConfig(volume_step=99, start_with_windows=True)
    res_fail = simulate_save_settings(bad_cfg)

    assert res_fail is False
    # Interface indica erro
    assert status_text.value == "erro ao salvar configurações"
    assert status_text.color == theme.DANGER
    assert status_dot.bgcolor == theme.DANGER
    # Efeito colateral de autostart NÃO foi aplicado
    assert len(sync_calls) == 0

    # 2. Simula recuperação com sucesso
    should_fail_replace = False
    good_cfg = AppConfig(volume_step=10, start_with_windows=True, start_with_windows_elevated=False)
    res_ok = simulate_save_settings(good_cfg)

    assert res_ok is True
    # Interface indica sucesso
    assert status_text.value == "salvamento automático ativo"
    assert status_text.color == theme.POSITIVE
    assert status_dot.bgcolor == theme.POSITIVE
    # Efeito colateral foi aplicado
    assert sync_calls == [(True, False)]
