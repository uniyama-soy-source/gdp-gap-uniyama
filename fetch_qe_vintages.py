"""過去のGDP速報（1次・2次の各回）の実質季節調整系列CSVをまとめて取得し、data/vintages/esri/ に保存する。

使い方:  python fetch_qe_vintages.py              # 2017年〜今年
         python fetch_qe_vintages.py --from 2022
すでにあるファイルは取り直さない。結果は data/vintages/esri/index.tsv（回・公表年・ファイル名・URL）。
以後の新しい回は update_data.py（gdp）が自動で同じ場所に保存する。
"""
import argparse
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from gapmodel import ingest as ig

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "vintages" / "esri"
ESRI = "https://www.esri.cao.go.jp/jp/sna/data/data_list/sokuhou/files"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="y0", type=int, default=2017)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    menus = {}
    for y in range(a.y0, date.today().year + 1):
        try:
            links = ig.page_links(f"{ESRI}/{y}/toukei_{y}.html")
        except RuntimeError as e:
            print(f"[{y}] 一覧が取れない：{e}")
            continue
        n = 0
        for u, _ in links:
            m = re.search(r"/(qe\d{3}(?:_2)?)/gdemenuja\.html$", urlparse(u).path)
            if m and m.group(1) not in menus:
                menus[m.group(1)] = (y, u)
                n += 1
        print(f"[{y}] 統計表メニュー {n}件")
    rows, got, skip, fail = [], 0, 0, 0
    for qe, (y, menu) in sorted(menus.items()):
        yy, q, second = re.match(r"qe(\d{2})(\d)(_2)?", qe).groups()
        fn = f"gaku-jk{yy}{q}{'2' if second else '1'}.csv"
        if (OUT / fn).exists():
            skip += 1
            rows.append((qe, y, fn, "（既存）"))
            continue
        url = menu.rsplit("/", 1)[0] + f"/tables/{fn}"
        try:
            b = ig.http_get(url)
        except RuntimeError:
            try:   # ファイル名の規則が違う回は、メニューページからたどる
                cand = [u for u, _ in ig.page_links(menu) if re.search(r"/tables/gaku-jk\d+\.csv$", urlparse(u).path)]
                url = cand[0]
                fn = Path(urlparse(url).path).name
                b = ig.http_get(url)
            except Exception as e:  # noqa: BLE001
                print(f"  {qe}：取れない（{e}）")
                fail += 1
                continue
        try:
            s = ig.parse_esri_gaku(b)
            note = f"{s.index[0]}〜{s.index[-1]}"
        except Exception as e:  # noqa: BLE001
            note = f"解析できない：{e}"
        (OUT / fn).write_bytes(b)
        rows.append((qe, y, fn, url))
        got += 1
        print(f"  {qe}：{fn}  {note}")
        time.sleep(1)
    with open(OUT / "index.tsv", "w", encoding="utf-8") as f:
        f.write("回\t一覧の年\tファイル\tURL\n")
        for r in rows:
            f.write("\t".join(map(str, r)) + "\n")
    print(f"\n取得 {got}件、既存 {skip}件、失敗 {fail}件 → {OUT}")


if __name__ == "__main__":
    main()
