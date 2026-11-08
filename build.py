#!/usr/bin/env python3
"""Front News static site generator. Stdlib only.
Usage:
  python3 build.py validate EDITION.json [--map map.json]
  python3 build.py publish  EDITION.json [--map map.json]   # validate, add to archive, rebuild atomically
  python3 build.py rebuild [--out DIR]                     # rebuild from editions/ (no new input)
  --out DIR  write a real directory (for hosts that cannot publish a symlink)
Without --out, public is swapped atomically as a symlink to the new release.
If no edition with publish:true exists, a holding page is written instead.
"""
import json, os, sys, shutil, tempfile, html, re
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.environ.get("FRONTNEWS_ROOT") or os.path.dirname(os.path.abspath(__file__))
PREVIEW = "--preview" in sys.argv
SUF = "-preview" if PREVIEW else ""
ED_DIR = os.path.join(ROOT, "editions" + SUF)
REL_DIR = os.path.join(ROOT, "releases" + SUF)
PUBLIC = os.path.join(ROOT, "public" + SUF)    # symlink -> releases/<build>
SUPPORTED = {"1"}                               # major schema versions understood
ARCHIVE_N = 8
ZRH = ZoneInfo("Europe/Zurich")
KIND = {"morning": "Morgenausgabe", "evening": "Abendausgabe"}
MONTHS = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"]
DAYS = ["Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag","Sonntag"]

class Invalid(Exception): pass

# ---------- adapter: map a foreign JSON layout onto schema v1 ----------
def dig(obj, path):
    for p in path.split("."):
        if obj is None: return None
        obj = obj[int(p)] if isinstance(obj, list) and p.isdigit() else (obj.get(p) if isinstance(obj, dict) else None)
    return obj

def adapt_frontnews_v0(raw):
    """Layout of Front News preview package: {"edition": {...meta}, "items": [...]}"""
    m = raw["edition"]
    kind = "evening" if str(m.get("id","")).endswith("evening") else "morning"
    out = {"schema_version": "1.0", "edition": kind, "id": m.get("id"),
           "published_at": m.get("published_at") or m.get("edition_prepared_at"),
           "time_approximate": "approx" in str(m.get("edition_prepared_at_note","")).lower(),
           "publish": m.get("publish", False), "status": m.get("status"),
           "language": m.get("language", "de-CH"), "verified_at": m.get("verified_at"), "items": []}
    for it in raw.get("items", []):
        n = {"id": it.get("id"), "headline": it.get("headline"), "summary": it.get("summary"),
             "section": it.get("section") or it.get("category"), "sources": it.get("sources", [])}
        im = it.get("image")
        if im:
            t = im.get("type")
            n["image"] = {"type": "illustration" if "illustration" in str(t) else t,
                          "status": "pending" if not im.get("src") else "ready",
                          "src": im.get("src"), "brief": im.get("brief"), "alt": im.get("alt") or im.get("brief"),
                          "caption": im.get("caption") or im.get("brief"), "credit": im.get("credit") or "KI-generiert"}
        out["items"].append(n)
    return out

def adapt(raw, mapping):
    """mapping = {"edition": {target: "dotted.source.path"}, "items_path": "...",
                  "item": {target: path}, "source": {target: path}, "image": {target: path},
                  "sources_path": "...", "image_path": "...", "constants": {...}}"""
    if not mapping:
        return adapt_frontnews_v0(raw) if isinstance(raw.get("edition"), dict) else raw
    out = dict(mapping.get("constants", {}))
    for k, p in mapping.get("edition", {}).items(): out[k] = dig(raw, p)
    items = []
    for it in dig(raw, mapping["items_path"]) or []:
        n = {k: dig(it, p) for k, p in mapping.get("item", {}).items()}
        n["sources"] = [{k: dig(s, p) for k, p in mapping.get("source", {}).items()}
                        for s in (dig(it, mapping.get("sources_path", "sources")) or [])]
        im = dig(it, mapping.get("image_path", "image"))
        if im: n["image"] = {k: dig(im, p) for k, p in mapping.get("image", {}).items()}
        items.append(n)
    out["items"] = items
    return out

