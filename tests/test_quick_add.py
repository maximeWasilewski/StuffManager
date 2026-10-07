import json
import time
from io import BytesIO
from urllib.error import HTTPError

import pytest
from PIL import Image

from app import recognition
from app.db import connect, create_item, create_location, get_item, list_categories, set_has_photo, set_quantity
from app.images import photo_file


def photo():
    buffer = BytesIO()
    Image.new("RGB", (48, 32), "gray").save(buffer, "PNG")
    return buffer.getvalue()


IDENTIFIED = {"name": "Câble USB-C vers USB-A", "reference": "", "category": "Câble",
              "confidence": "probable", "notes": "Vérifier la longueur avant de regrouper."}


def streamed(response):
    event_type = "response.completed" if response.get("status") == "completed" else "response.incomplete"
    event = {"type": event_type, "response": response}
    return BytesIO(("data: " + json.dumps(event) + "\n\n").encode())


@pytest.fixture
def identified(monkeypatch):
    async def recognize(photo, categories, *, store=None):
        return dict(IDENTIFIED)
    monkeypatch.setattr(recognition, "recognize", recognize)


def draft(client):
    response = client.post("/ajout-rapide", files={"photo": ("cable.png", photo(), "image/png")}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"]


def stock(client, name="Câble USB-A vers USB-C", quantity=4, used=0):
    conn = connect(client.app.state.db_path)
    category = next(c for c in list_categories(conn) if c["name"] == "Câble")
    if used:
        conn.execute("UPDATE categories SET track_usage=1 WHERE id=?", (category["id"],))
    location = create_location(conn, "Tiroir du bureau")
    item_id, _ = create_item(conn, name=name, category_id=category["id"], quantity=quantity,
                            used_quantity=used, location_id=location, spot="Boîte 3", usage_level="rare", notes="Note existante", reference="1 m")
    set_has_photo(conn, item_id, True)
    conn.commit()
    original_photo = b"existing-photo"
    photo_file(client.app.state.photos_dir, item_id).write_bytes(original_photo)
    item = get_item(conn, item_id)
    conn.close()
    return item


def merge_data(item, **overrides):
    return dict(mode="existing", item_id=str(item["id"]), q=IDENTIFIED["name"], quantite="2",
                expected_quantity=str(item["quantity"]), expected_location=str(item["location_id"]),
                expected_spot=item["spot"], **overrides)


def test_analysis_does_not_modify_stock_and_suggests_location(client, identified):
    item = stock(client)
    url = draft(client)
    page = client.get(url)
    assert "Tiroir du bureau · Boîte 3" in page.text
    assert "Identification probable" in page.text
    assert "Ajouter à cette fiche" in page.text
    conn = connect(client.app.state.db_path)
    assert get_item(conn, item["id"])["quantity"] == 4
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    conn.close()
    response = client.get(url + "/photo")
    assert response.headers["cache-control"] == "no-store"
    assert Image.open(BytesIO(response.content)).format == "JPEG"


def test_merge_keeps_exact_location_used_count_and_photo_and_is_idempotent(client, identified):
    item = stock(client, used=1)
    url = draft(client)
    for _ in range(2):
        response = client.post(url + "/confirmer", data=merge_data(item), follow_redirects=False)
        assert response.status_code == 303, response.text
    conn = connect(client.app.state.db_path)
    updated = get_item(conn, item["id"])
    assert updated["quantity"] == 6
    assert updated["used_quantity"] == 1
    assert updated["location_id"] == item["location_id"]
    assert updated["spot"] == "Boîte 3"
    assert updated["notes"] == "Note existante"
    assert updated["available"] == 5
    assert photo_file(client.app.state.photos_dir, item["id"]).read_bytes() == b"existing-photo"
    assert conn.execute("SELECT length(photo) FROM quick_drafts").fetchone()[0] == 0
    conn.close()


def test_stale_stock_requires_a_new_confirmation(client, identified):
    item = stock(client)
    url = draft(client)
    conn = connect(client.app.state.db_path)
    set_quantity(conn, item["id"], 5)
    conn.commit()
    conn.close()
    response = client.post(url + "/confirmer", data=merge_data(item))
    assert response.status_code == 409
    assert "a changé" in response.text
    conn = connect(client.app.state.db_path)
    assert get_item(conn, item["id"])["quantity"] == 5
    conn.close()


def test_new_fiche_keeps_photo_and_replay_does_not_duplicate(client, identified):
    url = draft(client)
    conn = connect(client.app.state.db_path)
    category_id = next(c["id"] for c in list_categories(conn) if c["name"] == "Câble")
    conn.close()
    data = dict(mode="new", nom="Câble USB-C vers USB-A", categorie_id=str(category_id), quantite="3", reference="1 m", notes="", spot="", emplacement_id="")
    first = client.post(url + "/confirmer", data=data, follow_redirects=False)
    second = client.post(url + "/confirmer", data=data, follow_redirects=False)
    assert first.status_code == second.status_code == 303
    assert first.headers["location"].split("?")[0] == second.headers["location"].split("?")[0]
    conn = connect(client.app.state.db_path)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    item = get_item(conn, 1)
    assert item["quantity"] == 3 and item["has_photo"]
    assert Image.open(photo_file(client.app.state.photos_dir, 1)).format == "JPEG"
    conn.close()


def test_invalid_new_form_retains_photo_and_corrected_values(client, identified):
    url = draft(client)
    response = client.post(url + "/confirmer", data={"mode": "new", "nom": "Mon câble", "quantite": "-1"})
    assert response.status_code == 400
    assert 'value="Mon câble"' in response.text
    assert client.get(url + "/photo").status_code == 200
    conn = connect(client.app.state.db_path)
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    conn.close()


def test_quick_add_notes_start_empty_and_keep_only_user_input(client, identified):
    url = draft(client)
    page = client.get(url)
    assert '<textarea name="notes" rows="3" maxlength="4000"></textarea>' in page.text
    conn = connect(client.app.state.db_path)
    category = next(c["id"] for c in list_categories(conn) if c["name"] == "Câble")
    conn.close()
    result = client.post(url + "/confirmer", data=dict(mode="new", nom=IDENTIFIED["name"],
                         categorie_id=str(category), quantite="1", notes="Mes notes personnelles"))
    assert result.status_code == 200
    conn = connect(client.app.state.db_path)
    assert conn.execute("SELECT notes FROM items").fetchone()[0] == "Mes notes personnelles"
    conn.close()


def test_removed_api_mode_is_not_shown_and_cannot_call_openai(client, monkeypatch):
    page = client.get("/ajout-rapide").text
    assert 'value="api"' not in page and "openai_api_key" not in page
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: pytest.fail("Removed provider must not call OpenAI"))
    result = client.post("/ajout-rapide", data={"provider": "api"},
                         files={"photo": ("cable.png", photo(), "image/png")})
    from html import unescape
    assert "mode d'identification valide" in unescape(result.text)


