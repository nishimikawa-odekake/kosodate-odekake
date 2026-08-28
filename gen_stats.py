#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stats.json 生成スクリプト v2（単一ソース / by_pref + status 対応）

v1 からの変更点:
  - 集計元をローカルの pages/{slug}/index.html（正データ）に変更。--source remote で従来どおり raw から取得も可能。
  - by_pref（46県の local / chain / total / status）を追加。
  - status は maturity_rules.json の多基準スコア（満点8, 完成>=6）で判定。
  - 愛知は別リポジトリのため aichi_fixed.json の固定値を使用。
  - --verify で本番 raw と件数を突合し差分を報告（生成物はローカル正のまま）。

既存トップレベルキー（aichi / national / prefs / total / updated）は v1 互換。

使い方:
  python3 gen_stats.py --pages ./pages -o stats.json
  python3 gen_stats.py --pages ./pages --verify
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.request

NAT_REPO_RAW = "https://raw.githubusercontent.com/nishimikawa-odekake/kosodate-odekake/main"

PREFS = [
    "akita", "aomori", "chiba", "ehime", "fukui", "fukuoka", "fukushima", "gifu",
    "gunma", "hiroshima", "hokkaido", "hyogo", "ibaraki", "ishikawa", "iwate",
    "kagawa", "kagoshima", "kanagawa", "kochi", "kumamoto", "kyoto", "mie",
    "miyagi", "miyazaki", "nagano", "nagasaki", "nara", "niigata", "oita",
    "okayama", "okinawa", "osaka", "saga", "saitama", "shiga", "shimane",
    "shizuoka", "tochigi", "tokushima", "tokyo", "tottori", "toyama",
    "wakayama", "yamagata", "yamaguchi", "yamanashi",
]

SPOTS_RE = re.compile(r"\bSPOTS\s*=\s*\[")
HERE = os.path.dirname(os.path.abspath(__file__))