def merge_manifest(ed, manifest_path, notes_path=None):
    """Attach generated illustrations (web sizes only) to items; copies files into ROOT/media/<edition>/."""
    man = json.load(open(manifest_path, encoding="utf-8")); mdir = os.path.dirname(os.path.abspath(manifest_path))
    notes = json.load(open(notes_path, encoding="utf-8")) if notes_path and os.path.isfile(notes_path) else {}
    by_id = {m["story_id"]: m for m in man.get("images", [])}
    dest_rel = os.path.join("media", ed.get("id") or slug(ed))
    for it in ed.get("items", []):
        m = by_id.get(it.get("id"))
        if not m: continue
        variants = {}
        for w, fm in m["web"].items():
            for fmt, rel in fm.items():
                src = os.path.join(mdir, rel)
                if os.path.isfile(src):
                    os.makedirs(os.path.join(ROOT, dest_rel), exist_ok=True)
                    shutil.copy2(src, os.path.join(ROOT, dest_rel, os.path.basename(rel)))
                variants.setdefault(w, {})[fmt] = os.path.join(dest_rel, os.path.basename(rel))
        desc = re.sub(r"\s*\(KI-Illustration\)\.?$", "", m.get("alt", "")).rstrip(".")
        ratio = m["height"] / m["width"]
        it["image"] = {"type": "illustration", "status": "ready", "variants": variants,
                       "width": 1024, "height": round(1024 * ratio),
                       "alt": f"Symbolische KI-Illustration: {desc}. Keine Darstellung des realen Ereignisses oder Ortes.",
                       "caption": "Symbolbild (KI-generiert) – keine Aufnahme des Ereignisses.",
                       "note": notes.get(it["id"]), "credit": "KI-generiert",
                       "license_status": m.get("license_status")}
    return ed

# ---------- validation ----------
def validate(ed, base_dir, preview=False):
    errs = []
    def need(cond, msg):
        if not cond: errs.append(msg)
    sv = str(ed.get("schema_version", ""))
    need(sv.split(".")[0] in SUPPORTED, f"schema_version '{sv}' nicht unterstützt (erwartet 1.x)")
    need(ed.get("edition") in KIND, "edition muss 'morning' oder 'evening' sein")
    try:
        ts = datetime.fromisoformat(str(ed.get("published_at")))
        need(ts.tzinfo is not None, "published_at braucht eine Zeitzone/Offset")
    except ValueError:
        errs.append("published_at ist kein ISO-8601-Zeitstempel")
    need(ed.get("language", "de-CH") == "de-CH", "language muss de-CH sein")
    if not preview:
        need(ed.get("publish", True) is True, f"publish={ed.get('publish')!r} (status {ed.get('status')}) – nicht zur Veröffentlichung freigegeben")
    items = ed.get("items")
    need(isinstance(items, list) and 10 <= len(items) <= 12, f"10–12 Meldungen erwartet, erhalten: {len(items) if isinstance(items, list) else 'keine'}")
    ids = set()
    for i, it in enumerate(items or []):
        tag = f"items[{i}]"
        for f in ("id", "headline", "summary", "section"):
            need(isinstance(it.get(f), str) and it[f].strip(), f"{tag}.{f} fehlt")
        need(it.get("id") not in ids, f"{tag}.id doppelt"); ids.add(it.get("id"))
        src = it.get("sources") or []
        need(len(src) >= 1, f"{tag}: mindestens eine Quelle nötig")
        for j, s in enumerate(src):
            need(isinstance(s.get("name"), str) and s["name"].strip(), f"{tag}.sources[{j}].name fehlt")
            need(re.match(r"^https?://", str(s.get("url", ""))), f"{tag}.sources[{j}].url ungültig")
        im = it.get("image")
        if im:
            need(im.get("type") in ("photo", "illustration"), f"{tag}.image.type muss photo|illustration sein")
            need(im.get("caption"), f"{tag}.image.caption fehlt")
            need(im.get("credit"), f"{tag}.image.credit fehlt")
            need(im.get("alt"), f"{tag}.image.alt fehlt")
            pending = im.get("status") == "pending"
            need(not pending or im.get("type") == "illustration", f"{tag}: nur Illustrationen dürfen 'pending' sein")
            need(not (pending and not preview), f"{tag}.image noch nicht erstellt (pending) – nicht publizierbar")
            p = str(im.get("src") or "")
            if pending: continue
            if im.get("variants"):
                files = [p for fm in im["variants"].values() for p in fm.values()]
                need(all("webp" in fm and "jpg" in fm for fm in im["variants"].values()), f"{tag}.image: WebP und JPG je Grösse nötig")
                for p in files:
                    need(os.path.isfile(os.path.join(base_dir, p)), f"{tag}.image fehlt: {p}")
                need(im.get("width") and im.get("height"), f"{tag}.image: width/height fehlen")
                continue
            need(not re.match(r"^[a-z]+:", p) and ".." not in p, f"{tag}.image.src muss lokaler Pfad sein")
            need(os.path.isfile(os.path.join(base_dir, p)), f"{tag}.image.src existiert nicht: {p}")
        for f in ("headline", "summary"):
            need("ß" not in str(it.get(f, "")), f"{tag}.{f}: 'ß' statt 'ss' (Schweizer Schreibweise)")
    if errs: raise Invalid("\n".join(errs))
    return ed