def test_missing_key_and_network_failure_allow_manual_add(client, monkeypatch):
    async def unavailable(photo, categories, *, store=None):
        raise recognition.RecognitionError("Impossible de joindre OpenAI.")
    monkeypatch.setattr(recognition, "recognize", unavailable)
    url = draft(client)
    page = client.get(url)
    assert "Impossible de joindre OpenAI." in page.text
    assert "Créer une nouvelle fiche" in page.text
    assert "reconnaissance n'est pas encore activée" in client.get("/ajout-rapide").text


def test_wrong_photo_expired_draft_and_foreign_origin(client, identified):
    bad = client.post("/ajout-rapide", files={"photo": ("bad.jpg", b"bad", "image/jpeg")})
    assert bad.status_code == 400
    assert client.post("/ajout-rapide", headers={"origin": "https://unrelated.example"}).status_code == 403
    url = draft(client)
    conn = connect(client.app.state.db_path)
    conn.execute("UPDATE quick_drafts SET created_at=?", (time.time() - 90000,))
    conn.commit()
    conn.close()
    assert client.get(url).status_code == 404
    assert client.post(url + "/confirmer", data={"mode": "new"}).status_code == 404


def test_incompatible_cable_cannot_be_merged(client, identified):
    item = stock(client, name="Câble USB-C vers HDMI")
    url = draft(client)
    assert "Ajouter à cette fiche" not in client.get(url).text
    assert client.post(url + "/confirmer", data=merge_data(item)).status_code == 400


