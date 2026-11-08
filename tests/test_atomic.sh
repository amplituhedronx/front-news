#!/usr/bin/env bash
# Publishes 4 good example editions, then a bad one; asserts live site is untouched and archive has >=4 entries.
set -u; cd "$(dirname "$0")/.."; SITE=$PWD
T=$(mktemp -d); cp -r assets "$T/"; export FRONTNEWS_ROOT=$T
mk(){ python3 -c "import json,sys;d=json.load(open('$SITE/sample-edition.json'));d['published_at']=sys.argv[1];d['edition']=sys.argv[2];json.dump(d,open(sys.argv[3],'w'))" "$@"; }
n=0; for ts in 2026-11-06T07:02:00+01:00:morning 2026-11-06T18:05:00+01:00:evening 2026-11-07T06:51:00+01:00:morning 2026-11-07T18:20:00+01:00:evening; do
  mk "${ts%:*}" "${ts##*:}" "$T/in$n.json"; python3 build.py publish "$T/in$n.json" >/dev/null || { echo FAIL good publish; exit 1; }; n=$((n+1)); done
before=$(readlink "$T/public"); hb=$(sha256sum "$T/public/index.html"); eb=$(ls "$T/editions")
# bad edition: 9 items, one without sources, missing image
python3 -c "import json;d=json.load(open('$SITE/sample-edition.json'));d['items']=d['items'][:9];d['items'][1]['sources']=[];d['items'][0]['image']['src']='assets/img/fehlt.svg';d['published_at']='2026-11-08T07:00:00+01:00';json.dump(d,open('$T/bad.json','w'))"
python3 build.py publish "$T/bad.json"; rc=$?
[ $rc -ne 0 ] || { echo "FAIL: bad edition accepted"; exit 1; }
[ "$before" = "$(readlink "$T/public")" ] && [ "$hb" = "$(sha256sum "$T/public/index.html")" ] && [ "$eb" = "$(ls "$T/editions")" ] || { echo "FAIL: live site changed"; exit 1; }
c=$(grep -o 'archiv/2026' "$T/public/index.html" | wc -l); [ "$c" -ge 4 ] || { echo "FAIL archive $c"; exit 1; }
# publish:false must be rejected without --preview
python3 build.py validate /workspace/front-news-preview/edition.json >/dev/null 2>&1 && { echo "FAIL: publish:false accepted"; exit 1; }
echo "PASS: bad edition rejected (rc=$rc), live site unchanged, archive entries=$c, publish:false rejected"; rm -rf "$T"
# empty archive -> holding page written to a real --out directory (not a symlink)
H=$(mktemp -d); cp -r assets "$H/"; export FRONTNEWS_ROOT=$H
python3 build.py rebuild --out public >/dev/null || { echo "FAIL: holding rebuild"; exit 1; }
[ -d "$H/public" ] && [ ! -L "$H/public" ] || { echo "FAIL: --out public is not a real directory"; exit 1; }
grep -q "bald verfügbar" "$H/public/index.html" || { echo "FAIL: holding text missing"; exit 1; }
grep -q "ß" "$H/public/index.html" && { echo "FAIL: holding page contains ß"; exit 1; }
grep -E -q "Beispielinhalt|Private Vorschau" "$H/public/index.html" && { echo "FAIL: preview/sample leaked"; exit 1; }
[ ! -e "$H/public/edition.json" ] || { echo "FAIL: holding page should not ship edition.json"; exit 1; }
echo "PASS: holding page in real public/ directory"; rm -rf "$H"