# ---------- rendering ----------
e = lambda s: html.escape(str(s), quote=True)
def zrh(ed): return datetime.fromisoformat(ed["published_at"]).astimezone(ZRH)
def de_date(d): return f"{DAYS[d.weekday()]}, {d.day}. {MONTHS[d.month-1]} {d.year}"
def slug(ed): return zrh(ed).strftime("%Y-%m-%d-") + ed["edition"]

def fig(im, cls="", eager=False, prefix=""):
    if not im: return ""
    if im.get("variants"):
        v = im["variants"]; ws = sorted(v, key=int)
        sset = lambda fmt: ", ".join(f'{prefix}{e(v[w][fmt])} {w}w' for w in ws)
        sizes = "(max-width: 640px) 100vw, 60vw" if "leadfig" in cls else "(max-width: 640px) 100vw, (max-width: 900px) 50vw, 380px"
        load = 'fetchpriority="high"' if eager else 'loading="lazy" decoding="async"'
        note = f' <span class="note">{e(im["note"])}</span>' if im.get("note") else ""
        return (f'<figure class="{cls}"><div class="imgwrap"><picture><source type="image/webp" srcset="{sset("webp")}" sizes="{sizes}">'
                f'<img src="{prefix}{e(v[ws[0]]["jpg"])}" srcset="{sset("jpg")}" sizes="{sizes}" width="{im["width"]}" height="{im["height"]}" alt="{e(im["alt"])}" {load}></picture>'
                f'<span class="badge badge-ai">KI-Illustration</span></div>'
                f'<figcaption>{e(im["caption"])}{note}</figcaption></figure>')
    if im.get("status") == "pending":
        return (f'<figure class="{cls} ph"><div class="imgwrap ph-box" role="img" aria-label="Platzhalter für KI-Illustration: {e(im.get("brief",""))}">'
                f'<span class="badge badge-ai">KI-Illustration folgt</span><span class="ph-brief">{e(im.get("brief",""))}</span></div></figure>')
    label = ('<span class="badge badge-ai">KI-generierte Illustration</span>' if im["type"] == "illustration"
             else '<span class="badge">Foto</span>')
    return (f'<figure class="{cls}"><div class="imgwrap"><img src="{e(im["src"])}" alt="{e(im["alt"])}" loading="lazy">{label}</div>'
            f'<figcaption>{e(im["caption"])} <span class="credit">{"Illustration" if im["type"]=="illustration" else "Foto"}: {e(im["credit"])}</span></figcaption></figure>')

