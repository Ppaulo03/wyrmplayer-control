from unittest.mock import MagicMock

from src.core import autostart


def test_get_startup_command_dev_mode_uses_no_admin_relaunch():
    command = autostart.get_startup_command()
    assert "main.py" in command
    assert "--no-admin-relaunch" in command


def test_is_enabled_true(monkeypatch):
    fake_key = MagicMock()
    fake_key.__enter__.return_value = fake_key
    fake_key.__exit__.return_value = False
    monkeypatch.setattr(autostart.winreg, "OpenKey", MagicMock(return_value=fake_key))
    monkeypatch.setattr(autostart.winreg, "QueryValueEx", MagicMock(return_value=("cmd", 1)))

    assert autostart.is_enabled() is True


def test_is_enabled_false_when_missing(monkeypatch):
    def _raise(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(autostart.winreg, "OpenKey", _raise)

    assert autostart.is_enabled() is False


def test_enable_writes_registry_value(monkeypatch):
    fake_key = MagicMock()
    fake_key.__enter__.return_value = fake_key
    fake_key.__exit__.return_value = False
    create_key_mock = MagicMock(return_value=fake_key)
    set_value_mock = MagicMock()
    monkeypatch.setattr(autostart.winreg, "CreateKeyEx", create_key_mock)
    monkeypatch.setattr(autostart.winreg, "SetValueEx", set_value_mock)

    assert autostart.enable() is True
    set_value_mock.assert_called_once()
    assert set_value_mock.call_args[0][1] == autostart.REGISTRY_VALUE_NAME


def test_disable_deletes_registry_value(monkeypatch):
    fake_key = MagicMock()
    fake_key.__enter__.return_value = fake_key
    fake_key.__exit__.return_value = False
    monkeypatch.setattr(autostart.winreg, "OpenKey", MagicMock(return_value=fake_key))
    delete_value_mock = MagicMock()
    monkeypatch.setattr(autostart.winreg, "DeleteValue", delete_value_mock)

    assert autostart.disable() is True
    delete_value_mock.assert_called_once()


def test_disable_missing_value_is_treated_as_success(monkeypatch):
    def _raise(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(autostart.winreg, "OpenKey", _raise)

    assert autostart.disable() is True


def test_sync_enables_when_needed(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)
    enable_mock = MagicMock()
    monkeypatch.setattr(autostart, "enable", enable_mock)
    disable_mock = MagicMock()
    monkeypatch.setattr(autostart, "disable", disable_mock)

    autostart.sync(True)

    enable_mock.assert_called_once()
    disable_mock.assert_not_called()


def test_sync_disables_when_needed(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda: True)
    enable_mock = MagicMock()
    monkeypatch.setattr(autostart, "enable", enable_mock)
    disable_mock = MagicMock()
    monkeypatch.setattr(autostart, "disable", disable_mock)

    autostart.sync(False)

    disable_mock.assert_called_once()
    enable_mock.assert_not_called()


def test_sync_no_op_when_already_matching(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda: True)
    enable_mock = MagicMock()
    monkeypatch.setattr(autostart, "enable", enable_mock)
    disable_mock = MagicMock()
    monkeypatch.setattr(autostart, "disable", disable_mock)

    autostart.sync(True)

    enable_mock.assert_not_called()
    disable_mock.assert_not_called()
