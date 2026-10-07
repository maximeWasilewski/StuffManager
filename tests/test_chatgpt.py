import base64
import hashlib
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app import chatgpt_auth as auth, recognition
from fastapi.testclient import TestClient
from tests.test_quick_add import IDENTIFIED, photo


MODELS = [{"slug": "vision-test", "display_name": "Vision de test", "visibility": "list"}]


@pytest.fixture
def provider(monkeypatch):
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth.JWKS, "get_signing_key_from_jwt", lambda token: SimpleNamespace(key=private.public_key()))
    calls = []
    def token(nonce, client="issued-client", subject="user-1", **changes):
        claims = dict(iss=auth.ISSUER, aud=client, sub=subject, exp=time.time() + 3600, nonce=nonce, email="demo@example.test")
        claims.update(changes)
        return jwt.encode(claims, private, algorithm="RS256")
    def remote(url, *, form=None, token=None):
        calls.append((url, form))
        if url.endswith("/models"):
            return {"models": MODELS}
        if url.endswith("/oauth/token"):
            if form["grant_type"] == "refresh_token":
                return dict(access_token="rotated-access", refresh_token="rotated-refresh", expires_in=3600, scope=auth.SCOPES)
            return dict(id_token=remote.id_token, access_token="mock-access-secret", refresh_token="mock-refresh-secret",
                        expires_in=3600, scope=remote.scopes, token_type="Bearer")
        if url.endswith("openid-configuration"):
            return {"revocation_endpoint": auth.ISSUER + "/api/accounts/oauth/revoke"}
        return {}
    remote.id_token = ""
    remote.scopes = auth.SCOPES
    monkeypatch.setattr(auth, "http_json", remote)
    return SimpleNamespace(token=token, remote=remote, calls=calls)


def connect(store, provider, browser="browser", client=None):
    url = store.start("http://127.0.0.1:8080/auth/callback", browser, client)
    query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
    provider.remote.id_token = provider.token(query["nonce"])
    store.finish(dict(state=query["state"], code="mock-code", client_id="issued-client"), browser)
    return query


def test_oauth_pkce_identity_persistence_and_no_secrets_in_status(tmp_path, provider):
    store = auth.ChatGPTStore(tmp_path)
    query = connect(store, provider)
    assert query["client_id"] == "dynamic_agent_client"
    assert query["agent_name_hint"] == "StuffManager"
    assert query["redirect_uri"] == "http://127.0.0.1:8080/auth/callback"
    assert query["ext_agent_host_id"].startswith("urn:uuid:")
    form = next(form for url, form in provider.calls if url == auth.TOKEN)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
    assert challenge == query["code_challenge"]
    assert form["client_id"] == "issued-client"
    assert form["redirect_uri"] == query["redirect_uri"]
    restarted = auth.ChatGPTStore(tmp_path)
    assert restarted.credentials() == ("mock-access-secret", "vision-test")
    assert "mock-access" not in json.dumps(restarted.status())
    active = restarted.status()["accounts"][0]["id"]
    next_url = restarted.start(query["redirect_uri"], "browser", active)
    later = parse_qs(urlsplit(next_url).query)
    assert later["client_id"] == ["issued-client"]
    assert later["ext_agent_host_id"] == [query["ext_agent_host_id"]]
    assert "agent_name_hint" not in later


@pytest.mark.parametrize("problem", ["state", "browser", "nonce", "audience", "expired", "scopes", "client", "denied"])
def test_invalid_oauth_never_connects(tmp_path, provider, problem):
    store = auth.ChatGPTStore(tmp_path)
    query = parse_qs(urlsplit(store.start("http://127.0.0.1:8080/auth/callback", "browser")).query)
    token_changes = {"aud": "other"} if problem == "audience" else {"exp": time.time() - 60} if problem == "expired" else {}
    provider.remote.id_token = provider.token("bad" if problem == "nonce" else query["nonce"][0], **token_changes)
    if problem == "scopes":
        provider.remote.scopes = "openid profile email"
    callback = dict(state="bad" if problem == "state" else query["state"][0], code="code", client_id="dynamic_agent_client" if problem == "client" else "issued-client")
    if problem == "denied":
        callback["error"] = "access_denied"
    with pytest.raises(auth.AuthError):
        store.finish(callback, "other" if problem == "browser" else "browser")
    assert not store.status()["connected"]


