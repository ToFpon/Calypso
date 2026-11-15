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
