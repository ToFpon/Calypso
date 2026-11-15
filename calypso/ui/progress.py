"""Fenêtre de progression des outils : apparaît si le travail dure plus de 0,4 s, « Annuler »
arrête le programme en cours (qpdf, Ghostscript)."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..backend.jobs import Job  # noqa: E402
from ..i18n import tr  # noqa: E402
from .common import DialogWindow, alert, shown  # noqa: E402


class ProgressWindow(DialogWindow):
    def __init__(self, job: Job):
        super().__init__(title=job.title, content_width=420, content_height=-1, deletable=False)
        self.job = job
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24, margin_bottom=24,
                      margin_start=24, margin_end=24)
        box.append(Gtk.Label(label=shown(job.title), css_classes=["heading"], xalign=0, wrap=True))
        self.bar = Gtk.ProgressBar()
        box.append(self.bar)
        self.status = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"], ellipsize=3)
        box.append(self.status)
        cancel = Gtk.Button(label=tr("Annuler"), halign=Gtk.Align.END)
        cancel.connect("clicked", lambda *_: job.cancel())
        box.append(cancel)
        tv = Adw.ToolbarView()
        tv.set_content(box)
        self.set_child(tv)
        self.connect("close-request", lambda *_: (job.cancel(), False)[1])

    def update(self, fraction, status):
        if fraction <= 0:
            self.bar.pulse()
        else:
            self.bar.set_fraction(fraction)
        self.status.set_label(shown(status or ""))


def run_job(parent, title: str, func, on_success=None) -> Job:
    """Lance func(job) dans un fil ; erreurs montrées dans une vraie boîte ; on_success(job) sinon."""
    state = {"win": None}

    def progress(fraction, status):
        if state["win"] is not None:
            state["win"].update(fraction, status)

    def done(job):
        if state.get("timer"):
            GLib.source_remove(state["timer"])
        if state["win"] is not None:
            state["win"].destroy()
            state["win"] = None
        if job.state == "done":
            if on_success is not None:
                on_success(job)
        elif job.state == "failed":
            alert(parent, tr("L'opération a échoué"), shown(job.error))

    job = Job(title, func, done, progress)

    def show():
        state["timer"] = 0
        if job.state == "running":
            w = ProgressWindow(job)
            w.present(parent)
            state["win"] = w
        return False
    state["timer"] = GLib.timeout_add(400, show)
    return job.start()
