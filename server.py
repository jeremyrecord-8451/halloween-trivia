"""Haunted Trivia: a Kahoot-style Halloween quiz server (Python standard library only).

Run:  py server.py   then open http://localhost:8000/host.html on the big screen.
Players join from their phones at http://<this computer's IP>:8000
"""
import json
import os
import random
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
QUESTION_SECONDS = 20
REVEAL_SECONDS = 5  # how long the correct answer shows before the leaderboard appears (0 = skip it)
MAX_POINTS = 1000
GAME_TTL = 4 * 3600

with open(os.path.join(ROOT, "questions.json"), encoding="utf-8") as f:
    QUESTIONS = json.load(f)

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

games = {}
lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # sends nothing; just picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


# On a cloud host, show its public address; Render sets RENDER_EXTERNAL_URL automatically.
PUBLIC_URL = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL")
JOIN_URL = PUBLIC_URL.rstrip("/") if PUBLIC_URL else f"http://{'localhost' if HOST.startswith('127.') else lan_ip()}:{PORT}"


def get_game(pin):
    game = games.get(str(pin).strip())
    if not game:
        raise ApiError("Game not found. Check the PIN.", 404)
    return game


def require_host(game, key):
    if not secrets.compare_digest(game["host_key"], str(key)):
        raise ApiError("Only the host can do that.", 403)


def tick(game):
    """Move a question to reveal once time runs out (or everyone answered), then on to the leaderboard."""
    if game["phase"] == "question":
        everyone = game["players"] and len(game["answers"]) >= len(game["players"])
        if time.time() >= game["deadline"] or everyone:
            game["phase"] = "reveal"
            game["revealed_at"] = time.time()
    if game["phase"] == "reveal" and time.time() >= game["revealed_at"] + REVEAL_SECONDS:
        game["phase"] = "leaderboard"


def ranked_ids(game):
    return sorted(game["players"], key=lambda pid: -game["players"][pid]["score"])


def current_question(game):
    return game["questions"][game["index"]]


# --- API actions ---------------------------------------------------------------

def create_game(body):
    cutoff = time.time() - GAME_TTL
    for pin in [p for p, g in games.items() if g["created"] < cutoff]:
        del games[pin]
    pin = str(random.randint(100000, 999999))
    while pin in games:
        pin = str(random.randint(100000, 999999))
    games[pin] = {
        "pin": pin,
        "host_key": secrets.token_hex(16),
        "created": time.time(),
        "phase": "lobby",
        "players": {},
        "questions": random.sample(QUESTIONS, len(QUESTIONS)),
        "index": -1,
        "answers": {},
        "started_at": 0,
        "deadline": 0,
        "revealed_at": 0,
    }
    return {"pin": pin, "hostKey": games[pin]["host_key"]}


def next_step(body):
    game = get_game(body.get("pin"))
    require_host(game, body.get("hostKey"))
    tick(game)
    phase = game["phase"]
    if phase == "lobby" and not game["players"]:
        raise ApiError("Wait for at least one player to join.")
    if phase in ("lobby", "leaderboard"):
        if game["index"] + 1 >= len(game["questions"]):
            game["phase"] = "end"
        else:
            game["index"] += 1
            game["answers"] = {}
            game["phase"] = "question"
            game["started_at"] = time.time()
            game["deadline"] = game["started_at"] + QUESTION_SECONDS
    elif phase == "question":
        game["phase"] = "reveal"
        game["revealed_at"] = time.time()
    elif phase == "reveal":
        game["phase"] = "leaderboard"
    return {"phase": game["phase"]}


def join(body):
    game = get_game(body.get("pin"))
    name = " ".join(str(body.get("name", "")).split())[:16]
    if not name:
        raise ApiError("Pick a nickname.")
    if game["phase"] == "end":
        raise ApiError("That game has already ended.")
    if any(p["name"].lower() == name.lower() for p in game["players"].values()):
        raise ApiError("That name is taken. Try another.")
    player_id = secrets.token_hex(8)
    game["players"][player_id] = {"name": name, "score": 0}
    return {"pin": game["pin"], "playerId": player_id, "name": name}


