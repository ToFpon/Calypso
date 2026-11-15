# Calypso

**A PDF document viewer for GNOME, built with GTK4 / libadwaita.**
**Une visionneuse de documents PDF pour GNOME, en GTK4 / libadwaita.**

Calypso reads PDF documents like GNOME Papers does (it uses the same engine, Poppler), and will add
the PDF tools of [Odysseus](https://github.com/ToFpon/Odysseus): compress, merge, watermark, extract /
delete / rotate / split pages.

Calypso lit les documents PDF comme Papers (même moteur, Poppler), et y ajoutera les outils PDF
d'[Odysseus](https://github.com/ToFpon/Odysseus) : compresser, fusionner, filigrane, extraire /
supprimer / pivoter / éclater des pages.

> Work in progress — version 0.1 (reading). / En cours — version 0.1 (lecture).

## Try it / L'essayer

```sh
sudo apt install python3-gi python3-cairo gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-poppler-0.18 qpdf ghostscript
git clone https://github.com/ToFpon/Calypso.git ~/Calypso
~/Calypso/bin/calypso document.pdf
```

License: GPL-3.0.
