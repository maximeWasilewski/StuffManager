"""SQLite schema, startup setup, and queries.

Tables are created on startup if they are missing. There is no separate
migration tool: this is a single-user file database.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

USAGE_LEVELS: dict[str, str] = {
    "jamais": "Jamais",
    "rare": "Rare",
    "occasionnel": "Occasionnel",
    "frequent": "Fréquent",
}

DEFAULT_CATEGORIES: tuple[str, ...] = (
    "Câble",
    "Électronique",
    "Connectique",
    "Outil",
    "Consommable",
    "Autre",
)

_MONTHS: tuple[str, ...] = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

# Item ids are AUTOINCREMENT so a deleted fiche never reuses its URL.
# Printed QR codes point at /composants/{id}; reuse would open the wrong part.
_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
        quantity INTEGER NOT NULL CHECK (quantity >= 0),
        location_id INTEGER REFERENCES locations(id) ON DELETE RESTRICT,
        spot TEXT,
        usage_level TEXT NOT NULL CHECK (
            usage_level IN ('jamais', 'rare', 'occasionnel', 'frequent')
        ),
        notes TEXT,
        reference TEXT,
        has_photo INTEGER NOT NULL DEFAULT 0,
        search_text TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_items_category ON items(category_id)",
    "CREATE INDEX IF NOT EXISTS idx_items_location ON items(location_id)",
    "CREATE INDEX IF NOT EXISTS idx_items_usage ON items(usage_level)",
)

_ITEM_SELECT = """
SELECT i.id, i.code, i.name, i.category_id, i.quantity, i.location_id, i.spot,
       i.usage_level, i.notes, i.reference, i.has_photo, i.search_text,
       i.created_at, i.updated_at,
       c.name AS category_name,
       l.name AS location_name
FROM items i
JOIN categories c ON c.id = i.category_id
LEFT JOIN locations l ON l.id = i.location_id
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fold(value: str) -> str:
    """Lowercase and strip accents so « Résistance » matches « resistance »."""
    decomposed = unicodedata.normalize("NFD", value)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return stripped.casefold()


def format_when(iso: str) -> str:
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"


def place_label(location_name: str | None, spot: str | None) -> str:
    if not location_name:
        return "Sans emplacement"
    if spot:
        return f"{location_name} · {spot}"
    return location_name


def build_search(name: str, reference: str | None, code: str) -> str:
    parts = [name, reference or "", code]
    return " ".join(fold(part) for part in parts if part)


def like_pattern(folded: str) -> str:
    escaped = folded.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        for statement in _SCHEMA:
            conn.execute(statement)
        conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('next_item_number', '1')"
        )
        count = conn.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
        if count == 0:
            stamp = now_iso()
            conn.executemany(
                "INSERT INTO categories (name, created_at) VALUES (?, ?)",
                [(name, stamp) for name in DEFAULT_CATEGORIES],
            )
        conn.commit()
    finally:
        conn.close()


def _enrich(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["usage_label"] = USAGE_LEVELS.get(item["usage_level"], item["usage_level"])
    item["place_label"] = place_label(item["location_name"], item["spot"])
    item["created_label"] = format_when(item["created_at"])
    return item


def _sorted(items: list[dict]) -> list[dict]:
    items.sort(key=lambda item: (fold(item["name"]), item["id"]))
    return items


def list_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id, c.name, c.created_at,
               (SELECT COUNT(*) FROM items i WHERE i.category_id = c.id) AS item_count
        FROM categories c
        """
    ).fetchall()
    categories = [dict(row) for row in rows]
    categories.sort(key=lambda category: (fold(category["name"]), category["id"]))
    return categories


def get_category(conn: sqlite3.Connection, category_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT c.id, c.name, c.created_at,
               (SELECT COUNT(*) FROM items i WHERE i.category_id = c.id) AS item_count
        FROM categories c
        WHERE c.id = ?
        """,
        (category_id,),
    ).fetchone()
    return dict(row) if row else None


