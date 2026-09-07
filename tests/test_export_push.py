"""HF push with a mocked HfApi — private by default, folder upload, version tag."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genie import export as ex
from genie.schemas import HFPushConfig


@pytest.fixture()
def api(monkeypatch):
    fake = MagicMock()
    fake.whoami.return_value = {"name": "andy"}
    monkeypatch.setattr(ex, "_hf_api", lambda token: fake)
    ex.clear_hf_status_cache()
    return fake


def test_push_bundle_private_default_upload_and_tag(api, tmp_path):
    url = ex.push_bundle(tmp_path, HFPushConfig(repo_id="andy/demo"), token="tok")
    assert url == "https://huggingface.co/datasets/andy/demo"
    api.create_repo.assert_called_once_with("andy/demo", repo_type="dataset", private=True, exist_ok=True)
    api.upload_folder.assert_called_once_with(
        folder_path=str(tmp_path),
        repo_id="andy/demo",
        repo_type="dataset",
        commit_message="Dataset Genie export v0.1.0",
    )
    api.create_tag.assert_called_once_with("andy/demo", tag="v0.1.0", repo_type="dataset", exist_ok=True)
    # order: create → upload → tag
    names = [c[0] for c in api.method_calls]
    assert names == ["create_repo", "upload_folder", "create_tag"]


def test_push_bundle_public_and_custom_tag(api, tmp_path):
    cfg = HFPushConfig(repo_id="andy/demo", private=False, version_tag="v1.2.3")
    ex.push_bundle(tmp_path, cfg, token="tok")
    assert api.create_repo.call_args.kwargs["private"] is False
    assert api.create_tag.call_args.kwargs["tag"] == "v1.2.3"
    assert api.upload_folder.call_args.kwargs["commit_message"] == "Dataset Genie export v1.2.3"


def test_push_bundle_requires_repo_and_token(api, tmp_path):
    with pytest.raises(ValueError):
        ex.push_bundle(tmp_path, HFPushConfig(repo_id=""), token="tok")
    with pytest.raises(ValueError):
        ex.push_bundle(tmp_path, HFPushConfig(repo_id="justname"), token="tok")
    with pytest.raises(ValueError):
        ex.push_bundle(tmp_path, HFPushConfig(repo_id="a/b"), token="")
    api.create_repo.assert_not_called()


def test_hf_status_uses_whoami_and_caches_five_minutes(api):
    assert ex.hf_status(None) == {"has_token": False, "username": None}
    s1 = ex.hf_status("hf_secretvalue", now=1000.0)
    assert s1 == {"has_token": True, "username": "andy"}
    assert "secretvalue" not in repr(s1)
    ex.hf_status("hf_secretvalue", now=1200.0)
    assert api.whoami.call_count == 1
    ex.hf_status("hf_secretvalue", now=1000.0 + 301)
    assert api.whoami.call_count == 2


def test_hf_status_bad_token_reports_presence_without_username(api):
    api.whoami.side_effect = RuntimeError("401")
    assert ex.hf_status("bad", now=5.0) == {"has_token": True, "username": None, "error": "RuntimeError"}


def test_get_hf_token_falls_back_to_env_when_secrets_module_absent(monkeypatch):
    monkeypatch.setattr(ex, "_secrets_module", lambda: None)
    monkeypatch.setenv("HF_TOKEN", "envtoken")
    assert ex.get_hf_token() == "envtoken"
    monkeypatch.delenv("HF_TOKEN")
    assert ex.get_hf_token() is None


def test_get_hf_token_prefers_keychain(monkeypatch):
    class FakeSecrets:
        @staticmethod
        def get_secret(name):
            assert name == "huggingface"
            return "from-keychain"

    monkeypatch.setattr(ex, "_secrets_module", lambda: FakeSecrets)
    monkeypatch.setenv("HF_TOKEN", "envtoken")
    assert ex.get_hf_token() == "from-keychain"
