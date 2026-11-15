"""Fil de rendu : les pages sont dessinées hors du fil principal, la plus urgente d'abord.

`want(channel, requests)` remplace la liste d'attente d'un canal (« view » : pages et tuiles
visibles ; « preview » : aperçus flous pendant le défilement ; « thumbs » : miniatures) : ce qui
n'est plus visible n'est jamais dessiné. Les canaux passent dans cet ordre. Le résultat arrive dans
le fil principal (`deliver(key, texture)`), en GdkTexture.
"""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib  # noqa: E402

CHANNELS = ("view", "preview", "thumbs")


def make_texture(data: bytes, w: int, h: int, stride: int) -> Gdk.Texture:
    # ARGB32 de cairo en mémoire (petit-boutiste) = B, G, R, A prémultipliés
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED, GLib.Bytes.new(data), stride)


class Renderer:
    def __init__(self, document, deliver: Callable):
        self.document = document
        self._deliver = deliver
        self._cond = threading.Condition()
        self._pending = {c: [] for c in CHANNELS}
        self._busy = set()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="calypso-render", daemon=True)
        self._thread.start()

    def want(self, channel: str, requests: list):
        """requests : [(clé, page, échelle, rotation, tuile)], le plus urgent d'abord."""
        with self._cond:
            self._pending[channel] = [r for r in requests if r[0] not in self._busy]
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._stopped = True
            for c in CHANNELS:
                self._pending[c] = []
            self._cond.notify()

    def _next(self):
        for c in CHANNELS:
            if self._pending[c]:
                return self._pending[c].pop(0)
        return None

    def _run(self):
        while True:
            with self._cond:
                while not self._stopped and (req := self._next()) is None:
                    self._cond.wait()
                if self._stopped:
                    return
                self._busy.add(req[0])
            key, page, scale, rotation, tile = req
            try:
                data, w, h, stride = self.document.render(page, scale, rotation, tile)
                texture = make_texture(data, w, h, stride)
            except Exception:               # page illisible : rien (fond blanc), pas de plantage du fil
                texture = None
            with self._cond:
                self._busy.discard(key)
                stopped = self._stopped
            if not stopped:
                GLib.idle_add(self._give, key, texture)

    def _give(self, key, texture):
        if not self._stopped:
            self._deliver(key, texture)
        return False
