#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stats.json 生成スクリプト（単一ソース）

- 全国版46都道府県の index.html を raw.githubusercontent.com から取得し、
  各ページの SPOTS 配列の実数（全件・児童館/チェーン込み）を合計 => national
- 愛知版 index.html の SPOTS 実数 => aichi
- total = national + aichi / prefs = 47（愛知を含む都道府県数）/ updated = 実行日

手打ちの件数は一切持たない。実行するたびに実データから再計算する。

使い方:  python3 gen_stats.py  [-o stats.json]
"""
import argparse
import datetime
import json
import re
import sys
import urllib.request

NAT_REPO_RAW = "https://raw.githubusercontent.com/nishimikawa-odekake/kosodate-odekake/main"
AICHI_URLS = [
    # 本番（github.io / 独自ドメイン）が引ければそれを優先。
    "https://nishimikawa-odekake.github.io/index.html",
    # 同一内容のソース（ネットワーク制限環境向けフォールバック）
    "https://raw.githubusercontent.com/nishimikawa-odekake/nishimikawa-odekake.github.io/main/index.html",
]

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


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "gen-stats/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def count_spots(html, label):
    """HTML中の SPOTS 配列リテラルをJSONとして解析し、要素数を返す。"""
    m = SPOTS_RE.search(html)
    if not m:
        raise ValueError("SPOTS array not found: %s" % label)
    start = html.index("[", m.start())
    arr, _ = json.JSONDecoder().raw_decode(html[start:])
    if not isinstance(arr, list):
        raise ValueError("SPOTS is not a list: %s" % label)
    return len(arr)


def count_from_urls(urls, label):
    last = None
    for u in urls:
        try:
            return count_spots(fetch(u), label), u
        except Exception as e:  # noqa: BLE001
            last = e
            print("  ! %s: %s" % (u, e), file=sys.stderr)
    raise SystemExit("failed to count %s: %s" % (label, last))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default="stats.json")
    args = ap.parse_args()

    national = 0
    for p in PREFS:
        n, _ = count_from_urls(["%s/%s/index.html" % (NAT_REPO_RAW, p)], p)
        national += n
        print("%-10s %5d" % (p, n))

    aichi, src = count_from_urls(AICHI_URLS, "aichi")
    print("%-10s %5d  (%s)" % ("aichi", aichi, src))

    stats = {
        "aichi": aichi,
        "national": national,
        "prefs": 47,
        "total": national + aichi,
        "updated": datetime.date.today().isoformat(),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print("\n=> %s: %s" % (args.out, json.dumps(stats, ensure_ascii=False)))


if __name__ == "__main__":
    main()
