"""genie.secrets: keyring-backed secret store with an injectable backend for tests."""
from __future__ import annotations

import json
import logging

import keyring.errors
import pytest

from genie import secrets


@pytest.fixture(autouse=True)
def fake_backend():
    store: dict[str, str] = {}
    secrets.set_backend_for_tests(store)
    yield store
    secrets.set_backend_for_tests(None)


def test_roundtrip_and_status(fake_backend):
    assert secrets.secret_status() == {"openrouter": False, "huggingface": False}
    assert secrets.get_secret("openrouter") is None
    secrets.set_secret("openrouter", "sk-or-abc")
    assert secrets.get_secret("openrouter") == "sk-or-abc"
    assert secrets.secret_status() == {"openrouter": True, "huggingface": False}
    secrets.delete_secret("openrouter")
    assert secrets.get_secret("openrouter") is None
    # deleting a missing secret is a no-op
    secrets.delete_secret("openrouter")


def test_unknown_name_rejected():
    with pytest.raises(ValueError):
        secrets.set_secret("aws", "x")
    with pytest.raises(ValueError):
        secrets.get_secret("aws")


def test_empty_value_rejected():
    with pytest.raises(ValueError):
        secrets.set_secret("openrouter", "   ")


def test_no_keyring_falls_back_to_file_store(monkeypatch, caplog, genie_home):
    secrets.set_backend_for_tests(None)  # use the "real" path

    class Broken:
        def get_password(self, service, name):
            raise keyring.errors.NoKeyringError("no backend")

        def set_password(self, service, name, value):
            raise keyring.errors.NoKeyringError("no backend")

        def delete_password(self, service, name):
            raise keyring.errors.NoKeyringError("no backend")

    monkeypatch.setattr(secrets, "_keyring", Broken())
    secrets._memory.clear()
    with caplog.at_level(logging.WARNING):
        secrets.set_secret("huggingface", "hf_x")
    assert "keyring" in caplog.text.lower()
    assert secrets.get_secret("huggingface") == "hf_x"
    assert secrets.secret_status()["huggingface"] is True
    secrets.delete_secret("huggingface")
    assert secrets.get_secret("huggingface") is None


def test_keyring_service_name_and_calls(monkeypatch):
    secrets.set_backend_for_tests(None)
    calls: list[tuple] = []

    class Recorder:
        def get_password(self, service, name):
            calls.append(("get", service, name))

        def set_password(self, service, name, value):
            calls.append(("set", service, name, value))

        def delete_password(self, service, name):
            calls.append(("del", service, name))

    monkeypatch.setattr(secrets, "_keyring", Recorder())
    secrets.set_secret("openrouter", "k")
    secrets.get_secret("openrouter")
    secrets.delete_secret("openrouter")
    assert calls == [
        ("set", "dataset-genie", "openrouter", "k"),
        ("get", "dataset-genie", "openrouter"),
        ("del", "dataset-genie", "openrouter"),
    ]


@pytest.mark.parametrize("exc_cls", [keyring.errors.KeyringLocked, keyring.errors.InitError,
                                     keyring.errors.KeyringError])
def test_other_keyring_errors_fall_back_to_file_store(monkeypatch, caplog, exc_cls, genie_home):
    secrets.set_backend_for_tests(None)

    class Broken:
        def get_password(self, service, name):
            raise exc_cls("locked")

        def set_password(self, service, name, value):
            raise exc_cls("locked")

        def delete_password(self, service, name):
            raise exc_cls("locked")

    monkeypatch.setattr(secrets, "_keyring", Broken())
    secrets._memory.clear()
    secrets._warned = False
    with caplog.at_level(logging.WARNING):
        assert secrets.secret_status() == {"openrouter": False, "huggingface": False}
        secrets.set_secret("openrouter", "k")
    assert "keyring" in caplog.text.lower()
    assert secrets.get_secret("openrouter") == "k"
    secrets.delete_secret("openrouter")
    assert secrets.get_secret("openrouter") is None


class _Broken:
    def get_password(self, service, name):
        raise keyring.errors.NoKeyringError("no backend")

    def set_password(self, service, name, value):
        raise keyring.errors.NoKeyringError("no backend")

    def delete_password(self, service, name):
        raise keyring.errors.NoKeyringError("no backend")


@pytest.fixture()
def no_keyring(monkeypatch, genie_home):
    """Real code path with an unusable keyring and no token env vars (the Docker situation)."""
    secrets.set_backend_for_tests(None)
    monkeypatch.setattr(secrets, "_keyring", _Broken())
    monkeypatch.setattr(secrets, "_warned", False)
    secrets._memory.clear()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    return genie_home


def test_env_vars_supply_secrets(no_keyring, monkeypatch):
    assert secrets.secret_status() == {"openrouter": False, "huggingface": False}
    monkeypatch.setenv("OPENROUTER_API_KEY", " sk-or-env ")
    monkeypatch.setenv("HF_TOKEN", "hf_env")
    assert secrets.get_secret("openrouter") == "sk-or-env"
    assert secrets.get_secret("huggingface") == "hf_env"
    assert secrets.secret_status() == {"openrouter": True, "huggingface": True}
    # an env var wins over a stored value, so a container's .env is authoritative
    secrets.set_secret("openrouter", "sk-or-stored")
    assert secrets.get_secret("openrouter") == "sk-or-env"


def test_file_store_persists_across_restart_with_owner_only_mode(no_keyring, caplog):
    with caplog.at_level(logging.WARNING):
        secrets.set_secret("openrouter", "sk-or-file")
    path = no_keyring / "secrets.json"
    assert "secrets.json" in caplog.text
    assert path.exists() and (path.stat().st_mode & 0o777) == 0o600
    assert json.loads(path.read_text()) == {"openrouter": "sk-or-file"}
    secrets._memory.clear()  # simulate a process restart: nothing cached in memory
    assert secrets.get_secret("openrouter") == "sk-or-file"
    secrets.set_secret("huggingface", "hf_file")
    assert json.loads(path.read_text()) == {"openrouter": "sk-or-file", "huggingface": "hf_file"}
    secrets.delete_secret("openrouter")
    assert json.loads(path.read_text()) == {"huggingface": "hf_file"}
    assert secrets.secret_status() == {"openrouter": False, "huggingface": True}
    assert "sk-or-file" not in caplog.text  # never logged


def test_file_store_is_not_touched_when_keyring_works(monkeypatch, genie_home):
    secrets.set_backend_for_tests(None)
    store: dict[str, str] = {}

    class Works:
        def get_password(self, service, name):
            return store.get(name)

        def set_password(self, service, name, value):
            store[name] = value

        def delete_password(self, service, name):
            store.pop(name, None)

    monkeypatch.setattr(secrets, "_keyring", Works())
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    secrets.set_secret("openrouter", "k")
    assert not (genie_home / "secrets.json").exists()
