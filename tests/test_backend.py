"""Tests du moteur (sans interface) : lecture Poppler, texte, liens, recherche, sommaire, historique."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import samples  # noqa: E402
from calypso.backend.document import Document, OpenError, PasswordNeeded  # noqa: E402
from calypso.backend.history import History  # noqa: E402


class DocumentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="calypso-test-")
        cls.book = os.path.join(cls.tmp, "livre.pdf")
        samples.make_book(cls.book)
        cls.mixed = os.path.join(cls.tmp, "mélange.pdf")
        samples.make_mixed(cls.mixed)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_open_and_sizes(self):
        d = Document.open(self.book)
        self.assertEqual(d.n_pages, 12)
        self.assertEqual(d.size(0), (595, 842))
        self.assertEqual(d.size(0, 90), (842, 595))
        self.assertEqual(d.title, "Le chant de Calypso")
        m = Document.open(self.mixed)
        self.assertEqual([m.size(i) for i in range(3)], [(595, 842), (842, 595), (612, 792)])

    def test_render_white_and_text(self):
        d = Document.open(self.book)
        data, w, h, stride = d.render(0, 0.5, 0, (0, 0, 298, 421))
        self.assertEqual((w, h), (298, 421))
        self.assertEqual(data[:4], b"\xff\xff\xff\xff")               # fond blanc
        dark = sum(1 for k in range(0, len(data), 4) if data[k] < 128)
        self.assertGreater(dark, 100)                                  # du texte dessiné
        # tuile : une partie de la page seulement
        data2, w2, h2, _ = d.render(0, 2.0, 0, (100, 100, 64, 32))
        self.assertEqual((w2, h2), (64, 32))

    def test_render_rotated(self):
        d = Document.open(self.book)
        _data, w, h, _s = d.render(0, 0.25, 90, (0, 0, 211, 149))
        self.assertEqual((w, h), (211, 149))

    def test_text_layout_matches(self):
        d = Document.open(self.book)
        text, boxes = d.text(0)
        self.assertIn("Chapitre 1", text)
        self.assertEqual(len(text), len(boxes))
        x1, y1, x2, y2 = boxes[0]
        self.assertTrue(0 < y1 < 120, boxes[0])                        # origine en haut

    def test_selection(self):
        d = Document.open(self.book)
        rects, text = d.selection(0, 60, 60, 300, 100)
        self.assertIn("Chapitre", text)
        self.assertTrue(rects)
        _r, word = d.selection(0, 100, 80, 100, 80, "word")
        self.assertEqual(word.strip(), "Chapitre")

    def test_find(self):
        d = Document.open(self.book)
        found = sorted(d.find(0, "CHAPITRE"), key=lambda r: r[1])     # casse ignorée
        self.assertEqual(len(found), 2)                                # titre + lien « Aller au chapitre 2 »
        x, y, w, h = found[0]
        self.assertTrue(50 < y < 100, found[0])                        # origine en haut (titre à y=90)
        self.assertEqual(d.find(0, "CHAPITRE", case=True), [])
        self.assertEqual(len(d.find(0, "chapitre", case=True)), 1)
        self.assertTrue(d.find(0, "lointaine"))
        self.assertTrue(d.find(0, "LOINTAINE"))
        before, match, after = d.context(0, found[0])
        self.assertEqual(match, "Chapitre")

    def test_links(self):
        d = Document.open(self.book)
        links = d.links(0)
        kinds = sorted(link.kind for _r, link in links)
        self.assertEqual(kinds, ["page", "uri"])
        page_link = next(link for _r, link in links if link.kind == "page")
        self.assertEqual(page_link.page, 5)
        uri = next(link for _r, link in links if link.kind == "uri")
        self.assertEqual(uri.uri, "https://example.org/calypso")
        (x, y, w, h), _l = next((r, l) for r, l in links if l.kind == "page")
        self.assertTrue(740 < y + h / 2 < 770, (x, y, w, h))          # le texte est à y=760
        self.assertIs(d.link_at(0, x + 2, y + h / 2), page_link)

    def test_outline(self):
        d = Document.open(self.book)
        toc = d.outline()
        self.assertEqual([i.title for i in toc], ["Chapitre 1", "Chapitre 2", "Chapitre 3"])
        self.assertEqual([i.link.page for i in toc], [0, 4, 8])
        self.assertEqual(toc[0].children[0].title, "Section 1.2")
        self.assertEqual(toc[0].children[0].link.page, 2)

    def test_images(self):
        d = Document.open(self.book)
        imgs = d.images(1)
        self.assertEqual(len(imgs), 1)
        (x, y, w, h), image_id = imgs[0]
        surface = d.image_surface(1, image_id)
        self.assertEqual((surface.get_width(), surface.get_height()), (200, 120))
        self.assertEqual(d.image_at(1, x + 5, y + 5), image_id)

    def test_labels(self):
        d = Document.open(self.book)
        self.assertEqual(d.find_label("3"), 2)
        self.assertEqual(d.find_label("99"), -1)
        self.assertEqual(d.find_label(""), -1)

    def test_password(self):
        enc = os.path.join(self.tmp, "secret.pdf")
        samples.make_encrypted(self.book, enc, "sésame")
        with self.assertRaises(PasswordNeeded) as e:
            Document.open(enc)
        self.assertFalse(e.exception.wrong)
        with self.assertRaises(PasswordNeeded) as e:
            Document.open(enc, "faux")
        self.assertTrue(e.exception.wrong)
        self.assertEqual(Document.open(enc, "sésame").n_pages, 12)

    def test_bad_files(self):
        bad = os.path.join(self.tmp, "pas un pdf.pdf")
        with open(bad, "w") as fh:
            fh.write("bonjour")
        with self.assertRaises(OpenError):
            Document.open(bad)
        with self.assertRaises(OpenError):
            Document.open(os.path.join(self.tmp, "absent.pdf"))

    def test_non_utf8_name(self):
        name = os.path.join(self.tmp.encode(), b"caf\xe9.pdf")
        shutil.copy(self.book, name)
        d = Document.open(os.fsdecode(name))
        self.assertEqual(d.n_pages, 12)

    def test_info(self):
        info = Document.open(self.book).info()
        self.assertEqual(info["author"], "Homère")
        self.assertEqual(info["pages"], 12)


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


class AnnotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="calypso-annot-")
        self.book = os.path.join(self.tmp, "livre.pdf")
        samples.make_book(self.book, pages=3)
        self.form = os.path.join(self.tmp, "formulaire.pdf")
        samples.make_form(self.form)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_add_save_reopen(self):
        d = Document.open(self.book)
        rects, _t = d.selection(0, 60, 70, 300, 100)
        d.add_markup(0, "highlight", rects, (1, 0.9, 0), "Tof")
        d.add_markup(0, "strikeout", rects, (1, 0, 0), "Tof")
        d.add_note(0, 400, 100, "Relire ce passage", (1, 0.8, 0), "Tof")
        d.add_shape(1, "circle", (100, 100, 80, 40), (0, 0, 1), "Tof")
        self.assertTrue(d.dirty)
        kinds = [a.kind for a in d.annots(0)]
        self.assertEqual(kinds, ["highlight", "strikeout", "note"])      # liens cachés
        note = d.annots(0)[2]
        x, y, w, h = note.rect
        self.assertTrue(abs(x - 400) < 1 and abs(y - 100) < 1, note.rect)   # origine en haut
        self.assertIs(d.annot_at(0, x + 5, y + 5).annot, note.annot)
        out = os.path.join(self.tmp, "copie.pdf")
        d.save(out)
        self.assertTrue(d.dirty)                                         # copie : l'original n'est pas enregistré
        d2 = Document.open(out)
        a = d2.annots(0)
        self.assertEqual([i.kind for i in a], ["highlight", "strikeout", "note"])
        self.assertEqual(a[2].contents, "Relire ce passage")
        self.assertEqual(a[0].author, "Tof")
        self.assertEqual([i.kind for i in d2.annots(1)], ["circle"])
        self.assertFalse(d2.dirty)

    def test_markup_is_drawn(self):
        """Les coins des surlignages sont bien écrits (premier essai : QuadPoints tout à 0, rien de
        dessiné ni d'enregistré — q.p1 de PyGObject est une copie)."""
        d = Document.open(self.book)
        d.add_markup(0, "highlight", [(64, 133, 431, 15)], (0.98, 0.85, 0.18), "Tof")
        d.add_markup(0, "underline", [(64, 165, 336, 15)], (0.4, 0.7, 1.0), "Tof")
        data, w, h, st = d.render(0, 1.0, 0, (0, 0, 595, 842))

        def count(y0, y1, cond):
            return sum(1 for y in range(y0, y1) for x in range(60, 480)
                       if cond(data[y * st + x * 4 + 2], data[y * st + x * 4 + 1], data[y * st + x * 4]))
        self.assertGreater(count(130, 150, lambda r, g, b: r > 200 and g > 180 and b < 120), 1000)
        self.assertGreater(count(160, 185, lambda r, g, b: b > 200 and r < 150), 100)
        out = os.path.join(self.tmp, "o.pdf")
        d.save(out)
        data2, _w, _h, st2 = Document.open(out).render(0, 1.0, 0, (0, 0, 595, 842))
        self.assertEqual(data2[140 * st2 + 300 * 4: 140 * st2 + 300 * 4 + 3], data[140 * st + 300 * 4: 140 * st + 300 * 4 + 3])

    def test_remove_restore_props(self):
        d = Document.open(self.book)
        a = d.add_note(0, 100, 100, "un", (1, 1, 0))
        d.remove_annot(0, a)
        self.assertEqual(d.annots(0), [])
        d.restore_annot(0, a)
        self.assertEqual(len(d.annots(0)), 1)
        d.set_annot_props(0, a, {"contents": "deux", "color": (0, 1, 0)})
        props = d.annot_props(a)
        self.assertEqual(props["contents"], "deux")
        self.assertAlmostEqual(props["color"][1], 1.0, places=3)

    def test_save_in_place_keeps_mode(self):
        os.chmod(self.book, 0o640)
        d = Document.open(self.book)
        d.add_note(0, 100, 100, "x", (1, 1, 0))
        d.save(self.book)
        self.assertFalse(d.dirty)
        self.assertEqual(os.stat(self.book).st_mode & 0o777, 0o640)
        self.assertEqual(len(Document.open(self.book).annots(0)), 1)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.startswith(".")], [])   # pas de reste caché
        # deuxième enregistrement depuis le même document ouvert : tout y est encore
        d.add_note(1, 100, 100, "y", (1, 1, 0))
        d.save(self.book)
        d2 = Document.open(self.book)
        self.assertEqual((len(d2.annots(0)), len(d2.annots(1))), (1, 1))

    def test_save_failure_keeps_original(self):
        d = Document.open(self.book)
        d.add_note(0, 100, 100, "x", (1, 1, 0))
        before = _read(self.book)
        with self.assertRaises(OSError):
            d.save(os.path.join(self.tmp, "absent", "x.pdf"))
        self.assertEqual(_read(self.book), before)

    def test_encrypted_save(self):
        enc = os.path.join(self.tmp, "secret.pdf")
        samples.make_encrypted(self.book, enc, "sésame")
        d = Document.open(enc, "sésame")
        d.add_note(0, 100, 100, "secret", (1, 1, 0))
        d.save(enc)
        with self.assertRaises(PasswordNeeded):
            Document.open(enc)                                           # toujours protégé
        self.assertEqual(Document.open(enc, "sésame").annots(0)[0].contents, "secret")

    def test_forms(self):
        d = Document.open(self.form)
        fields = d.fields(0)
        kinds = sorted((f.name, f.kind) for f in fields)
        self.assertEqual(kinds, [("accepte", "check"), ("nom", "text"), ("pays", "combo"),
                                 ("remarques", "multiline"), ("taille", "radio"), ("taille", "radio")])
        self.assertTrue(d.has_fields())
        self.assertFalse(Document.open(self.book).has_fields())
        get = {(f.name, f.rect[0]): f for f in fields}
        nom = next(f for f in fields if f.name == "nom")
        x, y, w, h = nom.rect
        self.assertTrue(abs(y - (842 - 760)) < 1, nom.rect)
        self.assertIs(d.field_at(0, x + 3, y + 3).field, nom.field)
        d.set_field_value(nom, "Tof Été")
        d.set_field_value(next(f for f in fields if f.name == "accepte"), True)
        d.set_field_value(get[("taille", 200.0)], True)
        pays = next(f for f in fields if f.name == "pays")
        self.assertEqual(d.choice_items(pays), ["France", "Belgique", "Suisse"])
        self.assertEqual(d.field_value(pays), ((0,), ""))
        d.set_field_value(pays, ((2,), ""))
        self.assertTrue(d.dirty)
        out = os.path.join(self.tmp, "rempli.pdf")
        d.save(out)
        d2 = Document.open(out)
        vals = {(f.name, f.rect[0]): d2.field_value(f) for f in d2.fields(0)}
        self.assertEqual(vals[("nom", 150.0)], "Tof Été")
        self.assertEqual(vals[("accepte", 150.0)], True)
        self.assertEqual((vals[("taille", 150.0)], vals[("taille", 200.0)]), (False, True))
        self.assertEqual(vals[("pays", 150.0)], ((2,), ""))


