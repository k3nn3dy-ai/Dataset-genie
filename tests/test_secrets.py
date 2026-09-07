"""genie.secrets: keyring-backed secret store with an injectable backend for tests."""
from __future__ import annotations

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


def test_no_keyring_falls_back_to_memory(monkeypatch, caplog):
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
def test_other_keyring_errors_fall_back_to_memory(monkeypatch, caplog, exc_cls):
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
