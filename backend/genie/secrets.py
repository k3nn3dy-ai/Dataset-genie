"""Secret storage. Secrets live in the OS keychain (service "dataset-genie").

Names are restricted to the two the app needs. Resolution order for a read:

1. environment variable (OPENROUTER_API_KEY / HF_TOKEN) - how Docker users pass tokens via .env;
2. the OS keychain;
3. when no keyring backend is usable (Linux containers, headless sessions) an owner-only file
   `GENIE_HOME/secrets.json`, so tokens entered in Settings survive a restart; memory only if even
   that file cannot be written.

The backend is injectable so tests never touch the real keychain. Values are never logged.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import MutableMapping
from pathlib import Path

import keyring
import keyring.errors

log = logging.getLogger(__name__)

SERVICE = "dataset-genie"
SECRET_NAMES: tuple[str, ...] = ("openrouter", "huggingface")
ENV_VARS: dict[str, str] = {"openrouter": "OPENROUTER_API_KEY", "huggingface": "HF_TOKEN"}
FILE_NAME = "secrets.json"

_keyring = keyring  # module-level indirection so tests can swap it
_memory: dict[str, str] = {}  # last-resort store when neither keyring nor the file is usable
_test_backend: MutableMapping[str, str] | None = None
_warned = False

# Every keyring failure mode we degrade on: no backend, locked keychain, init failure, or any
# other KeyringError. PasswordDeleteError is handled separately where it means "nothing stored".
_KEYRING_FAILURES: tuple[type[Exception], ...] = (keyring.errors.KeyringError,)


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
        log.warning("keyring backend unavailable (%s: %s); secrets stored in %s (owner-only file)",
                    type(exc).__name__, exc, _file_path())
        _warned = True


# ---------------------------------------------------------------- file fallback
def _file_path() -> Path:
    from .config import get_settings  # lazy: config imports nothing from here, but keep it light

    return get_settings().genie_home / FILE_NAME


def _file_read() -> dict[str, str]:
    try:
        data = json.loads(_file_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def _file_write(data: dict[str, str]) -> bool:
    path = _file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log.warning("could not write %s (%s); secret held in memory only", path, exc)
        return False


def _from_env(name: str) -> str | None:
    value = os.environ.get(ENV_VARS[name], "").strip()
    return value or None


def get_secret(name: str) -> str | None:
    _check_name(name)
    if _test_backend is not None:
        return _test_backend.get(name)
    env = _from_env(name)
    if env:
        return env
    if name in _memory:
        return _memory[name]
    try:
        value = _keyring.get_password(SERVICE, name)
    except _KEYRING_FAILURES as exc:
        _warn_once(exc)
        return _file_read().get(name) or None
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
    except _KEYRING_FAILURES as exc:
        _warn_once(exc)
        data = _file_read()
        data[name] = value
        if _file_write(data):
            _memory.pop(name, None)
        else:
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
    except _KEYRING_FAILURES as exc:
        _warn_once(exc)
        data = _file_read()
        if name in data:
            del data[name]
            _file_write(data)


def secret_status() -> dict[str, bool]:
    return {name: get_secret(name) is not None for name in SECRET_NAMES}
