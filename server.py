from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import json
import mimetypes
import secrets
import socket
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA_DIR = ROOT / "data"
DATA_FILE = DATA_DIR / "board.json"
HOST = "0.0.0.0"
PORT = 8080
ACCESS_CODE = "retro2026"


DEFAULT_BOARD = {
    "title": "RetroBoard: первая живая доска",
    "scenario": "Mad Sad Glad",
    "team": "Аналитики",
    "columns": [
        {"id": "mad", "title": "Mad", "hint": "Что злит или мешает"},
        {"id": "sad", "title": "Sad", "hint": "Что расстроило или тревожит"},
        {"id": "glad", "title": "Glad", "hint": "Что получилось хорошо"},
    ],
    "cards": [
        {
            "id": "demo-1",
            "columnId": "glad",
            "text": "Появилась общая история решений, к которой можно вернуться после ретро.",
            "author": "Мария С.",
            "anonymous": False,
            "createdAt": "2026-05-16T10:00:00",
            "reactions": {"like": [], "important": []},
            "solution": None,
        },
        {
            "id": "demo-2",
            "columnId": "sad",
            "text": "Внешние сервисы часто недоступны, и ретро приходится переносить.",
            "author": "Аноним",
            "anonymous": True,
            "createdAt": "2026-05-16T10:01:00",
            "reactions": {"like": [], "important": []},
            "solution": None,
        },
    ],
    "participants": {},
}


lock = threading.RLock()
condition = threading.Condition(lock)
version = 0


def ensure_data_file():
    DATA_DIR.mkdir(exist_ok=True)
    if not DATA_FILE.exists():
        write_board(DEFAULT_BOARD)


def read_board():
    ensure_data_file()
    with DATA_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_board(board):
    DATA_DIR.mkdir(exist_ok=True)
    tmp_file = DATA_FILE.with_suffix(".tmp")
    with tmp_file.open("w", encoding="utf-8") as file:
        json.dump(board, file, ensure_ascii=False, indent=2)
    tmp_file.replace(DATA_FILE)


def public_board(board):
    safe = dict(board)
    safe["participants"] = [
        {
            "id": participant["id"],
            "name": participant["displayName"],
            "joinedAt": participant["joinedAt"],
        }
        for participant in board.get("participants", {}).values()
    ]
    safe["cards"] = [public_card(card) for card in board.get("cards", [])]
    return safe


def public_card(card):
    safe = dict(card)
    safe["reactions"] = {
        name: len(users)
        for name, users in card.get("reactions", {}).items()
    }
    return safe


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def client_ip():
    try:
        hostname = socket.gethostname()
        for address in socket.gethostbyname_ex(hostname)[2]:
            if not address.startswith("127."):
                return address
    except OSError:
        pass
    return "localhost"


def publish_change():
    global version
    with condition:
        version += 1
        condition.notify_all()


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def error(handler, status, message):
    json_response(handler, status, {"error": message})


