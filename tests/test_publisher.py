#!/usr/bin/env python3
"""Publisher guards: good path plus the rejection cases from the handoff contract."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SITE = Path(__file__).resolve().parents[1]
EXAMPLE = Path("/workspace/front-news-preview/edition-v1-example.PROPOSED.json")
ZRH = ZoneInfo("Europe/Zurich")
NOW = datetime(2026, 11, 8, 8, 0, tzinfo=ZRH)


def run_pub(repo, handoff, extra=None, mode="manual", now=NOW):
    cmd = [
        sys.executable, str(SITE / "publish.py"),
        "--repo", str(repo),
        "--handoff-root", str(handoff),
        "--mode", mode,
        "--now", now.isoformat(),
    ]
    if extra:
        cmd.extend(extra)
    return subprocess.run(cmd, capture_output=True, text=True)


def payload_hash(ed):
    payload = {k: v for k, v in ed.items() if k != "handoff"}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()


def load_sample():
    return json.loads((SITE / "sample-edition.json").read_text(encoding="utf-8"))


def make_edition(edition_id="2026-11-08-morning", revision=1, ready_at=None, n_items=None,
                 drop_credit=False, publish=False):
    ed = load_sample()
    kind = edition_id.rsplit("-", 1)[-1]
    date = edition_id[:10]
    ed["edition"] = kind
    ed["publish"] = publish
    ed.pop("example", None)  # synthetic test editions are not example content
    ed["published_at"] = f"{date}T07:10:00+01:00" if kind == "morning" else f"{date}T18:10:00+01:00"
    if n_items is not None:
        ed["items"] = ed["items"][:n_items]
    if drop_credit:
        img = ed["items"][0].get("image") or {}
        img.pop("credit", None)
        ed["items"][0]["image"] = img
    rights = {}
    for it in ed["items"]:
        if it.get("image"):
            rights[it["id"]] = {"rights": "ai_generated_own", "generated": True, "license": None}
    ready = ready_at or ed["published_at"]
    ed["handoff"] = {
        "contract": "PROPOSED-v1",
        "edition_id": edition_id,
        "revision": revision,
        "supersedes": None,
        "hash_scope": "all top-level fields except handoff, sorted keys",
        "research_completed_at": ed["published_at"],
        "ready_at": ready,
        "publication_authorized": False,
        "publication_authorized_at": None,
        "authorized_by": None,
        "image_rights": rights,
    }
    ed["handoff"]["content_hash"] = payload_hash(ed)
    return ed


def write_artifact(handoff, ed, ready=True, good_hash=True, approve=True):
    h = ed["handoff"]
    edition_id = h["edition_id"]
    revision = h["revision"]
    token = f"r{revision:02d}"
    folder = Path(handoff) / "incoming" / edition_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{token}.json"
    path.write_text(json.dumps(ed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if ready:
        ready_hash = digest if good_hash else "0" * 64
        (folder / f"{token}.READY").write_text(f"sha256:{ready_hash}\n", encoding="utf-8")
    if approve:
        appr = Path(handoff) / "approvals"
        appr.mkdir(parents=True, exist_ok=True)
        (appr / f"{edition_id}-{token}.APPROVED").write_text(f"sha256:{digest}\n", encoding="utf-8")
    return path, digest


def write_state(handoff, edition_id, revision, content_hash):
    state_dir = Path(handoff) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "published.json").write_text(json.dumps({
        "edition_id": edition_id,
        "revision": revision,
        "content_hash": content_hash,
    }, indent=2), encoding="utf-8")


def adapt_example(now=NOW):
    ed = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    illu = [f"assets/img/illu{i}.svg" for i in range(1, 7)]
    for i, it in enumerate(ed["items"]):
        im = it.get("image")
        if not im:
            continue
        im["status"] = "ready"
        im["src"] = illu[i % len(illu)]
        if not im.get("credit"):
            im["credit"] = "KI-generiert (Front News)"
    handoff = ed.setdefault("handoff", {})
    handoff["ready_at"] = ed.get("published_at") or now.isoformat()
    handoff["ready_note"] = "adapted in a temp copy so normal validation can pass"
    for it in ed["items"]:
        if not it.get("image"):
            continue
        info = (handoff.get("image_rights") or {}).setdefault(it["id"], {})
        info.setdefault("rights", "ai_generated_own")
    handoff["content_hash"] = payload_hash(ed)
    return ed


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fn-pub-test-"))
        self.handoff = self.tmp / "handoff"
        self.repo = self.tmp / "repo"
        shutil.copytree(SITE / "assets", self.repo / "assets")
        shutil.copy2(SITE / "build.py", self.repo / "build.py")
        shutil.copy2(SITE / "publish.py", self.repo / "publish.py")
        (self.repo / "editions").mkdir()
        (self.repo / "editions" / ".gitkeep").write_text("", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_good_edition_dry_run_renders(self):
        ed = make_edition()
        write_artifact(self.handoff, ed)
        render = self.tmp / "render"
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run", "--render-out", str(render)])
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertIn("reason=ok", proc.stdout)
        index = render / "public" / "index.html"
        self.assertTrue(index.is_file())
        html = index.read_text(encoding="utf-8")
        self.assertIn(ed["items"][0]["headline"], html)
        self.assertNotIn("bald verfügbar", html)
        self.assertFalse(list((self.repo / "editions").glob("*.json")),
                         "dry-run must not write editions/ in the repo")

    def test_missing_ready(self):
        write_artifact(self.handoff, make_edition(), ready=False)
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run"])
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("reason=missing_ready", proc.stdout)

    def test_hash_mismatch(self):
        write_artifact(self.handoff, make_edition(), good_hash=False)
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run"])
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("reason=hash_mismatch", proc.stdout)

    def test_unapproved_manual(self):
        write_artifact(self.handoff, make_edition(), approve=False)
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run"], mode="manual")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("reason=unapproved", proc.stdout)

    def test_duplicate(self):
        ed = make_edition()
        write_artifact(self.handoff, ed)
        write_state(self.handoff, "2026-11-08-morning", 1, ed["handoff"]["content_hash"])
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run"])
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("reason=duplicate", proc.stdout)

    def test_out_of_order(self):
        ed = make_edition("2026-11-08-morning", 1)
        write_artifact(self.handoff, ed)
        write_state(self.handoff, "2026-11-08-evening", 1, "sha256:" + "ab" * 32)
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run"])
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("reason=out_of_order", proc.stdout)

    def test_stale(self):
        stale_at = (NOW - timedelta(hours=15)).isoformat()
        ed = make_edition(ready_at=stale_at)
        write_artifact(self.handoff, ed)
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run"])
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("reason=stale", proc.stdout)

    def test_under_ten_items(self):
        ed = make_edition(n_items=9)
        write_artifact(self.handoff, ed)
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run"])
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("reason=invalid_edition", proc.stdout)
        self.assertIn("10–12", proc.stdout)

    def test_missing_image_credit(self):
        ed = make_edition(drop_credit=True)
        write_artifact(self.handoff, ed)
        proc = run_pub(self.repo, self.handoff, extra=["--dry-run"])
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("reason=invalid_edition", proc.stdout)
        self.assertIn("credit", proc.stdout)

    def test_auto_mode_does_not_need_prior_approval(self):
        write_artifact(self.handoff, make_edition(), approve=False)
        render = self.tmp / "auto-render"
        proc = run_pub(self.repo, self.handoff,
                       extra=["--dry-run", "--render-out", str(render)], mode="auto")
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        self.assertFalse((self.handoff / "approvals").exists() or
                         any((self.handoff / "approvals").glob("*") if (self.handoff / "approvals").exists() else []))


    def test_default_mode_is_auto(self):
        write_artifact(self.handoff, make_edition(), approve=False)
        cmd = [sys.executable, str(SITE / "publish.py"), "--repo", str(self.repo),
               "--handoff-root", str(self.handoff), "--now", NOW.isoformat(), "--dry-run"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

    def test_live_publish_rejects_example(self):
        ed = make_edition()
        ed["example"] = True
        ed["handoff"]["content_hash"] = payload_hash(ed)
        write_artifact(self.handoff, ed)
        proc = run_pub(self.repo, self.handoff, mode="auto")
        self.assertEqual(proc.returncode, 2, proc.stderr + proc.stdout)
        self.assertIn("reason=example_content", proc.stdout)
        self.assertEqual([p.name for p in (self.repo / "editions").iterdir()], [".gitkeep"])
        self.assertFalse((self.handoff / "state" / "published.json").exists())


class ExampleDryRun(unittest.TestCase):
    def test_adapted_front_news_example_renders(self):
        tmp = Path(tempfile.mkdtemp(prefix="fn-example-dry-"))
        try:
            handoff = tmp / "handoff"
            repo = tmp / "repo"
            shutil.copytree(SITE / "assets", repo / "assets")
            shutil.copy2(SITE / "build.py", repo / "build.py")
            shutil.copy2(SITE / "publish.py", repo / "publish.py")
            (repo / "editions").mkdir()
            ed = adapt_example()
            write_artifact(handoff, ed, approve=True)
            render = tmp / "render"
            proc = run_pub(repo, handoff, extra=["--dry-run", "--render-out", str(render)])
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            html = (render / "public" / "index.html").read_text(encoding="utf-8")
            self.assertIn("Thomas Aeschi steigt ins Rennen um Parmelins Nachfolge", html)
            self.assertIn("YB gewinnt in Luzern dank spätem Tor", html)
            self.assertNotIn("bald verfügbar", html)
            print("EXAMPLE_DRY_RUN_OK", render / "public" / "index.html")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LivePublishToLocalRemote(unittest.TestCase):
    def test_commit_and_push_to_local_remote_only(self):
        tmp = Path(tempfile.mkdtemp(prefix="fn-pub-git-"))
        try:
            remote = tmp / "remote.git"
            repo = tmp / "repo"
            handoff = tmp / "handoff"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            shutil.copytree(SITE / "assets", repo / "assets")
            shutil.copy2(SITE / "build.py", repo / "build.py")
            shutil.copy2(SITE / "publish.py", repo / "publish.py")
            (repo / "editions").mkdir()
            (repo / "editions" / ".gitkeep").write_text("", encoding="utf-8")
            subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "publisher-test@example.invalid"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Publisher Test"],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", "seed"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)],
                           check=True, capture_output=True)
            subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "main"],
                           check=True, capture_output=True)
            ed = make_edition()
            write_artifact(handoff, ed)
            proc = run_pub(repo, handoff)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertIn("reason=published", proc.stdout)
            published = json.loads(next((repo / "editions").glob("*.json")).read_text(encoding="utf-8"))
            self.assertIs(published["publish"], True)
            log = subprocess.run(["git", "-C", str(remote), "log", "-1", "--oneline", "main"],
                                 capture_output=True, text=True, check=True)
            self.assertIn("Publish Front News edition 2026-11-08-morning", log.stdout)
            self.assertTrue((handoff / "state" / "published.json").is_file())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