def fetch(url, timeout=90):
    req = urllib.request.Request(url, headers={"User-Agent": "gen-stats/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_spots(html, label):
    m = SPOTS_RE.search(html)
    if not m:
        raise ValueError("SPOTS array not found: %s" % label)
    start = html.index("[", m.start())
    arr, _ = json.JSONDecoder().raw_decode(html[start:])
    if not isinstance(arr, list):
        raise ValueError("SPOTS is not a list: %s" % label)
    return arr


def tier(rules_tiers, value):
    for threshold, pts in rules_tiers:
        if value >= threshold:
            return pts
    return 0


def score_pref(spots, rules):
    """maturity_rules.json のスコア定義で採点する。"""
    local = [s for s in spots if not s.get("is_chain")]
    jidokan = sum(1 for s in local if s.get("is_jidokan"))
    free = sum(1 for s in local if s.get("is_free") is True)
    food_local = sum(1 for s in local if "food" in (s.get("play_types") or []))
    cats = {}
    for s in local:
        for t in (s.get("play_types") or []):
            if t == "food":
                continue
            cats[t] = cats.get(t, 0) + 1
    cats_ge5 = sum(1 for v in cats.values() if v >= 5)

    c = rules["score_components"]
    detail = {
        "volume": tier(c["volume"]["tiers"], len(local)),
        "jidokan": tier(c["jidokan"]["tiers"], jidokan),
        "category": tier(c["category"]["tiers"], cats_ge5),
        "food": tier(c["food"]["tiers"], food_local),
        "free": tier(c["free"]["tiers"], free),
    }
    total = sum(detail.values())
    if len(local) == 0:
        status = "chain_first"
        total = None
        detail = None
    elif total >= rules["threshold_mature"]:
        status = "mature"
    else:
        status = "growing"
    return status, total, detail, {
        "local": len(local), "jidokan": jidokan, "free": free,
        "food_local": food_local, "cats_ge5": cats_ge5,
    }


def load_html(pages_dir, slug, source):
    if source == "remote":
        return fetch("%s/%s/index.html" % (NAT_REPO_RAW, slug))
    with open(os.path.join(pages_dir, slug, "index.html"), encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------- HTML 同期
JIDOKAN_PAT = re.compile(
    r"(子連れおでかけスポット)([\d,]+)(件と児童館)([\d,]+)(館、子連れチェーン店)([\d,]+)(店)")
CREATOR_PAT = re.compile(
    r"(（おでかけスポット)([\d,]+)(＋児童館)([\d,]+)(館＋子連れチェーン店<span data-stat=\"by_pref:)"
    r"([a-z]+)(:chain\">)([\d,]+)(</span>店）)")


def _fmt(n):
    return "{:,}".format(int(n))


def _ds_sub(html, stats, counted):
    """data-stat のフォールバック値を stats の実値へ書き戻す。"""
    def rep(m):
        key, cur = m.group(2), m.group(3)
        v = stats
        for part in key.split(":"):
            if not isinstance(v, dict) or part not in v:
                return m.group(0)
            v = v[part]
        if not isinstance(v, (int, float)):
            return m.group(0)
        new = _fmt(v)
        if new != cur:
            counted.append((key, cur, new))
        return m.group(1) + new + m.group(4)
    return re.sub(r'(data-stat="([^"]+)"[^>]*>)([\d,]+)(<)', rep, html)


def sync_html(pages_dir, stats, jidokan_map, prefs, dry_run=False):
    """トップページ群の件数ハードコードを stats の実値へ同期する。

    SPOTS 配列は一切触らない（照合パターンが HTML/日本語文言に限定されているため
    JSON 配列内には一致しない）。呼び出し側で SPOTS のバイト同一性を検証すること。
    """
    changes = {}

    def write(path, before, after):
        if before == after:
            return 0
        if not dry_run:
            with open(path, "w", encoding="utf-8") as f:
                f.write(after)
        return 1

    def jidokan_counts(slug):
        b = stats["by_pref"][slug]
        jid = jidokan_map[slug]
        return b["local"] - jid, jid, b["chain"]

    # --- 各県 index / plan / map
    for p in prefs:
        for fn in ("index.html", "plan.html", "map.html"):
            path = os.path.join(pages_dir, p, fn)
            if not os.path.exists(path):
                continue
            html = open(path, encoding="utf-8").read()
            orig = html
            log = []
            spot, jid, chain = jidokan_counts(p)

            def jrep(m, _v=(spot, jid, chain)):
                new = (m.group(1) + str(_v[0]) + m.group(3) + str(_v[1])
                       + m.group(5) + str(_v[2]) + m.group(7))
                if new != m.group(0):
                    log.append(("meta文", m.group(2) + "/" + m.group(4) + "/" + m.group(6),
                                "%d/%d/%d" % _v))
                return new
            html = JIDOKAN_PAT.sub(jrep, html)

            def crep(m, _v=(spot, jid)):
                new = (m.group(1) + str(_v[0]) + m.group(3) + str(_v[1])
                       + "".join(m.group(i) for i in range(5, 10)))
                if new != m.group(0):
                    log.append(("creator行", m.group(2) + "/" + m.group(4), "%d/%d" % _v))
                return new
            html = CREATOR_PAT.sub(crep, html)

            html = _ds_sub(html, stats, log)
            if write(path, orig, html):
                changes["%s/%s" % (p, fn)] = log

    # --- 全国トップ / about / press
    for fn in ("index.html", "about.html", os.path.join("press", "index.html")):
        path = os.path.join(pages_dir, fn)
        if not os.path.exists(path):
            continue
        html = open(path, encoding="utf-8").read()
        orig = html
        log = []
        html = _ds_sub(html, stats, log)
        if fn == "index.html":
            # meta description の「47都道府県N件」
            def mrep(m):
                new = m.group(1) + _fmt(stats["total"]) + m.group(3)
                if new != m.group(0):
                    log.append(("meta総数", m.group(2), _fmt(stats["total"])))
                return new
            html = re.sub(r"(全国47都道府県)([\d,]+)(件を掲載)", mrep, html)

            # og:description の「全国約N.N万件」
            man = "%.1f" % (stats["total"] / 10000.0)

            def orep(m):
                new = m.group(1) + man + m.group(3)
                if new != m.group(0):
                    log.append(("og万件", m.group(2), man))
                return new
            html = re.sub(r"(全国約)([\d.]+)(万件を掲載)", orep, html)
        if write(path, orig, html):
            changes[fn] = log
    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="stats.json")
    ap.add_argument("--pages", default=HERE)
    ap.add_argument("--rules", default=os.path.join(HERE, "maturity_rules.json"))
    ap.add_argument("--aichi", default=os.path.join(HERE, "aichi_fixed.json"))
    ap.add_argument("--source", choices=["local", "remote"], default="local")
    ap.add_argument("--verify", action="store_true",
                    help="本番raw と件数を突合して差分を報告（生成はローカル正）")
    ap.add_argument("--detail-out", default=None, help="スコア内訳のJSON出力先")
    ap.add_argument("--keep-status", action="store_true",
                    help="statusを既存stats.jsonから引き継ぐ（件数だけ更新したいとき）")
    ap.add_argument("--sync-html", action="store_true",
                    help="トップページ群の件数ハードコードもstatsの実値へ書き戻す")
    ap.add_argument("--dry-run", action="store_true", help="書き込まず差分だけ表示")
    args = ap.parse_args()

    rules = json.load(open(args.rules, encoding="utf-8"))
    aichi = json.load(open(args.aichi, encoding="utf-8"))

    by_pref = {}
    details = {}
    jidokan_map = {}
    national = 0
    for p in PREFS:
        spots = parse_spots(load_html(args.pages, p, args.source), p)
        chain = sum(1 for s in spots if s.get("is_chain"))
        status, sc, sd, raw = score_pref(spots, rules)
        by_pref[p] = {
            "local": len(spots) - chain,
            "chain": chain,
            "total": len(spots),
            "status": status,
        }
        details[p] = {"score": sc, "score_detail": sd, **raw}
        jidokan_map[p] = raw["jidokan"]
        national += len(spots)
        print("%-10s local=%4d chain=%4d total=%5d  %s" % (
            p, by_pref[p]["local"], chain, len(spots), status))

    status_changes = []
    if args.keep_status:
        try:
            prev = json.load(open(args.out, encoding="utf-8"))["by_pref"]
        except Exception:
            prev = {}
        for p in PREFS:
            old = prev.get(p, {}).get("status")
            if old and old != by_pref[p]["status"]:
                status_changes.append((p, old, by_pref[p]["status"]))
                by_pref[p]["status"] = old
        if status_changes:
            print("\n--keep-status: 実データ再判定では変わるが据え置いたstatus")
            for p, o, n in status_changes:
                print("  %-10s %s -> %s (据え置き)" % (p, o, n))

    diffs = []
    if args.verify:
        print("\n--verify: 本番raw と突合中 ...", file=sys.stderr)
        for p in PREFS:
            try:
                n = len(parse_spots(fetch("%s/%s/index.html" % (NAT_REPO_RAW, p)), p))
            except Exception as e:  # noqa: BLE001
                diffs.append({"pref": p, "error": str(e)})
                continue
            if n != by_pref[p]["total"]:
                diffs.append({"pref": p, "local_total": by_pref[p]["total"], "remote_total": n})
        print("verify diffs: %d" % len(diffs))

    stats = {
        "aichi": aichi["total"],
        "national": national,
        "prefs": 47,
        "total": national + aichi["total"],
        "updated": datetime.date.today().isoformat(),
        "by_pref": by_pref,
        "status_rule": {"version": rules["status_rule"]["version"]},
    }
    if not args.dry_run:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
            f.write("\n")

    if args.sync_html:
        ch = sync_html(args.pages, stats, jidokan_map, PREFS, dry_run=args.dry_run)
        n = sum(len(v) for v in ch.values())
        print("\n--sync-html: %d ファイル / %d 箇所を更新%s"
              % (len(ch), n, "（dry-run）" if args.dry_run else ""))
        for k in sorted(ch):
            if ch[k]:
                head = "; ".join("%s %s->%s" % t for t in ch[k][:3])
                more = "" if len(ch[k]) <= 3 else " ...他%d" % (len(ch[k]) - 3)
                print("  %-22s %s%s" % (k, head, more))
    if args.detail_out:
        json.dump({"details": details, "verify_diffs": diffs},
                  open(args.detail_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n=> %s  national=%d aichi=%d total=%d" % (
        args.out, national, aichi["total"], stats["total"]))
    if diffs:
        print("verify差分: %s" % json.dumps(diffs, ensure_ascii=False))


if __name__ == "__main__":
    main()
