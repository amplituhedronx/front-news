# Front News publisher (not enabled)

This describes the local publishing setup. It is not turned on. No routine or cron was created, nothing was pushed, and no edition with `publish: true` was written into the live repository.

## What it does

`publish.py` looks at a handoff directory (default `/workspace/front-news-handoff`) for completed editorial artifacts:

```
incoming/<YYYY-MM-DD-morning|evening>/rNN.json
incoming/<YYYY-MM-DD-morning|evening>/rNN.READY
approvals/<YYYY-MM-DD-morning|evening>-rNN.APPROVED
state/published.json          # last successful publish (publisher-owned)
state/receipts/<id>-rNN.json  # deploy receipt after a successful push
state/publisher.lock          # exclusive lock
```

A `READY` file must be written last and must contain the SHA-256 of the JSON file bytes (`sha256:<hex>` or a small JSON object with a `sha256` field). Only the whole-file sha256 counts; `content_hash`/`hash` aliases are rejected. `content_hash` is used only for duplicate detection. The JSON must also carry a `handoff` object whose `content_hash` covers every top-level field except `handoff`, with sorted keys.

The publisher then:

1. Takes the newest complete artifact.
2. Checks `READY` against the file hash and `handoff.content_hash` against the payload.
3. Checks approval (see modes below).
4. Rejects stale artifacts (`handoff.research_completed_at` older than 16 hours; re-READYing does not refresh it), duplicates (same `edition_id` + `content_hash` as the durable state), older revisions of the same edition, and out-of-order `edition_id`s.
5. Accepts only handoff files that already carry `publish: true` and never changes their bytes: the file is copied byte-for-byte into `editions/`. Any change needs a new revision, a new READY and (in manual mode) a new approval. It validates with `build.py`: schema, 10–12 items, sections limited to Politik; Wirtschaft; Zürich & Regionen; Gesellschaft; Verkehr & Wetter; Sport, an image with `credit` on every item, and `image_rights` for every item (no skipping). Live (non-dry-run) runs also reject `example: true`.
6. On success, writes `editions/<slug>.json`, commits only that file, and pushes. Render builds from the push (`python3 build.py rebuild --out public`).
7. Only after a successful push does it update `state/published.json` and write a receipt with the git commit and `went_live_at`.

Any failed check exits with code 2, writes a single `PUBLISHER REJECT` log line, and leaves the last good live site unchanged. A duplicate or empty inbox is a `NOOP` with exit 0.

## How to run

```
python3 publish.py --dry-run
python3 publish.py --dry-run --mode manual --handoff-root /path/to/handoff --repo /path/to/site --now 2026-11-08T08:00:00+01:00 --render-out /tmp/fn-render
python3 publish.py --mode manual
python3 publish.py --mode auto
```

`--dry-run` runs every check and can rebuild a preview into `--render-out`. It does not write `editions/` in the site repo, does not commit or push, does not update publisher state, and does not write an auto-approval file.

Default mode is `auto` (chosen by Martin). Use `--mode manual` to require approval files. Default handoff root is `/workspace/front-news-handoff`. Tests live in `tests/test_publisher.py`.

Full handoff format, including the media provenance manifest (`handoff.media`): see `HANDOFF-CONTRACT.md` (also at `/workspace/front-news-handoff/CONTRACT.md`). Validate a file with `python3 build.py validate --handoff <file>`. Manifest images are copied to `assets/editions/<edition_id>/` and committed with the edition.

## Approval modes

Live (non-dry-run) publishes reject any edition with `example: true`; examples are only allowed in `--dry-run`.

- `manual` (opt-in via `--mode manual`): an approval file must exist and must be JSON `{"sha256": <whole-file sha256>, "edition_id": …, "revision": N, …}` matching this exact artifact; a bare hash or a content_hash is rejected. Editorial readiness is not approval.
- `auto` (default): an edition that passes every other check is treated as approved. On a real (not dry-run) publish the publisher writes a receipt with `"kind": "policy_validation"` (bound to sha256, edition_id and revision). That records automatic policy validation, not human approval. An existing approval file is still checked for its binding.

## Proposed trigger (not created)

A routine, not a host cron, running in `Europe/Zurich`:

- Idempotent runs at 07:20, 07:50, 08:30, 18:20, 18:50 and 19:30 Europe/Zurich: `python3 publish.py` (default auto mode) from this repo, with the agreed handoff root.
- Each run is a cheap no-op (exit 0) when nothing is READY or the newest READY revision is already published. It never triggers research; it only reads the handoff directory. A rejected artifact leaves the last good edition live, and a later run picks up a corrected new revision.

The publisher never goes by the clock alone: it only accepts an artifact that is complete, hashed, within 16 hours of `research_completed_at`, and passes every check. Nothing should invoke `publish.py` until this setup has been reviewed and explicitly enabled.

## What is still waiting

- What to do on a quiet day with fewer than 10 verified items (this setup refuses to pad or publish; the site keeps the last good edition).
- Generation and licensing metadata for the existing illustrations, before any of them go on the public site.