def test_refresh_rotates_once_across_concurrent_requests_and_restart(tmp_path, provider):
    store = auth.ChatGPTStore(tmp_path)
    connect(store, provider)
    with store.connect() as conn:
        row = conn.execute("SELECT id, record FROM accounts").fetchone()
        record = json.loads(row[1])
        record["expires_at"] = 0
        conn.execute("UPDATE accounts SET record=? WHERE id=?", (json.dumps(record), row[0]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: auth.ChatGPTStore(tmp_path).credentials(), range(2)))
    assert results == [("rotated-access", "vision-test")] * 2
    refresh = [form for _, form in provider.calls if form and form.get("grant_type") == "refresh_token"]
    assert len(refresh) == 1 and refresh[0]["client_id"] == "issued-client"
    with store.connect() as conn:
        record = json.loads(conn.execute("SELECT record FROM accounts").fetchone()[0])
        assert record["refresh_token"] == "rotated-refresh"


def test_encrypted_home_assistant_transfer_is_bound_single_use_and_preserves_host(tmp_path, provider):
    local = auth.ChatGPTStore(tmp_path / "local")
    connect(local, provider)
    with local.connect() as conn:
        record = json.loads(conn.execute("SELECT record FROM accounts").fetchone()[0])
    server = auth.ChatGPTStore(tmp_path / "server")
    setup = server.pairing()
    package = auth.encrypt_package(record, setup)
    assert b"mock-access-secret" not in package and b"mock-refresh-secret" not in package
    with pytest.raises(auth.AuthError):
        auth.ChatGPTStore(tmp_path / "other").import_package(package)
    server.import_package(package)
    assert server.credentials() == ("mock-access-secret", "vision-test")
    with server.connect() as conn:
        assert server.read(conn, "host") == setup["server_host_id"]
    with pytest.raises(auth.AuthError):
        server.import_package(package)


def test_tampered_and_expired_packages_do_not_replace_account(tmp_path, provider):
    store = auth.ChatGPTStore(tmp_path)
    connect(store, provider)
    setup = store.pairing()
    with store.connect() as conn:
        record = json.loads(conn.execute("SELECT record FROM accounts").fetchone()[0])
    raw = json.loads(auth.encrypt_package(record, setup))
    raw["data"] = base64.b64encode(b"tampered").decode()
    with pytest.raises(auth.AuthError):
        store.import_package(json.dumps(raw))
    with store.connect() as conn:
        pair = store.read(conn, "pairing")
        pair["created_at"] = 0
        store.write(conn, "pairing", pair)
    with pytest.raises(auth.AuthError):
        store.import_package(auth.encrypt_package(record, setup))
    assert store.credentials()[0] == "mock-access-secret"


def test_disconnect_clears_tokens_retains_registration_and_reports_remote_failure(tmp_path, provider, monkeypatch):
    store = auth.ChatGPTStore(tmp_path)
    connect(store, provider)
    account = store.status()["accounts"][0]["id"]
    assert store.disconnect(account)
    with store.connect() as conn:
        record = json.loads(conn.execute("SELECT record FROM accounts").fetchone()[0])
        assert record["client_id"] == "issued-client"
        assert not {"access_token", "refresh_token", "id_token"} & set(record)
    connect(store, provider, client=account)
    def failure(*args, **kwargs):
        raise auth.AuthError("offline")
    monkeypatch.setattr(auth, "http_json", failure)
    assert not store.disconnect(account)
    assert not store.status()["connected"]


