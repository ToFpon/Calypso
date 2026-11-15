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
- Travail directement sur main (accord de Tof, après validation de la 0.1).

## Feuille de route (plan validé par Tof)

1. Ossature et lecture — FAIT (0.1.0) : fenêtre, ouverture, défilement continu / page par page, page
   double (première page seule en option), zoom (ajuster largeur / page, libre, Ctrl+molette, pincement),
   rotation, mode nuit, plein écran, miniatures, sommaire, recherche (résultats surlignés + liste), sélection
   et copie du texte, liens (internes avec historique, Web, autres fichiers), aperçu des liens au survol,
   clic droit (copier, lien, enregistrer / copier une image), mot de passe, rechargement automatique,
   reprise (page, zoom, disposition par document), accueil avec documents récents, propriétés.
2. Annotations, formulaires, enregistrement — FAIT (0.2.0) : notes, surligner / souligner / barrer /
   onduler, rectangle, ellipse ; bulle (commentaire, couleur, supprimer), panneau « Annotations », auteur
   (Préférences), Ctrl+Z / Ctrl+Maj+Z ; formulaires (texte, multiligne, cases, radios, listes) ; Ctrl+S
   (dans le fichier même), Ctrl+Maj+S, question « Enregistrer les modifications ? » à la fermeture.
   Reste pour plus tard : texte libre et dessin à main levée (Poppler 24.02 ne sait pas les CRÉER :
   pas d'AnnotFreeText.new ni d'AnnotInk → pypdf, qui a FreeText / PolyLine).
