"""Ready Studio — mini API de persistance (V1 démo).

Bibliothèque standard uniquement. Derrière Caddy, qui sert le site et /uploads/* (DATA_DIR/uploads).
Ne jamais publier ce port hors du réseau Docker interne.

Authentification : formulaire de connexion de l'admin -> cookie de session
(HttpOnly, Secure, SameSite=Strict). Toutes les routes /api/admin/* l'exigent.
Identifiants dans DATA_DIR/auth.json (mot de passe haché scrypt), créé au premier
démarrage depuis INITIAL_ADMIN_USER / INITIAL_ADMIN_PASSWORD (.env).

Endpoints
  GET    /api/data                      contenu public (galerie, vidéos, témoignages)
  POST   /api/booking                   demande de devis depuis le site public
  POST   /api/login                     {"username", "password"} -> cookie de session
  POST   /api/logout
  GET    /api/session                   {"authenticated": bool, "username"}
  POST   /api/admin/password            {"current", "new"}
  GET    /api/admin/data                contenu + demandes
  PUT    /api/admin/content             remplace le contenu public
  POST   /api/admin/upload              corps = image brute (jpeg/png/webp) -> {"url": ...}
  POST   /api/admin/bookings            ajoute une réservation manuelle
  PATCH  /api/admin/bookings/<id>       {"status": "pending|confirmed|cancelled"}
  DELETE /api/admin/bookings/<id>
"""
import hashlib
import hmac
import json
import os
import secrets
import re
import threading
import time
import uuid
from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA_DIR = os.environ.get("DATA_DIR", "/data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
CONTENT_FILE = os.path.join(DATA_DIR, "content.json")
BOOKINGS_FILE = os.path.join(DATA_DIR, "bookings.json")
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")

SESSION_COOKIE = "rs_session"
SESSION_TTL = 7 * 24 * 3600
LOGIN_RATE = (10, 900)  # 10 échecs / 15 min / IP

MAX_JSON = 1 * 1024 * 1024
MAX_UPLOAD = 20 * 1024 * 1024
IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
STATUSES = {"pending", "confirmed", "cancelled"}
BOOKING_RATE = (5, 3600)  # 5 demandes publiques / heure / IP

# Contenu de départ : photos d'illustration, marquées demo=True (le site affiche
# alors un bandeau « photos d'illustration » tant qu'il en reste une).
SEED_CONTENT = {
    "gallery": [
        {"id": "w1", "url": "https://picsum.photos/seed/w1/800/1000", "category": "mariage", "alt": "Mariage", "tall": True, "demo": True},
        {"id": "w2", "url": "https://picsum.photos/seed/w2/600/600", "category": "mariage", "alt": "Mariage", "demo": True},
        {"id": "b1", "url": "https://picsum.photos/seed/b1/600/600", "category": "anniversaire", "alt": "Anniversaire", "demo": True},
        {"id": "p1", "url": "https://picsum.photos/seed/p1/600/600", "category": "portrait", "alt": "Portrait", "demo": True},
        {"id": "b2", "url": "https://picsum.photos/seed/b2/1200/600", "category": "anniversaire", "alt": "Anniversaire", "wide": True, "demo": True},
        {"id": "p2", "url": "https://picsum.photos/seed/p2/600/600", "category": "portrait", "alt": "Portrait", "demo": True},
        {"id": "w3", "url": "https://picsum.photos/seed/w3/800/1000", "category": "mariage", "alt": "Mariage", "tall": True, "demo": True},
        {"id": "b3", "url": "https://picsum.photos/seed/b3/600/600", "category": "anniversaire", "alt": "Anniversaire", "demo": True},
        {"id": "p3", "url": "https://picsum.photos/seed/p3/600/600", "category": "portrait", "alt": "Portrait", "demo": True},
    ],
    "videos": [],
    "testimonials": [],
}

lock = threading.Lock()
rate = {}
login_failures = {}
sessions = {}  # token -> (username, expiration) ; en mémoire : un redémarrage déconnecte


def hash_password(password):
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$%s$%s" % (salt.hex(), h.hex())


def check_password(password, stored):
    try:
        _, salt, h = stored.split("$")
        test = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(test.hex(), h)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def write_json(path, value):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def clean_str(v, limit):
    return v.strip()[:limit] if isinstance(v, str) else ""


def clean_url(v):
    v = clean_str(v, 1000)
    return v if re.match(r"^(https?://|/uploads/)", v) else ""


def clean_content(c):
    """Ne garde que les champs connus, avec des types et longueurs bornés."""
    out = {"gallery": [], "videos": [], "testimonials": []}
    for g in c.get("gallery", [])[:500]:
        url = clean_url(g.get("url"))
        if not url:
            continue
        out["gallery"].append({
            "id": clean_str(g.get("id"), 40) or uuid.uuid4().hex[:10],
            "url": url,
            "category": g.get("category") if g.get("category") in ("mariage", "anniversaire", "portrait") else "mariage",
            "alt": clean_str(g.get("alt"), 200),
            "tall": bool(g.get("tall")),
            "wide": bool(g.get("wide")),
            "demo": bool(g.get("demo")),
        })
    for v in c.get("videos", [])[:100]:
        out["videos"].append({
            "id": clean_str(v.get("id"), 40) or uuid.uuid4().hex[:10],
            "url": clean_url(v.get("url")),
            "link": clean_url(v.get("link")),
            "label": clean_str(v.get("label"), 200),
            "alt": clean_str(v.get("alt"), 200),
        })
    for t in c.get("testimonials", [])[:100]:
        stars = t.get("stars")
        out["testimonials"].append({
            "id": clean_str(t.get("id"), 40) or uuid.uuid4().hex[:10],
            "name": clean_str(t.get("name"), 120),
            "event": clean_str(t.get("event"), 120),
            "avatar": clean_url(t.get("avatar")),
            "stars": stars if isinstance(stars, int) and 1 <= stars <= 5 else 5,
            "text": clean_str(t.get("text"), 2000),
        })
    return out


def clean_booking(b, status="pending"):
    event_date = clean_str(b.get("eventDate"), 10)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", event_date):
        return None
    name = clean_str(b.get("clientName"), 120)
    if not name:
        return None
    return {
        "id": "b" + uuid.uuid4().hex[:12],
        "clientName": name,
        "email": clean_str(b.get("email"), 200),
        "eventType": clean_str(b.get("eventType"), 120) or "Non spécifié",
        "eventDate": event_date,
        "country": clean_str(b.get("country"), 60) or "France",
        "duration": clean_str(b.get("duration"), 10),
        "message": clean_str(b.get("message"), 4000),
        "status": status if status in STATUSES else "pending",
        "createdAt": date.today().isoformat(),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "readystudio"
    sys_version = ""

    def log_message(self, fmt, *args):
        print("%s %s" % (self.headers.get("X-Forwarded-For", self.client_address[0]), fmt % args), flush=True)

    def send(self, code, payload=None, cookie=None):
        body = json.dumps(payload if payload is not None else {}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > limit:
            return None
        return self.rfile.read(length)

    def json_body(self):
        raw = self.body(MAX_JSON)
        if raw is None:
            return None
        try:
            v = json.loads(raw)
        except ValueError:
            return None
        return v if isinstance(v, dict) else None

    def client_ip(self):
        return self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "?").split(",")[0].strip()

    def session_token(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c[SESSION_COOKIE].value if SESSION_COOKIE in c else None

    def current_user(self):
        s = sessions.get(self.session_token() or "")
        if not s or s[1] < time.time():
            return None
        return s[0]

    def blocked(self):
        """True (et 401 envoyé) si route admin sans session valide."""
        if self.path.startswith("/api/admin/") and not self.current_user():
            self.send(401, {"error": "non connecté"})
            return True
        return False

    # --- routes ---------------------------------------------------------
    def do_GET(self):
        if self.blocked():
            return
        if self.path == "/api/session":
            user = self.current_user()
            return self.send(200, {"authenticated": bool(user), "username": user})
        if self.path == "/api/data":
            return self.send(200, read_json(CONTENT_FILE, SEED_CONTENT))
        if self.path == "/api/admin/data":
            data = dict(read_json(CONTENT_FILE, SEED_CONTENT))
            data["bookings"] = read_json(BOOKINGS_FILE, [])
            return self.send(200, data)
        self.send(404, {"error": "not found"})

    def do_PUT(self):
        if self.blocked():
            return
        if self.path != "/api/admin/content":
            return self.send(404, {"error": "not found"})
        c = self.json_body()
        if c is None:
            return self.send(400, {"error": "invalid json"})
        content = clean_content(c)
        with lock:
            write_json(CONTENT_FILE, content)
        self.send(200, content)

    def do_POST(self):
        if self.blocked():
            return
        if self.path == "/api/booking":
            return self.public_booking()
        if self.path == "/api/login":
            return self.login()
        if self.path == "/api/logout":
            sessions.pop(self.session_token() or "", None)
            return self.send(200, {"ok": True}, cookie=SESSION_COOKIE + "=; Path=/api; Max-Age=0; HttpOnly; Secure; SameSite=Strict")
        if self.path == "/api/admin/password":
            return self.change_password()
        if self.path == "/api/admin/bookings":
            b = self.json_body()
            booking = b and clean_booking(b, b.get("status", "pending"))
            if not booking:
                return self.send(400, {"error": "invalid booking"})
            with lock:
                bookings = read_json(BOOKINGS_FILE, [])
                bookings.append(booking)
                write_json(BOOKINGS_FILE, bookings)
            return self.send(201, booking)
        if self.path == "/api/admin/upload":
            return self.upload()
        self.send(404, {"error": "not found"})

    def do_PATCH(self):
        if self.blocked():
            return
        m = re.match(r"^/api/admin/bookings/([\w-]+)$", self.path)
        b = self.json_body()
        if not m or not b or b.get("status") not in STATUSES:
            return self.send(400, {"error": "bad request"})
        with lock:
            bookings = read_json(BOOKINGS_FILE, [])
            for x in bookings:
                if x["id"] == m.group(1):
                    x["status"] = b["status"]
                    write_json(BOOKINGS_FILE, bookings)
                    return self.send(200, x)
        self.send(404, {"error": "not found"})

    def do_DELETE(self):
        if self.blocked():
            return
        m = re.match(r"^/api/admin/bookings/([\w-]+)$", self.path)
        if not m:
            return self.send(404, {"error": "not found"})
        with lock:
            bookings = read_json(BOOKINGS_FILE, [])
            kept = [x for x in bookings if x["id"] != m.group(1)]
            write_json(BOOKINGS_FILE, kept)
        self.send(200, {"deleted": len(bookings) - len(kept)})

    # --- helpers --------------------------------------------------------
    def login(self):
        ip = self.client_ip()
        now = time.time()
        fails = [t for t in login_failures.get(ip, []) if now - t < LOGIN_RATE[1]]
        if len(fails) >= LOGIN_RATE[0]:
            return self.send(429, {"error": "Trop de tentatives, réessayez dans 15 minutes"})
        b = self.json_body() or {}
        auth = read_json(AUTH_FILE, {})
        username = clean_str(b.get("username"), 100).lower()
        ok = check_password(b.get("password") or "", auth.get("password_hash", ""))  # toujours calculé : pas d'oracle de timing sur l'identifiant
        if not (ok and hmac.compare_digest(username, auth.get("username", ""))):
            login_failures[ip] = fails + [now]
            return self.send(401, {"error": "Identifiant ou mot de passe incorrect"})
        login_failures.pop(ip, None)
        for t, (_, exp) in list(sessions.items()):
            if exp < now:
                sessions.pop(t, None)
        token = secrets.token_urlsafe(32)
        sessions[token] = (username, now + SESSION_TTL)
        cookie = "%s=%s; Path=/api; Max-Age=%d; HttpOnly; Secure; SameSite=Strict" % (SESSION_COOKIE, token, SESSION_TTL)
        self.send(200, {"ok": True, "username": username}, cookie=cookie)

    def change_password(self):
        b = self.json_body() or {}
        new = b.get("new") or ""
        auth = read_json(AUTH_FILE, {})
        if not check_password(b.get("current") or "", auth.get("password_hash", "")):
            return self.send(403, {"error": "Mot de passe actuel incorrect"})
        if len(new) < 8:
            return self.send(400, {"error": "8 caractères minimum"})
        with lock:
            auth["password_hash"] = hash_password(new)
            write_json(AUTH_FILE, auth)
        # Déconnecte les autres appareils, garde la session courante
        current = self.session_token()
        for t in list(sessions):
            if t != current:
                sessions.pop(t, None)
        self.send(200, {"ok": True})

    def public_booking(self):
        ip = self.client_ip()
        now = time.time()
        hits = [t for t in rate.get(ip, []) if now - t < BOOKING_RATE[1]]
        if len(hits) >= BOOKING_RATE[0]:
            return self.send(429, {"error": "too many requests"})
        b = self.json_body()
        if not b or b.get("website"):  # "website" = champ piège anti-robots
            return self.send(400, {"error": "invalid booking"})
        booking = clean_booking(b)
        if not booking:
            return self.send(400, {"error": "invalid booking"})
        rate[ip] = hits + [now]
        with lock:
            bookings = read_json(BOOKINGS_FILE, [])
            bookings.append(booking)
            write_json(BOOKINGS_FILE, bookings)
        self.send(201, {"ok": True})

    def upload(self):
        ext = IMAGE_TYPES.get((self.headers.get("Content-Type") or "").split(";")[0].strip())
        if not ext:
            return self.send(415, {"error": "jpeg, png ou webp uniquement"})
        raw = self.body(MAX_UPLOAD)
        if raw is None:
            return self.send(413, {"error": "fichier vide ou trop lourd (20 Mo max)"})
        name = uuid.uuid4().hex + ext
        with open(os.path.join(UPLOAD_DIR, name), "wb") as f:
            f.write(raw)
        self.send(201, {"url": "/uploads/" + name})


if __name__ == "__main__":
    os.umask(0o077)  # auth.json, demandes… lisibles uniquement par l'API (Caddy, root, lit les uploads)
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    if not os.path.exists(CONTENT_FILE):
        write_json(CONTENT_FILE, SEED_CONTENT)
    if not os.path.exists(AUTH_FILE):
        write_json(AUTH_FILE, {
            "username": os.environ.get("INITIAL_ADMIN_USER", "ibrahim").lower(),
            "password_hash": hash_password(os.environ["INITIAL_ADMIN_PASSWORD"]),
        })
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
