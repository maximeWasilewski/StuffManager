"""Official Sign in with ChatGPT. Credentials never leave protected server storage."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPES = {"resource.invoke", "chatgpt.tokens.use.direct"}
JWKS = jwt.PyJWKClient(ISSUER + "/.well-known/jwks.json", timeout=20)


class AuthError(ValueError):
    pass


def expiry(tokens):
    try:
        seconds = float(tokens["expires_in"])
        if not 0 < seconds <= 365 * 86400:
            raise ValueError()
        return time.time() + seconds
    except (KeyError, ValueError, TypeError):
        raise AuthError("La durée de validité de la connexion est invalide. Recommencez.") from None


def http_json(url, *, form=None, token=None):
    headers = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urlopen(Request(url, data=data, headers=headers), timeout=30) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError()
        return json.loads(raw) if raw else {}
    except HTTPError as exc:
        if exc.code in (400, 401, 403):
            raise AuthError("La connexion ChatGPT a expiré ou n'est pas autorisée. Reconnectez le compte.") from None
        if exc.code == 429:
            raise AuthError("La limite de votre abonnement ChatGPT est atteinte. Consultez votre utilisation dans ChatGPT.") from None
        raise AuthError("OpenAI est momentanément indisponible. Réessayez plus tard.") from None
    except (URLError, OSError, ValueError):
        raise AuthError("Impossible de joindre OpenAI ou réponse invalide. Réessayez la connexion.") from None


def identity(id_token, client_id, nonce=None):
    try:
        key = JWKS.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(id_token, key.key, algorithms=["RS256"], issuer=ISSUER,
                            audience=client_id, options={"require": ["iss", "aud", "sub", "exp"]})
        if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ValueError()
        if not isinstance(claims["sub"], str) or not claims["sub"]:
            raise ValueError()
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError, OSError):
        raise AuthError("L'identité ChatGPT n'a pas pu être vérifiée. Recommencez la connexion.") from None


def pending_attempt(host_id, redirect_uri, client_id=None, subject=None):
    verifier = secrets.token_urlsafe(48)
    pending = dict(state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
                   verifier=verifier, redirect_uri=redirect_uri, client_id=client_id,
                   subject=subject, created_at=time.time())
    params = dict(client_id=client_id or "dynamic_agent_client", ext_agent_host_id=host_id,
                  response_type="code", redirect_uri=redirect_uri, scope=SCOPES,
                  resource=RESOURCE, state=pending["state"], nonce=pending["nonce"],
                  code_challenge_method="S256",
                  code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="))
    if not client_id:
        params["agent_name_hint"] = "StuffManager"
    # No ID token in the URL: returning users can choose their account explicitly.
    return pending, AUTHORIZE + "?" + urlencode(params)


def exchange(pending, callback):
    if time.time() - pending["created_at"] > 600:
        raise AuthError("Cette tentative de connexion a expiré. Recommencez.")
    if not secrets.compare_digest(str(callback.get("state", "")), pending["state"]):
        raise AuthError("Le retour de connexion ne correspond pas à cette tentative.")
    if callback.get("error"):
        raise AuthError("Connexion annulée. Autorisez l'utilisation du forfait ChatGPT pour activer l'analyse.")
    client = callback.get("client_id") or pending.get("client_id")
    if not client or client == "dynamic_agent_client" or not callback.get("code"):
        raise AuthError("L'enregistrement ChatGPT est incomplet. Recommencez.")
    if pending.get("client_id") and client != pending["client_id"]:
        raise AuthError("Ce retour appartient à une autre connexion ChatGPT.")
    tokens = http_json(TOKEN, form=dict(grant_type="authorization_code", client_id=client,
                       code=callback["code"], code_verifier=pending["verifier"],
                       redirect_uri=pending["redirect_uri"], resource=RESOURCE))
    claims = identity(tokens.get("id_token", ""), client, pending["nonce"])
    if pending.get("subject") and claims["sub"] != pending["subject"]:
        raise AuthError("Le compte sélectionné est différent. Ajoutez-le comme nouvelle connexion.")
    scopes = str(tokens.get("scope", "")).split()
    if not PLAN_SCOPES <= set(scopes):
        raise AuthError("L'utilisation du forfait ChatGPT n'a pas été autorisée. Recommencez et accordez cette permission.")
    if tokens.get("token_type", "").lower() != "bearer" or not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise AuthError("La connexion ChatGPT est incomplète.")
    return dict(client_id=client, subject=claims["sub"], email=str(claims.get("email", ""))[:254],
                id_token=tokens["id_token"], access_token=tokens["access_token"], refresh_token=tokens["refresh_token"],
                scopes=scopes, expires_at=expiry(tokens))


def available_models(token):
    data = http_json(RESOURCE + "/models", token=token)
    models = [dict(slug=m["slug"], name=m.get("display_name") or m["slug"])
              for m in data.get("models", []) if m.get("visibility") == "list" and isinstance(m.get("slug"), str)]
    if not models:
        raise AuthError("Aucun modèle disponible pour cette connexion ChatGPT.")
    return models


class ChatGPTStore:
    def __init__(self, data_dir):
        self.directory = Path(data_dir) / "chatgpt"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "sessions.db"
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, record TEXT NOT NULL)")
            if not self.read(conn, "host"):
                self.write(conn, "host", "urn:uuid:" + str(uuid.uuid4()))
        os.chmod(self.directory, 0o700)
        os.chmod(self.path, 0o600)

    def connect(self):
        return sqlite3.connect(self.path, timeout=65)

    @staticmethod
    def read(conn, key):
        row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def write(conn, key, value):
        conn.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))

    def status(self):
        with self.connect() as conn:
            active = self.read(conn, "active")
            accounts = []
            for account_id, raw in conn.execute("SELECT id, record FROM accounts ORDER BY id"):
                record = json.loads(raw)
                accounts.append(dict(id=account_id, email=record["email"], connected=bool(record.get("access_token")),
                                     active=account_id == active, model=record.get("model", "")))
        return dict(accounts=accounts, connected=any(a["active"] and a["connected"] for a in accounts))

    def start(self, redirect_uri, browser_id, account_id=None):
        with self.connect() as conn:
            record = None
            if account_id:
                row = conn.execute("SELECT record FROM accounts WHERE id=?", (account_id,)).fetchone()
                if not row:
                    raise AuthError("Connexion inconnue.")
                record = json.loads(row[0])
            pending, url = pending_attempt(self.read(conn, "host"), redirect_uri,
                                          record["client_id"] if record else None,
                                          record["subject"] if record else None)
            pending["browser_id"] = browser_id
            self.write(conn, "pending", pending)
        return url

    def finish(self, callback, browser_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            pending = self.read(conn, "pending")
            if not pending or not secrets.compare_digest(pending["browser_id"], browser_id):
                raise AuthError("Ouvrez le retour dans le navigateur qui a lancé la connexion.")
            # Consume even a failed attempt; never reuse an authorization code.
            conn.execute("DELETE FROM state WHERE key='pending'")
        record = exchange(pending, callback)
        models = available_models(record["access_token"])
        record["models"] = models
        record["model"] = models[0]["slug"]
        self.save(record)

    def save(self, record):
        account_id = hashlib.sha256((record["client_id"] + "\0" + record["subject"]).encode()).hexdigest()[:24]
        with self.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO accounts VALUES (?,?)", (account_id, json.dumps(record)))
            self.write(conn, "active", account_id)

    def select(self, account_id, model=None):
        with self.connect() as conn:
            row = conn.execute("SELECT record FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise AuthError("Connexion inconnue.")
            record = json.loads(row[0])
            if not record.get("access_token"):
                raise AuthError("Reconnectez ce compte avant de l'utiliser.")
            if model:
                if model not in {m["slug"] for m in record.get("models", [])}:
                    raise AuthError("Choisissez un modèle disponible pour ce compte.")
                record["model"] = model
                conn.execute("UPDATE accounts SET record=? WHERE id=?", (json.dumps(record), account_id))
            self.write(conn, "active", account_id)

    def credentials(self):
        with self.connect() as conn:
            # SQLite lock serializes refreshes across workers/processes.
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT record FROM accounts WHERE id=?", (self.read(conn, "active"),)).fetchone()
            if not row:
                raise AuthError("Connectez votre compte ChatGPT dans les réglages de l'ajout rapide.")
            record = json.loads(row[0])
            if not record.get("access_token"):
                raise AuthError("Reconnectez votre compte ChatGPT.")
            if record["expires_at"] < time.time() + 90:
                tokens = http_json(TOKEN, form=dict(grant_type="refresh_token", client_id=record["client_id"],
                                   refresh_token=record["refresh_token"], resource=RESOURCE))
                scopes = str(tokens.get("scope", " ".join(record["scopes"]))).split()
                if not PLAN_SCOPES <= set(scopes) or not tokens.get("access_token"):
                    raise AuthError("Le forfait ChatGPT n'est plus autorisé. Reconnectez le compte.")
                if tokens.get("id_token"):
                    claims = identity(tokens["id_token"], record["client_id"])
                    if claims["sub"] != record["subject"]:
                        raise AuthError("Le compte ChatGPT a changé. Reconnectez la connexion.")
                    record["id_token"] = tokens["id_token"]
                record.update(access_token=tokens["access_token"], refresh_token=tokens.get("refresh_token", record["refresh_token"]),
                              expires_at=expiry(tokens), scopes=scopes)
                conn.execute("UPDATE accounts SET record=? WHERE id=?", (json.dumps(record), self.read(conn, "active")))
            return record["access_token"], record["model"]

    def active_models(self):
        with self.connect() as conn:
            row = conn.execute("SELECT record FROM accounts WHERE id=?", (self.read(conn, "active"),)).fetchone()
            return json.loads(row[0]).get("models", []) if row else []

    def disconnect(self, account_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT record FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row:
                raise AuthError("Connexion inconnue.")
            record = json.loads(row[0])
            revoked = True
            if record.get("refresh_token"):
                try:
                    discovery = http_json(ISSUER + "/.well-known/openid-configuration")
                    endpoint = discovery.get("revocation_endpoint", "")
                    if not endpoint.startswith(ISSUER + "/"):
                        raise AuthError("Endpoint de révocation invalide.")
                    http_json(endpoint, form=dict(token=record["refresh_token"], token_type_hint="refresh_token", client_id=record["client_id"]))
                except AuthError:
                    revoked = False
            for key in ("access_token", "refresh_token", "id_token"):
                record.pop(key, None)
            conn.execute("UPDATE accounts SET record=? WHERE id=?", (json.dumps(record), account_id))
            if self.read(conn, "active") == account_id:
                conn.execute("DELETE FROM state WHERE key='active'")
        return revoked

    def pairing(self):
        """Public setup only; credentials return encrypted to this installation."""
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        with self.connect() as conn:
            pair = dict(id=secrets.token_urlsafe(32), created_at=time.time(),
                        private_key=private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                          serialization.NoEncryption()).decode())
            self.write(conn, "pairing", pair)
            host = self.read(conn, "host")
        return dict(pair_id=pair["id"], public_key=public, server_host_id=host)

    def import_package(self, raw):
        try:
            package = json.loads(raw)
            with self.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                pair = self.read(conn, "pairing")
                if not pair or time.time() - pair["created_at"] > 3600 or not secrets.compare_digest(pair["id"], package["pair_id"]):
                    raise ValueError()
                private = serialization.load_pem_private_key(pair["private_key"].encode(), password=None)
                aes_key = private.decrypt(base64.b64decode(package["key"], validate=True),
                                          padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
                plaintext = AESGCM(aes_key).decrypt(base64.b64decode(package["iv"], validate=True),
                                                   base64.b64decode(package["data"], validate=True), pair["id"].encode())
                record = json.loads(plaintext)
                claims = identity(record["id_token"], record["client_id"])
                if claims["sub"] != record["subject"] or not PLAN_SCOPES <= set(record["scopes"]):
                    raise ValueError()
                if not record.get("refresh_token") or not record.get("access_token"):
                    raise ValueError()
                if not isinstance(record.get("expires_at"), (int, float)) or record["expires_at"] <= time.time():
                    raise ValueError()
                record["email"] = str(claims.get("email", ""))[:254]
                models = available_models(record["access_token"])
                record["models"], record["model"] = models, models[0]["slug"]
                account_id = hashlib.sha256((record["client_id"] + "\0" + record["subject"]).encode()).hexdigest()[:24]
                conn.execute("INSERT OR REPLACE INTO accounts VALUES (?,?)", (account_id, json.dumps(record)))
                self.write(conn, "active", account_id)
                conn.execute("DELETE FROM state WHERE key='pairing'")
        except AuthError:
            raise
        except (ValueError, TypeError, KeyError, InvalidTag):
            raise AuthError("Le fichier ne correspond pas à cette installation ou a expiré. Téléchargez un nouvel assistant.") from None


def encrypt_package(record, setup):
    public = serialization.load_pem_public_key(setup["public_key"].encode())
    aes_key, iv = AESGCM.generate_key(bit_length=256), os.urandom(12)
    data = AESGCM(aes_key).encrypt(iv, json.dumps(record).encode(), setup["pair_id"].encode())
    encrypted_key = public.encrypt(aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return json.dumps(dict(pair_id=setup["pair_id"], key=base64.b64encode(encrypted_key).decode(),
                           iv=base64.b64encode(iv).decode(), data=base64.b64encode(data).decode())).encode()