def answer(body):
    game = get_game(body.get("pin"))
    player_id = str(body.get("playerId"))
    if player_id not in game["players"]:
        raise ApiError("You're not in this game.", 404)
    tick(game)
    if game["phase"] != "question":
        raise ApiError("Too late!")
    if player_id in game["answers"]:
        raise ApiError("You already answered.")
    question = current_question(game)
    try:
        choice = int(body.get("choice"))
    except (TypeError, ValueError):
        raise ApiError("Invalid answer.")
    if not 0 <= choice < len(question["options"]):
        raise ApiError("Invalid answer.")
    correct = choice == question["answer"]
    # Faster correct answers earn more: full points instantly, half at the buzzer.
    elapsed = min(time.time() - game["started_at"], QUESTION_SECONDS)
    points = round(MAX_POINTS * (1 - elapsed / QUESTION_SECONDS / 2)) if correct else 0
    game["answers"][player_id] = {"choice": choice, "points": points}
    game["players"][player_id]["score"] += points
    tick(game)
    return {"ok": True}


def state(pin, host_key, player_id):
    game = get_game(pin)
    tick(game)
    if host_key:
        require_host(game, host_key)
        return host_view(game)
    if player_id in game["players"]:
        return player_view(game, player_id)
    raise ApiError("You're not in this game.", 404)


def host_view(game):
    phase = game["phase"]
    view = {
        "pin": game["pin"],
        "phase": phase,
        "index": game["index"],
        "total": len(game["questions"]),
        "joinUrl": JOIN_URL,
        "players": [p["name"] for p in game["players"].values()],
    }
    if phase in ("question", "reveal"):
        q = current_question(game)
        view["question"] = {"text": q["question"], "options": q["options"]}
        view["remaining"] = max(0.0, game["deadline"] - time.time())
        view["answered"] = len(game["answers"])
    if phase == "reveal":
        counts = [0] * len(q["options"])
        for a in game["answers"].values():
            counts[a["choice"]] += 1
        view["counts"] = counts
        view["correct"] = q["answer"]
    if phase in ("leaderboard", "end"):
        view["leaderboard"] = [
            {"name": game["players"][pid]["name"], "score": game["players"][pid]["score"]}
            for pid in ranked_ids(game)[:5]
        ]
    return view


def player_view(game, player_id):
    player = game["players"][player_id]
    view = {
        "phase": game["phase"],
        "name": player["name"],
        "score": player["score"],
        "rank": ranked_ids(game).index(player_id) + 1,
        "playerCount": len(game["players"]),
        "index": game["index"],
        "total": len(game["questions"]),
    }
    if game["phase"] == "question":
        view["options"] = len(current_question(game)["options"])
        view["answered"] = player_id in game["answers"]
    if game["phase"] == "reveal":
        a = game["answers"].get(player_id)
        view["result"] = {
            "answered": a is not None,
            "correct": a is not None and a["choice"] == current_question(game)["answer"],
            "points": a["points"] if a else 0,
        }
    return view


POST_ROUTES = {"/api/create": create_game, "/api/next": next_step, "/api/join": join, "/api/answer": answer}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_body(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, data):
        self.send_body(status, json.dumps(data).encode(), "application/json")

    def run_api(self, action):
        try:
            with lock:
                data = action()
            self.send_json(200, data)
        except ApiError as e:
            self.send_json(e.status, {"error": str(e)})

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/state":
            query = parse_qs(url.query)
            arg = lambda k: (query.get(k) or [""])[0]
            self.run_api(lambda: state(arg("pin"), arg("hostKey"), arg("playerId")))
            return
        path = "/index.html" if url.path == "/" else url.path
        full = os.path.normpath(os.path.join(PUBLIC, path.lstrip("/")))
        if not full.startswith(PUBLIC + os.sep) or not os.path.isfile(full):
            self.send_body(404, b"Not found", "text/plain")
            return
        with open(full, "rb") as f:
            body = f.read()
        self.send_body(200, body, CONTENT_TYPES.get(os.path.splitext(full)[1], "application/octet-stream"))

    def do_POST(self):
        action = POST_ROUTES.get(urlparse(self.path).path)
        if not action:
            self.send_json(404, {"error": "Not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        self.run_api(lambda: action(body))


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Haunted Trivia is running!")
    print(f"  Host screen:  {PUBLIC_URL.rstrip('/') if PUBLIC_URL else f'http://localhost:{PORT}'}/host.html")
    print(f"  Players join: {JOIN_URL}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
