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
    monkeypatch.setattr(spotify_setup, "find_spicetify_executable", lambda: fake_spicetify)
    monkeypatch.setattr(spotify_setup, "get_spicetify_config_path", lambda: config_path)

    status = spotify_setup.check_status(websocket_port=spotify_setup.WEBNOWPLAYING_PORT)

    assert status.spicetify_installed is True
    assert status.extension_enabled is True
    assert status.port_matches is True
    assert status.ready is True
