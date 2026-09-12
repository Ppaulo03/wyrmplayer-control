import subprocess
from pathlib import Path

from src.services import spotify_setup


def test_is_extension_enabled_true(tmp_path: Path) -> None:
    config_path = tmp_path / "config-xpui.ini"
    config_path.write_text(
        "[AdditionalOptions]\nextensions = fullAppDisplay.js|webnowplaying.js\n",
        encoding="utf-8",
    )
    assert spotify_setup.is_extension_enabled(config_path) is True


def test_is_extension_enabled_false_when_missing(tmp_path: Path) -> None:
    config_path = tmp_path / "config-xpui.ini"
    config_path.write_text(
        "[AdditionalOptions]\nextensions = fullAppDisplay.js\n",
        encoding="utf-8",
    )
    assert spotify_setup.is_extension_enabled(config_path) is False


def test_is_extension_enabled_missing_file(tmp_path: Path) -> None:
    assert spotify_setup.is_extension_enabled(tmp_path / "does-not-exist.ini") is False


def test_is_extension_enabled_none_path() -> None:
    assert spotify_setup.is_extension_enabled(None) is False


def test_is_extension_enabled_missing_section(tmp_path: Path) -> None:
    config_path = tmp_path / "config-xpui.ini"
    config_path.write_text("[OtherSection]\nfoo = bar\n", encoding="utf-8")
    assert spotify_setup.is_extension_enabled(config_path) is False


def test_check_status_port_mismatch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(spotify_setup, "find_spotify_executable", lambda: None)
    monkeypatch.setattr(spotify_setup, "is_spotify_microsoft_store", lambda: False)
    monkeypatch.setattr(spotify_setup, "find_spicetify_executable", lambda: None)
    monkeypatch.setattr(spotify_setup, "get_spicetify_config_path", lambda: None)

    status = spotify_setup.check_status(websocket_port=9999)

    assert status.spicetify_installed is False
    assert status.port_matches is False
    assert status.ready is False


def test_check_status_ready(monkeypatch, tmp_path: Path) -> None:
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()
    config_path = tmp_path / "config-xpui.ini"
    config_path.write_text("[AdditionalOptions]\nextensions = webnowplaying.js\n", encoding="utf-8")
    extensions_dir = tmp_path / "Extensions"
    extensions_dir.mkdir()
    (extensions_dir / "webnowplaying.js").touch()

    monkeypatch.setattr(spotify_setup, "find_spotify_executable", lambda: None)
    monkeypatch.setattr(spotify_setup, "is_spotify_microsoft_store", lambda: False)
    monkeypatch.setattr(spotify_setup, "find_spicetify_executable", lambda: fake_spicetify)
    monkeypatch.setattr(spotify_setup, "get_spicetify_config_path", lambda: config_path)
    # extensions_dir é derivada de find_spicetify_executable() (mockado acima
    # para fake_spicetify) via spicetify_path.parent / "Extensions" — não
    # precisa mockar separadamente.

    status = spotify_setup.check_status(websocket_port=spotify_setup.WEBNOWPLAYING_PORT)

    assert status.spicetify_installed is True
    assert status.extension_enabled is True
    assert status.extension_file_present is True
    assert status.port_matches is True
    assert status.ready is True


def test_check_status_not_ready_when_extension_file_missing(monkeypatch, tmp_path: Path) -> None:
    """
    Registrar o nome no config (is_extension_enabled) não basta: se o arquivo
    webnowplaying.js não existe de fato na pasta de extensões, a integração
    nunca conecta — 'ready' precisa refletir isso.
    """
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()
    config_path = tmp_path / "config-xpui.ini"
    config_path.write_text("[AdditionalOptions]\nextensions = webnowplaying.js\n", encoding="utf-8")
    extensions_dir = tmp_path / "Extensions"
    extensions_dir.mkdir()  # pasta existe, mas vazia — arquivo não foi instalado

    monkeypatch.setattr(spotify_setup, "find_spotify_executable", lambda: None)
    monkeypatch.setattr(spotify_setup, "is_spotify_microsoft_store", lambda: False)
    monkeypatch.setattr(spotify_setup, "find_spicetify_executable", lambda: fake_spicetify)
    monkeypatch.setattr(spotify_setup, "get_spicetify_config_path", lambda: config_path)
    # extensions_dir é derivada de find_spicetify_executable() (mockado acima
    # para fake_spicetify) via spicetify_path.parent / "Extensions" — não
    # precisa mockar separadamente.

    status = spotify_setup.check_status(websocket_port=spotify_setup.WEBNOWPLAYING_PORT)

    assert status.extension_enabled is True
    assert status.extension_file_present is False
    assert status.ready is False