def srcmeta(s):
    bits = []
    if s.get("published_at"):
        try: bits.append("publiziert " + datetime.fromisoformat(s["published_at"]).astimezone(ZRH).strftime("%d.%m., %H:%M"))
        except ValueError: pass
    if s.get("paywall"): bits.append("Bezahlschranke")
    if s.get("note"): bits.append(str(s["note"]))
    return f' <span class="smeta">({e(" · ".join(bits))})</span>' if bits else ""

def sources(it):
    links = "; ".join(f'<a href="{e(s["url"])}" rel="noopener nofollow">{e(s["name"])}</a>{srcmeta(s)}' for s in it["sources"])
    single = ' <span class="flag" title="Nur ein Medium als Quelle">Einzelquelle</span>' if len(it["sources"]) == 1 else ""
    return f'<p class="sources"><span>Quelle{"n" if len(it["sources"])>1 else ""}:</span> {links}{single}</p>'

def page(ed, archive, prefix, is_current):
    t = zrh(ed); items = ed["items"]
    lead = next((i for i in items if i.get("lead")), items[0]); rest = [i for i in items if i is not lead]
    sample = ""
    if ed.get("example"):
        sample += '<div class="sample" role="note"><strong>Beispielinhalt</strong> – keine echten Nachrichten.</div>'
    if not ed.get("publish", True):
        sample += ('<div class="sample private" role="note"><strong>Private Vorschau – nicht veröffentlicht.</strong> '
                   f'Status: {e(ed.get("status") or "nicht freigegeben")}. Illustrationen werden erst nach Freigabe erstellt.</div>')
    cards = "".join(f'<article class="card" id="{e(i["id"])}">{fig(i.get("image"), prefix=prefix)}<p class="kicker">{e(i["section"])}</p>'
                    f'<h3>{e(i["headline"])}</h3><p>{e(i["summary"])}</p>{sources(i)}</article>' for i in rest)
    gallery = [i for i in items if i.get("image") and i["image"].get("status") != "pending"]
    gal = "".join(fig(i["image"], "g", prefix=prefix) for i in gallery if not i["image"].get("variants"))
    arch = "".join(f'<li><a href="{prefix}archiv/{slug(a)}.html"{" aria-current=page" if a is ed else ""}>'
                   f'{KIND[a["edition"]]} · {de_date(zrh(a))}</a></li>' for a in archive)
    return f'''<!doctype html><html lang="de-CH"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Front News – {KIND[ed["edition"]]} vom {de_date(t)}</title>
<meta name="description" content="Front News: neutrale Nachrichtenübersicht, zweimal täglich.">
<link rel="stylesheet" href="{prefix}assets/style.css"></head><body>
<a class="skip" href="#main">Zum Inhalt springen</a>
<header class="mast"><div class="wrap">
<p class="edition"><span class="pill pill-{ed["edition"]}">{KIND[ed["edition"]]}</span>
<span>{de_date(t)}</span></p>
<h1><a href="{prefix}index.html">Front News</a></h1>
<p class="meta">{"Veröffentlicht" if ed.get("publish", True) else "Erstellt"}: <time datetime="{e(ed["published_at"])}">{"ca. " if ed.get("time_approximate") else ""}{t.strftime("%d.%m.%Y, %H:%M")} Uhr (Zürich)</time> · {len(items)} Meldungen{"" if is_current else ' · <a href="'+prefix+'index.html">Zur aktuellen Ausgabe</a>'}</p>
</div></header>
<main id="main" class="wrap">{sample}
<article class="lead" id="{e(lead["id"])}">{fig(lead.get("image"), "leadfig", True, prefix)}<div class="leadtext">
<p class="kicker">{e(lead["section"])}</p><h2>{e(lead["headline"])}</h2><p class="dek">{e(lead["summary"])}</p>{sources(lead)}</div></article>
<h2 class="sec">Weitere Meldungen</h2><div class="grid">{cards}</div>
{'<h2 class="sec">Bilder dieser Ausgabe</h2><div class="gallery">'+gal+'</div>' if gal else ''}
<nav class="archive" aria-label="Archiv"><h2 class="sec">Archiv – letzte Ausgaben</h2><ul>{arch}</ul></nav>
</main><footer class="wrap foot"><p>Front News erscheint in der Regel morgens und abends; der genaue Zeitpunkt hängt von der Recherche ab.
Bilder sind als Foto (mit Bildnachweis) oder als KI-generierte Illustration gekennzeichnet.</p></footer></body></html>'''

