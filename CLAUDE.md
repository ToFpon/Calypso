# Calypso — notes de travail

Visionneuse de documents PDF GTK4 / libadwaita en Python sur Poppler (la bibliothèque de Papers et
d'Evince) : tout ce que fait Papers, plus les outils PDF d'Odysseus (compresser, fusionner, filigrane,
pages). Ossature reprise de Nausicaa (`ToFpon/Nausicaa`, elle-même reprise d'Ithaca et d'Odysseus) :
i18n, réglages, fonctionnalités, vrais dialogues, tests, paquet.

## Règles

- Répondre à Tof en français, tutoiement, avis francs. Messages de commit en français.
- Cible : Zorin OS 18.1 (Ubuntu 24.04, GNOME 46, X11, GTK 4.14, libadwaita 1.5, Poppler 24.02 —
  gir1.2-poppler-0.18 —, Python 3.12). Pas d'API plus récente (pas d'Adw.Dialog, Adw.AlertDialog…).
  Paquets apt seulement.
- Thème GTK du système : `data/base.css` ne fait que de la mise en page. Seule couleur fixe : le blanc
  du papier (et son ombre), inversé en mode nuit.
- Dialogues : de vraies fenêtres (`common.AlertDialog`, `common.DialogWindow`, `Adw.PreferencesWindow`,
  `Adw.AboutWindow`).
- Chaque fonctionnalité facultative est dans `calypso/features.py` (resume, link_preview, auto_reload) et
  se consulte au moment de l'usage.
- Tout texte visible passe par `tr()` / `ntr()`. Après modification : `python3.12 tools/update-pot.py`,
  compléter `data/locale/en.po` et `de.po`, puis `msgfmt -c --statistics` et `msgcmp`.
- Toute fonction passée à `idle_add` / `timeout_add` renvoie False (sauf répétition voulue).
- Branche de travail `claude/kind-davinci-j4ke1f`, main avancé quand Tof valide.

## Feuille de route (plan validé par Tof)

1. Ossature et lecture — FAIT (0.1.0) : fenêtre, ouverture, défilement continu / page par page, page
   double (première page seule en option), zoom (ajuster largeur / page, libre, Ctrl+molette, pincement),
   rotation, mode nuit, plein écran, miniatures, sommaire, recherche (résultats surlignés + liste), sélection
   et copie du texte, liens (internes avec historique, Web, autres fichiers), aperçu des liens au survol,
   clic droit (copier, lien, enregistrer / copier une image), mot de passe, rechargement automatique,
   reprise (page, zoom, disposition par document), accueil avec documents récents, propriétés.
2. Annotations (notes, surligner / souligner / barrer, texte libre, encre par pypdf : Poppler 24.02 n'a
   pas AnnotInk), formulaires, enregistrement.
3. Outils d'Odysseus (`odysseus/backend/pdf.py` : compresser, fusionner, filigrane, extraire / supprimer /
   pivoter / éclater des pages) dans un menu « Outils » ; protéger par mot de passe (qpdf) avec le
   générateur et la jauge de Pressoir ; réorganiser les pages en glissant les miniatures ; caviarder.
4. Présentation (F5), impression, signatures (vérifier, signer), mode curseur (F7), puis paquet (.deb,
   PPA, site) comme Nausicaa. Plus tard peut-être : DjVu, TIFF, BD (CBZ / CBR) comme Papers.

## Architecture

- `backend/document.py` : `Document` = Poppler.Document + `lock` (Poppler n'accepte pas deux appels
  simultanés sur un même document : rendu, recherche et fil principal passent tous par lui). Coordonnées :
  points de la page SANS rotation, origine en HAUT à gauche. Poppler donne liens, images et résultats de
  recherche avec l'origine en BAS (`_rect_top_left`) ; texte (`get_text_layout`) et sélection
  (`get_selected_region`) sont déjà en haut. `render(page, échelle, rotation, tuile)` : fond blanc, cairo
  ARGB32. `selection()` = celle de Poppler entre deux points (ordre de lecture, mots, lignes : comme
  Evince). `IndexIter.new` lève TypeError sans sommaire (constructeur NULL). `PasswordNeeded(wrong)`.
