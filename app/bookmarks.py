import json
import time
import uuid

import app.config as config


def _ensure_dir():
    config.BOOKMARKS_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_bookmarks():
    if not config.BOOKMARKS_FILE.exists():
        return []
    try:
        data = json.loads(config.BOOKMARKS_FILE.read_text(encoding="utf-8"))
        return data.get("bookmarks", [])
    except Exception:
        return []


def save_bookmarks(bookmarks):
    _ensure_dir()
    config.BOOKMARKS_FILE.write_text(
        json.dumps({"version": 1, "bookmarks": bookmarks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _new_id():
    return uuid.uuid4().hex[:8]


def add_bookmark(data):
    bookmarks = load_bookmarks()
    bm = {
        "id": _new_id(),
        "name": data.get("name", ""),
        "host": data.get("host", ""),
        "port": int(data.get("port", 22)),
        "username": data.get("username", ""),
        "auth_method": data.get("auth_method", "key"),
        "key_path": data.get("key_path", None),
        "default_path": data.get("default_path", "/"),
        "timestamp": int(time.time()),
    }
    bookmarks.append(bm)
    save_bookmarks(bookmarks)
    return bm


def get_bookmark(bid):
    for b in load_bookmarks():
        if b["id"] == bid:
            return b
    return None


def update_bookmark(bid, data):
    bookmarks = load_bookmarks()
    for b in bookmarks:
        if b["id"] == bid:
            for k in ("name", "host", "port", "username", "auth_method", "key_path", "default_path"):
                if k in data and data[k] is not None:
                    b[k] = data[k]
            b["timestamp"] = int(time.time())
            save_bookmarks(bookmarks)
            return b
    return None


def delete_bookmark(bid):
    bookmarks = load_bookmarks()
    new = [b for b in bookmarks if b["id"] != bid]
    save_bookmarks(new)