def test_merge_rejects_zero_negative_and_overflow(client, identified):
    item = stock(client, quantity=1000000)
    url = draft(client)
    for qty in ["0", "-1", "1", "abc", "1000001"]:
        data = merge_data(item)
        data["quantite"] = qty
        assert client.post(url + "/confirmer", data=data).status_code == 400
    conn = connect(client.app.state.db_path)
    assert get_item(conn, item["id"])["quantity"] == 1000000
    conn.close()


def test_connector_matching_handles_aliases_and_rejects_different_ends():
    def item(name, id):
        return {"name": name, "reference": "", "id": id}
    items = [item("Câble USB type A vers USB type C", 1), item("Câble micro USB", 2),
             item("Câble USB-C vers HDMI", 3), item("Câble mini-HDMI", 4), item("Câble USB-C vers USB-C", 5)]
    assert [i["id"] for i in recognition.suggestions(items, "Câble USB-C vers USB-A")] == [1]
    assert recognition.connectors("mini HDMI vers HDMI") == {"mini-hdmi": 1, "hdmi": 1}
    assert recognition.connectors("RJ-45 Ethernet") == {"rj45": 2}
    assert recognition.suggestions(items, "") == []


def test_openai_request_is_structured_and_sends_only_photo_and_categories(monkeypatch):
    captured = {}
    def urlopen(request, timeout):
        captured.update(json.loads(request.data))
        return streamed({"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps(IDENTIFIED)}]}]})
    monkeypatch.setattr(recognition, "urlopen", urlopen)
    result = recognition._recognize(photo(), "test-key-never-real", "gpt-4.1-mini", ["Câble"])
    assert result["name"] == IDENTIFIED["name"]
    assert captured["store"] is False
    assert captured["text"]["format"]["strict"] is True
    assert len(captured["input"][0]["content"]) == 2
    assert captured["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
    assert "test-key-never-real" not in json.dumps(captured)


@pytest.mark.parametrize("response", [
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": [{"content": [{"type": "refusal", "refusal": "no"}]}]},
    {"status": "completed", "output": [{"content": [{"type": "output_text", "text": '{"name": 1}'}]}]},
])
def test_openai_incomplete_refusal_and_bad_schema_are_handled(monkeypatch, response):
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: streamed(response))
    with pytest.raises(recognition.RecognitionError):
        recognition._recognize(photo(), "test", "test", [])


@pytest.mark.parametrize("code", [400, 401, 403, 429, 500])
def test_provider_errors_never_expose_body_or_key(monkeypatch, code):
    def failure(*args, **kwargs):
        raise HTTPError("https://api.openai.com", code, "test-key", {}, BytesIO(b'secret-account-data'))
    monkeypatch.setattr(recognition, "urlopen", failure)
    with pytest.raises(recognition.RecognitionError) as exc:
        recognition._recognize(photo(), "test-key", "test", [])
    assert "test-key" not in str(exc.value)
    assert "secret-account" not in str(exc.value)


def test_missing_account_preserves_photo_without_calling_inference(client, monkeypatch):
    def unexpected_call(*args, **kwargs):
        pytest.fail("No inference call should happen without a connected account")
    monkeypatch.setattr(recognition, "urlopen", unexpected_call)
    url = draft(client)
    assert "Connectez votre compte ChatGPT" in client.get(url).text
    assert client.get(url + "/photo").content


def test_draft_survives_restart(client, identified):
    from fastapi.testclient import TestClient
    from app.main import create_app
    url = draft(client)
    with TestClient(create_app(client.app.state.data_dir)) as restarted:
        assert IDENTIFIED["name"] in restarted.get(url).text
        assert restarted.get(url + "/photo").content
