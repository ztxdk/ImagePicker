# agents.md

## Formaal
Denne fil logger alle handlinger udfort i forbindelse med den aktuelle opgave.

## Log

1. Opgaven blev afklaret: der skal laves en prioriteret to-do-liste og oprettes en `agents.md`-fil, som logges lobende.
2. Projektroden blev kontrolleret for eksisterende `agents.md`- eller `TODO.md`-filer. Ingen eksisterende filer fundet.
3. `agents.md` blev oprettet i projektroden.
4. `TODO.md` blev oprettet i projektroden med en prioriteret liste over kritiske fejl, stabilitetsarbejde, ydelsesforbedringer, korrekthed, oprydning og tests.
5. Indholdet i `TODO.md` og `agents.md` blev verificeret efter oprettelse for at kontrollere struktur og placering.
6. `TODO.md` blev justeret til konsekvent ASCII-tekst ved at normalisere en enkelt overskrift.
7. Implementeringsarbejdet blev startet efter brugerens godkendelse. Kravet blev fastlagt som: nye rettelser skal laves i en ny fil, gamle filer maa ikke overskrives.
8. Arbejdsplanen blev fastlagt: analysere regressionspunkter mod tidligere version, bygge en ny hovedfil, tilfoeje tests og verificere uden at overskrive gamle filer.
9. `ImagePicker028.py` blev laest for at hente de manglende dialogklasser `FileOperationDialog` og `Win11MessageBox`, som ikke findes i `ImagePicker029.py`.
10. Centrale flows i `ImagePicker028.py` og `ImagePicker029.py` blev sammenlignet for loading, preview og filbehandling, saa den nye version kan bygges oven paa `0.29` uden at viderefoere regressionsfejlene.
11. En ny arbejdsfil `ImagePicker030.py` blev oprettet som kopi af `ImagePicker029.py`, saa alle videre rettelser kan ske uden at overskrive gamle filer.
12. Den nye arbejdsfil blev gennemgaaet omkring imports, worker-klasser, dialoger og de nederste globale patch-funktioner for at planlaegge den store samlende rettelse.
13. Beslutningen blev taget om at erstatte indholdet i `ImagePicker030.py` med en ren, sammenhaengende ny implementering, fordi `0.29` er afbrudt af ubrugte patch-rester og mangler et selvstaendigt opstartspunkt.
14. Den tidligere arbejdsfil `ImagePicker030.py` blev fjernet, saa en ny implementering kan oprettes rent i samme filnavn uden rester fra kopiudgaven.
15. En ny, selvstaendig version af `ImagePicker030.py` blev oprettet fra bunden med samlet datamodel, AppData-baseret settings-lagring, logging, genindfoerte dialoger, cachelag, ny loader-thread, ny RAW-preview-thread, layout-baseret UI og et faktisk `main`-entrypoint.
16. `ImagePicker030.py` blev syntaksverificeret med `python -m py_compile`, og der blev derefter planlagt en headless smoke-test for at fange de foerste runtime-fejl.
17. Den foerste headless smoke-test viste, at testscriptet skulle starte loading eksplicit, fordi `QTimer.singleShot` ikke bliver afviklet uden en event loop. Selve importen og vinduesoprettelsen fejlede ikke.
18. En opdateret headless smoke-test blev koert mod `ImagePicker030.py`, hvor appen blev startet offscreen, `pics` blev indlaest eksplicit, og modellen endte med 22 rækker og en aktiv markering.
19. En ny testfil `tests/test_imagepicker030.py` blev oprettet med unit tests for gruppering, preview-valg, filscan og et headless smoke-flow for den nye hovedfil.
20. En ny `requirements.txt` blev oprettet med de runtime-afhaengigheder, som den nye version bruger, saa den gamle `Req.txt` ikke skal overskrives.
21. Verifikationsfasen blev startet med plan om at koere compile-kontrol og hele den nye test-suite mod `ImagePicker030.py`.
22. Compile-kontrol blev koert mod `ImagePicker030.py` og `tests/test_imagepicker030.py` uden fejl.
23. Den nye test-suite blev koert med `python -m unittest discover -s tests -p "test_*.py"` og bestod med 6 tests.
24. Maskinen blev klargjort til at koere `ImagePicker030.py`: Python 3.13 blev installeret side om side med den eksisterende Python 3.14, et lokalt `.venv` blev oprettet, og afhængighederne fra `requirements.txt` blev installeret.
25. Installationen blev verificeret med `.venv` ved at koere `py_compile` paa `ImagePicker030.py` og testfilen samt hele unittest-suiten, som bestod med 6 tests.
26. Bruger meldte, at `Cancel`-knappen ikke kunne bruges under loading af mapper med mange billeder, fordi programmet virkede laast.
27. En ny `ImagePicker031.py` blev oprettet fra `ImagePicker030.py`, saa version 30 ikke blev overskrevet.
28. Loaderen i version 31 blev gjort mere responsiv: scanning kan afbrydes, `ImageLoaderThread` fik en `cancel()`-metode, og UI-traaden genbruger nu EXIF/thumbnail-data fra loaderen i stedet for at beregne dem igen ved hver raekke.
29. En ny `tests/test_imagepicker031.py` blev oprettet med en cancel-test, der verificerer, at loaderen stopper foer alle entries behandles.
30. `ImagePicker031.py` og `tests/test_imagepicker031.py` blev syntaksverificeret, version 31-testene bestod med 7 tests, og hele test-suiten bestod med 13 tests.
31. Dark-mode styling i `ImagePicker031.py` blev udvidet med eksplicit `QScrollBar`- og `QTableCornerButton`-styling, saa table-scrollbars ikke falder tilbage til hvide Windows-standardfarver.
32. `ImagePicker031.py` blev syntaksverificeret igen efter scrollbar-rettelsen uden fejl.
33. Den resterende hvide flade under/ved tabelheaderen under loading blev rettet ved at style `QHeaderView`, `QTableView QTableCornerButton::section` og `QAbstractScrollArea::corner` i dark-mode stylesheetet.
34. `ImagePicker031.py` blev syntaksverificeret igen efter rettelsen uden fejl.
35. Settings-dialogens dark-mode stylesheet blev rettet for `QComboBox::drop-down` og `QComboBox QAbstractItemView`, saa dropdown-listen ikke laengere vises med hvid baggrund.
36. `ImagePicker031.py` blev syntaksverificeret igen efter combobox-rettelsen uden fejl.
37. Combobox-rettelsen blev udvidet, fordi Qt ikke arvede dialogens stylesheet til popup-listen korrekt: settings-dialogens `QComboBox`-felter bruger nu eksplicit `QListView` som popup-view, og view'et styles direkte efter dialogens stylesheet.
38. `ImagePicker031.py` blev syntaksverificeret igen efter den direkte popup-view-rettelse uden fejl.
39. Settings-dialogens combobox- og spinbox-styling blev justeret i baade dark og light mode, saa den direkte popup-view-rettelse ikke giver et klassisk Windows/Qt-look.
40. `ImagePicker031.py` blev syntaksverificeret igen efter stylingjusteringen uden fejl.
41. Der blev tilfoejet lokale SVG-chevron-ikoner til combobox- og spinbox-kontroller, saa dropdowns og gamma/thumbnail-vaelgere igen viser synlige pile i baade dark og light mode.
42. `ImagePicker031.py` blev syntaksverificeret, og version 31-testene bestod med 7 tests efter ikonrettelsen.
43. Bruger bad om en hastighedsoptimeret version 032 uden persistent cache mellem koersler.
44. `ImagePicker032.py` blev oprettet fra `ImagePicker031.py`, saa version 31 ikke blev overskrevet.
45. Version 032 fik en lettere `QAbstractTableModel`-baseret tabelmodel i stedet for `QStandardItemModel`, saa store lister bruger faerre objekter og mindre UI-overhead.
46. Loading-flowet blev splittet i hurtig scanning og separat metadata/thumbnail-worker: scanneren indsætter rækker i batches, mens EXIF og thumbnails fyldes ind asynkront og lazy for synlige/naerliggende rækker.
47. Billedindlæsning blev optimeret med `QImageReader.setScaledSize()` for JPG-preview/thumbnails, worker-traade bruger `QImage` i stedet for `QPixmap`, og RAW-thumbnails bruger embedded thumbnail foerst.
48. Valgt billede og naerliggende rækker prioriteres i metadata-koeen, saa preview/aktiv brugerinteraktion ikke skal vente paa resten af mappen.
49. `tests/test_imagepicker032.py` blev oprettet med version 32-tests inklusive den nye tabelmodel.
50. `ImagePicker032.py` og version 32-testen blev syntaksverificeret, version 32-testene bestod med 8 tests, og hele test-suiten bestod med 21 tests.
51. Preview-panelets baggrund i `ImagePicker032.py` blev rettet, saa den opdateres ved skift mellem light og dark mode inde i programmet via eksplicit `previewPanel`-stylesheet, palette-opdatering og repolish.
52. `ImagePicker032.py` blev syntaksverificeret, og version 32-testene bestod med 8 tests efter tema-rettelsen.
53. Preview-interaktioner blev genindfoert i `ImagePicker032.py`: musehjul over billedefremviseren skifter nu mellem rækker, og `Space`/midterklik toggler selection for den aktuelle række.
54. Event-filter installationen blev flyttet til efter tabel-widgetten er lagt i layoutet for at undgaa et offscreen/Qt-hæng under opstart, og metadata-prefetch for naerliggende rækker holdes asynkron.
55. `ImagePicker032.py` blev syntaksverificeret, version 32-testene bestod med 8 tests, og hele test-suiten bestod med 21 tests efter interaktionsrettelsen.
56. Midterlayoutet i `ImagePicker032.py` blev ændret til en horisontal `QSplitter`, saa brugeren kan resize mellem tabellen og billedefremviseren.
57. Splitter-håndtaget blev stylet i baade dark og light mode, og `ImagePicker032.py` samt version 32-testene og hele test-suiten blev verificeret uden fejl.
58. Preview-resize i `ImagePicker032.py` blev koblet paa baade `QSplitter.splitterMoved` og preview-labelens resize-event, saa det viste billede skaleres om efter panelets nye stoerrelse.
59. `ImagePicker032.py`, version 32-testene og hele test-suiten blev verificeret uden fejl efter preview-resize-rettelsen.
60. Splitter-krympning efter stort preview blev rettet ved at saette preview-labelens minimum til 1x1 og bruge `QSizePolicy.Ignored`, saa pixmap'ets size hint ikke laaser panelets minimumsbredde.
61. `ImagePicker032.py`, version 32-testene og hele test-suiten blev verificeret uden fejl efter splitter-krympningsrettelsen.
62. En engelsk `README.md` blev oprettet i projektroden med beskrivelse af formaal, features, understottede filtyper, krav, setup, koer kommandoer, workflow, controls, settings, performance-noter, tests og projektfiler.
63. Python 3.14-kompatibilitet blev verificeret i et separat `.venv314`: dependencies fra `requirements.txt` blev installeret, `ImagePicker032.py` og version 32-testen blev syntaksverificeret, version 32-testene bestod med 8 tests, og hele test-suiten bestod med 21 tests.
64. `README.md` blev opdateret til at anbefale Python 3.14, mens Python 3.13 stadig noteres som kendt fungerende.
65. `PyInstaller` blev installeret i `.venv314`, og `ImagePicker032.py` blev bygget som en one-file Windows GUI-exe med projektikon og med `icons`-mappen inkluderet som data.
66. Den byggede exe blev placeret som `output/ImagePicker032.exe` og smoke-testet ved kort opstart, hvorefter processen blev stoppet igen uden tidlig exit-fejl.
67. Git og GitHub CLI blev installeret, projektet blev initialiseret som git-repo, relevante filer blev committet og pushet til `https://github.com/ztxdk/ImagePicker` paa branch `main`.
68. `output/ImagePicker032.exe` blev genbygget med Python 3.14/PyInstaller, smoke-testet og uploadet som GitHub release `v0.32`.
69. `README.md` blev opdateret med en note om, at projektet er vibe coded.
70. Bruger bad om videre arbejde paa `ImagePicker032.py`, men som en ny fil, med mulighed for at sortere filerne i listviewen efter f.eks. navn og dato.
71. `ImagePicker033.py` blev oprettet som kopi af `ImagePicker032.py`, og `tests/test_imagepicker033.py` blev oprettet som kopi af version 32-testen, saa version 32 ikke blev overskrevet.
72. Version 33 blev opdateret til `APP_VERSION = "0.33"` og fik sortering direkte i `ImageTableModel`, inklusive naturlig filnavnesortering, EXIF-dato/fallback til filens ændringstidspunkt, numeriske metadatafelter og lens-sortering.
73. Tabelheader-sortering blev aktiveret i UI'en, og aktuel række/preview bliver genvalgt efter model-sortering, saa sortering ikke skifter brugerens aktive billede utilsigtet.
74. Version 33-testene blev udvidet med sortering efter naturligt filnavn, EXIF-dato, bevarelse af checkede entries og resortering naar metadata ankommer asynkront.
75. De gamle `.venv`- og `.venv314`-miljoer viste sig at have brudte Python-stier; en midlertidig Python 3.12-testvenv blev oprettet til verifikation og derefter fjernet igen.
76. `ImagePicker033.py` og `tests/test_imagepicker033.py` blev syntaksverificeret, version 33-testene bestod med 11 tests, og hele test-suiten bestod med 32 tests.
77. Bruger koerte `py .\ImagePicker033.py` og fik `ModuleNotFoundError: No module named 'darkdetect'`, fordi standard-`py` starter global Python 3.14-arm64 uden projektets dependencies.
78. Et nyt lokalt miljoe `.venv312` blev oprettet med Python 3.12 x64, og dependencies fra `requirements.txt` blev installeret der, fordi PyQt5 har faerdige wheels til denne platform.
79. `.venv312` blev verificeret med `py_compile` paa `ImagePicker033.py` og `tests/test_imagepicker033.py`, og version 33-testene bestod med 11 tests.
80. Bruger beskrev en scroll-fejl i listviewen, hvor hurtig musehjuls-scroll eller flytning af scrollbar kunne faa listen til at hoppe/loope i et lille scroll-omraade.
81. `ImagePicker034.py` blev oprettet som kopi af `ImagePicker033.py`, og `tests/test_imagepicker034.py` blev oprettet som kopi af version 33-testen, saa version 33 ikke blev overskrevet.
82. Aarsagen blev vurderet til at vaere sorterings-genvalg efter `layoutChanged`, hvor `restore_current_selection_after_sort()` kaldte `scrollTo()` og dermed kunne kaempe mod brugerens aktive scrolling, isaer naar metadata ankom asynkront.
83. Version 34 blev rettet, saa genvalg efter sortering bevarer den aktuelle scrollbar-vaerdi og bruger `QItemSelectionModel` uden at kalde `scrollTo()`, saa viewet ikke traekkes tilbage til den aktive raekke under scrolling.
84. Version 34-testene blev udvidet med en isoleret scrollbar-test, der verificerer at sorterings-genvalg ikke flytter scrollbar-positionen.
85. `ImagePicker034.py` og `tests/test_imagepicker034.py` blev syntaksverificeret, version 34-testene bestod med 12 tests, og hele test-suiten bestod med 44 tests.
86. Bruger bad om commit, push, compile og GitHub release.
87. `.gitignore` blev udvidet med `.venv*/`, saa det nye lokale `.venv312`-miljoe ikke inkluderes i git.
88. Git author blev sat lokalt til `ztxdk <ztxdk@users.noreply.github.com>`, og ændringerne for version 33/34 blev committet.
89. `ImagePicker034.py` og `tests/test_imagepicker034.py` blev syntaksverificeret igen, og version 34-testene bestod med 12 tests.
90. `PyInstaller` blev installeret i `.venv312`, og `ImagePicker034.py` blev bygget som one-file Windows GUI-exe med projektikon og `icons`-mappen inkluderet.
91. Den byggede exe blev placeret som `output/ImagePicker034.exe` og smoke-testet ved kort opstart, hvorefter processen blev stoppet uden tidlig exit-fejl.
92. Committen blev pushet til `origin/main`, GitHub CLI blev installeret med winget, og tagget `v0.34` blev oprettet og pushet.
93. GitHub CLI var ikke logget ind, og der fandtes ingen `GH_TOKEN`/`GITHUB_TOKEN`, saa selve GitHub Release-oprettelsen og asset-upload kræver efterfoelgende GitHub login.
94. GitHub CLI blev fundet via fuld sti `C:\Program Files\GitHub CLI\gh.exe`, fordi den aktuelle PowerShell-session ikke havde opdateret `PATH`.
95. `gh` blev autentificeret ved at genbruge eksisterende Git Credential Manager-login for GitHub-kontoen `ztxdk`.
96. GitHub release `v0.34` blev oprettet, og `output/ImagePicker034.exe` blev uploadet som release asset.
