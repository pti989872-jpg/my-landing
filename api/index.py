from copy import deepcopy
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import secrets
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server import ACCESS_CODE, DEFAULT_BOARD, now_iso, public_board, public_card


try:
    from vercel.functions import RuntimeCache
except Exception:
    RuntimeCache = None


STORE_KEY = "retro-board-state"
_cache = RuntimeCache(namespace="retro-board") if RuntimeCache else None
_memory_record = {"board": deepcopy(DEFAULT_BOARD), "version": 0}


def _load_record():
    if _cache:
        try:
            record = _cache.get(STORE_KEY)
            if isinstance(record, str):
                record = json.loads(record)
            if isinstance(record, dict) and "board" in record:
                return record
        except Exception:
            pass
    return deepcopy(_memory_record)


def _save_record(record):
    global _memory_record
    record["version"] = int(record.get("version") or 0) + 1
    if _cache:
        try:
            _cache.set(
                STORE_KEY,
                record,
                {"name": "RetroBoard state", "tags": ["retro-board"]},
            )
        except Exception:
            pass
    _memory_record = deepcopy(record)


def _json(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error(handler, status, message):
    _json(handler, status, {"error": message})


def _read_json(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0"))
        body = handler.rfile.read(length).decode("utf-8")
        return json.loads(body or "{}")
    except (ValueError, json.JSONDecodeError):
        _error(handler, 400, "Некорректный JSON")
        return None


def _normalized_path(raw_path):
    parsed = urlparse(raw_path)
    query = parse_qs(parsed.query)
    forwarded = (query.get("path") or [""])[0].strip("/")

    if forwarded == "events":
        return "/events"
    if forwarded:
        return f"/api/{forwarded}"
    return parsed.path


def _require_participant(board, token):
    for participant in board.get("participants", {}).values():
        if secrets.compare_digest(participant["token"], str(token or "")):
            return participant
    return None


def _card_by_id(board, card_id):
    return next((card for card in board.get("cards", []) if card["id"] == card_id), None)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = _normalized_path(self.path)

        if path == "/api/state":
            record = _load_record()
            _json(
                self,
                200,
                {
                    "board": public_board(record["board"]),
                    "version": int(record.get("version") or 0),
                },
            )
            return

        if path == "/events":
            record = _load_record()
            body = (
                "event: change\n"
                f"data: {json.dumps({'version': int(record.get('version') or 0)})}\n\n"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        _error(self, 404, "Not found")

    def do_POST(self):
        path = _normalized_path(self.path)
        payload = _read_json(self)
        if payload is None:
            return

        if path == "/api/login":
            self._login(payload)
            return

        if path == "/api/cards":
            self._create_card(payload)
            return

        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "cards":
            card_id = parts[2]
            action = parts[3]
            if action == "move":
                self._move_card(card_id, payload)
                return
            if action == "react":
                self._react_card(card_id, payload)
                return
            if action == "solution":
                self._save_solution(card_id, payload)
                return

        _error(self, 404, "Not found")

    def _login(self, payload):
        name = str(payload.get("name", "")).strip()
        access_code = str(payload.get("accessCode", "")).strip()
        anonymous = bool(payload.get("anonymous", False))

        if access_code != ACCESS_CODE:
            _error(self, 403, "Неверный код доступа")
            return
        if not name:
            _error(self, 400, "Введите имя")
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

        record = _load_record()
        board = record["board"]
        board.setdefault("participants", {})[participant_id] = participant
        _save_record(record)

        _json(
            self,
            200,
            {
                "participant": {
                    "id": participant["id"],
                    "name": participant["displayName"],
                    "token": participant["token"],
                    "anonymous": anonymous,
                }
            },
        )

    def _create_card(self, payload):
        token = payload.get("token")
        text = str(payload.get("text", "")).strip()
        column_id = str(payload.get("columnId", "")).strip()
        anonymous = bool(payload.get("anonymous", False))

        if not text:
            _error(self, 400, "Пустой стикер не получится добавить")
            return

        record = _load_record()
        board = record["board"]
        participant = _require_participant(board, token)
        if not participant:
            _error(self, 401, "Сначала войдите на доску")
            return

        column_ids = {column["id"] for column in board.get("columns", [])}
        if column_id not in column_ids:
            _error(self, 400, "Неизвестная колонка")
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
        _save_record(record)
        _json(self, 201, {"card": public_card(card)})

    def _move_card(self, card_id, payload):
        token = payload.get("token")
        column_id = str(payload.get("columnId", "")).strip()
        position = payload.get("position", None)

        record = _load_record()
        board = record["board"]
        participant = _require_participant(board, token)
        if not participant:
            _error(self, 401, "Сначала войдите на доску")
            return

        column_ids = {column["id"] for column in board.get("columns", [])}
        if column_id not in column_ids:
            _error(self, 400, "Неизвестная колонка")
            return

        cards = board.setdefault("cards", [])
        card = _card_by_id(board, card_id)
        if not card:
            _error(self, 404, "Стикер не найден")
            return

        cards.remove(card)
        card["columnId"] = column_id
        same_column_count = sum(1 for item in cards if item["columnId"] == column_id)
        try:
            position = int(position)
        except (TypeError, ValueError):
            position = same_column_count
        position = max(0, min(position, same_column_count))

        insert_at = len(cards)
        seen_in_column = 0
        for index, item in enumerate(cards):
            if item["columnId"] == column_id:
                if seen_in_column == position:
                    insert_at = index
                    break
                seen_in_column += 1
        cards.insert(insert_at, card)
        _save_record(record)
        _json(self, 200, {"card": public_card(card)})

    def _react_card(self, card_id, payload):
        token = payload.get("token")
        reaction = str(payload.get("reaction", "")).strip()
        if reaction not in {"like", "important"}:
            _error(self, 400, "Неизвестная реакция")
            return

        record = _load_record()
        board = record["board"]
        participant = _require_participant(board, token)
        if not participant:
            _error(self, 401, "Сначала войдите на доску")
            return

        card = _card_by_id(board, card_id)
        if not card:
            _error(self, 404, "Стикер не найден")
            return

        users = card.setdefault("reactions", {}).setdefault(reaction, [])
        if participant["id"] in users:
            users.remove(participant["id"])
        else:
            users.append(participant["id"])
        _save_record(record)
        _json(self, 200, {"card": public_card(card)})

    def _save_solution(self, card_id, payload):
        token = payload.get("token")
        owner = str(payload.get("owner", "")).strip()
        due_date = str(payload.get("dueDate", "")).strip()

        record = _load_record()
        board = record["board"]
        participant = _require_participant(board, token)
        if not participant:
            _error(self, 401, "Сначала войдите на доску")
            return

        card = _card_by_id(board, card_id)
        if not card:
            _error(self, 404, "Стикер не найден")
            return

        card["solution"] = {
            "owner": owner[:80],
            "dueDate": due_date[:20],
            "status": "new",
            "createdAt": now_iso(),
        }
        _save_record(record)
        _json(self, 200, {"card": public_card(card)})
