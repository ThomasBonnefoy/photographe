"""Ready Studio — mini API de persistance (V1 démo).

Bibliothèque standard uniquement. Derrière Caddy, qui :
  - protège /api/admin/* par basic auth (ce serveur ne vérifie PAS l'auth lui-même) ;
  - sert /uploads/* directement depuis DATA_DIR/uploads.
Ne jamais publier ce port hors du réseau Docker interne.

Endpoints
  GET    /api/data                      contenu public (galerie, vidéos, témoignages)
  POST   /api/booking                   demande de devis depuis le site public
  GET    /api/admin/data                contenu + demandes
  PUT    /api/admin/content             remplace le contenu public
  POST   /api/admin/upload              corps = image brute (jpeg/png/webp) -> {"url": ...}
  POST   /api/admin/bookings            ajoute une réservation manuelle
  PATCH  /api/admin/bookings/<id>       {"status": "pending|confirmed|cancelled"}
  DELETE /api/admin/bookings/<id>
"""
import json
import os
import re
import threading
import time
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DATA_DIR = os.environ.get("DATA_DIR", "/data")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
CONTENT_FILE = os.path.join(DATA_DIR, "content.json")
BOOKINGS_FILE = os.path.join(DATA_DIR, "bookings.json")

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

    def send(self, code, payload=None):
        body = json.dumps(payload if payload is not None else {}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
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

    # --- routes ---------------------------------------------------------
    def do_GET(self):
        if self.path == "/api/data":
            return self.send(200, read_json(CONTENT_FILE, SEED_CONTENT))
        if self.path == "/api/admin/data":
            data = dict(read_json(CONTENT_FILE, SEED_CONTENT))
            data["bookings"] = read_json(BOOKINGS_FILE, [])
            return self.send(200, data)
        self.send(404, {"error": "not found"})

    def do_PUT(self):
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
        if self.path == "/api/booking":
            return self.public_booking()
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
        m = re.match(r"^/api/admin/bookings/([\w-]+)$", self.path)
        if not m:
            return self.send(404, {"error": "not found"})
        with lock:
            bookings = read_json(BOOKINGS_FILE, [])
            kept = [x for x in bookings if x["id"] != m.group(1)]
            write_json(BOOKINGS_FILE, kept)
        self.send(200, {"deleted": len(bookings) - len(kept)})

    # --- helpers --------------------------------------------------------
    def public_booking(self):
        ip = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For", "?").split(",")[0]
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
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    if not os.path.exists(CONTENT_FILE):
        write_json(CONTENT_FILE, SEED_CONTENT)
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
