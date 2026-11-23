"""Audit de novembre 2026 (repris de celui d'Odysseus) : « % » pour Ghostscript, liens symboliques.
Chaque cas échouait sur l'ancien code."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import samples  # noqa: E402
from calypso.backend import pdftools as T  # noqa: E402
from calypso.backend.document import Document  # noqa: E402
from calypso.backend.jobs import Job  # noqa: E402


def job():
    return Job("t", lambda j: None)


class AuditTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    @unittest.skipUnless(T.gs() and T.qpdf(), "ghostscript / qpdf")
    def test_ghostscript_percent_in_folder(self):
        """Dossier « TVA 20 % » : la compression échouait (« Unable to open the initial device ») et le filigrane
        aplati était annoncé réussi avec un PDF vide."""
        for name in ("TVA 20 %", "Taux %d"):
            folder = os.path.join(self.d, name)
            os.makedirs(folder)
            src = os.path.join(folder, "doc.pdf")
            samples.make_book(src, 3)
            r = T.compress(job(), src, os.path.join(folder, "doc (c).pdf"), level="/screen", grayscale=True)
            self.assertEqual(T.page_count(r.dst), 3)
            out = T.watermark(job(), src, os.path.join(folder, "doc (f).pdf"),
                              T.WatermarkOptions(text="X", flatten=True))
            self.assertEqual(T.page_count(out), 3)
            self.assertEqual(sorted(os.listdir(folder)), ["doc (c).pdf", "doc (f).pdf", "doc.pdf"])

    @unittest.skipUnless(T.qpdf(), "qpdf")
    def test_replace_through_symlink_keeps_link(self):
        """Organiser sur place / Ctrl+S d'un PDF ouvert par un lien symbolique : le lien devenait un fichier et le
        vrai PDF restait tel quel."""
        os.makedirs(os.path.join(self.d, "vrai"))
        real = os.path.join(self.d, "vrai", "r.pdf")
        samples.make_book(real, 3)
        link = os.path.join(self.d, "l.pdf")
        os.symlink(real, link)
        T.arrange(job(), link, [(2, 0), (1, 0), (0, 0)], link, replace=True)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(sorted(os.listdir(os.path.join(self.d, "vrai"))), ["r.pdf"])
        before = os.path.getmtime(real)
        doc = Document.open(link)
        doc.save(link)
        self.assertTrue(os.path.islink(link))
        self.assertGreaterEqual(os.path.getmtime(real), before)
        self.assertEqual(sorted(os.listdir(os.path.join(self.d, "vrai"))), ["r.pdf"])


if __name__ == "__main__":
    unittest.main()
