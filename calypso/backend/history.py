"""Documents récents et état de lecture de chaque document (page, zoom, disposition).

Un seul fichier JSON (~/.local/share/calypso/history.json), clé = chemin réel. Écrit par un nom
temporaire puis `os.replace` : jamais de fichier à moitié écrit. 300 documents au plus.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from ..settings import DATA_DIR

HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
LIMIT = 300
STATE_KEYS = {"page": int, "offset": float, "zoom_mode": str, "scale": float, "rotation": int,
              "continuous": bool, "dual": bool, "dual_cover": bool, "night": bool, "sidebar": bool}


def _key(path: str) -> str:
    return os.path.realpath(path)


class History:
    def __init__(self, path: str = HISTORY_FILE):
        self.path = path
        self.entries: dict[str, dict] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self.entries = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}
        except (OSError, ValueError):
            pass

    def state(self, path: str) -> dict:
        """État retenu pour ce document, seulement les valeurs du bon type."""
        entry = self.entries.get(_key(path)) or {}
        out = {}
        for k, kind in STATE_KEYS.items():
            v = entry.get(k)
            if kind is float and isinstance(v, int) and not isinstance(v, bool):
                v = float(v)
            if isinstance(v, kind) and not (kind is not bool and isinstance(v, bool)):
                out[k] = v
        return out

    def remember(self, path: str, state: dict, title: str = ""):
        key = _key(path)
        entry = dict(self.entries.get(key) or {})
        entry.update({k: v for k, v in state.items() if k in STATE_KEYS})
        entry["time"] = time.time()
        if title:
            entry["title"] = title
        self.entries.pop(key, None)
        self.entries[key] = entry                   # le plus récent en dernier
        while len(self.entries) > LIMIT:
            self.entries.pop(next(iter(self.entries)))
        self.save()

    def forget(self, path: str):
        if self.entries.pop(_key(path), None) is not None:
            self.save()

    def clear(self):
        self.entries = {}
        self.save()

    def recent(self, limit: int = 40) -> list[tuple[str, dict]]:
        """Documents récents encore présents, le plus récent d'abord."""
        out = []
        for path, entry in sorted(self.entries.items(), key=lambda kv: -float(kv[1].get("time", 0) or 0)):
            if os.path.isfile(path):
                out.append((path, entry))
                if len(out) >= limit:
                    break
        return out

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.entries, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError:
            pass


_history: Optional[History] = None


def history() -> History:
    global _history
    if _history is None:
        _history = History()
    return _history