class FormRenderTest(unittest.TestCase):
    """Poppler 24.02 + NeedAppearances : une case redessinée après avoir été cochée s'enregistrait
    décochée (apparence à un seul état). backend/formfix.py remet les apparences d'origine."""

    def test_checked_then_rendered_then_saved(self):
        tmp = tempfile.mkdtemp(prefix="calypso-form-")
        try:
            p = os.path.join(tmp, "f.pdf")
            samples.make_form(p)
            d = Document.open(p)
            get = lambda n, x=None: next(f for f in d.fields(0) if f.name == n and (x is None or f.rect[0] == x))
            d.set_field_value(get("accepte"), True)
            d.set_field_value(get("taille", 200.0), True)
            d.render(0, 1.0, 0, (0, 0, 200, 200))                  # la vue redessine la page
            d.save(p)
            d.render(0, 1.0, 0, (0, 0, 200, 200))
            d.save(p)                                               # deuxième enregistrement
            d2 = Document.open(p)
            vals = {(f.name, f.rect[0]): d2.field_value(f) for f in d2.fields(0) if f.kind in ("check", "radio")}
            self.assertEqual(vals, {("accepte", 150.0): True, ("taille", 150.0): False, ("taille", 200.0): True})
            d2.set_field_value(next(f for f in d2.fields(0) if f.name == "accepte"), False)
            d2.render(0, 1.0, 0, (0, 0, 200, 200))
            d2.save(p)
            d3 = Document.open(p)
            self.assertFalse(d3.field_value(next(f for f in d3.fields(0) if f.name == "accepte")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_restore_after_remove_is_saved(self):
        tmp = tempfile.mkdtemp(prefix="calypso-form-")
        try:
            p = os.path.join(tmp, "b.pdf")
            samples.make_book(p, pages=2)
            d = Document.open(p)
            rects, _t = d.selection(0, 60, 70, 300, 100)
            a = d.add_markup(0, "highlight", rects, (1, 1, 0), "Tof")
            d.remove_annot(0, a)
            a = d.restore_annot(0, a)                               # annuler la suppression
            c = d.add_shape(1, "square", (50, 50, 30, 30), (1, 0, 0))
            d.remove_annot(1, c)
            d.restore_annot(1, c)
            d.save(p)
            d2 = Document.open(p)
            self.assertEqual([x.kind for x in d2.annots(0)], ["highlight"])
            self.assertEqual(d2.annots(0)[0].author, "Tof")
            self.assertEqual([x.kind for x in d2.annots(1)], ["square"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="calypso-hist-")
        self.file = os.path.join(self.tmp, "history.json")
        self.doc = os.path.join(self.tmp, "a.pdf")
        open(self.doc, "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_state_roundtrip(self):
        h = History(self.file)
        h.remember(self.doc, {"page": 4, "zoom_mode": "free", "scale": 1.5, "night": True, "junk": 1})
        h2 = History(self.file)
        st = h2.state(self.doc)
        self.assertEqual(st, {"page": 4, "zoom_mode": "free", "scale": 1.5, "night": True})
        self.assertEqual([p for p, _e in h2.recent()], [os.path.realpath(self.doc)])

    def test_bad_types_ignored(self):
        h = History(self.file)
        h.entries[os.path.realpath(self.doc)] = {"page": "trois", "night": 1, "scale": 2}
        st = h.state(self.doc)
        self.assertEqual(st, {"scale": 2.0})

    def test_broken_file(self):
        with open(self.file, "w") as fh:
            fh.write("[1, 2")
        self.assertEqual(History(self.file).entries, {})

    def test_missing_files_not_recent(self):
        h = History(self.file)
        h.remember(self.doc, {"page": 1})
        os.unlink(self.doc)
        self.assertEqual(h.recent(), [])

    def test_limit(self):
        h = History(self.file)
        for k in range(320):
            h.entries[f"/x/{k}.pdf"] = {"time": k}
        h.remember(self.doc, {"page": 0})
        self.assertLessEqual(len(h.entries), 300)
        self.assertIn(os.path.realpath(self.doc), h.entries)


if __name__ == "__main__":
    unittest.main()
