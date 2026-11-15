"""Miniatures de la première page pour l'accueil (documents récents), en cache sur le disque.

Fichier PNG dans ~/.cache/calypso/thumbs, nommé d'après le chemin réel, la taille et la date du
document : un document modifié a une nouvelle miniature. Rendu dans un fil, un document à la fois.
"""

from __future__ import annotations

import hashlib
import os
import queue
import threading
from typing import Callable, Optional

import cairo
from gi.repository import GLib

from ..settings import CACHE_DIR
from .document import Document, OpenError, PasswordNeeded

THUMBS_DIR = os.path.join(CACHE_DIR, "thumbs")
WIDTH = 256


def cache_path(path: str) -> Optional[str]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = f"{os.path.realpath(path)}\0{st.st_size}\0{st.st_mtime_ns}".encode("utf-8", "surrogateescape")
    return os.path.join(THUMBS_DIR, hashlib.sha1(key).hexdigest() + ".png")


def make(path: str) -> Optional[str]:
    """Chemin de la miniature (créée si besoin) ; None : document illisible ou protégé."""
    out = cache_path(path)
    if out is None:
        return None
    if os.path.exists(out):
        return out
    try:
        doc = Document.open(path)
    except (OpenError, PasswordNeeded):
        return None
    w, h = doc.size(0)
    scale = WIDTH / w
    data, tw, th, stride = doc.render(0, scale, 0, (0, 0, int(w * scale), int(h * scale)))
    surface = cairo.ImageSurface.create_for_data(bytearray(data), cairo.FORMAT_ARGB32, tw, th, stride)
    os.makedirs(THUMBS_DIR, exist_ok=True)
    tmp = out + ".tmp"
    try:
        surface.write_to_png(tmp)
        os.replace(tmp, out)
    except OSError:
        return None
    return out


class ThumbQueue:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._thread = None

    def request(self, path: str, done: Callable[[Optional[str]], None]):
        self._q.put((path, done))
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="calypso-thumbs", daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            try:
                path, done = self._q.get(timeout=5)
            except queue.Empty:
                return
            try:
                result = make(path)
            except Exception:
                result = None
            GLib.idle_add(lambda d=done, r=result: (d(r), False)[1])


thumb_queue = ThumbQueue()
