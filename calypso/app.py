"""Point d'entrée de l'application Calypso."""

from __future__ import annotations

import os
import signal
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME, VERSION  # noqa: E402
from .i18n import tr  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


class CalypsoApp(Adw.Application):
    """Instance unique, une fenêtre par document comme Papers : « calypso x.pdf » lancé une seconde
    fois ouvre x.pdf dans la première instance (fenêtre déjà ouverte sur lui : ramenée devant)."""

    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE | Gio.ApplicationFlags.HANDLES_OPEN)
        GLib.set_application_name(APP_NAME)
        self.add_main_option("version", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             tr("Afficher la version"), None)
        self.add_main_option("page", ord("p"), GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
                             tr("Ouvrir à cette page (numéro ou étiquette)"), tr("PAGE"))
        self.add_main_option("new-window", ord("w"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             tr("Ouvrir une nouvelle fenêtre"), None)

    def do_startup(self):
        Adw.Application.do_startup(self)
        display = Gdk.Display.get_default()
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(DATA_DIR, "base.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        icons = os.path.join(DATA_DIR, "icons")
        if os.path.isdir(icons):
            Gtk.IconTheme.get_for_display(display).add_search_path(icons)
        from .ui.preferences import apply_theme
        apply_theme()
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, lambda: (self._quit(), False)[1])
        for name, cb, accels in (("preferences", self._prefs, ["<Control>comma"]),
                                 ("about", self._about, []),
                                 ("new-window", lambda: self.new_window().present(), ["<Control>n"]),
                                 ("quit", self._quit, ["<Control>q"])):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    # -- fenêtres -------------------------------------------------------------------------------
    def windows(self) -> list:
        from .ui.window import MainWindow
        return [w for w in self.get_windows() if isinstance(w, MainWindow)]

    def new_window(self):
        from .ui.window import MainWindow
        return MainWindow(self)

    def open_path(self, path: str, page: str = "", new_window: bool = False):
        """Document déjà ouvert : sa fenêtre revient devant ; sinon la fenêtre active si elle est
        vide (accueil), ou une nouvelle fenêtre."""
        real = os.path.realpath(path)
        for w in self.windows():
            if w.path and os.path.realpath(w.path) == real:
                w.present()
                if page:
                    w.go_to_label(page)
                return w
        target = None
        if not new_window:
            active = self.get_active_window()
            for w in ([active] if active in self.windows() else []) + self.windows():
                if w.path is None and not w.loading:
                    target = w
                    break
        if target is None:
            target = self.new_window()
        target.present()
        target.open_document(path, page=page)
        return target

    def do_activate(self):
        wins = self.windows()
        if wins:
            wins[0].present()
        else:
            self.new_window().present()

    def do_open(self, files, n, hint):
        opened = False
        for f in files:
            path = f.get_path()
            if path:
                self.open_path(path)
                opened = True
        if not opened:
            self.do_activate()

    def do_command_line(self, cmd):
        opts = cmd.get_options_dict().end().unpack()
        if opts.get("version"):
            cmd.print_literal(f"{APP_NAME} {VERSION}\n")
            return 0
        cwd = cmd.get_cwd() or os.path.expanduser("~")
        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")
        paths = []
        for a in cmd.get_arguments()[1:]:
            f = Gio.File.new_for_commandline_arg_and_cwd(a, cwd)
            if f.get_path():
                paths.append(f.get_path())
        if not paths:
            if opts.get("new-window"):
                self.new_window().present()
            else:
                self.do_activate()
            return 0
        for p in paths:
            self.open_path(p, page=opts.get("page") or "", new_window=bool(opts.get("new-window")))
        return 0

    def _quit(self):
        """Ctrl+Q : chaque fenêtre se ferme ; une fenêtre aux modifications non enregistrées pose sa
        question, et l'application s'arrête d'elle-même avec la dernière fenêtre."""
        pending = False
        for w in self.windows():
            w.save_state()
            if w.document is not None and w.document.dirty:
                pending = True
                w.present()
            w.close()
        if not pending:
            self.quit()

    # -- fenêtres annexes ------------------------------------------------------------------------
    def _prefs(self):
        from .ui.preferences import PreferencesWindow
        d = PreferencesWindow()
        d.set_transient_for(self.get_active_window())
        d.present()

    def _about(self):
        about = Adw.AboutWindow(transient_for=self.get_active_window(), modal=True,
                                application_name=APP_NAME, application_icon=APP_ID, version=VERSION,
                                developer_name="Tof",
                                comments=tr("Une visionneuse de documents PDF pour GNOME, avec les outils "
                                            "pour les modifier."),
                                website="https://github.com/ToFpon/Calypso",
                                issue_url="https://github.com/ToFpon/Calypso/issues",
                                license_type=Gtk.License.GPL_3_0)
        about.add_credit_section(tr("Inspiré de"), ["Papers https://apps.gnome.org/Papers", "Evince"])
        about.add_acknowledgement_section(tr("Moteurs"), ["Poppler https://poppler.freedesktop.org", "qpdf",
                                                          "Ghostscript"])
        about.present()


def _process_name(name: str):
    """Nom du processus pour le noyau : « calypso » dans le Moniteur système, ps, killall."""
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(15, ctypes.c_char_p(name.encode()[:15]), 0, 0, 0)     # 15 = PR_SET_NAME
    except (OSError, AttributeError):
        pass


def main(argv=None) -> int:
    _process_name("calypso")
    import faulthandler
    faulthandler.enable(all_threads=True)
    GLib.set_prgname(APP_ID)
    app = CalypsoApp()
    return app.run(argv if argv is not None else sys.argv)
