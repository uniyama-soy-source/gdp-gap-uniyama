"""取得先の下見：各統計の一覧ページと候補ファイルを取得して保存する（Python標準ライブラリのみ）。

使い方（Windows の PowerShell で、このフォルダにいる状態で）:
    python fetch_probe.py
結果: downloads/probe_<日時>/ に、ページのHTML・リンク一覧（links.tsv）・候補ファイル・ログ（probe_log.txt）
個人情報や認証情報は送りません。User-Agent は一般的なブラウザ名だけです。
"""
from __future__ import annotations

import re
import sys
import time
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "downloads" / f"probe_{datetime.now():%Y%m%d_%H%M%S}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gapmodel-probe"

# (名前, 一覧ページURL, 候補ファイルを選ぶ正規表現（リンク先のURLに当てる）, 最大取得数)
PAGES = [
    ("esri_qe_2026", "https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2026/toukei_2026.html",
     r"gaku-jk\d+\.csv$", 4),
    ("esri_qe_2025", "https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2025/toukei_2025.html",
     r"gaku-jk\d+\.csv$", 0),
    ("esri_stock", "https://www.esri.cao.go.jp/jp/sna/data/data_list/kotei/files/files_kotei.html",
     r"\.(csv|xlsx|xls)$", 6),
    ("meti_iip", "https://www.meti.go.jp/statistics/tyo/iip/b2020_result-2.html",
     r"b2020_n?g[os]m1j\.xlsx$", 6),
    ("meti_ita", "https://www.meti.go.jp/statistics/tyo/sanzi/result-2.html",
     r"\.(csv|xlsx)$", 0),
    ("mhlw_mk_top", "https://www.mhlw.go.jp/toukei/list/30-1a.html", r"$^", 0),
    ("mhlw_mk_2609p", "https://www.mhlw.go.jp/toukei/itiran/roudou/monthly/r08/2609p/2609p.html",
     r"t08p\.xlsx$", 2),
    ("stat_lfs_longtime", "https://www.stat.go.jp/data/roudou/longtime/03roudou.html", r"$^", 0),
    ("cao_shihyo", "https://www5.cao.go.jp/keizai3/shihyo/index.html", r"$^", 0),
    ("jcer_esp", "https://www.jcer.or.jp/esp-forecast-top", r"\.pdf$", 0),
]

# 直接取得を試すファイル（e-Stat はアプリケーションIDなしのファイルダウンロード、日銀は時系列統計API）
DIRECT = [
    ("estat_lfs_1a1", "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000031831358&fileKind=0"),
    ("estat_pesa_kyujin", "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040505317&fileKind=0"),
    ("estat_mk_long_sa", "https://www.e-stat.go.jp/stat-search/file-download?statInfId=000040259772&fileKind=0"),
    ("boj_api_meta_co", "https://www.stat-search.boj.or.jp/api/v1/getMetadata?format=json&lang=jp&db=CO"),
]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self._href, self._text = [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data.strip())

    def handle_endtag(self, tag):
        if tag == "a" and self._href:
            self.links.append((self._href, " ".join(t for t in self._text if t)))
            self._href = None


def get(url: str, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.headers.get("Content-Disposition", ""), r.read()


def decode(b: bytes) -> str:
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    log = open(OUT / "probe_log.txt", "w", encoding="utf-8")

    def say(*a):
        s = " ".join(str(x) for x in a)
        print(s)
        log.write(s + "\n")
        log.flush()

    say(f"probe {datetime.now():%Y-%m-%d %H:%M:%S}  python {sys.version.split()[0]}")
    for name, url, pat, nmax in PAGES:
        try:
            st, ct, _, body = get(url)
        except Exception as e:  # noqa: BLE001
            say(f"[NG] {name} {url} -> {type(e).__name__}: {e}")
            continue
        html = decode(body)
        (OUT / f"{name}.html").write_text(html, encoding="utf-8")
        p = Links()
        p.feed(html)
        rows = [(urljoin(url, h), t) for h, t in p.links]
        with open(OUT / f"{name}_links.tsv", "w", encoding="utf-8") as f:
            for u, t in rows:
                f.write(f"{u}\t{t}\n")
        say(f"[OK] {name} {st} {len(body):,}B links={len(rows)}")
        cand = [u for u, _ in rows if re.search(pat, urlparse(u).path)]
        seen = []
        for u in cand:
            if u not in seen:
                seen.append(u)
        for u in seen[:nmax]:
            fn = OUT / f"{name}__{Path(urlparse(u).path).name}"
            try:
                st2, ct2, _, b2 = get(u)
                fn.write_bytes(b2)
                say(f"    file {st2} {len(b2):,}B {u}")
            except Exception as e:  # noqa: BLE001
                say(f"    file NG {u} -> {type(e).__name__}: {e}")
            time.sleep(1)
        if len(seen) > nmax:
            say(f"    (候補 {len(seen)} 件中 {nmax} 件を取得)")
        time.sleep(1)

    for name, url in DIRECT:
        try:
            st, ct, cd, body = get(url)
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd or "")
            ext = Path(m.group(1)).suffix if m else (".json" if "json" in ct else ".bin")
            (OUT / f"{name}{ext}").write_bytes(body)
            say(f"[OK] {name} {st} {ct} {len(body):,}B disp={cd[:120]}")
        except Exception as e:  # noqa: BLE001
            say(f"[NG] {name} {url} -> {type(e).__name__}: {e}")
        time.sleep(1)
    say(f"保存先: {OUT}")


if __name__ == "__main__":
    main()
