# Front News – statische Website (Generator)

Python 3 (nur Standardbibliothek), kein Build-Framework. Eine Ausgabe = eine JSON-Datei; dieselbe Datei speist Chat und Website (keine zweite Recherche).

## Befehle
    python3 build.py validate AUSGABE.json [--map map.json] [--preview]
    python3 build.py publish  AUSGABE.json [--map map.json] [--preview]
    python3 build.py rebuild [--preview] [--out DIR]
    tests/test_atomic.sh      # fehlerhafte Ausgabe → Live-Seite bleibt unverändert

`rebuild --out public` schreibt ein echtes Verzeichnis (kein Symlink), damit Hosts wie Render die Seite ausliefern können. Ohne `--out` bleibt `public` ein atomar getauschter Symlink auf den letzten guten Build. Gibt es keine Ausgabe mit `publish:true`, entsteht eine neutrale Haltseite («Front News – bald verfügbar») statt Beispiel- oder Vorschauinhalten.

`publish`: validiert die ganze Ausgabe, legt sie unter `editions/` ab, validiert das ganze Archiv, baut in `releases/build-XXXX/` (temp) und tauscht den Symlink `public` atomar (`os.replace`). Bei jedem Fehler: Exit-Code 2, `public` zeigt weiter auf den letzten guten Build. Der vorherige Build bleibt für Rollback erhalten.
`--preview` nutzt getrennte Ordner (`editions-preview/`, `public-preview/`), erlaubt `publish:false` und noch nicht erstellte Illustrationen und zeigt das Banner «Private Vorschau – nicht veröffentlicht». Ohne `--preview` werden `publish:false` und `pending`-Bilder abgelehnt.

## Schema v1 (`schema_version: "1.x"`)
| Feld | Pflicht | Bedeutung |
|---|---|---|
| schema_version | ja | "1.0"; Hauptversion muss unterstützt sein |
| edition | ja | `morning` (Morgenausgabe) / `evening` (Abendausgabe) |
| published_at | ja | ISO 8601 mit Offset; tatsächlicher Abschluss (keine Planzeit). Anzeige in Zürcher Zeit |
| time_approximate | nein | true → «ca.» vor der Zeit |
| publish | nein (Default true) | false → nur mit `--preview` |
| status, verified_at, example | nein | Anzeige/Banner (`example:true` → «Beispielinhalt») |
| language | nein | muss `de-CH` sein |
| items[] | ja | 10–12 Meldungen |
| items[].id, headline, summary, section | ja | Text; «ß» wird abgelehnt (Schweizer Schreibweise) |
| items[].lead | nein | true = Aufmacher (sonst erste Meldung) |
| items[].sources[] | ja, ≥1 | `name`, `url` (http/s); optional `published_at`, `updated_at`, `paywall` (bool), `note` |
| items[].image | nein | `type` photo/illustration, `src` (lokaler Pfad, muss existieren), `alt`, `caption`, `credit`; `status:"pending"` nur für Illustrationen und nur in der Vorschau |

Darstellung: Illustrationen tragen sichtbar «KI-generierte Illustration», ausstehende «KI-Illustration folgt»; Fotos «Foto» + Bildnachweis. Meldungen mit nur einer Quelle erhalten den Hinweis «Einzelquelle»; `paywall:true` → «Bezahlschranke». Archiv: die letzten 8 Ausgaben unter `archiv/JJJJ-MM-TT-morning|evening.html`.

## Fremde JSON-Strukturen anpassen
1. Das aktuelle Front-News-Paket (`{"edition":{…}, "items":[…]}` mit `category`, `edition_prepared_at`, `image.type:"ai_illustration"`, `image.brief`) wird automatisch erkannt (`adapt_frontnews_v0`).
2. Für andere Pfade eine Mapping-Datei (`--map`) mit Punkt-Pfaden:
```json
{"constants":{"schema_version":"1.0","language":"de-CH"},
 "edition":{"edition":"meta.kind","published_at":"meta.finished_at","publish":"meta.publish"},
 "items_path":"data.stories",
 "item":{"id":"slug","headline":"title","summary":"body","section":"rubric"},
 "sources_path":"refs","source":{"name":"outlet","url":"link","paywall":"paywalled"},
 "image_path":"visual","image":{"type":"kind","src":"file","alt":"alt","caption":"caption","credit":"credit"}}
```
Danach läuft dieselbe Validierung. Neue Pflichtfelder → `schema_version` 2.x und `SUPPORTED` erweitern.

## Dateien
`build.py`, `assets/style.css`, `assets/img/illu*.svg` (lokale Platzhalter), `sample-edition.json` (Beispielinhalt), `tests/test_atomic.sh`, `preview-desktop.png`, `preview-mobile.png`, `public-preview/` (gerenderte Vorschau aus /workspace/front-news-preview/edition.json, unverändert gelesen).

## Generierte Illustrationen (`--manifest`)
    python3 build.py publish /workspace/front-news-preview/edition.json --manifest generated-assets/manifest.json --preview
`merge_manifest` ordnet Bilder per `story_id` zu, kopiert nur die Web-Grössen (640/1024, WebP+JPG) nach `media/<edition-id>/` und setzt `image.variants`, `width`/`height`, Alt-Text («Symbolische KI-Illustration: … Keine Darstellung des realen Ereignisses oder Ortes.») und die Legende «Symbolbild (KI-generiert) – keine Aufnahme des Ereignisses.». Zusätzliche Hinweise je Meldung stehen in `image-notes.json`. Ausgabe: `<picture>` mit WebP-`srcset` und JPG-Fallback; nur das Aufmacherbild wird sofort geladen, alle anderen mit `loading="lazy"`. Originale werden nie in den Build kopiert. Hinweis: auch `validate --manifest` kopiert die Web-Dateien nach `media/`.
