"""Parse and validate HTML form fields. Messages are shown as-is in the UI."""

from __future__ import annotations

from dataclasses import dataclass

from app.db import USAGE_LEVELS

ITEM_NAME_MAX = 160
LABEL_MAX = 60
REFERENCE_MAX = 80
SPOT_MAX = 80
NOTES_MAX = 4000
QUANTITY_MAX = 1_000_000

DEFAULT_VALUES: dict[str, str] = {
    "nom": "",
    "categorie_id": "",
    "quantite": "1",
    "emplacement_id": "",
    "spot": "",
    "niveau": "occasionnel",
    "reference": "",
    "notes": "",
    "utilises": "0",
}


@dataclass
class ItemData:
    name: str
    category_id: int
    quantity: int
    location_id: int | None
    spot: str | None
    usage_level: str
    notes: str | None
    reference: str | None
    used_quantity: int


def parse_quantity(raw: str) -> int:
    if not raw.isdigit():
        raise ValueError("La quantité doit être un nombre entier, zéro ou plus.")
    if len(raw) > 7 or int(raw) > QUANTITY_MAX:
        raise ValueError("La quantité est trop grande.")
    return int(raw)


def parse_label(raw: object) -> str:
    name = " ".join(str(raw or "").split())
    if not name:
        raise ValueError("Le nom est obligatoire.")
    if len(name) > LABEL_MAX:
        raise ValueError(f"Le nom est trop long ({LABEL_MAX} caractères maximum).")
    return name


def values_from_item(item: dict) -> dict[str, str]:
    return {
        "nom": item["name"],
        "categorie_id": str(item["category_id"]),
        "quantite": str(item["quantity"]),
        "emplacement_id": "" if item["location_id"] is None else str(item["location_id"]),
        "spot": item["spot"] or "",
        "niveau": item["usage_level"],
        "reference": item["reference"] or "",
        "notes": item["notes"] or "",
        "utilises": str(item["used_quantity"]),
    }


def parse_item_form(
    form,
    *,
    category_ids: set[int],
    location_ids: set[int],
    tracking_ids: set[int],
    preserved_used: int = 0,
) -> tuple[ItemData | None, list[str], dict[str, str]]:
    errors: list[str] = []
    nom = " ".join(str(form.get("nom") or "").split())
    raw_qty = str(form.get("quantite") or "").strip()
    raw_used = str(form.get("utilises") or "").strip()
    raw_cat = str(form.get("categorie_id") or "").strip()
    raw_loc = str(form.get("emplacement_id") or "").strip()
    spot_raw = str(form.get("spot") or "")
    ref_raw = str(form.get("reference") or "")
    notes_raw = str(form.get("notes") or "")
    niveau = str(form.get("niveau") or "").strip()

    values = {
        "nom": nom,
        "categorie_id": raw_cat,
        "quantite": raw_qty,
        "utilises": raw_used,
        "emplacement_id": raw_loc,
        "spot": spot_raw,
        "niveau": niveau,
        "reference": ref_raw,
        "notes": notes_raw,
    }

    if not nom:
        errors.append("Le nom est obligatoire.")
    elif len(nom) > ITEM_NAME_MAX:
        errors.append(f"Le nom est trop long ({ITEM_NAME_MAX} caractères maximum).")

    quantity: int | None = None
    if not raw_qty:
        errors.append("La quantité est obligatoire.")
    else:
        try:
            quantity = parse_quantity(raw_qty)
        except ValueError as exc:
            errors.append(str(exc))

    category_id: int | None = None
    if not raw_cat.isdigit() or int(raw_cat) not in category_ids:
        errors.append("Choisissez une catégorie.")
    else:
        category_id = int(raw_cat)

    location_id: int | None = None
    if raw_loc:
        if not raw_loc.isdigit() or int(raw_loc) not in location_ids:
            errors.append("Choisissez un emplacement de la liste, ou laissez « Aucun ».")
        else:
            location_id = int(raw_loc)

    if niveau not in USAGE_LEVELS:
        errors.append("Choisissez un niveau d'utilisation.")

    used_quantity = preserved_used
    tracks = category_id is not None and category_id in tracking_ids
    if tracks:
        if not raw_used.isdigit():
            if raw_used:
                errors.append(
                    "Les utilisés doivent être un nombre entier entre 0 et la quantité."
                )
            else:
                errors.append("Indiquez combien sont utilisés.")
        elif quantity is not None and (len(raw_used) > 7 or int(raw_used) > quantity):
            errors.append("Les utilisés ne peuvent pas dépasser la quantité.")
        elif quantity is not None:
            used_quantity = int(raw_used)

    spot = " ".join(spot_raw.split())
    if len(spot) > SPOT_MAX:
        errors.append(
            f"Le détail d'emplacement est trop long ({SPOT_MAX} caractères maximum)."
        )

    reference = " ".join(ref_raw.split())
    if len(reference) > REFERENCE_MAX:
        errors.append(
            f"La référence est trop longue ({REFERENCE_MAX} caractères maximum)."
        )

    notes = notes_raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(notes) > NOTES_MAX:
        errors.append(f"Les notes sont trop longues ({NOTES_MAX} caractères maximum).")

    if (
        errors
        or quantity is None
        or category_id is None
        or niveau not in USAGE_LEVELS
        or len(spot) > SPOT_MAX
        or len(reference) > REFERENCE_MAX
        or len(notes) > NOTES_MAX
    ):
        return None, errors, values

    return (
        ItemData(
            name=nom,
            category_id=category_id,
            quantity=quantity,
            location_id=location_id,
            spot=spot or None,
            usage_level=niveau,
            notes=notes or None,
            reference=reference or None,
            used_quantity=used_quantity,
        ),
        errors,
        values,
    )
