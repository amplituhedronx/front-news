# Front News handoff contract v1 (enforced)

Directory: `/workspace/front-news-handoff/`

```
incoming/<YYYY-MM-DD-morning|evening>/rNN.json    edition, immutable once READY exists
incoming/<edition_id>/media/<file>                image files for that edition
incoming/<edition_id>/rNN.READY                   written LAST
approvals/<edition_id>-rNN.APPROVED               only needed in --mode manual
state/                                            publisher-owned, do not write
```
Write `rNN.json` as `rNN.json.tmp`, then rename it with `os.replace`. Do the same for media and READY. Revisions only go up. Any change at all needs a new `rNN`, a new READY and, in manual mode, a new approval. The publisher copies files byte-for-byte and never edits them.

## Check before writing READY
```
python3 /workspace/front-news-site/build.py validate --handoff /workspace/front-news-handoff/incoming/<edition_id>/rNN.json
```
`OK: …` with exit code 0 means valid. Exit code 2 prints the errors; in that case don't write READY.
The publisher runs at 07:20, 07:50, 08:30, 18:20, 18:50 and 19:30 Europe/Zurich, and each run is idempotent. If nothing is READY, or the edition is already published, the run does nothing. It never triggers research.

## Schema (schema_version 1.x)
Top level:
- `schema_version` "1.x"
- `edition` "morning"|"evening"
- `published_at` (ISO-8601 with offset, the real editorial completion time)
- `language` "de-CH"
- `publish`: **true** (required in the handoff file; the publisher does not set it)
- `example`: absent or false
- `items`: 10–12 items

Each item:
- `id` (unique)
- `headline`, `summary`: Swiss spelling, no "ß"
- `section`: one of `Politik`, `Wirtschaft`, `Zürich & Regionen`, `Gesellschaft`, `Verkehr & Wetter`, `Sport`
- `lead` (optional bool)
- `sources`: at least 1, each `{name, url (http/https), published_at|updated_at (ISO with offset or null)}`
- `image` (**required**): `{type: "illustration"|"photo", src: "assets/editions/<edition_id>/<file>", alt, caption, credit, status: "ready"}`

`handoff`:
```json
{
  "edition_id": "2026-11-08-morning",
  "revision": 1,
  "research_completed_at": "2026-11-08T07:05:00+01:00",
  "ready_at": "2026-11-08T07:12:00+01:00",
  "content_hash": "sha256:<64 hex>",
  "image_rights": {"<item id>": {"rights": "ai_generated_own"}
                   /* or {"rights":"licensed","license","license_url","attribution","verified_at"} */},
  "media": { "<item id>": <provenance entry> }
}
```
`edition_id` has to match the folder name and the `edition` kind, and its date has to match the Zurich date of `published_at`. `revision` has to match `rNN`. An edition is stale and rejected once `research_completed_at` is more than 16 h old.

## content_hash
`"sha256:" + sha256(json.dumps({every top-level key except "handoff"}, ensure_ascii=False, sort_keys=True, separators=(",",":")).encode("utf-8")).hexdigest()`
This is the same as `build.content_payload_hash`. It is only used to detect duplicates.

## READY
Either `sha256:<64 hex>` or `{"sha256":"<64 hex>"}`, where the hash is the sha256 of the exact bytes of `rNN.json`. No other keys or aliases are accepted.

## APPROVED (manual mode only)
`{"sha256":"<file sha256>","edition_id":"…","revision":N,"authorized_by":"…","authorized_at":"…"}`. In auto mode (the default) the publisher writes this file itself with `"kind":"policy_validation"`. That marks an automatic policy check, not a human approval.

## Media provenance manifest (`handoff.media`)
There is exactly one entry per item, keyed by item id:
```json
{
  "path": "media/<file>.svg|png|jpg|jpeg|webp",
  "sha256": "<64 hex of the file bytes>",
  "generator": "<tool>", "model": "<model>",
  "license": "<licence>",
  "created_at": "2026-11-08T06:55:00+01:00",
  "credit": "<identical to items[].image.credit>",
  "image_rights": "<identical to handoff.image_rights[id].rights>"
}
```
- `generator` and `model` are required when `image_rights` is `ai_generated_own`.
- `license` is required when it is `licensed`.
- `path` is relative to the edition folder `incoming/<edition_id>/`.
- The publisher copies each file to `assets/editions/<edition_id>/<file>` in the site, so `items[].image.src` must be exactly that path.
- The validator checks that each file exists and that its sha256 matches.
- Photos taken from news sites are not allowed.