- `backend/render.py` : un fil de rendu par document, canaux « view » (pages / tuiles visibles), « preview »
  (aperçus 0,3 Mpx montrés étirés pendant un défilement rapide ou un zoom), « thumbs » (miniatures) ;
  `want()` REMPLACE la file d'un canal (ce qui n'est plus visible n'est jamais dessiné). Textures
  `Gdk.MemoryTexture` B8G8R8A8_PREMULTIPLIED (= ARGB32 de cairo en petit-boutiste).
- `ui/view.py` : `DocView(Gtk.Widget, Gtk.Scrollable)` dans un Gtk.ScrolledWindow (propriétés
  hadjustment / vadjustment / *-scroll-policy en GObject.Property ; un `__gtype_name__` d'au moins 3
  lettres, sinon « could not create new GType »). Ne dessine que le visible (`do_snapshot`). Page entière
  jusqu'à 4 Mpx, au-delà tuiles de 1024 px (seulement les visibles). Cache 320 Mo (LRU, les tuiles
  visibles restent), 120 aperçus. 100 % = 96 ppp (`PX_PER_PT`). Zoom à la molette : aperçus d'abord, rendu
  net 150 ms après le dernier cran (`_settle_zoom`). Point d'ancrage (`_capture_anchor` / `_apply_anchor`)
  : le zoom garde le point sous le pointeur, un redimensionnement garde le haut de la page. Mode non
  continu : seule la ligne courante est mise en page, Page↓ / molette en bas = page suivante. Historique
  (Alt+← / →) : liens, sommaire, numéro tapé, miniatures. Mode nuit = matrice de couleur inversée sur le
  papier et la texture (`INVERT`). Sélection : GestureClick (1 / 2 / 3 clics = glyphes / mot / ligne) +
  GestureDrag (défilement auto près des bords) ; clic sans glisser sur un lien = lien suivi.
- `ui/window.py` : une fenêtre par document comme Papers (`app.open_path` : déjà ouvert → sa fenêtre ;
  sinon la fenêtre vide active, sinon une nouvelle). Ouverture dans un fil (`_open_gen` : la plus récente
  gagne). Raccourcis : GTK 4 traite ceux de l'application AVANT le widget qui a le focus → `TEXT_KEYS` en
  capture rend Ctrl+A / C / X / V / Z, Ctrl+← / → et Ctrl+Début / Fin au champ de saisie qui a le focus
  (sinon Ctrl+A dans la recherche sélectionnait tout le document, Ctrl+← pivotait la page). Échap en
  CAPTURE (plein écran, puis recherche) : sinon la vue le prenait pour effacer sa sélection. Rechargement :
  Gio.FileMonitor, 700 ms après le dernier événement, seulement si taille / date ont changé.
- `ui/sidebar.py` : miniatures (Gtk.ListView, rendues à la demande des cellules liées), sommaire
  (Gtk.TreeListModel, entrées ouvertes comme dans le document), résultats de recherche (triés par page).
- `ui/search.py` : recherche dans un fil depuis la page affichée, résultats page par page, génération pour
  abandonner l'ancienne ; accents et casse ignorés (IGNORE_DIACRITICS), mots coupés en fin de ligne trouvés
  (MULTILINE) ; options casse / mots entiers.
- `backend/history.py` : récents + état par document (chemin réel), 300 au plus, valeurs du bon type
  seulement. `backend/thumbs.py` : vignettes des récents (PNG en cache, clé = chemin + taille + date).

## Tests

Le `python3` du bac à sable n'a pas `gi` : utiliser `python3.12`.

```sh
python3.12 -m unittest discover -s tests            # moteur (vrai Poppler, PDF faits par cairo)
xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tests/ui_smoke.py   # doit finir par « Aucune erreur. »
LANGUAGE=en … ; LANGUAGE=de …
xvfb-run -a -s "-screen 0 1400x1000x24" python3.12 tests/ui_mouse.py   # vrais gestes (xdotool, openbox)
python3 -m pyflakes calypso tests tools
```

Documents d'essai : `tests/samples.py` (livre de 12 pages avec sommaire, liens, image ; pages de tailles
différentes ; chiffré par qpdf). Captures : `xvfb-run -a -s "-screen 0 1400x1000x24" python3.12
tools/screenshot.py SORTIE.png [--dark] [--sidebar=outline] [--search=TEXTE] [--welcome] …`.
