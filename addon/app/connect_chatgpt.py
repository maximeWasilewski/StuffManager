"""Desktop helper, bundled with chatgpt_auth.py and public pairing metadata."""
import json
import argparse
import os
import secrets
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from chatgpt_auth import AuthError, encrypt_package, exchange, pending_attempt


def protected_write(path, payload):
    temporary = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    folder = Path(__file__).resolve().parent
    setup = json.loads((folder / "setup.json").read_text(encoding="utf-8"))
    cache_dir = Path.home() / ".stuffmanager-chatgpt"
    cache_dir.mkdir(mode=0o700, exist_ok=True)
    cache = cache_dir / "registration.json"
    metadata = json.loads(cache.read_text()) if cache.is_file() else {"host": "urn:uuid:" + str(uuid.uuid4())}
    parser = argparse.ArgumentParser(description="Connexion officielle ChatGPT pour StuffManager")
    parser.add_argument("--new-account", action="store_true", help="Connecter un autre compte ou espace ChatGPT")
    args = parser.parse_args()
    if args.new_account:
        metadata.pop("client_id", None)
        metadata.pop("subject", None)
    protected_write(cache, json.dumps(metadata).encode())  # Persist host before first authorization.
    completed = False
    pending = None

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log authorization codes or callback URLs.

        def do_GET(self):
            nonlocal completed
            if urlsplit(self.path).path != "/auth/callback":
                self.send_error(404)
                return
            query = parse_qs(urlsplit(self.path).query)
            try:
                if any(len(values) != 1 for values in query.values()):
                    raise AuthError("Retour de connexion invalide.")
                record = exchange(pending, {k: v[0] for k, v in query.items()})
                # Only non-secret registration metadata remains on this computer.
                metadata.update(client_id=record["client_id"], subject=record["subject"])
                for path, payload in [(cache, json.dumps(metadata).encode()),
                                      (folder / "connexion-stuffmanager.chatgpt", encrypt_package(record, setup))]:
                    protected_write(path, payload)
                message = "Connexion terminée. Revenez dans StuffManager et importez connexion-stuffmanager.chatgpt depuis ce dossier."
                completed = True
            except (AuthError, OSError) as exc:
                message = str(exc) if isinstance(exc, AuthError) else "Le fichier n'a pas pu être enregistré. Vérifiez les droits du dossier et relancez l'assistant."
                completed = True
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(message.encode())
            print(message)

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 1
        callback = f"http://127.0.0.1:{server.server_port}/auth/callback"
        pending, url = pending_attempt(metadata["host"], callback, metadata.get("client_id"), metadata.get("subject"))
        print("Ouverture de la connexion officielle OpenAI dans votre navigateur…")
        if not webbrowser.open(url):
            print("Ouvrez cette adresse dans le navigateur de ce PC :", url)
        deadline = time.time() + 600
        while not completed and time.time() < deadline:
            server.handle_request()
        if not completed:
            print("La connexion a expiré. Relancez l'assistant.")


if __name__ == "__main__":
    main()
