"""OAuth 2.0 con Google (flujo de app instalada con redirección a 127.0.0.1)."""

import base64
import hashlib
import http.server
import json
import secrets
import shutil
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path

import requests

from . import config


def install_client_secret(src: Path) -> Path:
    data = json.loads(Path(src).read_text())
    if "installed" not in data and "web" not in data:
        raise ValueError("El JSON no parece un client_secret de OAuth (falta 'installed').")
    dest = config.client_secret_path()
    shutil.copyfile(src, dest)
    dest.chmod(0o600)
    return dest


def _client() -> dict:
    path = config.client_secret_path()
    if not path.exists():
        raise SystemExit(
            f"No encuentro {path}.\n"
            "Descarga el client_secret.json de Google Cloud y ejecuta:\n"
            "  fitbit-air setup /ruta/a/client_secret.json"
        )
    data = json.loads(path.read_text())
    return data.get("installed") or data["web"]


def _save_token(tok: dict) -> None:
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
    path = config.token_path()
    old = json.loads(path.read_text()) if path.exists() else {}
    # Google no siempre devuelve refresh_token al refrescar: conservar el anterior.
    if "refresh_token" not in tok and "refresh_token" in old:
        tok["refresh_token"] = old["refresh_token"]
    path.write_text(json.dumps(tok, indent=2))
    path.chmod(0o600)


class _Handler(http.server.BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Handler.result = {k: v[0] for k, v in qs.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        ok = "code" in _Handler.result
        msg = "Listo, ya puedes cerrar esta pestaña." if ok else "Algo falló, revisa la terminal."
        self.wfile.write(f"<h2>Fitbit Air Lab</h2><p>{msg}</p>".encode())

    def log_message(self, *args):
        pass


def login() -> None:
    client = _client()
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    params = {
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(config.SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = config.AUTH_URL + "?" + urllib.parse.urlencode(params)
    print("Abre este enlace si el navegador no se abre solo:\n\n" + url + "\n")
    webbrowser.open(url)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=300)
    server.server_close()

    res = _Handler.result
    if res.get("state") != state or "code" not in res:
        raise SystemExit(f"Autorización fallida: {res.get('error', 'sin respuesta')}")

    r = requests.post(config.TOKEN_URL, data={
        "code": res["code"],
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }, timeout=30)
    r.raise_for_status()
    _save_token(r.json())
    print(f"Autorizado. Token guardado en {config.token_path()}")


def access_token(force_refresh: bool = False) -> str:
    path = config.token_path()
    if not path.exists():
        raise SystemExit("No hay sesión. Ejecuta: fitbit-air login")
    tok = json.loads(path.read_text())
    if not force_refresh and tok.get("expires_at", 0) > time.time():
        return tok["access_token"]
    if "refresh_token" not in tok:
        raise SystemExit("El token ha caducado y no hay refresh_token. Ejecuta: fitbit-air login")
    client = _client()
    r = requests.post(config.TOKEN_URL, data={
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret", ""),
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }, timeout=30)
    if r.status_code >= 400:
        raise SystemExit(f"No se pudo renovar el token ({r.status_code}). Ejecuta: fitbit-air login")
    _save_token(r.json())
    return r.json()["access_token"]