def test_check_status_microsoft_store_not_ready(monkeypatch, tmp_path: Path) -> None:
    """Mesmo com tudo mais certo, a versão da Microsoft Store nunca fica 'ready'."""
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()
    config_path = tmp_path / "config-xpui.ini"
    config_path.write_text("[AdditionalOptions]\nextensions = webnowplaying.js\n", encoding="utf-8")

    monkeypatch.setattr(spotify_setup, "find_spotify_executable", lambda: None)
    monkeypatch.setattr(spotify_setup, "is_spotify_microsoft_store", lambda: True)
    monkeypatch.setattr(spotify_setup, "find_spicetify_executable", lambda: fake_spicetify)
    monkeypatch.setattr(spotify_setup, "get_spicetify_config_path", lambda: config_path)

    status = spotify_setup.check_status(websocket_port=spotify_setup.WEBNOWPLAYING_PORT)

    assert status.spotify_is_microsoft_store is True
    assert status.ready is False


def test_is_spotify_microsoft_store_detects_package(monkeypatch, tmp_path: Path) -> None:
    packages_dir = tmp_path / "Packages"
    (packages_dir / f"{spotify_setup.SPOTIFY_STORE_PACKAGE_PREFIX}zpdnekdrzrea0").mkdir(
        parents=True
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert spotify_setup.is_spotify_microsoft_store() is True


def test_is_spotify_microsoft_store_false_when_absent(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "Packages").mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert spotify_setup.is_spotify_microsoft_store() is False


def test_run_startup_check_configures_without_admin_when_elevated(
    monkeypatch, tmp_path: Path
) -> None:
    """Quando elevado, configura mesmo assim, mas sempre pedindo avoid_admin=True."""
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()
    config_path = tmp_path / "config-xpui.ini"
    config_path.write_text("[AdditionalOptions]\nextensions = \n", encoding="utf-8")

    monkeypatch.setattr(spotify_setup, "find_spotify_executable", lambda: None)
    monkeypatch.setattr(spotify_setup, "is_spotify_microsoft_store", lambda: False)
    monkeypatch.setattr(spotify_setup, "find_spicetify_executable", lambda: fake_spicetify)
    monkeypatch.setattr(spotify_setup, "get_spicetify_config_path", lambda: config_path)
    monkeypatch.setattr(spotify_setup.win32, "is_process_elevated", lambda: True)

    calls: list[bool] = []

    def _record_call(_path: Path, *, avoid_admin: bool) -> bool:
        calls.append(avoid_admin)
        return True

    monkeypatch.setattr(spotify_setup, "configure_extension", _record_call)

    status = spotify_setup.run_startup_check(websocket_port=spotify_setup.WEBNOWPLAYING_PORT)

    assert calls == [True]

    assert status.extension_enabled is False


def test_apply_changes_success(monkeypatch, tmp_path: Path) -> None:
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()

    calls: list[list[str]] = []

    def _fake_run(command: list[str], *, avoid_admin: bool, timeout: float, on_output=None):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(spotify_setup, "_run_spicetify", _fake_run)

    assert spotify_setup.apply_changes(fake_spicetify) is True
    # Sempre checa/atualiza o Spicetify proativamente antes de tentar aplicar.
    assert calls == [
        [str(fake_spicetify), "update"],
        [str(fake_spicetify), "apply"],
    ]


def test_apply_changes_retries_with_backup_on_version_mismatch(monkeypatch, tmp_path: Path) -> None:
    """
    'spicetify apply' recusa quando o Spotify se atualizou sozinho e o backup do
    Spicetify ficou desatualizado — nesse caso específico, deve tentar
    'spicetify backup apply' automaticamente antes de desistir.
    """
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()

    calls: list[list[str]] = []
    mismatch_stdout = (
        "spicetify v2.43.2\n"
        " warning  Spotify version and backup version are mismatched.\n"
        ' info  Please run "spicetify backup apply"\n'
    )

    def _fake_run(command: list[str], *, avoid_admin: bool, timeout: float, on_output=None):
        calls.append(command)
        if command == [str(fake_spicetify), "apply"]:
            return subprocess.CompletedProcess(command, 1, mismatch_stdout, "")
        return subprocess.CompletedProcess(command, 0, "backed up and applied", "")

    monkeypatch.setattr(spotify_setup, "_run_spicetify", _fake_run)
    # Evita chamar taskkill/tasklist de verdade durante o teste.
    monkeypatch.setattr(spotify_setup, "_close_spotify_processes", lambda: None)

    assert spotify_setup.apply_changes(fake_spicetify) is True
    assert calls == [
        [str(fake_spicetify), "update"],
        [str(fake_spicetify), "apply"],
        [str(fake_spicetify), "backup", "apply"],
    ]


def test_apply_changes_retries_with_backup_when_never_backed_up(
    monkeypatch, tmp_path: Path
) -> None:
    """Mensagem diferente ('You haven't backed up'), mesmo remédio sugerido pelo Spicetify."""
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()

    calls: list[list[str]] = []
    never_backed_up_stdout = (
        'spicetify v2.43.2\n error  You haven\'t backed up. Run "spicetify backup apply"\n'
    )

    def _fake_run(command: list[str], *, avoid_admin: bool, timeout: float, on_output=None):
        calls.append(command)
        if command == [str(fake_spicetify), "apply"]:
            return subprocess.CompletedProcess(command, 1, never_backed_up_stdout, "")
        return subprocess.CompletedProcess(command, 0, "backed up and applied", "")

    monkeypatch.setattr(spotify_setup, "_run_spicetify", _fake_run)
    monkeypatch.setattr(spotify_setup, "_close_spotify_processes", lambda: None)

    assert spotify_setup.apply_changes(fake_spicetify) is True
    assert calls == [
        [str(fake_spicetify), "update"],
        [str(fake_spicetify), "apply"],
        [str(fake_spicetify), "backup", "apply"],
    ]


def test_apply_changes_fails_without_retry_on_other_errors(monkeypatch, tmp_path: Path) -> None:
    """Uma falha genérica (sem a marca de dessincronia) não deve disparar o retry."""
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()

    calls: list[list[str]] = []

    def _fake_run(command: list[str], *, avoid_admin: bool, timeout: float, on_output=None):
        calls.append(command)
        if command == [str(fake_spicetify), "apply"]:
            return subprocess.CompletedProcess(command, 1, "some other error", "boom")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(spotify_setup, "_run_spicetify", _fake_run)

    assert spotify_setup.apply_changes(fake_spicetify) is False
    assert calls == [
        [str(fake_spicetify), "update"],
        [str(fake_spicetify), "apply"],
    ]


def test_apply_changes_updates_spicetify_proactively_before_applying(
    monkeypatch, tmp_path: Path
) -> None:
    """
    Roda 'spicetify update' incondicionalmente antes do apply, não só reagindo
    a um erro de "version mismatch" — esse erro só aparece em certos cenários
    (ex.: patch de custom app), então um apply que só mexe em extensões podia
    "ter sucesso" sem nunca corrigir o Spicetify desatualizado.
    """
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()

    calls: list[list[str]] = []

    def _fake_run(command: list[str], *, avoid_admin: bool, timeout: float, on_output=None):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(spotify_setup, "_run_spicetify", _fake_run)

    assert spotify_setup.apply_changes(fake_spicetify) is True
    assert calls[0] == [str(fake_spicetify), "update"]
    assert calls[1] == [str(fake_spicetify), "apply"]


def test_apply_changes_reactively_updates_on_version_mismatch_during_apply(
    monkeypatch, tmp_path: Path
) -> None:
    """
    Mesmo com a checagem proativa, se o 'apply' em si ainda falhar com
    "version mismatch" (ex.: a checagem proativa não achou nada pra
    atualizar, mas o patch de um custom app específico falha mesmo assim),
    o retry reativo continua funcionando como rede de segurança.
    """
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()

    calls: list[list[str]] = []
    mismatch_stdout = (
        "spicetify v2.43.2\n"
        " error  Spotify version mismatch with Spicetify. Please report it on our "
        "github repository.\n"
        ' info  Please run "spicetify update" to check for a new version.\n'
    )
    apply_calls = 0

    def _fake_run(command: list[str], *, avoid_admin: bool, timeout: float, on_output=None):
        nonlocal apply_calls
        calls.append(command)
        if command == [str(fake_spicetify), "apply"]:
            apply_calls += 1
            if apply_calls == 1:
                return subprocess.CompletedProcess(command, 1, mismatch_stdout, "")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(spotify_setup, "_run_spicetify", _fake_run)

    assert spotify_setup.apply_changes(fake_spicetify) is True
    assert calls == [
        [str(fake_spicetify), "update"],  # checagem proativa
        [str(fake_spicetify), "apply"],  # falha com version mismatch
        [str(fake_spicetify), "update"],  # retry reativo
        [str(fake_spicetify), "apply"],  # sucesso
    ]


def test_apply_changes_chains_update_then_backup_apply(monkeypatch, tmp_path: Path) -> None:
    """
    Cenário observado na prática: mesmo depois da checagem proativa, o
    'apply' ainda pede backup (ex.: a atualização invalidou um backup que já
    existia) — deve encadear a recuperação reativa também, em vez de desistir.
    """
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()

    calls: list[list[str]] = []
    needs_backup_stdout = 'error  You haven\'t backed up. Run "spicetify backup apply"\n'
    apply_calls = 0

    def _fake_run(command: list[str], *, avoid_admin: bool, timeout: float, on_output=None):
        nonlocal apply_calls
        calls.append(command)
        if command == [str(fake_spicetify), "apply"]:
            apply_calls += 1
            if apply_calls == 1:
                return subprocess.CompletedProcess(command, 1, needs_backup_stdout, "")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(spotify_setup, "_run_spicetify", _fake_run)
    monkeypatch.setattr(spotify_setup, "_close_spotify_processes", lambda: None)

    assert spotify_setup.apply_changes(fake_spicetify) is True
    assert calls == [
        [str(fake_spicetify), "update"],  # checagem proativa
        [str(fake_spicetify), "apply"],  # falha, pede backup
        [str(fake_spicetify), "backup", "apply"],  # recupera
    ]
