"""Settings and OAuth routes for the selected ChatGPT account."""
import asyncio
import io
import secrets
import zipfile
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.chatgpt_auth import AuthError
from app.quick_routes import _same_origin


def create_chatgpt_router(render):
    router = APIRouter()

    def page(request, *, error=None, message=None, status_code=200):
        store = request.app.state.chatgpt
        response = render(request, "chatgpt.html", nav="rapide", chatgpt=store.status(),
                          models=store.active_models(), local=request.url.hostname == "127.0.0.1",
                          error=error, message=message, status_code=status_code,
                          csrf=request.cookies.get("chatgpt_csrf") or secrets.token_urlsafe(32))
        if not request.cookies.get("chatgpt_csrf"):
            # HttpOnly nonce guards pairing/import/switch/disconnect as well as OAuth.
            response.set_cookie("chatgpt_csrf", response.context["csrf"], httponly=True,
                                secure=request.url.scheme == "https", samesite="lax", max_age=3600)
        return response

    async def checked_form(request):
        _same_origin(request)
        form = await request.form()
        if not request.cookies.get("chatgpt_csrf") or not secrets.compare_digest(str(form.get("csrf", "")), request.cookies["chatgpt_csrf"]):
            raise HTTPException(403, "Rechargez les réglages avant de confirmer.")
        return form

    @router.get("/chatgpt")
    async def settings(request: Request):
        msg = "Connexion ChatGPT activée. Vos prochaines analyses peuvent utiliser votre abonnement." if request.query_params.get("connected") else None
        return page(request, message=msg)

    @router.post("/chatgpt/connecter")
    async def connect(request: Request):
        form = await checked_form(request)
        if request.url.hostname != "127.0.0.1" or request.url.scheme != "http":
            return page(request, error="Sur Home Assistant, utilisez l'assistant de connexion ci-dessous.", status_code=400)
        try:
            callback = str(request.base_url).rstrip("/") + "/auth/callback"
            url = request.app.state.chatgpt.start(callback, request.cookies["chatgpt_csrf"], str(form.get("account") or "") or None)
            return RedirectResponse(url, status_code=303)
        except AuthError as exc:
            return page(request, error=str(exc), status_code=400)

    @router.get("/auth/callback")
    async def callback(request: Request):
        try:
            pairs = request.state.oauth_query
            if len({key for key, _ in pairs}) != len(pairs):
                raise AuthError("Retour de connexion invalide.")
            await asyncio.to_thread(request.app.state.chatgpt.finish, dict(pairs), request.cookies.get("chatgpt_csrf", ""))
            response = RedirectResponse("/chatgpt?connected=1", status_code=303)
        except AuthError as exc:
            response = page(request, error=str(exc), status_code=400)
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @router.post("/chatgpt/assistant")
    async def assistant(request: Request):
        await checked_form(request)
        setup = await asyncio.to_thread(request.app.state.chatgpt.pairing)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in ("chatgpt_auth.py", "connect_chatgpt.py"):
                bundle.writestr(name, (Path(__file__).parent / name).read_bytes())
            bundle.writestr("setup.json", json.dumps(setup))
            bundle.writestr("requirements.txt", "PyJWT[crypto]==2.10.1\n")
            bundle.writestr("LIRE-MOI.txt", "StuffManager — connexion ChatGPT\n\nDans ce dossier extrait, avec Python 3.12 ou plus :\npython -m pip install -r requirements.txt\npython connect_chatgpt.py\n\nPuis importez connexion-stuffmanager.chatgpt dans StuffManager, sous une heure.\nLe fichier est chiffré pour votre installation. Ne le publiez pas.\nL'assistant doit être téléchargé depuis votre instance de confiance.\n")
        return Response(archive.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="connexion-chatgpt.zip"', "Cache-Control": "no-store"})

    @router.post("/chatgpt/importer")
    async def import_connection(request: Request):
        form = await checked_form(request)
        try:
            upload = form.get("connexion")
            if not getattr(upload, "filename", None):
                raise AuthError("Choisissez le fichier produit par l'assistant.")
            raw = await upload.read(65537)
            if len(raw) > 65536:
                raise AuthError("Le fichier de connexion est trop volumineux.")
            await asyncio.to_thread(request.app.state.chatgpt.import_package, raw)
            return RedirectResponse("/chatgpt?connected=1", status_code=303)
        except AuthError as exc:
            return page(request, error=str(exc), status_code=400)

    @router.post("/chatgpt/choisir")
    async def select(request: Request):
        form = await checked_form(request)
        try:
            request.app.state.chatgpt.select(str(form.get("account", "")), str(form.get("model") or "") or None)
            return RedirectResponse("/chatgpt", status_code=303)
        except AuthError as exc:
            return page(request, error=str(exc), status_code=400)

    @router.post("/chatgpt/deconnecter")
    async def disconnect(request: Request):
        form = await checked_form(request)
        try:
            revoked = await asyncio.to_thread(request.app.state.chatgpt.disconnect, str(form.get("account", "")))
            message = "Compte déconnecté." if revoked else "Compte déconnecté ici. La révocation chez OpenAI n'a pas pu être confirmée : retirez aussi StuffManager dans les réglages ChatGPT."
            return page(request, message=message)
        except AuthError as exc:
            return page(request, error=str(exc), status_code=400)

    return router