def list_locations(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT l.id, l.name, l.created_at,
               (SELECT COUNT(*) FROM items i WHERE i.location_id = l.id) AS item_count
        FROM locations l
        """
    ).fetchall()
    locations = [dict(row) for row in rows]
    locations.sort(key=lambda location: (fold(location["name"]), location["id"]))
    return locations


def get_location(conn: sqlite3.Connection, location_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT l.id, l.name, l.created_at,
               (SELECT COUNT(*) FROM items i WHERE i.location_id = l.id) AS item_count
        FROM locations l
        WHERE l.id = ?
        """,
        (location_id,),
    ).fetchone()
    return dict(row) if row else None


def count_unassigned(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM items WHERE location_id IS NULL"
    ).fetchone()
    return int(row["c"])


def count_items(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()
    return int(row["c"])


def _label_taken(
    conn: sqlite3.Connection,
    table: str,
    name: str,
    exclude_id: int | None = None,
) -> bool:
    if table not in {"categories", "locations"}:
        raise RuntimeError(table)
    rows = conn.execute(f"SELECT id, name FROM {table}").fetchall()
    target = fold(name)
    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue
        if fold(row["name"]) == target:
            return True
    return False


def create_category(conn: sqlite3.Connection, name: str) -> int:
    if _label_taken(conn, "categories", name):
        raise ValueError("Cette catégorie existe déjà.")
    try:
        cursor = conn.execute(
            "INSERT INTO categories (name, created_at) VALUES (?, ?)",
            (name, now_iso()),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Cette catégorie existe déjà.") from exc
    return int(cursor.lastrowid)


def rename_category(conn: sqlite3.Connection, category_id: int, name: str) -> None:
    if get_category(conn, category_id) is None:
        raise KeyError(category_id)
    if _label_taken(conn, "categories", name, exclude_id=category_id):
        raise ValueError("Cette catégorie existe déjà.")
    try:
        conn.execute(
            "UPDATE categories SET name = ? WHERE id = ?",
            (name, category_id),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Cette catégorie existe déjà.") from exc


def delete_category(conn: sqlite3.Connection, category_id: int) -> None:
    category = get_category(conn, category_id)
    if category is None:
        raise KeyError(category_id)
    if category["item_count"]:
        raise ValueError(
            "Impossible de supprimer une catégorie encore utilisée par des composants."
        )
    conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))


def create_location(conn: sqlite3.Connection, name: str) -> int:
    if _label_taken(conn, "locations", name):
        raise ValueError("Cet emplacement existe déjà.")
    try:
        cursor = conn.execute(
            "INSERT INTO locations (name, created_at) VALUES (?, ?)",
            (name, now_iso()),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Cet emplacement existe déjà.") from exc
    return int(cursor.lastrowid)


def rename_location(conn: sqlite3.Connection, location_id: int, name: str) -> None:
    if get_location(conn, location_id) is None:
        raise KeyError(location_id)
    if _label_taken(conn, "locations", name, exclude_id=location_id):
        raise ValueError("Cet emplacement existe déjà.")
    try:
        conn.execute(
            "UPDATE locations SET name = ? WHERE id = ?",
            (name, location_id),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Cet emplacement existe déjà.") from exc


def delete_location(conn: sqlite3.Connection, location_id: int) -> None:
    location = get_location(conn, location_id)
    if location is None:
        raise KeyError(location_id)
    if location["item_count"]:
        raise ValueError(
            "Impossible de supprimer un emplacement qui contient encore des composants."
        )
    conn.execute("DELETE FROM locations WHERE id = ?", (location_id,))


def _require_category(conn: sqlite3.Connection, category_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM categories WHERE id = ?",
        (category_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Cette catégorie n'existe pas.")


def _require_location(conn: sqlite3.Connection, location_id: int) -> None:
    row = conn.execute(
        "SELECT 1 FROM locations WHERE id = ?",
        (location_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Cet emplacement n'existe pas.")


def _allocate_code(conn: sqlite3.Connection) -> str:
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('next_item_number', '1')"
    )
    # RETURNING yields the new counter. The code uses the previous value,
    # so a rolled-back insert does not skip or reuse a number.
    row = conn.execute(
        """
        UPDATE meta
        SET value = CAST(value AS INTEGER) + 1
        WHERE key = 'next_item_number'
        RETURNING value
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("Compteur de codes absent.")
    number = int(row["value"]) - 1
    if number < 1:
        raise RuntimeError("Compteur de codes invalide.")
    return f"SM-{number:06d}"


def create_item(
    conn: sqlite3.Connection,
    *,
    name: str,
    category_id: int,
    quantity: int,
    location_id: int | None,
    spot: str | None,
    usage_level: str,
    notes: str | None,
    reference: str | None,
) -> tuple[int, str]:
    _require_category(conn, category_id)
    if location_id is not None:
        _require_location(conn, location_id)
    code = _allocate_code(conn)
    stamp = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO items (
            code, name, category_id, quantity, location_id, spot,
            usage_level, notes, reference, has_photo, search_text,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
        (
            code,
            name,
            category_id,
            quantity,
            location_id,
            spot,
            usage_level,
            notes,
            reference,
            build_search(name, reference, code),
            stamp,
            stamp,
        ),
    )
    return int(cursor.lastrowid), code


def update_item(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    name: str,
    category_id: int,
    quantity: int,
    location_id: int | None,
    spot: str | None,
    usage_level: str,
    notes: str | None,
    reference: str | None,
    has_photo: bool,
) -> None:
    existing = get_item(conn, item_id)
    if existing is None:
        raise KeyError(item_id)
    _require_category(conn, category_id)
    if location_id is not None:
        _require_location(conn, location_id)
    conn.execute(
        """
        UPDATE items
        SET name = ?, category_id = ?, quantity = ?, location_id = ?, spot = ?,
            usage_level = ?, notes = ?, reference = ?, has_photo = ?,
            search_text = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            name,
            category_id,
            quantity,
            location_id,
            spot,
            usage_level,
            notes,
            reference,
            1 if has_photo else 0,
            build_search(name, reference, existing["code"]),
            now_iso(),
            item_id,
        ),
    )


def set_has_photo(conn: sqlite3.Connection, item_id: int, has_photo: bool) -> None:
    conn.execute(
        "UPDATE items SET has_photo = ?, updated_at = ? WHERE id = ?",
        (1 if has_photo else 0, now_iso(), item_id),
    )


def set_quantity(conn: sqlite3.Connection, item_id: int, quantity: int) -> None:
    conn.execute(
        "UPDATE items SET quantity = ?, updated_at = ? WHERE id = ?",
        (quantity, now_iso(), item_id),
    )


def get_item(conn: sqlite3.Connection, item_id: int) -> dict | None:
    row = conn.execute(_ITEM_SELECT + " WHERE i.id = ?", (item_id,)).fetchone()
    return _enrich(row) if row else None


def find_by_code(conn: sqlite3.Connection, code: str) -> dict | None:
    row = conn.execute(
        _ITEM_SELECT + " WHERE i.code = ? COLLATE NOCASE",
        (code.strip(),),
    ).fetchone()
    return _enrich(row) if row else None


def delete_item(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))


def search_items(
    conn: sqlite3.Connection,
    *,
    q: str,
    category_id: int | None,
    location_id: int | None,
    unassigned: bool,
    usage: str | None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    if q:
        clauses.append("i.search_text LIKE ? ESCAPE '!'")
        params.append(like_pattern(fold(q)))
    if category_id is not None:
        clauses.append("i.category_id = ?")
        params.append(category_id)
    if unassigned:
        clauses.append("i.location_id IS NULL")
    elif location_id is not None:
        clauses.append("i.location_id = ?")
        params.append(location_id)
    if usage:
        clauses.append("i.usage_level = ?")
        params.append(usage)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(_ITEM_SELECT + where, params).fetchall()
    return _sorted([_enrich(row) for row in rows])


def items_for_location(conn: sqlite3.Connection, location_id: int) -> list[dict]:
    rows = conn.execute(
        _ITEM_SELECT + " WHERE i.location_id = ?",
        (location_id,),
    ).fetchall()
    return _sorted([_enrich(row) for row in rows])


def items_without_location(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(_ITEM_SELECT + " WHERE i.location_id IS NULL").fetchall()
    return _sorted([_enrich(row) for row in rows])
