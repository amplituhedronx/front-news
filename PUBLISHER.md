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

A `READY` file must be written last and must contain the SHA-256 of the JSON file bytes (`sha256:<hex>` or a small JSON object with a `sha256` field). The JSON must also carry a `handoff` object whose `content_hash` covers every top-level field except `handoff`, with sorted keys.

The publisher then:

1. Takes the newest complete artifact.
2. Checks `READY` against the file hash and `handoff.content_hash` against the payload.
3. Checks approval (see modes below).
4. Rejects stale artifacts (`ready_at` older than 14 hours), duplicates (same `edition_id` + `content_hash` as the durable state), older revisions of the same edition, and out-of-order `edition_id`s.
5. Copies the edition, sets `publish: true` on that copy only, and validates it with `build.py` (schema, 10–12 items, `image.credit`, and handoff provenance / `image_rights`).
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

Default mode is `manual`. Default handoff root is `/workspace/front-news-handoff`. Tests live in `tests/test_publisher.py`.

## Approval modes

- `manual` (default, until a mode is chosen): an approval file must exist and must be bound to this artifact’s file hash (or its `content_hash`). Editorial readiness is not approval.
- `auto`: an edition that passes every other check is treated as approved. On a real (not dry-run) publish the publisher writes the hash-bound approval file itself.

## Proposed trigger (not created)

A routine, not a host cron, running in `Europe/Zurich`:

- About 07:20 and 18:20: `python3 publish.py --mode <manual|auto>` from this repo, with the agreed handoff root.
- A fallback re-check about 40 minutes later (about 08:00 and 19:00) for editions that finished a bit after the first pass.

Those times sit after the usual editorial window (work often starts around 06:46 / 17:46) without going by the clock alone: the publisher still only accepts an artifact that is complete, hashed, within the 14-hour freshness window, and approved. Nothing should invoke `publish.py` until this setup has been reviewed and explicitly enabled.

## What is still waiting

- Manual vs automatic approval.
- What to do on a quiet day with fewer than 10 verified items (this setup refuses to pad or publish; the site keeps the last good edition).
- Generation and licensing metadata for the existing illustrations, before any of them go on the public site.
