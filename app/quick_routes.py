"""Two-step quick add: a photo draft, then an explicit stock confirmation."""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app import recognition
from app.db import (create_item, get_item, list_categories, list_locations,
                    search_items, set_has_photo, set_quantity)
from app.forms import DEFAULT_VALUES, QUANTITY_MAX, parse_item_form, parse_quantity
from app.images import MAX_PHOTO_BYTES, PhotoError, photo_file, process_photo, write_photo

TTL = 24 * 60 * 60


def _same_origin(request: Request) -> None:
    source = request.headers.get("origin") or request.headers.get("referer")
    if source and urlsplit(source).netloc != request.url.netloc:
        raise HTTPException(403, "Ouvrez l'ajout rapide depuis StuffManager.")


def _draft(conn, token):
    row = conn.execute("SELECT * FROM quick_drafts WHERE token = ? AND created_at > ?",
                       (token, time.time() - TTL)).fetchone()
    if row is None:
        raise HTTPException(404, "Cette photo a expiré. Prenez une nouvelle photo.")
    return row


def _inventory(conn):
    return search_items(conn, q="", category_id=None, location_id=None, unassigned=False, usage=None)


def create_quick_router(render, get_conn, redirect):
    router = APIRouter()

    def result_page(request, conn, draft, *, q=None, error=None, status_code=200, values=None):
        analysis = json.loads(draft["analysis"])
        categories = list_categories(conn)
        if q is None:
            q = analysis["name"]
        q = q[:240]
        defaults = dict(DEFAULT_VALUES, nom=analysis["name"], reference=analysis["reference"], notes=analysis["notes"])
        defaults["categorie_id"] = next((str(c["id"]) for c in categories if c["name"] == analysis["category"]), "")
        return render(request, "quick_result.html", nav="rapide", token=draft["token"],
                      analysis=analysis, q=q, candidates=recognition.suggestions(_inventory(conn), q, analysis["reference"]),
                      categories=categories, locations=list_locations(conn), values=values or defaults,
                      error=error, status_code=status_code)

    @router.get("/ajout-rapide")
    async def start(request: Request):
        return render(request, "quick_start.html", nav="rapide", configured=bool(recognition.recognition_settings()[0]))

    @router.post("/ajout-rapide")
    async def analyze(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        _same_origin(request)
        conn.execute("DELETE FROM quick_drafts WHERE created_at <= ?", (time.time() - TTL,))
        conn.commit()  # Never hold a SQLite write lock during a network request.
        if conn.execute("SELECT COUNT(*) FROM quick_drafts WHERE completed_item IS NULL").fetchone()[0] >= 100:
            return render(request, "quick_start.html", nav="rapide", configured=bool(recognition.recognition_settings()[0]),
                          error="Trop de photos en attente. Terminez un ajout ou réessayez demain.", status_code=429)
        form = await request.form()
        upload = form.get("photo")
        if not getattr(upload, "filename", None):
            upload = form.get("galerie")
        try:
            if not getattr(upload, "filename", None):
                raise PhotoError("Prenez une photo ou choisissez une image.")
            raw = bytearray()
            while chunk := await upload.read(1024 * 1024):
                raw.extend(chunk)
                if len(raw) > MAX_PHOTO_BYTES:
                    raise PhotoError("La photo dépasse 8 Mo.")
            photo = process_photo(bytes(raw))
        except PhotoError as exc:
            return render(request, "quick_start.html", nav="rapide", configured=bool(recognition.recognition_settings()[0]),
                          error=str(exc), status_code=400)
        try:
            analysis = await recognition.recognize(photo, [c["name"] for c in list_categories(conn)])
            analysis["warning"] = ""
        except recognition.RecognitionError as exc:
            analysis = {"name": "", "category": "", "reference": "", "notes": "",
                        "confidence": "uncertain", "warning": str(exc)}
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO quick_drafts(token, photo, analysis, created_at) VALUES (?, ?, ?, ?)",
                     (token, photo, json.dumps(analysis, ensure_ascii=False), time.time()))
        return redirect("/ajout-rapide/" + token)

    @router.get("/ajout-rapide/{token}")
    async def review(token: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        draft = _draft(conn, token)
        if draft["completed_item"]:
            return redirect(f"/composants/{draft['completed_item']}")
        return result_page(request, conn, draft, q=request.query_params.get("q"))

    @router.get("/ajout-rapide/{token}/photo")
    async def preview(token: str, conn: sqlite3.Connection = Depends(get_conn)):
        draft = _draft(conn, token)
        return Response(draft["photo"], media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.post("/ajout-rapide/{token}/confirmer")
    async def confirm(token: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        _same_origin(request)
        form = await request.form()
        # Serialize confirmations across workers and make double taps/retries
        # idempotent. Quantity is read only after acquiring the write lock.
        conn.execute("BEGIN IMMEDIATE")
        draft = _draft(conn, token)
        if draft["completed_item"]:
            return redirect(f"/composants/{draft['completed_item']}")
        q = str(form.get("q") or "")[:240]
        mode = str(form.get("mode") or "")
        if mode == "existing":
            try:
                quantity = parse_quantity(str(form.get("quantite") or ""))
                if quantity < 1:
                    raise ValueError("Ajoutez au moins un objet.")
                raw_id = str(form.get("item_id") or "")
                if not raw_id.isdigit():
                    raise ValueError("Choisissez une fiche existante.")
                item = get_item(conn, int(raw_id))
                analysis = json.loads(draft["analysis"])
                allowed = recognition.suggestions(_inventory(conn), q, analysis["reference"])
                if item is None or item["id"] not in {candidate["id"] for candidate in allowed}:
                    raise ValueError("Cette fiche n'est plus proposée. Vérifiez les résultats.")
                if (str(item["quantity"]) != str(form.get("expected_quantity")) or
                    str(item["location_id"] or "") != str(form.get("expected_location")) or
                    (item["spot"] or "") != str(form.get("expected_spot") or "")):
                    return result_page(request, conn, draft, q=q, error="Le stock ou l'emplacement a changé. Vérifiez puis confirmez à nouveau.", status_code=409)
                if item["quantity"] + quantity > QUANTITY_MAX:
                    raise ValueError("La quantité totale serait trop grande.")
                set_quantity(conn, item["id"], item["quantity"] + quantity)
                item_id = item["id"]
                # Keep existing photo, notes, used quantity and exact storage spot.
            except (ValueError, KeyError) as exc:
                return result_page(request, conn, draft, q=q, error=str(exc), status_code=400)
        elif mode == "new":
            categories, locations = list_categories(conn), list_locations(conn)
            submitted = dict(form)
            submitted.update(niveau="occasionnel", utilises="0")
            data, errors, values = parse_item_form(submitted, category_ids={c["id"] for c in categories},
                                                  location_ids={loc["id"] for loc in locations},
                                                  tracking_ids={c["id"] for c in categories if c["track_usage"]})
            if data and data.quantity < 1:
                errors.append("Ajoutez au moins un objet.")
            if errors or data is None:
                return result_page(request, conn, draft, q=q, error=" ".join(errors), status_code=400, values=values)
            item_id, _ = create_item(conn, name=data.name, category_id=data.category_id, quantity=data.quantity,
                                      used_quantity=0, location_id=data.location_id, spot=data.spot,
                                      usage_level=data.usage_level, notes=data.notes, reference=data.reference)
            path = photo_file(request.app.state.photos_dir, item_id)
            try:
                write_photo(path, draft["photo"])
                set_has_photo(conn, item_id, True)
            except OSError:
                conn.rollback()
                path.unlink(missing_ok=True)
                return result_page(request, conn, draft, q=q, error="La photo n'a pas pu être enregistrée. Réessayez.", status_code=500, values=values)
        else:
            return result_page(request, conn, draft, q=q, error="Choisissez une fiche existante ou créez une nouvelle fiche.", status_code=400)
        conn.execute("UPDATE quick_drafts SET completed_item = ?, photo = X'' WHERE token = ?", (item_id, token))
        return redirect(f"/composants/{item_id}", "quantite" if mode == "existing" else "cree")

    return router