def test_settings_csrf_bundle_and_unconfigured_chatgpt_manual_fallback(client):
    assert client.get("/chatgpt").status_code == 200
    csrf = client.cookies["chatgpt_csrf"]
    assert client.post("/chatgpt/assistant", data={}).status_code == 403
    result = client.post("/chatgpt/assistant", data={"csrf": csrf})
    assert result.status_code == 200
    with zipfile.ZipFile(io.BytesIO(result.content)) as bundle:
        setup = json.loads(bundle.read("setup.json"))
        assert "private_key" not in setup
        assert "chatgpt_auth.py" in bundle.namelist()
    assert client.post("/chatgpt/importer", data={"csrf": csrf}, files={"connexion": ("bad.chatgpt", b"garbage")}).status_code == 400
    assert client.post("/chatgpt/connecter", data={"csrf": csrf}, follow_redirects=False).status_code == 400 # testserver is not loopback
    result = client.post("/ajout-rapide", data={"provider": "chatgpt"}, files={"photo": ("cable.png", photo(), "image/png")})
    assert "Connectez votre compte ChatGPT" in result.text
    assert "Créer une nouvelle fiche" in result.text


def test_loopback_connect_and_callback_guards(client, provider):
    with TestClient(client.app, base_url="http://127.0.0.1:8090") as local:
        local.get("/chatgpt")
        csrf = local.cookies["chatgpt_csrf"]
        response = local.post("/chatgpt/connecter", data={"csrf": csrf}, follow_redirects=False)
        assert response.status_code == 303
        url = response.headers["location"]
        assert url.startswith(auth.AUTHORIZE + "?")
        query = parse_qs(urlsplit(url).query)
        assert query["redirect_uri"] == ["http://127.0.0.1:8090/auth/callback"]
        provider.remote.id_token = provider.token(query["nonce"][0])
        response = local.get("/auth/callback", params=dict(state=query["state"][0], code="secret-code", client_id="issued-client"), follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/chatgpt?connected=1"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "mock-access-secret" not in local.get("/chatgpt").text
        assert "mock-refresh-secret" not in local.get("/chatgpt").text
        # The authorization attempt cannot be reused.
        assert local.get("/auth/callback", params=dict(state=query["state"][0], code="secret-code", client_id="issued-client")).status_code == 400


def test_selected_chatgpt_never_falls_back_to_paid_api(client, monkeypatch):
    def credentials():
        raise auth.AuthError("Reconnectez votre compte ChatGPT.")
    monkeypatch.setattr(client.app.state.chatgpt, "credentials", credentials)
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: pytest.fail("Paid API must not be called"))
    result = client.post("/ajout-rapide", data={"provider": "chatgpt"}, files={"photo": ("test.png", photo())})
    assert "Reconnectez votre compte ChatGPT" in result.text


def test_stream_request_uses_account_model_and_supported_fields(monkeypatch):
    captured = {}
    event = {"type": "response.completed", "response": {"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(IDENTIFIED)}]}]}}
    def urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return io.BytesIO(("event: response.completed\ndata: " + json.dumps(event) + "\n\n").encode())
    monkeypatch.setattr(recognition, "urlopen", urlopen)
    assert recognition._recognize(photo(), "mock-token", "account-model", ["Câble"]) == IDENTIFIED
    assert captured["stream"] is True and captured["store"] is False
    assert captured["model"] == "account-model" and "max_output_tokens" not in captured
    assert isinstance(captured["input"], list)


@pytest.mark.parametrize("stream", [b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n', b'data: {"type":"response.failed"}\n\n'])
def test_incomplete_stream_never_saves_partial_identification(stream):
    with pytest.raises(recognition.RecognitionError):
        recognition.read_completed_stream(io.BytesIO(stream))


def test_subscription_limit_without_leaking_body(monkeypatch):
    def error(*args, **kwargs):
        raise HTTPError("https://api.openai.com", 429, "fail", {}, io.BytesIO(json.dumps({"error": {"code": "insufficient_quota", "message": "SECRET"}}).encode()))
    monkeypatch.setattr(recognition, "urlopen", error)
    with pytest.raises(recognition.RecognitionError, match="limite ChatGPT") as exc:
        recognition._recognize(photo(), "key", "model", [])
    assert "SECRET" not in str(exc.value)
