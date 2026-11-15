"""Tâches de fond des outils (compression, fusion, filigrane…) : progression, annulation, programmes
externes (qpdf, Ghostscript) lancés dans leur propre groupe pour pouvoir les arrêter net.
Repris de l'Engine d'Odysseus, réduit à ce dont Calypso a besoin."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from typing import Callable, Optional

from gi.repository import GLib

from ..i18n import tr


class ToolError(Exception):
    def __init__(self, message: str, output: str = "", code: int = 0):
        super().__init__(message)
        self.output = output
        self.code = code


class Cancelled(Exception):
    pass


def quote_cmd(argv: list) -> str:
    """Commande lisible, mot de passe masqué."""
    out = []
    for a in argv:
        a = str(a)
        if a.startswith(("--password=", "-sPDFPassword=")):
            a = a.split("=", 1)[0] + "=••••"
        out.append(a if a and all(c.isalnum() or c in "-_./=:,+" for c in a) else "'" + a.replace("'", "'\\''") + "'")
    return " ".join(out)


class Job:
    """Une tâche : `func(job)` tourne dans un fil ; `on_progress(fraction, texte)` et
    `on_done(job)` sont appelés dans le fil principal."""

    def __init__(self, title: str, func: Callable, on_done: Optional[Callable] = None,
                 on_progress: Optional[Callable] = None):
        self.title = title
        self.func = func
        self.on_done = on_done
        self.on_progress = on_progress
        self.state = "pending"        # pending, running, done, failed, cancelled
        self.result = None
        self.error = ""
        self.output = ""
        self.commands: list = []
        self.fraction = 0.0
        self.status = ""
        self._cancel = threading.Event()
        self._procs: list = []
        self._notify_pending = False

    # -- côté fil de travail ----------------------------------------------------------------------
    def progress(self, fraction: Optional[float] = None, status: Optional[str] = None):
        if fraction is not None:
            self.fraction = max(0.0, min(1.0, fraction))
        if status is not None:
            self.status = status
        if not self._notify_pending and self.on_progress is not None:
            self._notify_pending = True
            GLib.idle_add(self._notify)

    def _notify(self):
        self._notify_pending = False
        if self.on_progress is not None:
            self.on_progress(self.fraction, self.status)
        return False

    def check_cancel(self):
        if self._cancel.is_set():
            raise Cancelled()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def popen(self, argv: list, **kw) -> subprocess.Popen:
        self.check_cancel()
        self.commands.append(quote_cmd(argv))
        kw.setdefault("stdin", subprocess.DEVNULL)
        p = subprocess.Popen([str(a) for a in argv], start_new_session=True, **kw)
        self._procs.append(p)
        return p

    # -- côté fil principal -------------------------------------------------------------------------
    def cancel(self):
        self._cancel.set()
        for p in list(self._procs):
            try:
                if p.poll() is None:
                    os.killpg(p.pid, signal.SIGTERM)
            except OSError:
                try:
                    p.kill()
                except OSError:
                    pass

    def start(self):
        self.state = "running"

        def work():
            try:
                self.result = self.func(self)
                self.state = "cancelled" if self.cancelled else "done"
            except Cancelled:
                self.state = "cancelled"
            except ToolError as e:
                self.state = "cancelled" if self.cancelled else "failed"
                self.error, self.output = str(e), e.output
            except Exception as e:                 # jamais une tâche muette
                self.state = "cancelled" if self.cancelled else "failed"
                self.error = str(e) or e.__class__.__name__
            GLib.idle_add(self._finished)
        threading.Thread(target=work, name="calypso-job", daemon=True).start()
        return self

    def _finished(self):
        if self.on_done is not None:
            self.on_done(self)
        return False


def human_size(n: int) -> str:
    from ..i18n import decimal
    for unit, div in ((tr("Go"), 1 << 30), (tr("Mo"), 1 << 20), (tr("ko"), 1 << 10)):
        if n >= div:
            return f"{decimal(n / div)} {unit}"
    return tr("{0} octets").format(n)
