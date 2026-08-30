import json

from yt_media.metadata_optimizer import MANAGE_SCOPES, _has_required_stored_scopes, _stored_scopes


def test_stored_scope_validation_requires_analytics(tmp_path):
    token = tmp_path / "youtube_manage_token.json"
    token.write_text(json.dumps({
        "token": "x",
        "refresh_token": "y",
        "client_id": "id",
        "client_secret": "secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/youtube"],
    }), encoding="utf-8")
    assert _has_required_stored_scopes(token) is False

    payload = json.loads(token.read_text(encoding="utf-8"))
    payload["scopes"] = list(MANAGE_SCOPES)
    token.write_text(json.dumps(payload), encoding="utf-8")
    assert _has_required_stored_scopes(token) is True
    assert set(MANAGE_SCOPES).issubset(_stored_scopes(token))