class RetroHandler(BaseHTTPRequestHandler):
    server_version = "RetroLive/0.1"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.serve_file(PUBLIC / "index.html")
            return
        if parsed.path == "/events":
            self.serve_events()
            return
        if parsed.path == "/api/state":
            with lock:
                board = public_board(read_board())
            json_response(self, 200, {"board": board, "version": version})
            return
        path = (PUBLIC / parsed.path.lstrip("/")).resolve()
        if not str(path).startswith(str(PUBLIC.resolve())) or not path.exists():
            error(self, 404, "Not found")
            return
        self.serve_file(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        payload = self.read_json()
        if payload is None:
            return

        if parsed.path == "/api/login":
            self.login(payload)
            return
        if parsed.path == "/api/cards":
            self.create_card(payload)
            return
        if parsed.path.startswith("/api/cards/") and parsed.path.endswith("/move"):
            card_id = parsed.path.split("/")[3]
            self.move_card(card_id, payload)
            return
        if parsed.path.startswith("/api/cards/") and parsed.path.endswith("/react"):
            card_id = parsed.path.split("/")[3]
            self.react_card(card_id, payload)
            return
        if parsed.path.startswith("/api/cards/") and parsed.path.endswith("/solution"):
            card_id = parsed.path.split("/")[3]
            self.save_solution(card_id, payload)
            return
        error(self, 404, "Not found")

    def serve_file(self, path):
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last_seen = -1
        while True:
            with condition:
                condition.wait_for(lambda: version != last_seen, timeout=20)
                last_seen = version
                data = json.dumps({"version": version}, ensure_ascii=False)
            try:
                self.wfile.write(f"event: change\ndata: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                break

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            return json.loads(body or "{}")
        except (ValueError, json.JSONDecodeError):
            error(self, 400, "Invalid JSON")
            return None

    def require_participant(self, board, token):
        for participant in board.get("participants", {}).values():
            if secrets.compare_digest(participant["token"], str(token or "")):
                return participant
        return None

    def login(self, payload):
        name = str(payload.get("name", "")).strip()
        access_code = str(payload.get("accessCode", "")).strip()
        anonymous = bool(payload.get("anonymous", False))

        if access_code != ACCESS_CODE:
            error(self, 403, "Неверный код доступа")
            return
        if not name:
            error(self, 400, "Введите имя")
            return

        participant_id = uuid.uuid4().hex
        participant = {
            "id": participant_id,
            "name": name[:80],
            "displayName": "Аноним" if anonymous else name[:80],
            "anonymous": anonymous,
            "token": secrets.token_urlsafe(24),
            "joinedAt": now_iso(),
        }

        with lock:
            board = read_board()
            board.setdefault("participants", {})[participant_id] = participant
            write_board(board)
        publish_change()
        json_response(self, 200, {
            "participant": {
                "id": participant["id"],
                "name": participant["displayName"],
                "token": participant["token"],
                "anonymous": anonymous,
            }
        })

    def create_card(self, payload):
        token = payload.get("token")
        text = str(payload.get("text", "")).strip()
        column_id = str(payload.get("columnId", "")).strip()
        anonymous = bool(payload.get("anonymous", False))

        if not text:
            error(self, 400, "Пустой стикер не получится добавить")
            return

        with lock:
            board = read_board()
            participant = self.require_participant(board, token)
            if not participant:
                error(self, 401, "Сначала войдите на доску")
                return
            column_ids = {column["id"] for column in board.get("columns", [])}
            if column_id not in column_ids:
                error(self, 400, "Неизвестная колонка")
                return
            is_anonymous = anonymous or participant.get("anonymous", False)
            card = {
                "id": uuid.uuid4().hex,
                "columnId": column_id,
                "text": text[:1000],
                "author": "Аноним" if is_anonymous else participant["name"],
                "anonymous": is_anonymous,
                "createdAt": now_iso(),
                "reactions": {"like": [], "important": []},
                "solution": None,
            }
            board.setdefault("cards", []).append(card)
            write_board(board)
        publish_change()
        json_response(self, 201, {"card": public_card(card)})

    def move_card(self, card_id, payload):
        token = payload.get("token")
        column_id = str(payload.get("columnId", "")).strip()
        position = payload.get("position", None)

        with lock:
            board = read_board()
            participant = self.require_participant(board, token)
            if not participant:
                error(self, 401, "Сначала войдите на доску")
                return
            column_ids = {column["id"] for column in board.get("columns", [])}
            if column_id not in column_ids:
                error(self, 400, "Неизвестная колонка")
                return
            cards = board.setdefault("cards", [])
            card = next((item for item in cards if item["id"] == card_id), None)
            if not card:
                error(self, 404, "Стикер не найден")
                return
            cards.remove(card)
            card["columnId"] = column_id
            same_column_count = sum(1 for item in cards if item["columnId"] == column_id)
            try:
                position = int(position)
            except (TypeError, ValueError):
                position = same_column_count
            position = max(0, min(position, same_column_count))
            insert_at = 0
            seen_in_column = 0
            for index, item in enumerate(cards):
                if item["columnId"] == column_id:
                    if seen_in_column == position:
                        insert_at = index
                        break
                    seen_in_column += 1
                insert_at = index + 1
            cards.insert(insert_at, card)
            write_board(board)
        publish_change()
        json_response(self, 200, {"card": public_card(card)})

    def react_card(self, card_id, payload):
        token = payload.get("token")
        reaction = str(payload.get("reaction", "")).strip()
        if reaction not in {"like", "important"}:
            error(self, 400, "Неизвестная реакция")
            return

        with lock:
            board = read_board()
            participant = self.require_participant(board, token)
            if not participant:
                error(self, 401, "Сначала войдите на доску")
                return
            card = next((item for item in board.get("cards", []) if item["id"] == card_id), None)
            if not card:
                error(self, 404, "Стикер не найден")
                return
            users = card.setdefault("reactions", {}).setdefault(reaction, [])
            if participant["id"] in users:
                users.remove(participant["id"])
            else:
                users.append(participant["id"])
            write_board(board)
        publish_change()
        json_response(self, 200, {"card": public_card(card)})

    def save_solution(self, card_id, payload):
        token = payload.get("token")
        owner = str(payload.get("owner", "")).strip()
        due_date = str(payload.get("dueDate", "")).strip()

        with lock:
            board = read_board()
            participant = self.require_participant(board, token)
            if not participant:
                error(self, 401, "Сначала войдите на доску")
                return
            card = next((item for item in board.get("cards", []) if item["id"] == card_id), None)
            if not card:
                error(self, 404, "Стикер не найден")
                return
            card["solution"] = {
                "owner": owner[:80],
                "dueDate": due_date[:20],
                "status": "new",
                "createdAt": now_iso(),
            }
            write_board(board)
        publish_change()
        json_response(self, 200, {"card": public_card(card)})


def main():
    ensure_data_file()
    httpd = ThreadingHTTPServer((HOST, PORT), RetroHandler)
    local_url = f"http://localhost:{PORT}"
    network_url = f"http://{client_ip()}:{PORT}"
    print("RetroLive started")
    print(f"Local:   {local_url}")
    print(f"Network: {network_url}")
    print(f"Access code: {ACCESS_CODE}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
