"""StuffManager web app. Server-rendered HTML, one user, one SQLite file."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import (
    USAGE_LEVELS,
    connect,
    count_items,
    count_unassigned,
    create_category,
    create_item,
    create_location,
    delete_category,
    delete_item,
    delete_location,
    find_by_code,
    get_item,
    get_location,
    init_db,
    items_for_location,
    items_without_location,
    list_categories,
    list_locations,
    rename_category,
    rename_location,
    search_items,
    set_has_photo,
    set_quantity,
    update_item,
)
from app.forms import (
    QUANTITY_MAX,
    DEFAULT_VALUES,
    parse_item_form,
    parse_label,
    parse_quantity,
    values_from_item,
)
from app.images import (
    MAX_PHOTO_BYTES,
    PhotoError,
    code128_png,
    photo_file,
    process_photo,
    qr_payload,
    qr_png,
    remove_photo,
    write_photo,
)

APP_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["usage_levels"] = USAGE_LEVELS

MESSAGES = {
    "cree": "Composant enregistré.",
    "modifie": "Modifications enregistrées.",
    "supprime": "Composant supprimé.",
    "quantite": "Quantité mise à jour.",
    "categorie-ajoutee": "Catégorie ajoutée.",
    "categorie-renommee": "Catégorie renommée.",
    "categorie-supprimee": "Catégorie supprimée.",
    "emplacement-ajoute": "Emplacement ajouté.",
    "emplacement-renomme": "Emplacement renommé.",
    "emplacement-supprime": "Emplacement supprimé.",
}

router = APIRouter()


def render(request: Request, name: str, *, status_code: int = 200, **context):
    if "message" not in context:
        context["message"] = MESSAGES.get(request.query_params.get("msg", ""))
    context.setdefault("error", None)
    context.setdefault("nav", "")
    context["request"] = request
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def redirect(path: str, msg: str | None = None) -> RedirectResponse:
    if msg:
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}msg={msg}"
    return RedirectResponse(path, status_code=303)


async def get_conn(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def attach_photos(items: list[dict], photos_dir: Path) -> None:
    for item in items:
        if item["has_photo"] and not photo_file(photos_dir, item["id"]).is_file():
            item["has_photo"] = 0


async def read_limited(upload, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise PhotoError("La photo dépasse 8 Mo.")
        chunks.append(chunk)
    return b"".join(chunks)


async def uploaded_photo(form) -> tuple[bytes | None, str | None]:
    photo = form.get("photo")
    if photo is None or isinstance(photo, str):
        return None, None
    if not (getattr(photo, "filename", None) or ""):
        return None, None
    try:
        data = await read_limited(photo, MAX_PHOTO_BYTES)
        if not data:
            return None, None
        return process_photo(data), None
    except PhotoError as exc:
        return None, str(exc)


def _attempt(form) -> str:
    return str(form.get("nom") or "")[:200]


# --- stock ---


@router.get("/")
async def home(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    q = request.query_params.get("q", "").strip()
    raw_category = request.query_params.get("categorie", "").strip()
    raw_location = request.query_params.get("emplacement", "").strip()
    raw_usage = request.query_params.get("niveau", "").strip()

    empty_hint = None
    if q:
        found = find_by_code(conn, q)
        if found:
            return RedirectResponse(f"/composants/{found['id']}", status_code=302)
        path_match = re.fullmatch(r"/composants/(\d+)/?", q)
        if path_match:
            by_path = get_item(conn, int(path_match.group(1)))
            if by_path:
                return RedirectResponse(f"/composants/{by_path['id']}", status_code=302)
            empty_hint = "Cette fiche n'existe pas ou a été supprimée."

    categories = list_categories(conn)
    locations = list_locations(conn)
    category_ids = {category["id"] for category in categories}
    location_ids = {location["id"] for location in locations}
    category_id = (
        int(raw_category)
        if raw_category.isdigit() and int(raw_category) in category_ids
        else None
    )
    unassigned = raw_location == "aucun"
    location_id = (
        int(raw_location)
        if raw_location.isdigit() and int(raw_location) in location_ids
        else None
    )
    usage = raw_usage if raw_usage in USAGE_LEVELS else None

    items: list[dict] = []
    if empty_hint is None:
        items = search_items(
            conn,
            q=q,
            category_id=category_id,
            location_id=location_id,
            unassigned=unassigned,
            usage=usage,
        )
        attach_photos(items, request.app.state.photos_dir)
        if not items and re.fullmatch(r"SM-\d{6}", q, re.IGNORECASE):
            empty_hint = "Aucun composant ne porte ce code."

    filtered = bool(q or category_id or unassigned or location_id or usage)
    return render(
        request,
        "index.html",
        nav="home",
        items=items,
        categories=categories,
        locations=locations,
        q=q,
        categorie=str(category_id) if category_id else "",
        emplacement="aucun" if unassigned else (str(location_id) if location_id else ""),
        niveau=usage or "",
        filtered=filtered,
        filters_open=bool(category_id or unassigned or location_id or usage),
        total=count_items(conn),
        empty_hint=empty_hint,
    )


# --- fiches ---


def _form_context(conn: sqlite3.Connection):
    categories = list_categories(conn)
    locations = list_locations(conn)
    return categories, locations


@router.get("/composants/nouveau")
async def new_item(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    categories, locations = _form_context(conn)
    return render(
        request,
        "item_form.html",
        nav="ajouter",
        item=None,
        values=dict(DEFAULT_VALUES),
        errors=[],
        categories=categories,
        locations=locations,
    )


@router.post("/composants")
async def create_composant(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    form = await request.form()
    categories, locations = _form_context(conn)
    data, errors, values = parse_item_form(
        form,
        category_ids={category["id"] for category in categories},
        location_ids={location["id"] for location in locations},
    )
    photo_bytes, photo_error = await uploaded_photo(form)
    if photo_error:
        errors.append(photo_error)
        data = None
    elif photo_bytes is not None and errors:
        errors.append("La photo n'a pas été enregistrée : sélectionnez-la à nouveau.")
    if data is None:
        return render(
            request,
            "item_form.html",
            status_code=400,
            nav="ajouter",
            item=None,
            values=values,
            errors=errors,
            categories=categories,
            locations=locations,
        )
    try:
        item_id, _code = create_item(
            conn,
            name=data.name,
            category_id=data.category_id,
            quantity=data.quantity,
            location_id=data.location_id,
            spot=data.spot,
            usage_level=data.usage_level,
            notes=data.notes,
            reference=data.reference,
        )
    except ValueError as exc:
        errors.append(str(exc))
        return render(
            request,
            "item_form.html",
            status_code=400,
            nav="ajouter",
            item=None,
            values=values,
            errors=errors,
            categories=categories,
            locations=locations,
        )
    if photo_bytes is not None:
        path = photo_file(request.app.state.photos_dir, item_id)
        try:
            write_photo(path, photo_bytes)
            set_has_photo(conn, item_id, True)
        except Exception:
            remove_photo(path)
            raise
    return redirect(f"/composants/{item_id}", "cree")


def _require_item(conn: sqlite3.Connection, item_id: int) -> dict:
    item = get_item(conn, item_id)
    if item is None:
        raise HTTPException(status_code=404)
    return item


@router.get("/composants/{item_id}")
async def item_detail(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    item = _require_item(conn, item_id)
    attach_photos([item], request.app.state.photos_dir)
    return render(request, "item_detail.html", nav="home", item=item)


@router.get("/composants/{item_id}/modifier")
async def edit_item_form(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    item = _require_item(conn, item_id)
    attach_photos([item], request.app.state.photos_dir)
    categories, locations = _form_context(conn)
    return render(
        request,
        "item_form.html",
        nav="home",
        item=item,
        values=values_from_item(item),
        errors=[],
        categories=categories,
        locations=locations,
    )


@router.post("/composants/{item_id}")
async def update_composant(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    item = _require_item(conn, item_id)
    attach_photos([item], request.app.state.photos_dir)
    form = await request.form()
    categories, locations = _form_context(conn)
    data, errors, values = parse_item_form(
        form,
        category_ids={category["id"] for category in categories},
        location_ids={location["id"] for location in locations},
    )
    photo_bytes, photo_error = await uploaded_photo(form)
    if photo_error:
        errors.append(photo_error)
        data = None
    elif photo_bytes is not None and errors:
        errors.append("La photo n'a pas été enregistrée : sélectionnez-la à nouveau.")
    if data is None:
        return render(
            request,
            "item_form.html",
            status_code=400,
            nav="home",
            item=item,
            values=values,
            errors=errors,
            categories=categories,
            locations=locations,
        )
    wants_remove = str(form.get("supprimer_photo") or "") == "1"
    has_photo = bool(item["has_photo"])
    if photo_bytes is not None:
        has_photo = True
    elif wants_remove:
        has_photo = False
    try:
        update_item(
            conn,
            item_id,
            name=data.name,
            category_id=data.category_id,
            quantity=data.quantity,
            location_id=data.location_id,
            spot=data.spot,
            usage_level=data.usage_level,
            notes=data.notes,
            reference=data.reference,
            has_photo=has_photo,
        )
    except KeyError:
        raise HTTPException(status_code=404) from None
    except ValueError as exc:
        errors.append(str(exc))
        return render(
            request,
            "item_form.html",
            status_code=400,
            nav="home",
            item=item,
            values=values,
            errors=errors,
            categories=categories,
            locations=locations,
        )
    path = photo_file(request.app.state.photos_dir, item_id)
    if photo_bytes is not None:
        write_photo(path, photo_bytes)
    elif wants_remove:
        remove_photo(path)
    return redirect(f"/composants/{item_id}", "modifie")


@router.post("/composants/{item_id}/quantite")
async def update_quantity(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    item = _require_item(conn, item_id)
    form = await request.form()
    action = str(form.get("action") or "")
    error = None
    quantity = item["quantity"]
    if action == "inc":
        if quantity >= QUANTITY_MAX:
            error = "La quantité est trop grande."
        else:
            quantity += 1
    elif action == "dec":
        quantity = max(0, quantity - 1)
    elif action == "set":
        try:
            quantity = parse_quantity(str(form.get("quantite") or "").strip())
        except ValueError as exc:
            error = str(exc)
    else:
        error = "Action inconnue."
    if error:
        attach_photos([item], request.app.state.photos_dir)
        return render(
            request,
            "item_detail.html",
            status_code=400,
            nav="home",
            item=item,
            error=error,
        )
    set_quantity(conn, item_id, quantity)
    return redirect(f"/composants/{item_id}", "quantite")


@router.get("/composants/{item_id}/supprimer")
async def delete_item_confirm(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    item = _require_item(conn, item_id)
    return render(request, "item_delete.html", nav="home", item=item)


@router.post("/composants/{item_id}/supprimer")
async def delete_composant(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    item = _require_item(conn, item_id)
    delete_item(conn, item["id"])
    remove_photo(photo_file(request.app.state.photos_dir, item["id"]))
    return redirect("/", "supprime")


@router.get("/composants/{item_id}/etiquette")
async def label(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    item = _require_item(conn, item_id)
    return render(request, "label.html", nav="home", item=item)


@router.get("/composants/{item_id}/code128.png")
async def barcode_image(item_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    item = _require_item(conn, item_id)
    return Response(
        content=code128_png(item["code"]),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/composants/{item_id}/qr.png")
async def qr_image(item_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    _require_item(conn, item_id)
    return Response(
        content=qr_png(qr_payload(item_id)),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/photos/{item_id}.jpg")
async def photo(
    item_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    item = _require_item(conn, item_id)
    path = photo_file(request.app.state.photos_dir, item_id)
    if not item["has_photo"] or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"},
    )


# --- emplacements ---


@router.get("/emplacements")
async def locations_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return render(
        request,
        "locations.html",
        nav="lieux",
        locations=list_locations(conn),
        unassigned=count_unassigned(conn),
        error=None,
        attempt="",
    )


@router.post("/emplacements")
async def create_emplacement(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    form = await request.form()
    try:
        name = parse_label(form.get("nom"))
        location_id = create_location(conn, name)
    except ValueError as exc:
        return render(
            request,
            "locations.html",
            status_code=400,
            nav="lieux",
            locations=list_locations(conn),
            unassigned=count_unassigned(conn),
            error=str(exc),
            attempt=_attempt(form),
        )
    return redirect(f"/emplacements/{location_id}", "emplacement-ajoute")


@router.get("/emplacements/sans")
async def unassigned_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    items = items_without_location(conn)
    attach_photos(items, request.app.state.photos_dir)
    return render(
        request,
        "location_detail.html",
        nav="lieux",
        location=None,
        unassigned=True,
        items=items,
        error=None,
        attempt="",
    )


@router.get("/emplacements/{location_id}")
async def location_detail(
    location_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    location = get_location(conn, location_id)
    if location is None:
        raise HTTPException(status_code=404)
    items = items_for_location(conn, location_id)
    attach_photos(items, request.app.state.photos_dir)
    return render(
        request,
        "location_detail.html",
        nav="lieux",
        location=location,
        unassigned=False,
        items=items,
        error=None,
        attempt=location["name"],
    )


@router.post("/emplacements/{location_id}/renommer")
async def rename_emplacement(
    location_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    location = get_location(conn, location_id)
    if location is None:
        raise HTTPException(status_code=404)
    form = await request.form()
    try:
        rename_location(conn, location_id, parse_label(form.get("nom")))
    except ValueError as exc:
        items = items_for_location(conn, location_id)
        attach_photos(items, request.app.state.photos_dir)
        return render(
            request,
            "location_detail.html",
            status_code=400,
            nav="lieux",
            location=location,
            unassigned=False,
            items=items,
            error=str(exc),
            attempt=_attempt(form),
        )
    return redirect(f"/emplacements/{location_id}", "emplacement-renomme")


@router.post("/emplacements/{location_id}/supprimer")
async def delete_emplacement(
    location_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    location = get_location(conn, location_id)
    if location is None:
        raise HTTPException(status_code=404)
    try:
        delete_location(conn, location_id)
    except ValueError as exc:
        items = items_for_location(conn, location_id)
        attach_photos(items, request.app.state.photos_dir)
        return render(
            request,
            "location_detail.html",
            status_code=400,
            nav="lieux",
            location=location,
            unassigned=False,
            items=items,
            error=str(exc),
            attempt=location["name"],
        )
    return redirect("/emplacements", "emplacement-supprime")


# --- catégories ---


def _categories_page(request, conn, *, error, error_target, attempt, status_code=200):
    return render(
        request,
        "categories.html",
        status_code=status_code,
        nav="categories",
        categories=list_categories(conn),
        error=error,
        error_target=error_target,
        attempt=attempt,
    )


@router.get("/categories")
async def categories_page(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    return _categories_page(request, conn, error=None, error_target=None, attempt="")


@router.post("/categories")
async def create_categorie(request: Request, conn: sqlite3.Connection = Depends(get_conn)):
    form = await request.form()
    try:
        create_category(conn, parse_label(form.get("nom")))
    except ValueError as exc:
        return _categories_page(
            request,
            conn,
            error=str(exc),
            error_target="new",
            attempt=_attempt(form),
            status_code=400,
        )
    return redirect("/categories", "categorie-ajoutee")


@router.post("/categories/{category_id}/renommer")
async def rename_categorie(
    category_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    form = await request.form()
    try:
        rename_category(conn, category_id, parse_label(form.get("nom")))
    except KeyError:
        raise HTTPException(status_code=404) from None
    except ValueError as exc:
        return _categories_page(
            request,
            conn,
            error=str(exc),
            error_target=category_id,
            attempt=_attempt(form),
            status_code=400,
        )
    return redirect("/categories", "categorie-renommee")


@router.post("/categories/{category_id}/supprimer")
async def delete_categorie(
    category_id: int,
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
):
    try:
        delete_category(conn, category_id)
    except KeyError:
        raise HTTPException(status_code=404) from None
    except ValueError as exc:
        return _categories_page(
            request,
            conn,
            error=str(exc),
            error_target=category_id,
            attempt="",
            status_code=400,
        )
    return redirect("/categories", "categorie-supprimee")


@router.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


def create_app(data_dir: str | Path) -> FastAPI:
    data_path = Path(data_dir)
    photos_dir = data_path / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)
    for temporary in photos_dir.glob("*.tmp"):
        temporary.unlink(missing_ok=True)
    db_path = data_path / "stuffmanager.db"
    init_db(db_path)

    application = FastAPI(
        title="StuffManager",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.data_dir = data_path
    application.state.photos_dir = photos_dir
    application.state.db_path = db_path

    @application.middleware("http")
    async def extra_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        return response

    application.include_router(router)

    @application.exception_handler(404)
    async def not_found(request: Request, _exc: Exception):
        return render(request, "not_found.html", status_code=404)

    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return application


app = create_app(Path(os.environ.get("STUFFMANAGER_DATA", "data")))
