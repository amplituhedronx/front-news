#!/usr/bin/env python3
"""Front News publisher: consume editorial handoff artifacts and publish to the site repo.

Stdlib only. Default approval mode is 'auto' (Martin's choice); --mode manual
requires a hash-bound approval file. Live publishes reject example: true. --dry-run does every check
and can render a preview, but does not write editions/, commit, push, or
update durable publisher state.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ZRH = ZoneInfo("Europe/Zurich")
STALE_AFTER = timedelta(hours=14)
EDITION_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(morning|evening)$")
REV_FILE_RE = re.compile(r"^(r(\d{2}))\.json$")
DEFAULT_HANDOFF = "/workspace/front-news-handoff"


class Rejected(Exception):
    def __init__(self, reason, detail="", edition_id=None, revision=None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason
        self.edition_id = edition_id
        self.revision = revision


class Noop(Exception):
    def __init__(self, reason, detail="", edition_id=None, revision=None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail or reason
        self.edition_id = edition_id
        self.revision = revision


def log(status, reason, edition_id=None, revision=None, extra=""):
    now = datetime.now(ZRH).isoformat(timespec="seconds")
    bits = [now, "PUBLISHER", status, f"reason={reason}"]
    if edition_id:
        bits.append(f"edition_id={edition_id}")
    if revision is not None:
        bits.append(f"revision={revision}")
    if extra:
        bits.append(extra)
    print(" ".join(bits), flush=True)


def load_build(repo):
    repo = os.path.abspath(repo)
    os.environ["FRONTNEWS_ROOT"] = repo
    if repo not in sys.path:
        sys.path.insert(0, repo)
    sys.modules.pop("build", None)
    import build
    return build


def normalize_hash(value):
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("sha256") or value.get("content_hash") or value.get("hash")
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            return normalize_hash(json.loads(text))
        except json.JSONDecodeError:
            return None
    if text.lower().startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    text = text.split()[0] if text.split() else text
    if re.fullmatch(r"[0-9a-fA-F]{64}", text):
        return text.lower()
    return None


def read_hash_file(path):
    if not os.path.isfile(path):
        return None
    return normalize_hash(open(path, encoding="utf-8").read())


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_now(value):
    if not value:
        return datetime.now(ZRH)
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZRH)
    return ts


def edition_sort_key(edition_id):
    m = EDITION_ID_RE.match(edition_id or "")
    if not m:
        return ("", 0)
    return (m.group(1), 0 if m.group(2) == "morning" else 1)


def write_json_atomic(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def write_text_atomic(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_state(path):
    if not os.path.isfile(path):
        return None
    return json.load(open(path, encoding="utf-8"))


def acquire_lock(handoff_root):
    lock_dir = os.path.join(handoff_root, "state")
    os.makedirs(lock_dir, exist_ok=True)
    path = os.path.join(lock_dir, "publisher.lock")
    fh = open(path, "a+")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise Rejected("locked", "publisher lock is already held")
    return fh


def list_json_artifacts(incoming):
    found = []
    if not os.path.isdir(incoming):
        return found
    for edition_id in sorted(os.listdir(incoming)):
        folder = os.path.join(incoming, edition_id)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            m = REV_FILE_RE.match(name)
            if not m:
                continue
            rev_token, rev_s = m.group(1), m.group(2)
            found.append({
                "edition_id": edition_id,
                "revision": int(rev_s),
                "rev_token": rev_token,
                "json_path": os.path.join(folder, name),
                "ready_path": os.path.join(folder, rev_token + ".READY"),
            })
    found.sort(key=lambda a: (edition_sort_key(a["edition_id"]), a["revision"]))
    return found


def approval_path(handoff_root, edition_id, rev_token):
    return os.path.join(handoff_root, "approvals", f"{edition_id}-{rev_token}.APPROVED")


def select_candidate(artifacts):
    if not artifacts:
        raise Noop("no_candidate", "no incoming edition JSON found")
    complete = [a for a in artifacts if os.path.isfile(a["ready_path"])]
    if not complete:
        latest = artifacts[-1]
        raise Rejected("missing_ready", "READY file is missing; artifact is incomplete",
                       latest["edition_id"], latest["revision"])
    return complete[-1]


def inspect_candidate(art, handoff_root, mode, now, state, build):
    edition_id, revision = art["edition_id"], art["revision"]
    digest = file_sha256(art["json_path"])
    ready_hash = read_hash_file(art["ready_path"])
    if ready_hash != digest:
        raise Rejected("hash_mismatch", "READY hash does not match the JSON file",
                       edition_id, revision)
    try:
        ed = json.load(open(art["json_path"], encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise Rejected("invalid_json", str(ex), edition_id, revision)
    handoff = ed.get("handoff") if isinstance(ed.get("handoff"), dict) else {}
    declared_id = handoff.get("edition_id")
    declared_rev = handoff.get("revision")
    if declared_id != edition_id:
        raise Rejected("edition_id_mismatch",
                       f"directory {edition_id} != handoff.edition_id {declared_id!r}",
                       edition_id, revision)
    if declared_rev != revision:
        raise Rejected("revision_mismatch",
                       f"filename revision {revision} != handoff.revision {declared_rev!r}",
                       edition_id, revision)
    expected_content = build.content_payload_hash(ed)
    got_content = str(handoff.get("content_hash") or "")
    if got_content.lower() != expected_content.lower():
        raise Rejected("content_hash_mismatch",
                       f"handoff.content_hash does not match payload (expected {expected_content})",
                       edition_id, revision)
    try:
        ready_at = datetime.fromisoformat(str(handoff.get("ready_at")))
        if ready_at.tzinfo is None:
            raise ValueError("ready_at missing offset")
    except (TypeError, ValueError):
        raise Rejected("invalid_ready_at", "handoff.ready_at is not an ISO-8601 timestamp with offset",
                       edition_id, revision)
    if now - ready_at > STALE_AFTER:
        raise Rejected("stale", f"ready_at {ready_at.isoformat()} is older than {int(STALE_AFTER.total_seconds()//3600)}h",
                       edition_id, revision)

    if state:
        prev_id = state.get("edition_id")
        prev_rev = state.get("revision")
        prev_hash = str(state.get("content_hash") or "")
        if prev_id == edition_id and prev_hash.lower() == got_content.lower():
            raise Noop("duplicate", "same edition_id and content_hash as last published",
                       edition_id, revision)
        if prev_id == edition_id and isinstance(prev_rev, int):
            if revision < prev_rev:
                raise Noop("older_revision", f"revision {revision} is lower than published {prev_rev}",
                           edition_id, revision)
            if revision == prev_rev:
                raise Noop("duplicate", "same edition_id and revision as last published",
                           edition_id, revision)
        if edition_sort_key(edition_id) < edition_sort_key(prev_id or ""):
            raise Rejected("out_of_order",
                           f"{edition_id} is older than last published {prev_id}",
                           edition_id, revision)

    approved = approval_path(handoff_root, edition_id, art["rev_token"])
    appr_hash = read_hash_file(approved)
    if mode == "manual":
        if not os.path.isfile(approved):
            raise Rejected("unapproved", "manual mode requires a hash-bound approval file",
                           edition_id, revision)
        if appr_hash not in {digest, normalize_hash(got_content)}:
            raise Rejected("approval_hash_mismatch",
                           "approval file hash does not match this artifact",
                           edition_id, revision)
    return ed, digest, got_content, approved


def copy_for_publish(ed):
    out = json.loads(json.dumps(ed))
    out["publish"] = True
    return out


def render_preview(build_mod, repo, published, dest):
    tmp = dest or tempfile.mkdtemp(prefix="fn-publish-render-")
    os.makedirs(tmp, exist_ok=True)
    assets_src = os.path.join(repo, "assets")
    assets_dst = os.path.join(tmp, "assets")
    if os.path.isdir(assets_dst):
        shutil.rmtree(assets_dst)
    shutil.copytree(assets_src, assets_dst)
    ed_dir = os.path.join(tmp, "editions")
    os.makedirs(ed_dir, exist_ok=True)
    slug = build_mod.slug(published)
    write_json_atomic(os.path.join(ed_dir, slug + ".json"), published)
    env = os.environ.copy()
    env["FRONTNEWS_ROOT"] = tmp
    cmd = [sys.executable, os.path.join(repo, "build.py"), "rebuild", "--out", "public"]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Rejected("render_failed", (proc.stderr or proc.stdout or "rebuild failed").strip(),
                       published.get("handoff", {}).get("edition_id"),
                       published.get("handoff", {}).get("revision"))
    index = os.path.join(tmp, "public", "index.html")
    return tmp, index


def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo, *args], check=check,
                          capture_output=True, text=True)


def publish_to_repo(repo, published, slug, message):
    dest = os.path.join(repo, "editions", slug + ".json")
    existed = os.path.isfile(dest)
    previous = open(dest, encoding="utf-8").read() if existed else None
    write_json_atomic(dest, published)
    rel = os.path.join("editions", slug + ".json")
    try:
        git(repo, "add", "--", rel)
        git(repo, "commit", "-m", message, "--", rel)
    except subprocess.CalledProcessError as ex:
        if existed and previous is not None:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(previous)
        elif os.path.isfile(dest):
            os.remove(dest)
        err = (ex.stderr or ex.stdout or str(ex)).strip()
        raise Rejected("git_commit_failed", err,
                       published.get("handoff", {}).get("edition_id"),
                       published.get("handoff", {}).get("revision"))
    commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    try:
        git(repo, "push")
    except subprocess.CalledProcessError as ex:
        git(repo, "reset", "--mixed", "HEAD~1", check=False)
        if existed and previous is not None:
            with open(dest, "w", encoding="utf-8") as fh:
                fh.write(previous)
            git(repo, "checkout", "--", rel, check=False)
        elif os.path.isfile(dest):
            os.remove(dest)
        err = (ex.stderr or ex.stdout or str(ex)).strip()
        raise Rejected("git_push_failed", err,
                       published.get("handoff", {}).get("edition_id"),
                       published.get("handoff", {}).get("revision"))
    return commit


def write_auto_approval(path, digest, now):
    payload = {
        "sha256": digest,
        "mode": "auto",
        "authorized_at": now.isoformat(timespec="seconds"),
        "authorized_by": "publisher-auto",
    }
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def run(args):
    repo = os.path.abspath(args.repo)
    handoff_root = os.path.abspath(args.handoff_root)
    mode = args.mode
    now = parse_now(args.now)
    incoming = os.path.join(handoff_root, "incoming")
    state_path = os.path.join(handoff_root, "state", "published.json")
    lock = acquire_lock(handoff_root)
    try:
        build = load_build(repo)
        artifacts = list_json_artifacts(incoming)
        art = select_candidate(artifacts)
        state = load_state(state_path)
        ed, digest, content_hash, approved = inspect_candidate(
            art, handoff_root, mode, now, state, build)
        published = copy_for_publish(ed)
        try:
            build.validate(published, repo, preview=False, require_handoff=True)
        except build.Invalid as ex:
            raise Rejected("invalid_edition", str(ex), art["edition_id"], art["revision"])
        render_dir = None
        index = None
        if args.dry_run or args.render_out:
            render_dir, index = render_preview(build, repo, published, args.render_out)
        if args.dry_run:
            extra = f"file_sha256={digest}"
            if index:
                extra += f" render={index}"
            log("DRY_RUN", "ok", art["edition_id"], art["revision"], extra)
            if index:
                print(f"DRY_RUN_RENDER {index}")
            return 0

        if published.get("example"):
            raise Rejected("example_content",
                           "example: true editions may only be used with --dry-run",
                           art["edition_id"], art["revision"])

        if mode == "auto" and not os.path.isfile(approved):
            write_auto_approval(approved, digest, now)

        slug = build.slug(published)
        message = f"Publish Front News edition {art['edition_id']} {art['rev_token']}"
        commit = publish_to_repo(repo, published, slug, message)
        went_live = datetime.now(ZRH)
        record = {
            "edition_id": art["edition_id"],
            "revision": art["revision"],
            "content_hash": content_hash,
            "file_sha256": digest,
            "slug": slug,
            "commit": commit,
            "went_live_at": went_live.isoformat(timespec="seconds"),
            "mode": mode,
        }
        write_json_atomic(state_path, record)
        receipt = os.path.join(handoff_root, "state", "receipts",
                               f"{art['edition_id']}-{art['rev_token']}.json")
        write_json_atomic(receipt, record)
        log("OK", "published", art["edition_id"], art["revision"],
            f"commit={commit} file_sha256={digest} receipt={receipt}")
        return 0
    finally:
        lock.close()


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(description="Publish a completed Front News edition to the static site.")
    p.add_argument("--dry-run", action="store_true",
                   help="Run every check and optional render; do not commit, push, or update state")
    p.add_argument("--mode", choices=("manual", "auto"), default="auto",
                   help="Approval mode (default: auto)")
    p.add_argument("--handoff-root", default=os.environ.get("FRONTNEWS_HANDOFF") or DEFAULT_HANDOFF)
    p.add_argument("--repo", default=os.environ.get("FRONTNEWS_ROOT") or here)
    p.add_argument("--now", help="Override current time (ISO-8601) for stale/order checks")
    p.add_argument("--render-out", help="Write a rebuilt site preview into this directory")
    args = p.parse_args(argv)
    try:
        return run(args)
    except Noop as ex:
        log("NOOP", ex.reason, ex.edition_id, ex.revision, ex.detail)
        return 0
    except Rejected as ex:
        log("REJECT", ex.reason, ex.edition_id, ex.revision, ex.detail)
        return 2
    except Exception as ex:
        log("REJECT", "error", extra=str(ex))
        return 2


if __name__ == "__main__":
    sys.exit(main())
