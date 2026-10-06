from io import BytesIO
import sqlite3

import pytest
from PIL import Image

from app.db import connect, create_location, delete_location, init_db
from app.images import qr_png
from test_inventory import category_id, clean_path, create


def make_location(client, name="Placard de la cuisine"):
    response = client.post("/emplacements", data={"nom": name}, follow_redirects=False)
    assert response.status_code == 303
    return clean_path(response.headers["location"])


def test_location_qr_opens_its_inventory(client):
    path = make_location(client)
    create(client, nom="Plat à gratin", categorie_id=category_id(client, "Cuisine"),
           emplacement_id=path.rsplit("/", 1)[-1])
    create(client, nom="Livre ailleurs", categorie_id=category_id(client, "Livre"))
    page = client.get(path)
    assert "Plat à gratin" in page.text
    assert "Livre ailleurs" not in page.text
    assert f'href="{path}/etiquette"' in page.text

    label = client.get(f"{path}/etiquette")
    assert label.status_code == 200
    assert "Placard de la cuisine" in label.text
    assert "Imprimer" in label.text
    assert "Télécharger le QR code" in label.text
    assert f"http://testserver{path}" in label.text

    qr = client.get(f"{path}/qr.png")
    assert qr.status_code == 200
    assert qr.headers["content-type"] == "image/png"
    assert qr.headers["cache-control"] == "no-store"
    # Compare the generated image against the expected absolute scan target.
    assert qr.content == qr_png(f"http://testserver{path}")
    Image.open(BytesIO(qr.content)).verify()
    assert client.get(f"http://testserver{path}").status_code == 200


def test_location_qr_uses_accessed_host_scheme_and_port(client):
    path = make_location(client)
    response = client.get(f"https://maison.example:8443{path}/qr.png")
    assert response.content == qr_png(f"https://maison.example:8443{path}")
    assert response.content != client.get(f"{path}/qr.png").content


def test_location_qr_survives_rename_and_empty_inventory(client):
    path = make_location(client)
    original = client.get(f"{path}/qr.png").content
    client.post(f"{path}/renommer", data={"nom": "Cuisine"})
    assert client.get(f"{path}/qr.png").content == original
    assert "Cuisine" in client.get(f"{path}/etiquette").text
    assert "Aucun objet ici" in client.get(path).text


@pytest.mark.parametrize("suffix", ["", "/qr.png", "/etiquette"])
def test_missing_and_deleted_location_returns_404(client, suffix):
    assert client.get(f"/emplacements/999{suffix}").status_code == 404
    path = make_location(client)
    client.post(f"{path}/supprimer")
    assert client.get(f"{path}{suffix}").status_code == 404
    assert make_location(client, "Salon") != path


def test_location_counter_upgrades_existing_db_and_survives_restart(tmp_path):
    db_path = tmp_path / "existing.db"
    with sqlite3.connect(db_path) as legacy:
        legacy.executescript("""
            CREATE TABLE locations (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            INSERT INTO locations VALUES (42, 'Ancien lieu', '2024-01-01');
        """)
    init_db(db_path)
    with connect(db_path) as conn:
        assert conn.execute("SELECT name FROM locations WHERE id = 42").fetchone()[0] == "Ancien lieu"
        delete_location(conn, 42)
        new_id = create_location(conn, "Cuisine")
        assert new_id > 42
        delete_location(conn, new_id)
    init_db(db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        assert create_location(conn, "Salon") > new_id


def test_household_wording_and_custom_categories(client):
    home = client.get("/").text
    assert "Inventaire de la maison" in home
    assert "Ajouter un objet" in home
    client.post("/categories", data={"nom": "Souvenirs"})
    path = create(client, nom="Album de famille", categorie_id=category_id(client, "Souvenirs"))
    assert "Album de famille" in client.get(path).text
