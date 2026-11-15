"""Fenêtre principale : un document par fenêtre, comme Papers.

En-tête : panneau latéral, numéro de page ; titre ; recherche, zoom, menu principal. Contenu :
accueil (documents récents) tant qu'aucun document n'est ouvert, sinon panneau latéral + barre de
recherche + vue des pages. Le document est ouvert dans un fil (gros fichiers, partages réseau).
"""

from __future__ import annotations

import os
import threading
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_NAME, features  # noqa: E402
from ..backend.document import Document, OpenError, PasswordNeeded  # noqa: E402
from ..backend.history import history  # noqa: E402
from ..i18n import ntr, tr  # noqa: E402
from ..settings import settings  # noqa: E402
from .common import AlertDialog, alert, choose_files, choose_save, open_with_default, shown, \
    show_in_folder, toast  # noqa: E402
from .search import Search  # noqa: E402
from .sidebar import Sidebar  # noqa: E402
from .view import DocView, MAX_ZOOM, MIN_ZOOM  # noqa: E402
from .welcome import Welcome  # noqa: E402

PDF_FILTERS = [(tr("Documents PDF"), ["*.pdf", "*.PDF"]), (tr("Tous les fichiers"), ["*"])]

ACCELS = {
    "win.open": ["<Control>o"],
    "win.close": ["<Control>w"],
    "win.save": ["<Control>s"],
    "win.save-as": ["<Control><Shift>s"],
    "win.undo": ["<Control>z"],
    "win.redo": ["<Control><Shift>z", "<Control>y"],
    "win.reload": ["<Control>r"],
    "win.properties": ["<Alt>Return"],
    "win.find": ["<Control>f"],
    "win.find-next": ["<Control>g", "F3"],
    "win.find-previous": ["<Control><Shift>g", "<Shift>F3"],
    "win.copy": ["<Control>c", "<Control>Insert"],
    "win.select-all": ["<Control>a"],
    "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
    "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
    "win.zoom-reset": ["<Control>0", "<Control>KP_0"],
    "win.rotate-left": ["<Control>Left"],
    "win.rotate-right": ["<Control>Right"],
    "win.night": ["<Control>i"],
    "win.sidebar": ["F9"],
    "win.fullscreen": ["F11"],
    "win.go-back": ["<Alt>Left"],
    "win.go-forward": ["<Alt>Right"],
    "win.first-page": ["<Control>Home"],
    "win.last-page": ["<Control>End"],
    "win.go-page": ["<Control>l"],
    "win.shortcuts": ["F1", "<Control>question"],
}