def load_editions():
    os.makedirs(ED_DIR, exist_ok=True); eds = []
    for f in sorted(os.listdir(ED_DIR)):
        if f.endswith(".json"):
            eds.append(json.load(open(os.path.join(ED_DIR, f), encoding="utf-8")))
    eds.sort(key=lambda d: datetime.fromisoformat(d["published_at"]), reverse=True)
    return eds

def flag_value(a, name):
    if name not in a: return None
    i = a.index(name)
    if i + 1 >= len(a) or str(a[i + 1]).startswith("--"):
        raise Invalid(f"{name} braucht einen Pfad")
    return a[i + 1]

def published_editions(eds):
    if PREVIEW: return eds
    return [ed for ed in eds if ed.get("publish", True) is True]

def holding_page():
    return (
        '<!doctype html><html lang="de-CH"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<title>Front News \u2013 bald verf\u00fcgbar</title>\n'
        '<meta name="description" content="Front News: neutrale Nachrichten\u00fcbersicht, zweimal t\u00e4glich. Bald verf\u00fcgbar.">\n'
        '<link rel="stylesheet" href="assets/style.css"></head><body>\n'
        '<a class="skip" href="#main">Zum Inhalt springen</a>\n'
        '<header class="mast"><div class="wrap">\n'
        '<h1>Front News</h1>\n'
        '<p class="meta">Neutrale Nachrichten\u00fcbersicht, zweimal t\u00e4glich</p>\n'
        '</div></header>\n'
        '<main id="main" class="wrap">\n'
        '<div class="sample" role="status"><strong>Front News \u2013 bald verf\u00fcgbar</strong>\n'
        '<p>Die erste Ausgabe ist noch nicht freigegeben. Die Website erscheint in der Regel morgens und abends; der genaue Zeitpunkt h\u00e4ngt von der Recherche ab.</p></div>\n'
        '</main>\n'
        '<footer class="wrap foot"><p>Front News erscheint in der Regel morgens und abends. Bilder sind als Foto (mit Bildnachweis) oder als KI-generierte Illustration gekennzeichnet.</p></footer>\n'
        '</body></html>'
    )

def write_edition_site(tmp, eds):
    archive = eds[:ARCHIVE_N]
    shutil.copytree(os.path.join(ROOT, "assets"), os.path.join(tmp, "assets"))
    os.makedirs(os.path.join(tmp, "archiv"))
    for ed in archive:
        for it in ed["items"]:
            for fm in (it.get("image") or {}).get("variants", {}).values():
                for p in fm.values():
                    os.makedirs(os.path.join(tmp, os.path.dirname(p)), exist_ok=True)
                    shutil.copy2(os.path.join(ROOT, p), os.path.join(tmp, p))
    open(os.path.join(tmp, "index.html"), "w", encoding="utf-8").write(page(eds[0], archive, "", True))
    for ed in archive:
        open(os.path.join(tmp, "archiv", slug(ed) + ".html"), "w", encoding="utf-8").write(page(ed, archive, "../", ed is eds[0]))
    with open(os.path.join(tmp, "edition.json"), "w", encoding="utf-8") as fh: json.dump(eds[0], fh, ensure_ascii=False, indent=1)