3. Outils — FAIT (0.3.0), menu « Outils » de l'en-tête : organiser les pages (glisser, pivoter, dupliquer,
   supprimer, extraire, éclater ; « Remplacer le document » avec « Annuler »), fusionner, compresser,
   filigrane (aperçu), caviarder, protéger par un mot de passe (générateur + jauge de Pressoir), retirer
   le mot de passe.
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
- Accueil (`ui/welcome.py`) : vignettes des récents ; petite croix au survol (idée de Tof, aussi au focus
  clavier) = « Retirer de la liste », toast « Annuler » (`History.forget` renvoie l'entrée, `put_back`).
  Vignette = Gtk.Overlay (bouton + croix) : pas de bouton dans un bouton.
- `backend/history.py` : récents + état par document (chemin réel), 300 au plus, valeurs du bon type
  seulement. `backend/thumbs.py` : vignettes des récents (PNG en cache, clé = chemin + taille + date).

- Annotations / formulaires (`backend/document.py` `_AnnotsMixin`, `ui/annotations.py` `Annotator`) :
  l'Annotator est le seul à modifier le document ; chaque changement → pile d'annulation (opérations
  en LISTES : une annotation remise est une copie neuve qui remplace l'ancienne), `view.invalidate_page`
  (anciennes tuiles gardées jusqu'aux nouvelles : pas de passage par le flou), `window.document_changed`
  (« • » dans le titre). `Document.dirty`. Outils de la vue (`DocView.tool`) : select (clic sur un champ
  → `activate_field`, sur une annotation → bulle), note (clic), surligner… (glisser = sélection, puis
  annotation à la fin du glisser — `_on_drag_end` : GestureClick n'émet pas « released » après un
  glisser), rectangle / ellipse (tracé). Champ texte en saisie = widget ENFANT de la vue (`open_editor`,
  placé dans `do_size_allocate`, `snapshot_child` ; un size_allocate fait à part n'était pas dessiné),
  fond `@view_bg_color` (`.calypso-field`, sinon transparent).
- Pièges de Poppler 24.02 (glib, par PyGObject), tous contournés et testés :
  - `Quadrilateral.p1` renvoie une COPIE : poser des `Poppler.Point` neufs (`_quad`) — sinon QuadPoints
    à 0, surlignage ni dessiné ni enregistré.
  - `get_quadrilaterals` : « free(): invalid pointer » → jamais appelé ; nos quadrilatères sont gardés
    (`_quads`), sinon le rectangle sert de quadrilatère.
  - Une annotation retirée puis RAJOUTÉE telle quelle n'est pas écrite à l'enregistrement →
    `restore_annot` pose une copie (`_clone`).
  - Chaque lecture des annotations d'une page donne de NOUVEAUX objets Python sans nom (/NM) →
    `find_annot` compare type + rectangle.
  - Formulaire en NeedAppearances : une case cochée puis REDESSINÉE s'enregistre avec une apparence à
    un seul état (relue décochée) → `backend/formfix.repair` remet, après l'écriture, les /AP à états du
    fichier d'origine (pypdf ; sans pypdf ou document chiffré : rien).
  - AnnotFreeText / AnnotInk : pas de constructeur.
- GTK 4.14 : une bulle (Gtk.Popover) DÉTRUITE juste après sa fermeture fait planter la fenêtre
  (`gtk_window_native_layout` → `gdk_surface_request_motion(NULL)`, vu avec Échap dans la bulle d'une
  note, comme dans Odysseus) → une seule bulle de chaque sorte, gardée et réutilisée (annotation, liste
  d'un champ, aperçu de lien), détachée seulement à la fermeture de la fenêtre (`teardown`).
- Enregistrement (`Document.save`) : nom caché dans le dossier de destination, fsync, `os.replace`,
  fsync du dossier ; droits du fichier remplacé gardés ; document chiffré : reste chiffré (Poppler).
  Fait dans un fil ; notre propre écriture ne déclenche pas le rechargement (date notée) ; document
  modifié : jamais rechargé tout seul. Fichier en lecture seule : « Enregistrer sous ».

- Outils (`backend/pdftools.py`, repris d'Odysseus `backend/pdf.py` ; `backend/jobs.py` = Job réduit ;
  `ui/tools.py`, `ui/organizer.py`, `ui/progress.py`) : travaillent sur le FICHIER → document modifié :
  « Enregistrer d'abord ? » (`MainWindow._with_saved`). Sortie nouvelle à côté, « nom (compressé).pdf »…,
  jamais par-dessus un fichier (`place_new` : lien dur puis retrait du nom caché ; repli renommage sans
  liens durs) ; remplacement voulu par `replace_into` (droits gardés, fsync). Document protégé : copie
  déchiffrée dans un dossier privé 0700, effacée après (`plain`). Toast « Ouvrir » (`tool_result`) ;
  document remplacé : relu à la même page (`reopen_after_tool`), « Annuler » remet la copie gardée dans
  ~/.cache/calypso/undo (organiser). Progression : fenêtre après 0,4 s, « Annuler » tue le programme
  (groupe de processus). Organiser = UNE commande qpdf (`--pages` dans l'ordre + `--rotate=+N:pages`).
  Caviarder : pages touchées redessinées en IMAGE (zones peintes en noir) puis remises par qpdf — le texte
  dessous n'existe plus (testé). Compression plus grosse que l'original : rien n'est produit.
  Organiseur : sélection gérée par nous (`sel`, clic / Ctrl / Maj, classe `calypso-picked`) — en sélection
  MULTIPLE, la FlowBox prenait le glisser pour son cadre de sélection (et sans adjustment : CRITICAL
  gtk_adjustment_get_value) : les miniatures ne partaient jamais. Vrai glisser testé dans ui_mouse (le
  glisser xdotool tourne dans un FIL : la boucle GTK doit traiter les mouvements au fil de l'eau).

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
différentes ; chiffré par qpdf ; formulaire écrit à la main : texte, multiligne, case, radios, liste).
Une capture ou un test qui quitte avec un document modifié doit poser `dirty = False` (sinon la question
« Enregistrer ? » attend). Captures : `xvfb-run -a -s "-screen 0 1400x1000x24" python3.12
tools/screenshot.py SORTIE.png [--dark] [--sidebar=outline] [--search=TEXTE] [--welcome] …`.