# (touche, Ctrl, Maj) → ce que fait la touche dans un champ de saisie. GTK 4 traite les raccourcis de
# l'application AVANT le widget qui a le focus : sans ça, Ctrl+A / Ctrl+C dans la recherche
# sélectionnaient ou copiaient le document, Ctrl+← pivotait la page.
TEXT_KEYS = {
    ("a", True, False): ("action", "selection.select-all"),
    ("c", True, False): ("action", "clipboard.copy"),
    ("Insert", True, False): ("action", "clipboard.copy"),
    ("x", True, False): ("action", "clipboard.cut"),
    ("v", True, False): ("action", "clipboard.paste"),
    ("z", True, False): ("action", "text.undo"),
    ("z", True, True): ("action", "text.redo"),
    ("y", True, False): ("action", "text.redo"),
    ("Left", True, False): ("move", Gtk.MovementStep.WORDS, -1, False),
    ("Right", True, False): ("move", Gtk.MovementStep.WORDS, 1, False),
    ("Left", True, True): ("move", Gtk.MovementStep.WORDS, -1, True),
    ("Right", True, True): ("move", Gtk.MovementStep.WORDS, 1, True),
    ("Home", True, False): ("move", Gtk.MovementStep.BUFFER_ENDS, -1, False),
    ("End", True, False): ("move", Gtk.MovementStep.BUFFER_ENDS, 1, False),
}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(settings["window_width"], settings["window_height"])
        self.set_size_request(360, 320)
        self.path: Optional[str] = None
        self.document: Optional[Document] = None
        self.loading = False
        self._open_gen = 0
        self._monitor = None
        self._reload_timer = 0
        self._save_timer = 0
        self._stamp = None
        self._ctx = None
        self._fs_hide = 0
        self._discard_ok = False
        self._saving = False
        self._build()
        self._actions()
        if not getattr(app, "_calypso_accels", False):
            for name, accels in ACCELS.items():
                app.set_accels_for_action(name, accels)
            app._calypso_accels = True
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_capture_key)
        self.add_controller(keys)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_window_motion)
        self.add_controller(motion)
        self.connect("close-request", self._on_close_request)
        self.connect("notify::fullscreened", self._on_fullscreen)
        self._sync_actions()

    # ======================================================================================
    # Construction
    # ======================================================================================
    def _build(self):
        self.toast_overlay = Adw.ToastOverlay()
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.toolbar.add_top_bar(self.header)

        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                            tooltip_text=tr("Panneau latéral (F9)"), action_name="win.sidebar")
        self.header.pack_start(self.sidebar_btn)
        self.page_box = Gtk.Box(spacing=6)
        self.page_entry = Gtk.Entry(width_chars=4, max_width_chars=8, xalign=1,
                                    tooltip_text=tr("Numéro de la page (Ctrl+L)"))
        self.page_entry.connect("activate", self._on_page_entry)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self._show_page_number())
        self.page_entry.add_controller(focus)
        self.page_total = Gtk.Label(css_classes=["dim-label", "numeric"])
        self.page_box.append(self.page_entry)
        self.page_box.append(self.page_total)
        self.header.pack_start(self.page_box)

        self.title_widget = Adw.WindowTitle(title=APP_NAME)
        self.header.set_title_widget(self.title_widget)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=self._main_menu(),
                                  tooltip_text=tr("Menu principal"), primary=True)
        self.header.pack_end(menu_btn)
        self.zoom_btn = Gtk.MenuButton(label="100 %", menu_model=self._zoom_menu(),
                                       tooltip_text=tr("Zoom"), css_classes=["numeric"])
        self.header.pack_end(self.zoom_btn)
        self.search_btn = Gtk.ToggleButton(icon_name="edit-find-symbolic", tooltip_text=tr("Rechercher (Ctrl+F)"))
        self.search_btn.connect("toggled", self._on_search_toggled)
        self.header.pack_end(self.search_btn)
        self.annotate_btn = Gtk.ToggleButton(icon_name="document-edit-symbolic", action_name="win.annotate",
                                             tooltip_text=tr("Annoter"))
        self.header.pack_end(self.annotate_btn)
        self.tools_btn = Gtk.MenuButton(icon_name="applications-utilities-symbolic", tooltip_text=tr("Outils"),
                                        menu_model=self._tools_menu())
        self.header.pack_end(self.tools_btn)

        # contenu
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.welcome = Welcome(self)
        self.stack.add_named(self.welcome, "welcome")
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.stack.add_named(spinner, "loading")
        self.error_page = Adw.StatusPage(icon_name="dialog-error-symbolic")
        self.error_page.set_child(Gtk.Button(label=tr("Ouvrir…"), halign=Gtk.Align.CENTER, action_name="win.open",
                                             css_classes=["pill"]))
        self.stack.add_named(self.error_page, "error")

        self.view = DocView()
        self.view.connect("page-changed", self._on_page_changed)
        self.view.connect("zoom-changed", lambda *_: self._sync_zoom())
        self.view.connect("external-link", self._on_external_link)
        self.view.connect("selection-changed", lambda *_: self._sync_actions())
        self.view.connect("history-changed", lambda *_: self._sync_actions())
        self.view.connect("context-menu", self._on_context_menu)
        self.scroller = Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True)
        self.scroller.add_css_class("calypso-scroller")
        self.sidebar = Sidebar(self.view)
        self.search = Search(self)
        from .annotations import Annotator
        self.annotator = Annotator(self)
        self.view.annotator = self.annotator
        self.tools_bar = self._annot_bar()
        self.forms_banner = Adw.Banner(title=tr("Ce document contient un formulaire à remplir"),
                                       button_label=tr("Compris"))
        self.forms_banner.connect("button-clicked", lambda b: b.set_revealed(False))
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.forms_banner)
        content.append(self.search.bar)
        content.append(self.tools_bar)
        self.redact_bar = self._redact_bar()
        content.append(self.redact_bar)
        content.append(self.scroller)
        self.split = Adw.OverlaySplitView(sidebar=self.sidebar, content=content, min_sidebar_width=170,
                                          max_sidebar_width=260, sidebar_width_fraction=0.2,
                                          show_sidebar=settings["sidebar_visible"])
        self.split.connect("notify::show-sidebar", self._on_sidebar_shown)
        self.stack.add_named(self.split, "document")
        self.stack.set_visible_child_name("welcome")
        self.toolbar.set_content(self.stack)
        self.toast_overlay.set_child(self.toolbar)
        self.set_content(self.toast_overlay)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

    def _main_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        files = Gio.Menu()
        files.append(tr("Ouvrir…"), "win.open")
        files.append(tr("Nouvelle fenêtre"), "app.new-window")
        files.append(tr("Enregistrer"), "win.save")
        files.append(tr("Enregistrer sous…"), "win.save-as")
        files.append(tr("Afficher dans le dossier"), "win.show-folder")
        files.append(tr("Recharger"), "win.reload")
        menu.append_section(None, files)
        view = Gio.Menu()
        view.append(tr("Défilement continu"), "win.continuous")
        view.append(tr("Deux pages côte à côte"), "win.dual")
        view.append(tr("Première page seule"), "win.dual-cover")
        view.append(tr("Mode nuit"), "win.night")
        menu.append_section(None, view)
        turn = Gio.Menu()
        turn.append(tr("Pivoter à gauche"), "win.rotate-left")
        turn.append(tr("Pivoter à droite"), "win.rotate-right")
        turn.append(tr("Plein écran"), "win.fullscreen")
        menu.append_section(None, turn)
        misc = Gio.Menu()
        misc.append(tr("Propriétés du document"), "win.properties")
        misc.append(tr("Préférences"), "app.preferences")
        misc.append(tr("Raccourcis clavier"), "win.shortcuts")
        misc.append(tr("À propos de Calypso"), "app.about")
        menu.append_section(None, misc)
        return menu

    def _tools_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        pages = Gio.Menu()
        pages.append(tr("Organiser les pages…"), "win.organize")
        pages.append(tr("Fusionner avec d'autres PDF…"), "win.merge")
        menu.append_section(None, pages)
        change = Gio.Menu()
        change.append(tr("Compresser…"), "win.compress")
        change.append(tr("Filigrane…"), "win.watermark")
        change.append(tr("Caviarder…"), "win.redact")
        menu.append_section(None, change)
        lock = Gio.Menu()
        lock.append(tr("Protéger par un mot de passe…"), "win.protect")
        lock.append(tr("Retirer le mot de passe…"), "win.unprotect")
        menu.append_section(None, lock)
        return menu

    def _zoom_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        fit = Gio.Menu()
        fit.append(tr("Ajuster à la largeur"), "win.zoom-mode::fit-width")
        fit.append(tr("Ajuster à la page"), "win.zoom-mode::fit-page")
        menu.append_section(None, fit)
        steps = Gio.Menu()
        for z in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0):
            item = Gio.MenuItem.new(f"{int(z * 100)} %", None)
            item.set_action_and_target_value("win.zoom-set", GLib.Variant.new_double(z))
            steps.append_item(item)
        menu.append_section(None, steps)
        io = Gio.Menu()
        io.append(tr("Zoom avant"), "win.zoom-in")
        io.append(tr("Zoom arrière"), "win.zoom-out")
        menu.append_section(None, io)
        return menu

    # ======================================================================================
    # Actions
    # ======================================================================================
    def _add(self, name, cb, param: Optional[str] = None):
        a = Gio.SimpleAction.new(name, GLib.VariantType.new(param) if param else None)
        if param:
            a.connect("activate", lambda _a, v, cb=cb: cb(v.unpack()))
        else:
            a.connect("activate", lambda _a, _p, cb=cb: cb())
        self.add_action(a)
        return a

    def _add_toggle(self, name, initial: bool, cb):
        a = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(bool(initial)))

        def act(action, _v):
            value = not action.get_state().get_boolean()
            action.set_state(GLib.Variant.new_boolean(value))
            cb(value)
        a.connect("activate", act)
        self.add_action(a)
        return a

    def _actions(self):
        v = self.view
        self._add("open", self.choose_document)
        self._add("close", self.close)
        self._add("save", self.save)
        self._add("save-as", self.save_as)
        self._add("undo", lambda: self.annotator.undo())
        self._add("redo", lambda: self.annotator.redo())
        self._add_toggle("annotate", False, self._set_annotate)
        a = Gio.SimpleAction.new_stateful("tool", GLib.VariantType.new("s"), GLib.Variant.new_string("select"))
        a.connect("activate", lambda act, val: (act.set_state(val), self.view.set_tool(val.get_string()),
                                                self._sync_tool_buttons()))
        self.add_action(a)
        self._add("markup", lambda kind: self.annotator.markup(kind), "s")
        from . import tools as tool_ui
        self._add("organize", lambda: self._with_saved(self._organize))
        self._add("merge", lambda: self._with_saved(lambda: tool_ui.MergeDialog(self).present()))
        self._add("compress", lambda: self._with_saved(lambda: tool_ui.CompressDialog(self).present()))
        self._add("watermark", lambda: self._with_saved(lambda: tool_ui.WatermarkDialog(self).present()))
        self._add("protect", lambda: self._with_saved(lambda: tool_ui.ProtectDialog(self).present()))
        self._add("unprotect", lambda: self._with_saved(lambda: tool_ui.unprotect(self)))
        self._add("redact", lambda: self._with_saved(self._start_redact))
        self._add("edit-annot", lambda: self._ctx_annot() and self.annotator.edit(self._ctx_annot()))
        self._add("delete-annot", lambda: self._ctx_annot() and self.annotator.delete(self._ctx_annot()))
        self._add("show-folder", lambda: self.path and show_in_folder(self, self.path))
        self._add("reload", lambda: self.reload(force=True))
        self._add("properties", self.show_properties)
        self._add("find", self.open_search)
        self._add("find-next", lambda: (self._ensure_search(), self.search.next()))
        self._add("find-previous", lambda: (self._ensure_search(), self.search.previous()))
        self._add("copy", self.copy)
        self._add("select-all", v.select_all)
        self._add("zoom-in", lambda: v.zoom_in())
        self._add("zoom-out", lambda: v.zoom_out())
        self._add("zoom-reset", lambda: v.set_zoom(1.0))
        self._add("zoom-set", lambda z: v.set_zoom(z), "d")
        a = Gio.SimpleAction.new_stateful("zoom-mode", GLib.VariantType.new("s"), GLib.Variant.new_string("fit-width"))
        a.connect("activate", lambda _a, val: v.set_zoom_mode(val.get_string()))
        self.add_action(a)
        self._add("rotate-left", lambda: self._rotate(-90))
        self._add("rotate-right", lambda: self._rotate(90))
        self._add_toggle("continuous", v.continuous, lambda on: self._layout(continuous=on))
        self._add_toggle("dual", v.dual, lambda on: self._layout(dual=on))
        self._add_toggle("dual-cover", v.dual_cover, lambda on: self._layout(dual_cover=on))
        self._add_toggle("night", v.night, self._set_night)
        self._add_toggle("sidebar", settings["sidebar_visible"], self._set_sidebar)
        self._add("fullscreen", lambda: self.unfullscreen() if self.is_fullscreen() else self.fullscreen())
        self._add("go-back", v.go_back)
        self._add("go-forward", v.go_forward)
        self._add("first-page", v.first_page)
        self._add("last-page", v.last_page)
        self._add("go-page", self._focus_page_entry)
        self._add("shortcuts", self._shortcuts)
        self._add_toggle("search-case", False, lambda on: self.search.set_options(case=on))
        self._add_toggle("search-whole", False, lambda on: self.search.set_options(whole=on))
        # menus contextuels de la vue et de l'accueil
        self._add("open-link", self._ctx_open_link)
        self._add("copy-link", self._ctx_copy_link)
        self._add("save-image", self._ctx_save_image)
        self._add("copy-image", self._ctx_copy_image)
        self._add("open-path-new", lambda p: self.get_application().open_path(p, new_window=True), "s")
        self._add("show-path", lambda p: show_in_folder(self, p), "s")
        self._add("forget-recent", self._forget_recent, "s")
        # Échap : quitte le plein écran, puis ferme la recherche ; en capture, sinon la vue le prend
        # pour effacer sa sélection et le plein écran restait
        keys = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Escape"),
                                       action=Gtk.CallbackAction.new(self._on_escape)))
        self.add_controller(keys)
        for key, name in (("c", "continuous"), ("d", "dual")):
            v_keys = Gtk.ShortcutController()
            v_keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string(key),
                                             action=Gtk.NamedAction.new("win." + name)))
            self.view.add_controller(v_keys)

    def _set_state(self, name: str, value):
        a = self.lookup_action(name)
        if a is not None:
            a.set_state(GLib.Variant.new_boolean(bool(value)) if isinstance(value, bool)
                        else GLib.Variant.new_string(value))

    def _sync_actions(self):
        has = self.document is not None
        for name in ("save-as", "annotate", "organize", "merge", "compress", "watermark", "protect", "redact",
                     "show-folder", "reload", "properties", "find", "find-next", "find-previous",
                     "select-all", "zoom-in", "zoom-out", "zoom-reset", "zoom-set", "zoom-mode", "rotate-left",
                     "rotate-right", "continuous", "dual", "dual-cover", "night", "first-page", "last-page",
                     "go-page", "sidebar"):
            a = self.lookup_action(name)
            if a is not None:
                a.set_enabled(has)
        self.lookup_action("dual-cover").set_enabled(has and self.view.dual)
        self.lookup_action("save").set_enabled(has and self.document.dirty)
        self.lookup_action("undo").set_enabled(has and self.annotator.can_undo())
        self.lookup_action("redo").set_enabled(has and self.annotator.can_redo())
        self.annotate_btn.set_visible(has)
        self.tools_btn.set_visible(has)
        self.lookup_action("unprotect").set_enabled(has and bool(self.password))
        self.lookup_action("go-back").set_enabled(has and self.view.can_go_back())
        self.lookup_action("go-forward").set_enabled(has and self.view.can_go_forward())
        self.page_box.set_visible(has)
        self.zoom_btn.set_visible(has)
        self.search_btn.set_visible(has)
        self.sidebar_btn.set_visible(has)

    def _sync_view_states(self):
        v = self.view
        self._set_state("continuous", v.continuous)
        self._set_state("dual", v.dual)
        self._set_state("dual-cover", v.dual_cover)
        self._set_state("night", v.night)
        self._sync_zoom()

    # ======================================================================================
    # Ouverture
    # ======================================================================================
    def choose_document(self):
        initial = os.path.dirname(self.path) if self.path else os.path.expanduser("~")
        choose_files(self, tr("Ouvrir un document"), initial, self._open_chosen, multiple=True, filters=PDF_FILTERS)

    def _open_chosen(self, paths: list):
        app = self.get_application()
        for k, p in enumerate(paths):
            if k == 0 and self.document is None and not self.loading:
                self.open_document(p)
            else:
                app.open_path(p, new_window=self.document is not None or k > 0)

    def open_document(self, path: str, page: str = "", password: Optional[str] = None, state: Optional[dict] = None):
        self.save_state()
        self._open_gen += 1
        gen = self._open_gen
        self.loading = True
        GLib.timeout_add(250, lambda: (self.loading and gen == self._open_gen
                                       and self.stack.set_visible_child_name("loading"), False)[1])

        def work():
            try:
                doc = Document.open(path, password)
                result = ("ok", doc)
            except PasswordNeeded as e:
                result = ("password", e.wrong)
            except OpenError as e:
                result = ("error", str(e))
            except Exception as e:            # fichier étrange : jamais de fenêtre figée
                result = ("error", str(e))
            GLib.idle_add(self._opened, gen, path, page, password, state, result)
        threading.Thread(target=work, name="calypso-open", daemon=True).start()

    def _opened(self, gen, path, page, password, state, result):
        if gen != self._open_gen:
            return False
        kind, value = result
        if kind == "password":
            self.loading = False
            self._ask_password(path, page, value)
            return False
        self.loading = False
        if kind == "error":
            self._show_error(path, value)
            return False
        self._set_document(path, value, page, state)
        return False

    def _show_error(self, path, message):
        if self.document is None:
            self.error_page.set_title(tr("Impossible d'ouvrir le document"))
            self.error_page.set_description(GLib.markup_escape_text(
                shown(os.path.basename(path)) + "\n" + shown(_reason(message))))
            self.stack.set_visible_child_name("error")
        else:
            self.stack.set_visible_child_name("document")
            alert(self, tr("Impossible d'ouvrir le document"),
                  shown(os.path.basename(path)) + "\n" + shown(_reason(message)))

    def _ask_password(self, path, page, wrong):
        if self.document is None:
            self.stack.set_visible_child_name("welcome")
        group = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        entry = Adw.PasswordEntryRow(title=tr("Mot de passe"))
        group.append(entry)
        body = tr("« {0} » est protégé par un mot de passe.").format(shown(os.path.basename(path)))
        if wrong:
            body = tr("Mot de passe incorrect.") + " " + body
        d = AlertDialog(tr("Document protégé"), body)
        d.add_response("cancel", tr("Annuler"))
        d.add_response("unlock", tr("Déverrouiller"))
        d.set_response_appearance("unlock", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("unlock")
        d.set_close_response("cancel")
        d.set_extra_child(group)
        entry.connect("entry-activated", lambda *_: (d.response("unlock"), d.close()))

        def done(_d, r):
            if r == "unlock":
                self.open_document(path, page, entry.get_text())
        d.connect("response", done)
        d.present(self)
        GLib.idle_add(lambda: (entry.grab_focus(), False)[1])

    def _set_document(self, path: str, doc: Document, page: str = "", state: Optional[dict] = None):
        reloading = state is not None
        if not reloading and self.document is not None and self.path:
            self.save_state()
        self.path = path
        self.document = doc
        try:
            st = os.stat(path)
            self._stamp = (st.st_size, st.st_mtime_ns)
        except OSError:
            self._stamp = None
        if state is None:
            state = {"zoom_mode": settings["zoom_mode"], "continuous": settings["continuous"],
                     "dual": settings["dual"], "dual_cover": settings["dual_odd_left"], "night": settings["night"]}
            if features.enabled("resume"):
                state.update(history().state(path))
        sidebar_shown = state.get("sidebar")
        self.annotator.reset()
        self._discard_ok = False
        self.view.set_document(doc, state)
        self.sidebar.set_document(doc)
        self.forms_banner.set_revealed(False)
        GLib.idle_add(self._check_forms, doc)
        if sidebar_shown is not None and not reloading:
            self.split.set_show_sidebar(sidebar_shown)
        if self.search.bar.get_search_mode() and self.search.query:
            self.search.start(self.search.query)
        else:
            self.search.stop()
        name = shown(os.path.basename(path))
        self.title_widget.set_title(name)
        sub = doc.title if doc.title and doc.title.lower() != os.path.basename(path).lower() else ""
        self.title_widget.set_subtitle(shown(sub))
        self.set_title(name)
        self.document_changed()
        self.stack.set_visible_child_name("document")
        self._show_page_number()
        self._sync_view_states()
        self._sync_actions()
        self._watch(path)
        history().remember(path, self.view.get_state() | {"sidebar": self.split.get_show_sidebar()}, doc.title)
        if page:
            self.go_to_label(page)
        GLib.idle_add(lambda: (self.view.grab_focus(), False)[1])

    def _check_forms(self, doc):
        if doc is self.document and doc.has_fields():
            self.forms_banner.set_revealed(True)
        return False

    def go_to_label(self, label: str):
        if self.document is None:
            return
        i = self.document.find_label(label)
        if i >= 0:
            self.view.go_to(i, remember=True)

    def save_state(self):
        if self.document is not None and self.path:
            history().remember(self.path, self.view.get_state() | {"sidebar": self.split.get_show_sidebar()},
                               self.document.title)

    # -- rechargement --------------------------------------------------------------------------------
    def _watch(self, path: str):
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        try:
            self._monitor = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
        except GLib.Error:
            return
        self._monitor.connect("changed", self._on_file_changed)

    def _on_file_changed(self, _m, _f, _o, event):
        if event not in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                         Gio.FileMonitorEvent.CHANGED, Gio.FileMonitorEvent.ATTRIBUTE_CHANGED):
            return
        if not features.enabled("auto_reload"):
            return
        if self._reload_timer:
            GLib.source_remove(self._reload_timer)
        # écriture en plusieurs fois : on attend qu'elle soit finie
        self._reload_timer = GLib.timeout_add(700, self._reload_later)

    def _reload_later(self):
        self._reload_timer = 0
        self.reload()
        return False

    def reload(self, force: bool = False):
        if self.path is None or self.document is None:
            return
        try:
            st = os.stat(self.path)
        except OSError:
            return
        if not force and (self._stamp == (st.st_size, st.st_mtime_ns) or self.document.dirty
                          or self._saving):
            # modifications pas encore enregistrées : on ne les jette pas pour relire le fichier
            return
        state = self.view.get_state()
        self.open_document(self.path, password=self.document.password or None, state=state)

    # ======================================================================================
    # Pages, zoom, disposition
    # ======================================================================================
    def _on_page_changed(self, _v, _page):
        self._show_page_number()
        if self._save_timer:
            GLib.source_remove(self._save_timer)
        self._save_timer = GLib.timeout_add_seconds(3, self._save_later)

    def _save_later(self):
        self._save_timer = 0
        self.save_state()
        return False

    def _show_page_number(self):
        doc = self.document
        if doc is None:
            return
        i = self.view.page
        label = doc.page_label(i)
        if not self.page_entry.has_focus() or self.get_focus() is not self.page_entry.get_first_child():
            self.page_entry.set_text(shown(label))
        if label == str(i + 1):
            self.page_total.set_label(tr("sur {0}").format(doc.n_pages))
        else:
            self.page_total.set_label(tr("({0} sur {1})").format(i + 1, doc.n_pages))

    def _on_page_entry(self, entry):
        if self.document is None:
            return
        i = self.document.find_label(entry.get_text())
        if i < 0:
            entry.error_bell()
            self._show_page_number()
            return
        self.view.go_to(i, remember=True)
        self.view.grab_focus()

    def _focus_page_entry(self):
        self.page_entry.grab_focus()
        self.page_entry.select_region(0, -1)

    def _sync_zoom(self):
        self.zoom_btn.set_label(f"{round(self.view.zoom * 100)} %")
        a = self.lookup_action("zoom-mode")
        if a is not None:
            a.set_state(GLib.Variant.new_string(self.view.zoom_mode))
        for name, ok in (("zoom-in", self.view.zoom < MAX_ZOOM - 1e-6), ("zoom-out", self.view.zoom > MIN_ZOOM + 1e-6)):
            act = self.lookup_action(name)
            if act is not None:
                act.set_enabled(self.document is not None and ok)

    def _layout(self, **kw):
        self.view.set_layout(**kw)
        self._sync_actions()

    def _rotate(self, delta):
        self.view.rotate(delta)
        self.sidebar.rotation_changed()

    def _set_night(self, on):
        self.view.set_night(on)
        self.sidebar.queue_draw()
        for child in _descendants(self.sidebar):
            child.queue_draw()

    def _set_sidebar(self, on):
        self.split.set_show_sidebar(on)

    def _on_sidebar_shown(self, split, _p):
        on = split.get_show_sidebar()
        self._set_state("sidebar", on)
        if self.sidebar_btn.get_active() != on:
            self.sidebar_btn.set_active(on)
        if self.document is not None:
            settings["sidebar_visible"] = on

    # ======================================================================================
    # Recherche, copie
    # ======================================================================================
    def open_search(self):
        if self.document is None:
            return
        self.search.bar.set_search_mode(True)
        self.search_btn.set_active(True)
        self.search.entry.grab_focus()
        self.search.entry.select_region(0, -1)

    def _ensure_search(self):
        if not self.search.bar.get_search_mode():
            self.open_search()

    def close_search(self):
        self.search.bar.set_search_mode(False)
        self.search_btn.set_active(False)
        self.view.grab_focus()

    def _on_search_toggled(self, btn):
        on = btn.get_active()
        if on != self.search.bar.get_search_mode():
            if on:
                self.open_search()
            else:
                self.close_search()

    def copy(self):
        if not self.view.copy_selection():
            self.get_display().beep()

    # ======================================================================================
    # Liens, menu contextuel
    # ======================================================================================
    def _on_external_link(self, _v, link):
        if link.kind == "uri":
            uri = link.uri
            if "://" not in uri and not uri.startswith("mailto:"):
                uri = "http://" + uri
            Gtk.UriLauncher.new(uri).launch(self, None, _launched)
        elif link.kind == "file":
            path = link.uri
            if not os.path.exists(path):
                alert(self, tr("Fichier introuvable"), shown(path))
            elif path.lower().endswith(".pdf"):
                w = self.get_application().open_path(path)
                if link.page > 0 and w is not None:
                    GLib.timeout_add(400, lambda: (w.view.go_to(link.page), False)[1])
            else:
                open_with_default(self, path)

    def _on_context_menu(self, view, x, y, info):
        self._ctx = info
        menu = Gio.Menu()
        link = info.get("link")
        if link is not None:
            sec = Gio.Menu()
            if link.kind == "page":
                sec.append(tr("Aller à la page {0}").format(shown(self.document.page_label(link.page))),
                           "win.open-link")
            elif link.kind in ("uri", "file"):
                sec.append(tr("Ouvrir le lien"), "win.open-link")
                sec.append(tr("Copier l'adresse du lien"), "win.copy-link")
            menu.append_section(None, sec)
        if info.get("image") is not None:
            sec = Gio.Menu()
            sec.append(tr("Enregistrer l'image sous…"), "win.save-image")
            sec.append(tr("Copier l'image"), "win.copy-image")
            menu.append_section(None, sec)
        annot = info.get("annot")
        if annot is not None:
            self.view.select_annot(annot)
            sec = Gio.Menu()
            sec.append(tr("Modifier l'annotation…"), "win.edit-annot")
            sec.append(tr("Supprimer l'annotation"), "win.delete-annot")
            menu.append_section(None, sec)
        if self.view.has_selection():
            sec = Gio.Menu()
            for kind, label in (("highlight", tr("Surligner")), ("underline", tr("Souligner")),
                                ("strikeout", tr("Barrer"))):
                item = Gio.MenuItem.new(label, None)
                item.set_action_and_target_value("win.markup", GLib.Variant.new_string(kind))
                sec.append_item(item)
            menu.append_section(None, sec)
        sec = Gio.Menu()
        if self.view.has_selection():
            sec.append(tr("Copier"), "win.copy")
        sec.append(tr("Tout sélectionner"), "win.select-all")
        menu.append_section(None, sec)
        from .common import popup_menu
        popup_menu(self.view, menu, x, y)

    def _ctx_open_link(self):
        link = (self._ctx or {}).get("link")
        if link is not None:
            self.view.follow(link)

    def _ctx_copy_link(self):
        link = (self._ctx or {}).get("link")
        if link is not None and link.uri:
            self.get_clipboard().set(shown(link.uri))

    def _ctx_image(self):
        info = self._ctx or {}
        hit, image_id = info.get("hit"), info.get("image")
        if hit is None or image_id is None or self.document is None:
            return None
        try:
            return self.document.image_surface(hit[0], image_id)
        except Exception:
            return None

    def _ctx_save_image(self):
        surface = self._ctx_image()
        if surface is None:
            return
        base = os.path.splitext(os.path.basename(self.path or "image"))[0]
        suggestion = os.path.join(os.path.dirname(self.path or "") or os.path.expanduser("~"),
                                  tr("{0} - image page {1}.png").format(base, self._ctx["hit"][0] + 1))

        def save(dst):
            if not dst.lower().endswith(".png"):
                dst += ".png"
            try:
                tmp = dst + ".calypso-tmp"
                surface.write_to_png(tmp)
                os.replace(tmp, dst)
                toast(self, tr("Image enregistrée"))
            except Exception as e:
                alert(self, tr("Impossible d'enregistrer l'image"), shown(str(e)))
        choose_save(self, tr("Enregistrer l'image"), suggestion, save)

    def _ctx_copy_image(self):
        surface = self._ctx_image()
        if surface is None:
            return
        import io
        buf = io.BytesIO()
        surface.write_to_png(buf)
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue()))
        self.get_clipboard().set(texture)
        toast(self, tr("Image copiée"))

    def _forget_recent(self, path):
        entry = history().forget(path)
        self.welcome.refresh()

        def undo():
            history().put_back(path, entry)
            self.welcome.refresh()
        if entry is not None:
            toast(self, tr("« {0} » retiré de la liste").format(shown(os.path.basename(path))),
                  tr("Annuler"), undo)

    # ======================================================================================
    # Enregistrer une copie, propriétés
    # ======================================================================================
    def save(self, then=None):
        """Ctrl+S : annotations et champs écrits dans le fichier même (nom caché, mise sur disque,
        puis remplacement d'un coup). Fichier en lecture seule : « Enregistrer sous… »."""
        if self.document is None or self.path is None:
            return
        if not os.access(self.path, os.W_OK) or not os.access(os.path.dirname(os.path.abspath(self.path)), os.W_OK):
            self.save_as(then)
            return
        self._write(self.path, then)

    def save_as(self, then=None):
        if self.document is None:
            return

        def chosen(dst):
            if not dst.lower().endswith(".pdf"):
                dst += ".pdf"
            self._write(dst, then)
        choose_save(self, tr("Enregistrer sous"), self.path or "document.pdf", chosen)

    def _write(self, dst: str, then=None):
        doc = self.document
        self.view.close_editor(commit=True)
        self.annotator.close_popover(commit=True)
        self._saving = True

        def work():
            try:
                doc.save(dst)
                err = None
            except Exception as e:
                err = getattr(e, "strerror", None) or str(e)
            GLib.idle_add(done, err)

        def done(err):
            self._saving = False
            if doc is not self.document:
                return False
            if err:
                alert(self, tr("Impossible d'enregistrer le document"), shown(err))
                return False
            if os.path.abspath(dst) != os.path.abspath(self.path):
                # enregistré ailleurs : la fenêtre suit le nouveau fichier
                self.path = dst
                doc.path = dst
                doc.dirty = False
                self._watch(dst)
                self.title_widget.set_subtitle("")
            try:
                st = os.stat(dst)
                self._stamp = (st.st_size, st.st_mtime_ns)
            except OSError:
                pass
            self.document_changed()
            self.save_state()
            toast(self, tr("Document enregistré"))
            if then is not None:
                then()
            return False
        threading.Thread(target=work, name="calypso-save", daemon=True).start()

    # -- outils ------------------------------------------------------------------------------------
    @property
    def password(self) -> str:
        return self.document.password if self.document is not None else ""

    def _with_saved(self, then):
        """Les outils travaillent sur le FICHIER : annotations et champs d'abord enregistrés."""
        if self.document is None:
            return
        if not self.document.dirty:
            then()
            return
        d = AlertDialog(tr("Enregistrer d'abord ?"),
                        tr("Les outils travaillent sur le fichier : vos annotations et les champs remplis "
                           "doivent d'abord être enregistrés."))
        d.add_response("cancel", tr("Annuler"))
        d.add_response("save", tr("Enregistrer et continuer"))
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("save")
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r == "save" and self.save(then=then))
        d.present(self)

    def tool_result(self, path: str, detail: str = "", folder: str = ""):
        """Fichier créé par un outil : toast « Ouvrir »."""
        if not path:
            return
        name = shown(os.path.basename(path))
        text = tr("« {0} » créé").format(name) + (f" — {detail}" if detail else "")
        if folder:
            toast(self, text, tr("Ouvrir le dossier"), lambda: show_in_folder(self, path), timeout=8)
        else:
            toast(self, text, tr("Ouvrir"), lambda: self.get_application().open_path(path), timeout=8)

    def reopen_after_tool(self, password: str, message: str, backup: str = ""):
        """Le document a été remplacé par un outil : relu à la même page ; « Annuler » remet
        l'original gardé de côté (backup)."""
        state = self.view.get_state()
        self.open_document(self.path, password=password or None, state=state)
        if backup:
            path, old_pw = self.path, self.password

            def undo():
                from ..backend.pdftools import replace_into
                import shutil
                tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".calypso-undo")
                try:
                    shutil.copy2(backup, tmp)
                    replace_into(tmp, path)
                except OSError as e:
                    alert(self, tr("Impossible de rétablir l'original"), shown(e.strerror or str(e)))
                    return
                self.open_document(path, password=old_pw or None, state=self.view.get_state())
            toast(self, message, tr("Annuler"), undo, timeout=10)
        else:
            toast(self, message)

    def _organize(self):
        from .organizer import PageOrganizer
        PageOrganizer(self).present()

    # -- caviardage ---------------------------------------------------------------------------------
    def _redact_bar(self):
        bar = Gtk.Box(spacing=8, margin_start=8, margin_end=8, margin_top=4, margin_bottom=4)
        bar.append(Gtk.Image(icon_name="security-high-symbolic"))
        self.redact_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        bar.append(self.redact_label)
        clear = Gtk.Button(label=tr("Tout effacer"))
        clear.connect("clicked", lambda *_: self.view.clear_redactions())
        bar.append(clear)
        done = Gtk.Button(label=tr("Terminer"))
        done.connect("clicked", lambda *_: self._end_redact())
        bar.append(done)
        self.redact_apply = Gtk.Button(label=tr("Caviarder…"), css_classes=["destructive-action"])
        self.redact_apply.connect("clicked", lambda *_: self._apply_redact())
        bar.append(self.redact_apply)
        rev = Gtk.Revealer(child=bar, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        rev.add_css_class("toolbar")
        self.view.connect("redactions-changed", lambda *_: self._sync_redact())
        return rev

    def _start_redact(self):
        if self.lookup_action("annotate").get_state().get_boolean():
            self.activate_action("win.annotate", None)
        self.redact_bar.set_reveal_child(True)
        self.view.set_tool("redact")
        self._sync_redact()

    def _end_redact(self):
        self.redact_bar.set_reveal_child(False)
        self.view.clear_redactions()
        self.view.set_tool("select")

    def _sync_redact(self):
        n = sum(len(r) for r in self.view.redactions.values())
        self.redact_label.set_label(
            tr("Tracez les zones à masquer ; un clic dans une zone la retire.") if not n else
            ntr("{0} zone à masquer définitivement", "{0} zones à masquer définitivement", n).format(n))
        self.redact_apply.set_sensitive(n > 0)

    def _apply_redact(self):
        boxes = {p: list(r) for p, r in self.view.redactions.items() if r}
        if not boxes:
            return
        src, pw = self.path, self.password
        from ..backend import pdftools

        def chosen(dst):
            if not dst.lower().endswith(".pdf"):
                dst += ".pdf"
            self._end_redact()
            from .progress import run_job
            run_job(self, tr("Caviardage"), lambda j: pdftools.redact(j, src, boxes, dst, password=pw),
                    lambda job: self.tool_result(job.result, tr("pages touchées converties en images")))
        choose_save(self, tr("Enregistrer le document caviardé"), pdftools.suggest_output(src, tr("caviardé")),
                    chosen)

    def document_changed(self):
        """Annotation ou champ modifié (ou enregistré) : titre, actions, panneau."""
        doc = self.document
        if doc is None:
            return
        name = shown(os.path.basename(self.path))
        title = ("• " + name) if doc.dirty else name
        self.title_widget.set_title(title)
        self.set_title(title)
        self._sync_actions()

    # -- barre d'annotation -------------------------------------------------------------------------
    def _annot_bar(self):
        from .annotations import TOOLS, swatches
        bar = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        tools = Gtk.Box(css_classes=["linked"])
        self._tool_buttons = {}
        first = None
        for tid, icon, label in TOOLS:
            b = Gtk.ToggleButton(icon_name=icon, tooltip_text=label)
            if first is not None:
                b.set_group(first)
            first = first or b
            b.connect("toggled", lambda btn, t=tid: btn.get_active() and self.activate_action(
                "win.tool", GLib.Variant.new_string(t)))
            self._tool_buttons[tid] = b
            tools.append(b)
        self._tool_buttons["select"].set_active(True)
        bar.append(tools)
        self.color_btn = Gtk.MenuButton(icon_name="color-select-symbolic", tooltip_text=tr("Couleur"))
        pop = Gtk.Popover()
        self._swatch_holder = Gtk.Box(margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        pop.set_child(self._swatch_holder)

        def fill(*_):
            from .common import clear_box
            clear_box(self._swatch_holder)
            self._swatch_holder.append(swatches(self.annotator.color, lambda rgb: (self.annotator.set_color(rgb),
                                                                                   pop.popdown())))
        pop.connect("show", fill)
        self.color_btn.set_popover(pop)
        bar.append(self.color_btn)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        undo = Gtk.Box(css_classes=["linked"])
        undo.append(Gtk.Button(icon_name="edit-undo-symbolic", action_name="win.undo", tooltip_text=tr("Annuler (Ctrl+Z)")))
        undo.append(Gtk.Button(icon_name="edit-redo-symbolic", action_name="win.redo",
                               tooltip_text=tr("Rétablir (Ctrl+Maj+Z)")))
        bar.append(undo)
        bar.append(Gtk.Box(hexpand=True))
        save = Gtk.Button(label=tr("Enregistrer"), action_name="win.save", css_classes=["suggested-action"])
        bar.append(save)
        rev = Gtk.Revealer(child=bar, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        rev.add_css_class("toolbar")
        return rev

    def _set_annotate(self, on: bool):
        self.tools_bar.set_reveal_child(on)
        if not on:
            self.activate_action("win.tool", GLib.Variant.new_string("select"))

    def _sync_tool_buttons(self):
        b = self._tool_buttons.get(self.view.tool)
        if b is not None and not b.get_active():
            b.set_active(True)

    def _ctx_annot(self):
        return (self._ctx or {}).get("annot") or self.view.selected_annot

    def show_properties(self):
        if self.document is None:
            return
        from .properties import PropertiesWindow
        PropertiesWindow(self.document, self.path).present(self)

    def _shortcuts(self):
        from .shortcuts import shortcuts_window
        w = shortcuts_window()
        w.set_transient_for(self)
        w.present()

    # ======================================================================================
    # Clavier, glisser-déposer, plein écran, fermeture
    # ======================================================================================
    def _text_focus(self):
        w = self.get_focus()
        return w if isinstance(w, Gtk.Text) else None

    def _on_capture_key(self, _c, keyval, _code, state):
        if state & (Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        text = self._text_focus()
        if text is None:
            return False
        key = Gdk.keyval_name(keyval) or ""
        if len(key) == 1:
            key = key.lower()
        what = TEXT_KEYS.get((key, bool(state & Gdk.ModifierType.CONTROL_MASK),
                              bool(state & Gdk.ModifierType.SHIFT_MASK)))
        if what is None:
            return False
        if what[0] == "action":
            text.activate_action(what[1], None)
        else:
            text.emit("move-cursor", what[1], what[2], what[3])
        return True

    def _on_escape(self, *_):
        if self.is_fullscreen():
            self.unfullscreen()
            return True
        if self.search.bar.get_search_mode():
            self.close_search()
            return True
        return False

    def _on_drop(self, _t, value, _x, _y):
        files = value.get_files() if value is not None else []
        paths = [f.get_path() for f in files if f.get_path()]
        if not paths:
            return False
        self._open_chosen(paths)
        return True

    def _on_fullscreen(self, *_):
        full = self.is_fullscreen()
        self.toolbar.set_reveal_top_bars(not full)

    def _on_window_motion(self, _c, _x, y):
        if not self.is_fullscreen():
            return
        if y <= 6:
            self.toolbar.set_reveal_top_bars(True)
        elif y > self.header.get_height() + 30 and self.toolbar.get_reveal_top_bars():
            self.toolbar.set_reveal_top_bars(False)

    def _on_close_request(self, *_):
        if self.document is not None and self.document.dirty and not self._discard_ok:
            self._ask_save_before_close()
            return True
        self.save_state()
        if not self.is_maximized() and not self.is_fullscreen():
            settings["window_width"], settings["window_height"] = self.get_width(), self.get_height()
        self.teardown()
        return False

    def _ask_save_before_close(self):
        name = shown(os.path.basename(self.path or ""))
        d = AlertDialog(tr("Enregistrer les modifications de « {0} » ?").format(name),
                        tr("Les annotations et les champs remplis seront perdus si vous n'enregistrez pas."))
        d.add_response("cancel", tr("Annuler"))
        d.add_response("discard", tr("Ne pas enregistrer"))
        d.add_response("save", tr("Enregistrer"))
        d.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("save")
        d.set_close_response("cancel")

        def done(_d, r):
            if r == "discard":
                self._discard_ok = True
                self.close()
            elif r == "save":
                self.save(then=self.close)
        d.connect("response", done)
        d.present(self)

    def teardown(self):
        self._open_gen += 1
        for src in ("_reload_timer", "_save_timer"):
            if getattr(self, src):
                GLib.source_remove(getattr(self, src))
                setattr(self, src, 0)
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        self.search.stop()
        self.view.teardown()


def _launched(launcher, res):
    try:
        launcher.launch_finish(res)
    except GLib.Error:
        pass


def _reason(message: str) -> str:
    low = (message or "").lower()
    if "no such file" in low or "not found" in low or "couldn't open" in low and "no such" in low:
        return tr("Le fichier n'existe plus.")
    if "permission" in low:
        return tr("Accès refusé.")
    if "no pages" in low:
        return tr("Le document n'a aucune page.")
    return tr("Ce n'est pas un document PDF valide, ou il est abîmé.")


def _descendants(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _descendants(child)
        child = child.get_next_sibling()
