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

    monkeypatch.setattr(spotify_setup, "find_spotify_executable", lambda: None)
    monkeypatch.setattr(spotify_setup, "is_spotify_microsoft_store", lambda: False)
    monkeypatch.setattr(spotify_setup, "find_spicetify_executable", lambda: fake_spicetify)
    monkeypatch.setattr(spotify_setup, "get_spicetify_config_path", lambda: config_path)

    status = spotify_setup.check_status(websocket_port=spotify_setup.WEBNOWPLAYING_PORT)

    assert status.spicetify_installed is True
    assert status.extension_enabled is True
    assert status.port_matches is True
    assert status.ready is True


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


def test_run_startup_check_skips_configure_when_elevated(monkeypatch, tmp_path: Path) -> None:
    """Nunca chama configure_extension sem consentimento explícito enquanto elevado."""
    fake_spicetify = tmp_path / "spicetify.exe"
    fake_spicetify.touch()
    config_path = tmp_path / "config-xpui.ini"
    config_path.write_text("[AdditionalOptions]\nextensions = \n", encoding="utf-8")

    monkeypatch.setattr(spotify_setup, "find_spotify_executable", lambda: None)
    monkeypatch.setattr(spotify_setup, "is_spotify_microsoft_store", lambda: False)
    monkeypatch.setattr(spotify_setup, "find_spicetify_executable", lambda: fake_spicetify)
    monkeypatch.setattr(spotify_setup, "get_spicetify_config_path", lambda: config_path)
    monkeypatch.setattr(spotify_setup.win32, "is_process_elevated", lambda: True)

    def _fail_if_called(*_args: object, **_kwargs: object) -> bool:
        raise AssertionError("configure_extension não deveria ser chamado quando elevado")

    monkeypatch.setattr(spotify_setup, "configure_extension", _fail_if_called)

    status = spotify_setup.run_startup_check(websocket_port=spotify_setup.WEBNOWPLAYING_PORT)

    assert status.extension_enabled is False
