import io
import json

import pytest

from app import recognition


IDENTIFIED = {"name": "Câble USB-A vers USB-C", "category": "Câble", "reference": "",
              "confidence": "probable", "notes": "Vérifier la longueur."}


def stream(*events):
    return io.BytesIO("".join("data: " + json.dumps(event) + "\n\n" for event in events).encode())


@pytest.mark.parametrize("terminal_output", [[], [{"type": "reasoning", "summary": []}]])
def test_text_delivered_before_terminal_event_is_identified(monkeypatch, terminal_output):
    text = json.dumps(IDENTIFIED)
    events = [
        {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": text[:20]},
        {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": text[20:]},
        {"type": "response.output_text.done", "output_index": 1, "content_index": 0, "text": text},
        {"type": "response.completed", "response": {"status": "completed", "output": terminal_output}},
    ]
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: stream(*events))
    assert recognition._recognize(b"photo", "secret", "model", ["Câble"], chatgpt=True) == IDENTIFIED


def test_output_item_delivered_before_terminal_event_is_identified(monkeypatch):
    events = [
        {"type": "response.output_item.done", "output_index": 1, "item": {"type": "message", "content": [
            {"type": "output_text", "text": json.dumps(IDENTIFIED)}]}},
        {"type": "response.completed", "response": {"status": "completed"}},
    ]
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: stream(*events))
    assert recognition._recognize(b"photo", "secret", "model", ["Câble"], chatgpt=True) == IDENTIFIED


@pytest.mark.parametrize("ending", [None, "response.failed", "response.incomplete"])
def test_valid_text_without_successful_completion_is_never_accepted(ending):
    events = [{"type": "response.output_text.done", "text": json.dumps(IDENTIFIED)}]
    if ending:
        events.append({"type": ending})
    with pytest.raises(recognition.RecognitionError):
        recognition.read_completed_stream(stream(*events))


@pytest.mark.parametrize("wrapper", ["```json\n{}\n```", "```\n{}\n```", "{}"])
def test_json_wrappers_are_accepted(monkeypatch, wrapper):
    response = {"status": "completed", "output": [{"content": [
        {"type": "output_text", "text": wrapper.format(json.dumps(IDENTIFIED))}]}]}
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()))
    assert recognition._recognize(b"photo", "secret", "model", ["Câble"]) == IDENTIFIED


def test_format_error_is_not_reported_as_unrecognizable_object(monkeypatch, caplog):
    response = {"status": "completed", "output": [{"content": [
        {"type": "output_text", "text": "PRIVATE PHOTO DESCRIPTION"}]}]}
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()))
    with pytest.raises(recognition.RecognitionError, match="format attendu"):
        recognition._recognize(b"photo", "SECRET KEY", "model", [])
    assert "invalid response format" in caplog.text
    assert "PRIVATE" not in caplog.text and "SECRET" not in caplog.text


def test_empty_name_is_reported_as_unidentified(monkeypatch):
    response = {"status": "completed", "output": [{"content": [
        {"type": "output_text", "text": json.dumps(dict(IDENTIFIED, name=" "))}]}]}
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(response).encode()))
    with pytest.raises(recognition.RecognitionError, match="pas identifié d'objet"):
        recognition._recognize(b"photo", "secret", "model", [])


def test_completed_refusal_is_not_overridden_by_earlier_text(monkeypatch):
    events = [
        {"type": "response.output_text.done", "text": json.dumps(IDENTIFIED)},
        {"type": "response.completed", "response": {"status": "completed", "output": [{"content": [
            {"type": "refusal", "refusal": "no"}]}]}},
    ]
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: stream(*events))
    with pytest.raises(recognition.RecognitionError, match="refusé"):
        recognition._recognize(b"photo", "secret", "model", [], chatgpt=True)


def test_chatgpt_stream_identification_reaches_review_and_stock_suggestions(client, monkeypatch):
    from app.db import connect, create_item, create_location, list_categories
    from test_quick_add import photo

    conn = connect(client.app.state.db_path)
    location = create_location(conn, "Tiroir bureau")
    category = next(c for c in list_categories(conn) if c["name"] == "Câble")
    create_item(conn, name=IDENTIFIED["name"], category_id=category["id"], quantity=4, used_quantity=0,
                location_id=location, spot="Boîte 3", usage_level="rare", notes="", reference="")
    conn.commit()
    conn.close()
    monkeypatch.setattr(client.app.state.chatgpt, "credentials", lambda: ("mock-secret", "model"))
    events = [
        {"type": "response.output_text.done", "text": "```json\n" + json.dumps(IDENTIFIED) + "\n```"},
        {"type": "response.completed", "response": {"status": "completed", "output": []}},
    ]
    monkeypatch.setattr(recognition, "urlopen", lambda *args, **kwargs: stream(*events))
    result = client.post("/ajout-rapide", data={"provider": "chatgpt"},
                         files={"photo": ("photo.png", photo(), "image/png")})
    assert IDENTIFIED["name"] in result.text
    assert "Tiroir bureau · Boîte 3" in result.text
    assert "Ajouter à cette fiche" in result.text
    assert "format attendu" not in result.text
