import re
from io import BytesIO
from urllib.parse import urlparse

from PIL import Image

from app.db import connect, init_db, list_categories
from app.images import qr_payload


def path_from_location(location: str) -> str:
    if location.startswith("http://") or location.startswith("https://"):
        parsed = urlparse(location)
        path = parsed.path
        if parsed.query:
            return f"{path}?{parsed.query}"
        return path
    return location


def clean_path(location: str) -> str:
    return path_from_location(location).split("?", 1)[0]


def item_id(location: str) -> str:
    return clean_path(location).rstrip("/").rsplit("/", 1)[-1]


def category_id(client, name: str) -> str:
    html = client.get("/composants/nouveau").text
    match = re.search(
        rf'<option value="(\d+)"[^>]*>{re.escape(name)}</option>',
        html,
    )
    assert match, f"catégorie {name} introuvable"
    return match.group(1)


def payload(client, **overrides):
    data = {
        "nom": "Résistance 10k",
        "categorie_id": category_id(client, "Électronique"),
        "quantite": "4",
        "emplacement_id": "",
        "spot": "",
        "niveau": "rare",
        "reference": "R10K",
        "notes": "",
    }
    data.update(overrides)
    return data


def create(client, **overrides) -> str:
    response = client.post(
        "/composants",
        data=payload(client, **overrides),
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return path_from_location(response.headers["location"])


def read_code(client, location: str) -> str:
    html = client.get(clean_path(location)).text
    match = re.search(r"SM-\d{6}", html)
    assert match, html
    return match.group(0)


def test_home_empty_state(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "L'inventaire est vide" in page.text
    assert "StuffManager" in page.text


def test_database_init_is_idempotent(tmp_path):
    db_path = tmp_path / "inv.sqlite"
    init_db(db_path)
    init_db(db_path)
    conn = connect(db_path)
    try:
        names = {category["name"] for category in list_categories(conn)}
    finally:
        conn.close()
    assert names == {
        "Câble",
        "Électronique",
        "Connectique",
        "Outil",
        "Consommable",
        "Autre",
    }


def test_create_item(client):
    location = create(client, nom="Résistance 10k", notes="lot 2024", quantite="4")
    page = client.get(clean_path(location))
    assert page.status_code == 200
    assert "Résistance 10k" in page.text
    assert "SM-000001" in page.text
    assert "R10K" in page.text
    assert "Rare" in page.text
    assert "Électronique" in page.text
    assert "lot 2024" in page.text
    assert 'value="4"' in page.text

    by_name = client.get("/?q=resistance")
    assert "Résistance 10k" in by_name.text
    by_ref = client.get("/?q=r10k")
    assert "Résistance 10k" in by_ref.text

    label = client.get(f"/composants/{item_id(location)}/etiquette")
    assert label.status_code == 200
    assert "SM-000001" in label.text
    assert "Résistance 10k" in label.text
    assert "Imprimer" in label.text


def test_filter_by_category(client):
    create(
        client,
        nom="Câble HDMI",
        categorie_id=category_id(client, "Câble"),
        reference="HDMI",
    )
    create(
        client,
        nom="Condensateur 100 nF",
        categorie_id=category_id(client, "Électronique"),
        reference="100nF",
    )
    page = client.get("/", params={"categorie": category_id(client, "Câble")})
    assert page.status_code == 200
    assert "Câble HDMI" in page.text
    assert "Condensateur 100 nF" not in page.text


def test_assign_item_to_location(client):
    created = client.post(
        "/emplacements",
        data={"nom": "Tiroir A"},
        follow_redirects=False,
    )
    assert created.status_code == 303, created.text
    loc_id = item_id(created.headers["location"])
    item = create(
        client,
        nom="Ruban de cuivre",
        emplacement_id=loc_id,
        spot="compartiment 3",
        reference="CU",
    )
    create(client, nom="Vis M3", reference="M3")

    page = client.get(f"/emplacements/{loc_id}")
    assert "Ruban de cuivre" in page.text
    assert "Vis M3" not in page.text

    detail = client.get(clean_path(item))
    assert "Tiroir A" in detail.text
    assert "compartiment 3" in detail.text

    filtered = client.get("/", params={"emplacement": loc_id})
    assert "Ruban de cuivre" in filtered.text
    assert "Vis M3" not in filtered.text

    unassigned = client.get("/", params={"emplacement": "aucun"})
    assert "Vis M3" in unassigned.text
    assert "Ruban de cuivre" not in unassigned.text

    blocked = client.post(f"/emplacements/{loc_id}/supprimer", follow_redirects=False)
    assert blocked.status_code == 400
    assert "Ruban de cuivre" in client.get(f"/emplacements/{loc_id}").text

    empty = client.post(
        "/emplacements",
        data={"nom": "Bac vide"},
        follow_redirects=False,
    )
    empty_id = item_id(empty.headers["location"])
    removed = client.post(
        f"/emplacements/{empty_id}/supprimer",
        follow_redirects=False,
    )
    assert removed.status_code == 303, removed.text
    assert "Bac vide" not in client.get("/emplacements").text

    label = client.get(f"/composants/{item_id(item)}/etiquette")
    assert "Ruban de cuivre" in label.text
    assert "Tiroir A" in label.text
    assert "compartiment 3" in label.text


def test_barcode_codes_are_unique_and_not_reused(client):
    first = create(client, nom="Premier", reference="A")
    second = create(client, nom="Second", reference="B")
    assert read_code(client, first) == "SM-000001"
    assert read_code(client, second) == "SM-000002"

    ident = item_id(first)
    png = client.get(f"/composants/{ident}/code128.png")
    assert png.status_code == 200
    assert png.headers["content-type"].startswith("image/png")
    assert png.content.startswith(b"\x89PNG")
    qr = client.get(f"/composants/{ident}/qr.png")
    assert qr.status_code == 200
    assert qr.content.startswith(b"\x89PNG")
    assert qr_payload(int(ident)) == f"/composants/{ident}"

    gone = client.post(f"/composants/{ident}/supprimer", follow_redirects=False)
    assert gone.status_code == 303
    assert client.get(f"/composants/{ident}").status_code == 404

    third = create(client, nom="Troisième", reference="C")
    assert read_code(client, third) == "SM-000003"
    assert item_id(third) != ident

    latest = item_id(third)
    removed_latest = client.post(
        f"/composants/{latest}/supprimer",
        follow_redirects=False,
    )
    assert removed_latest.status_code == 303
    fourth = create(client, nom="Quatrième", reference="D")
    assert read_code(client, fourth) == "SM-000004"
    assert item_id(fourth) != latest

    found = client.get("/?q=SM-000002", follow_redirects=False)
    assert found.status_code == 302
    assert clean_path(found.headers["location"]) == clean_path(second)

    by_qr = client.get(
        "/",
        params={"q": f"/composants/{item_id(second)}"},
        follow_redirects=False,
    )
    assert by_qr.status_code == 302
    assert clean_path(by_qr.headers["location"]) == clean_path(second)


def test_category_in_use_cannot_be_deleted(client):
    cable = category_id(client, "Câble")
    create(client, nom="Rallonge", categorie_id=cable, reference="RAL")
    denied = client.post(f"/categories/{cable}/supprimer", follow_redirects=False)
    assert denied.status_code == 400
    assert 'value="Câble"' in client.get("/categories").text

    added = client.post("/categories", data={"nom": "Capteurs"}, follow_redirects=False)
    assert added.status_code == 303, added.text
    cap = category_id(client, "Capteurs")
    renamed = client.post(
        f"/categories/{cap}/renommer",
        data={"nom": "Capteurs divers"},
        follow_redirects=False,
    )
    assert renamed.status_code == 303, renamed.text
    assert 'value="Capteurs divers"' in client.get("/categories").text
    removed = client.post(
        f"/categories/{category_id(client, 'Capteurs divers')}/supprimer",
        follow_redirects=False,
    )
    assert removed.status_code == 303, removed.text
    assert "Capteurs divers" not in client.get("/categories").text


def test_quantity_stepper(client):
    location = create(client, nom="Vis", quantite="2", reference="V")
    ident = item_id(location)
    inc = client.post(
        f"/composants/{ident}/quantite",
        data={"action": "inc", "quantite": "2"},
        follow_redirects=False,
    )
    assert inc.status_code == 303
    assert 'value="3"' in client.get(f"/composants/{ident}").text
    dec = client.post(
        f"/composants/{ident}/quantite",
        data={"action": "dec", "quantite": "3"},
        follow_redirects=True,
    )
    assert 'value="2"' in dec.text
    set_qty = client.post(
        f"/composants/{ident}/quantite",
        data={"action": "set", "quantite": "0"},
        follow_redirects=True,
    )
    assert 'value="0"' in set_qty.text
    floor = client.post(
        f"/composants/{ident}/quantite",
        data={"action": "dec", "quantite": "0"},
        follow_redirects=True,
    )
    assert 'value="0"' in floor.text


def test_photo_upload_becomes_jpeg(client):
    buffer = BytesIO()
    Image.new("RGB", (16, 10), (10, 20, 30)).save(buffer, format="PNG")
    response = client.post(
        "/composants",
        data=payload(client, nom="Module photo", reference="PIC"),
        files={"photo": ("module.png", buffer.getvalue(), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    page = client.get(clean_path(response.headers["location"]))
    match = re.search(r'src="(/photos/\d+\.jpg)"', page.text)
    assert match, page.text
    image = client.get(match.group(1))
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/jpeg")
    assert image.content[:2] == b"\xff\xd8"


def test_reject_invalid_photo_and_negative_quantity(client):
    bad_photo = client.post(
        "/composants",
        data=payload(client, nom="Photo bidon"),
        files={"photo": ("note.txt", b"pas une image", "text/plain")},
        follow_redirects=False,
    )
    assert bad_photo.status_code == 400
    assert "JPEG" in bad_photo.text
    assert "Photo bidon" not in client.get("/").text

    bad_qty = client.post(
        "/composants",
        data=payload(client, nom="Quantité négative", quantite="-3"),
        follow_redirects=False,
    )
    assert bad_qty.status_code == 400
    assert "Quantité négative" not in client.get("/").text
