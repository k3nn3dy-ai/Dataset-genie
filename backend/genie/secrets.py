"""Secret storage. Secrets live only in the OS keychain (service "dataset-genie").

Names are restricted to the two the app needs. The backend is injectable so tests never touch
the real keychain; if no keyring backend is available we fall back to an in-memory store and log
a warning (the secret then lives only for the lifetime of the process).
"""
from __future__ import annotations

import logging
from collections.abc import MutableMapping

import keyring
import keyring.errors

log = logging.getLogger(__name__)

SERVICE = "dataset-genie"
SECRET_NAMES: tuple[str, ...] = ("openrouter", "huggingface")

_keyring = keyring  # module-level indirection so tests can swap it
_memory: dict[str, str] = {}  # fallback store when no keyring backend exists
_test_backend: MutableMapping[str, str] | None = None
_warned = False


def set_backend_for_tests(store: MutableMapping[str, str] | None) -> None:
    """Route all secret operations to `store` (dict-like). Pass None to restore keyring."""
    global _test_backend
    _test_backend = store


def _check_name(name: str) -> str:
    if name not in SECRET_NAMES:
        raise ValueError(f"unknown secret {name!r}; expected one of {list(SECRET_NAMES)}")
    return name


def _warn_once(exc: Exception) -> None:
    global _warned
    if not _warned:
        log.warning("keyring backend unavailable (%s); secrets held in memory only", exc)
        _warned = True


def get_secret(name: str) -> str | None:
    _check_name(name)
    if _test_backend is not None:
        return _test_backend.get(name)
    if name in _memory:
        return _memory[name]
    try:
        value = _keyring.get_password(SERVICE, name)
    except keyring.errors.NoKeyringError as exc:
        _warn_once(exc)
        return None
    return value or None


def set_secret(name: str, value: str) -> None:
    _check_name(name)
    if not value or not value.strip():
        raise ValueError("secret value must be non-empty")
    value = value.strip()
    if _test_backend is not None:
        _test_backend[name] = value
        return
    try:
        _keyring.set_password(SERVICE, name, value)
        _memory.pop(name, None)
    except keyring.errors.NoKeyringError as exc:
        _warn_once(exc)
        _memory[name] = value


def delete_secret(name: str) -> None:
    _check_name(name)
    if _test_backend is not None:
        _test_backend.pop(name, None)
        return
    _memory.pop(name, None)
    try:
        _keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass  # nothing stored
    except keyring.errors.NoKeyringError as exc:
        _warn_once(exc)


def secret_status() -> dict[str, bool]:
    return {name: get_secret(name) is not None for name in SECRET_NAMES}