def write_holding_site(tmp):
    shutil.copytree(os.path.join(ROOT, "assets"), os.path.join(tmp, "assets"))
    open(os.path.join(tmp, "index.html"), "w", encoding="utf-8").write(holding_page())

def _rm(path):
    if not os.path.lexists(path): return
    if os.path.islink(path) or os.path.isfile(path): os.remove(path)
    else: shutil.rmtree(path)

def materialize_dir(src, dest):
    """Copy src to dest as a real directory, replacing dest atomically when possible."""
    staging = dest + ".staging"
    _rm(staging)
    shutil.copytree(src, staging)
    if os.path.islink(dest) or os.path.isfile(dest):
        os.remove(dest)
    if os.path.isdir(dest):
        old = dest + ".old"
        _rm(old)
        os.replace(dest, old)
        os.replace(staging, dest)
        shutil.rmtree(old, ignore_errors=True)
    else:
        os.replace(staging, dest)

def swap_public_symlink(tmp):
    link = PUBLIC + ".new"
    if os.path.lexists(link): os.remove(link)
    os.symlink(os.path.relpath(tmp, ROOT), link)
    old = os.path.realpath(PUBLIC) if os.path.islink(PUBLIC) else None
    os.replace(link, PUBLIC)
    return old

def build_all(out_rel=None):
    eds = published_editions(load_editions())
    if eds:
        for ed in eds: validate(ed, ROOT, PREVIEW)           # whole published archive must be valid
    os.makedirs(REL_DIR, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="build-", dir=REL_DIR); os.chmod(tmp, 0o755)
    try:
        if eds: write_edition_site(tmp, eds)
        else: write_holding_site(tmp)
        if out_rel:
            dest = out_rel if os.path.isabs(out_rel) else os.path.join(ROOT, out_rel)
            materialize_dir(tmp, dest)
            old = None
            live = dest
        else:
            old = swap_public_symlink(tmp)
            live = tmp
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True); raise
    keep = {os.path.realpath(tmp)}
    if old: keep.add(old)
    if not out_rel: keep.add(os.path.realpath(PUBLIC))
    for d in sorted(os.listdir(REL_DIR))[:-2]:
        p = os.path.join(REL_DIR, d)
        if p not in keep: shutil.rmtree(p, ignore_errors=True)
    return live

def main(a):
    mapping = json.load(open(a[a.index("--map")+1])) if "--map" in a else None
    out_rel = flag_value(a, "--out")
    cmd = a[0] if a and not str(a[0]).startswith("--") else "rebuild"
    try:
        if cmd in ("validate", "publish"):
            ed = adapt(json.load(open(a[1], encoding="utf-8")), mapping)
            if "--manifest" in a:
                ed = merge_manifest(ed, a[a.index("--manifest")+1], os.path.join(ROOT, "image-notes.json"))
            ed = validate(ed, ROOT, PREVIEW)
            print("OK:", ed["edition"], ed["published_at"], len(ed["items"]), "Meldungen")
            if cmd == "publish":
                dst = os.path.join(ED_DIR, slug(ed) + ".json")
                tmpf = dst + ".tmp"
                os.makedirs(ED_DIR, exist_ok=True)
                with open(tmpf, "w", encoding="utf-8") as fh: json.dump(ed, fh, ensure_ascii=False, indent=1)
                os.replace(tmpf, dst)
                try: print("Live:", build_all(out_rel))
                except Exception:
                    os.remove(dst); raise
        else:
            print("Live:", build_all(out_rel))
    except (Invalid, ValueError, KeyError, json.JSONDecodeError) as ex:
        print("ABGELEHNT – letzte gültige Ausgabe bleibt live:\n" + str(ex), file=sys.stderr); sys.exit(2)

if __name__ == "__main__": main(sys.argv[1:])
