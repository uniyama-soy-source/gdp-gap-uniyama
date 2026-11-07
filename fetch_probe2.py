"""下見その2：GDP速報CSV・第3次産業活動指数・e-Stat一覧ページ・日銀APIのデータ取得を試す（標準ライブラリのみ）。

使い方:  python fetch_probe2.py
"""
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from fetch_probe import Links, decode, get, ROOT

OUT = ROOT / "downloads" / f"probe2_{datetime.now():%Y%m%d_%H%M%S}"
OUT.mkdir(parents=True, exist_ok=True)
log = open(OUT / "probe_log.txt", "w", encoding="utf-8")


def say(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    log.write(s + "\n")
    log.flush()


def save_page(name, url):
    try:
        st, ct, cd, body = get(url)
    except Exception as e:  # noqa: BLE001
        say(f"[NG] {name} {url} -> {type(e).__name__}: {e}")
        return []
    html = decode(body)
    (OUT / f"{name}.html").write_text(html, encoding="utf-8")
    p = Links()
    p.feed(html)
    rows = [(urljoin(url, h), t) for h, t in p.links]
    with open(OUT / f"{name}_links.tsv", "w", encoding="utf-8") as f:
        for u, t in rows:
            f.write(f"{u}\t{t}\n")
    say(f"[OK] {name} {st} {len(body):,}B links={len(rows)}")
    time.sleep(1)
    return rows


def save_file(name, url):
    try:
        st, ct, cd, body = get(url)
        fn = OUT / f"{name}__{Path(urlparse(url).path).name or 'data'}"
        if "json" in ct and not fn.suffix:
            fn = fn.with_suffix(".json")
        fn.write_bytes(body)
        say(f"    file {st} {ct} {len(body):,}B {url}")
    except Exception as e:  # noqa: BLE001
        say(f"    file NG {url} -> {type(e).__name__}: {e}")
    time.sleep(1)


# 1. GDP速報：最新の統計表メニュー → 実質季調系列CSV
toukei = save_page("esri_qe_2026", "https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files/2026/toukei_2026.html")
menus = [u for u, _ in toukei if u.endswith("gdemenuja.html")]
if menus:
    rows = save_page("esri_gdemenu", menus[0])
    for u, t in rows:
        if re.search(r"\.csv$", u):
            say(f"    csv-link\t{t}\t{u}")
    for u in [u for u, _ in rows if re.search(r"gaku-jk\d+\.csv$", u)][:2]:
        save_file("esri", u)

# 2. 第3次産業活動指数（月次 原指数・季調）
for f in ["b2020_komj.xlsx", "b2020_ksmj.xlsx"]:
    save_file("meti_ita", f"https://www.meti.go.jp/statistics/tyo/sanzi/result/excel/{f}")

# 3. e-Stat の一覧ページ（職業安定業務統計・毎勤）
for name, url in [
    ("estat_pesa_list", "https://www.e-stat.go.jp/stat-search/files?page=1&toukei=00450222"),
    ("estat_pesa_list_q", "https://www.e-stat.go.jp/stat-search/files?page=1&layout=datalist&toukei=00450222&query=%E5%AD%A3%E7%AF%80%E8%AA%BF%E6%95%B4"),
    ("estat_mk_list", "https://www.e-stat.go.jp/stat-search/files?page=1&toukei=00450071&tstat=000001011791"),
    ("estat_lfs_basic", "https://www.e-stat.go.jp/stat-search/files?tclass=000001226526&cycle=0"),
]:
    rows = save_page(name, url)
    for u, t in rows[:400]:
        if "stat-search/files?" in u and t:
            log.write(f"    sub\t{t}\t{u}\n")

# 4. 日銀 時系列統計API（短観の設備判断DI・雇用人員判断DI、全規模、製造業・非製造業）
codes = "TK99F1000607GCQ00000,TK99F2000607GCQ00000,TK99F1000608GCQ00000,TK99F2000608GCQ00000"
save_file("boj_tankan", f"https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=jp&db=CO&code={codes}")
say(f"保存先: {OUT}")
