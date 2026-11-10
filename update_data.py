"""統計を取得して data/raw を更新する。

使い方（Windows の PowerShell で、このフォルダにいる状態で）:
    python update_data.py                 # すべて取得して反映
    python update_data.py --dry-run       # 取得・解析・差分の確認だけ（data/raw は変えない）
    python update_data.py --only gdp,iip  # 一部だけ

やること
  1. 各統計の一覧ページ（URLが変わらないページ）から最新のファイルを見つけて取得する。
  2. 取得したファイルは downloads/<日時>/ に保存する（GDP速報は data/vintages/esri/ にも保存）。
  3. 解析して既存の系列と比べ、新しい期・改定・大きすぎる変化を報告する。
  4. data/raw をまるごと data/backup/raw_<日時>/ に写してから、上書きする。
報告は画面と downloads/<日時>/update_report.txt に出る。
送るのは一般的なブラウザ名（User-Agent）だけで、個人情報や認証情報は送らない。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
import traceback
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

from gapmodel import ingest as ig

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
TS = datetime.now().strftime("%Y%m%d_%H%M%S")
DL = ROOT / "downloads" / TS

ESRI = "https://www.esri.cao.go.jp/jp/sna/data/data_list"
METI = "https://www.meti.go.jp/statistics/tyo"
ESTAT = "https://www.e-stat.go.jp/stat-search"


class Ctx:
    def __init__(self, dry: bool):
        self.dry = dry
        self.changes: list[ig.Change] = []
        self.notes: list[str] = []
        self.files: dict[str, ig.SeriesFile] = {}
        self.pending: dict[Path, str] = {}   # 直接書くファイル（csv など）: 内容
        self.log_lines: list[str] = []

    def log(self, *a):
        s = " ".join(str(x) for x in a)
        print(s)
        self.log_lines.append(s)

    def get(self, url: str, save_as: str | None = None) -> bytes:
        b = ig.http_get(url)
        name = save_as or Path(urlparse(url).path).name or "data"
        DL.mkdir(parents=True, exist_ok=True)
        (DL / name).write_bytes(b)
        self.log(f"    取得 {len(b):>9,}B {name}  ← {url.split('?')[0]}")
        return b

    def page(self, url: str, name: str) -> list[tuple[str, str]]:
        """一覧ページを取得して downloads に保存し、リンク一覧を返す（リンクが見つからないときの原因調べ用）。"""
        b = ig.http_get(url)
        DL.mkdir(parents=True, exist_ok=True)
        (DL / f"page_{name}.html").write_bytes(b)
        p = ig._Links()
        p.feed(ig.decode(b))
        from urllib.parse import urljoin
        return [(urljoin(url, h), t) for h, t in p.links]

    def get_first(self, candidates: list[str], what: str) -> bytes:
        """候補URLを順に試す（一覧ページのリンク → 既知の固定URL）。"""
        errs = []
        for u in dict.fromkeys(candidates):
            try:
                return self.get(u)
            except RuntimeError as e:
                errs.append(str(e))
        raise RuntimeError(f"{what} が取れない：" + " / ".join(errs))

    def sfile(self, fname: str, default_start: str | None = None) -> ig.SeriesFile:
        if fname not in self.files:
            self.files[fname] = ig.SeriesFile(RAW / fname, default_start)
        return self.files[fname]

    def merge(self, fname: str, new: dict, rev=3.0, jump=15.0, absolute=False, default_start=None):
        sf = self.sfile(fname, default_start)
        for k, s in new.items():
            comb, ch = ig.merge_series(sf.series.get(k), s, rev, jump, fname, k, absolute)
            sf.set(k, comb)
            self.changes.append(ch)


def find(links, pattern: str, text: str | None = None) -> list[str]:
    out = []
    for u, t in links:
        if re.search(pattern, urlparse(u).path) and (text is None or text in ig.nfkc(t)):
            if u not in out:
                out.append(u)
    return out


# ------------------------------------------------------------ 統計ごとの取得・反映
def src_gdp(c: Ctx):
    """GDP速報：統計表一覧（年別）→ 最新の統計表メニュー → 実質季節調整系列CSV。"""
    menus = []
    for y in (date.today().year, date.today().year - 1):
        try:
            menus += find(ig.page_links(f"{ESRI}/sokuhou/files/{y}/toukei_{y}.html"), r"/qe\d{3}(_2)?/gdemenuja\.html$")
        except RuntimeError as e:
            c.log(f"    （{y}年の一覧なし：{e}）")
    if not menus:
        raise RuntimeError("GDP速報の統計表メニューが見つからない")
    key = lambda u: (re.search(r"/qe(\d{2})(\d)(_2)?/", u).groups())  # noqa: E731
    latest = max(menus, key=lambda u: (key(u)[0], key(u)[1], key(u)[2] or ""))
    csvs = find(ig.page_links(latest), r"/tables/gaku-jk\d+\.csv$")
    if not csvs:
        raise RuntimeError(f"実質季節調整系列CSVが見つからない：{latest}")
    yy, q, second = key(latest)
    label = f"20{yy}Q{q} {'2次' if second else '1次'}速報"
    b = c.get(csvs[0])
    vint = ROOT / "data" / "vintages" / "esri"
    if not c.dry:
        vint.mkdir(parents=True, exist_ok=True)
        (vint / Path(urlparse(csvs[0]).path).name).write_bytes(b)
    s = ig.parse_esri_gaku(b)
    old = pd.read_csv(RAW / "qe_real_gdp_sa.csv")
    old = pd.Series(old.rgdp.values, index=pd.PeriodIndex(old.q, freq="Q"))
    _, ch = ig.merge_series(old, s, 1.0, 3.0, "qe_real_gdp_sa.csv", "rgdp")
    c.changes.append(ch)
    c.notes.append(f"GDP：{label}（{Path(urlparse(csvs[0]).path).name}）")
    body = "q,rgdp\n" + "".join(f"{p},{ig.fmt(v)}\n" for p, v in s.items())
    c.pending[RAW / "qe_real_gdp_sa.csv"] = body


def src_iip(c: Ctx):
    """鉱工業：一覧ページのリンク（見つからなければ既知の固定URL）から生産・能力の時系列xlsx。"""
    try:
        links = c.page(f"{METI}/iip/b2020_result-2.html", "meti_iip")
    except RuntimeError as e:
        c.log(f"    （一覧ページが取れない：{e}）")
        links = []
    b = {}
    for k in ("gom1j", "gsm1j", "ngom1j"):
        cand = find(links, rf"/b2020_{k}\.xlsx$") + [f"{METI}/iip/xls/b2020_{k}.xlsx"]
        if not find(links, rf"/b2020_{k}\.xlsx$"):
            c.log(f"    （一覧ページに b2020_{k}.xlsx のリンクがないため固定URLを使う）")
        b[k] = c.get_first(cand, f"鉱工業 b2020_{k}.xlsx")
    new = ig.parse_iip(b["gom1j"], b["gsm1j"], b["ngom1j"])
    c.merge("iip_monthly.txt", new, rev=3.0, jump=15.0)
    c.notes.append("鉱工業：生産（原・季調）、生産能力・稼働率（原）")


def src_ita(c: Ctx):
    """第3次産業活動指数：一覧ページのリンク（見つからなければ既知の固定URL）から総合の月次xlsx。"""
    try:
        links = c.page(f"{METI}/sanzi/result-2.html", "meti_ita")
    except RuntimeError as e:
        c.log(f"    （一覧ページが取れない：{e}）")
        links = []
    b = {}
    for k in ("komj", "ksmj"):
        found = find(links, rf"/b2020_{k}\.xlsx$")
        if not found:
            c.log(f"    （一覧ページに b2020_{k}.xlsx のリンクがないため固定URLを使う）")
        b[k] = c.get_first(found + [f"{METI}/sanzi/result/excel/b2020_{k}.xlsx"], f"第3次産業活動指数 b2020_{k}.xlsx")
    new = ig.parse_ita(b["komj"], b["ksmj"])
    c.merge("ita_monthly.txt", new, rev=3.0, jump=10.0)
    c.notes.append("第3次産業活動指数：総合（原・季調、2018年〜。接続指数は据え置き）")


def src_link(c: Ctx):
    """接続指数（2017年以前の実データ）：鉱工業の季節調整済指数と、第3次産業活動指数の総合の原指数。
    経産省が接続指数を更新したとき（年1回程度）だけ中身が変わる。現行系列とつなぎ目が合うことを確かめてから反映する。"""
    jobs = [("iip", f"{METI}/iip/b2020_result-2.html", "b2020_sgs1j", f"{METI}/iip/xls/b2020_sgs1j.xlsx",
             ig.parse_iip_link, "iip_monthly.txt", {"ip_mm_s_link": "ip_mm_s", "ip_mfg_s_link": "ip_mfg_s"}),
            ("ita", f"{METI}/sanzi/result-2.html", "b2020_ITA_linkj", f"{METI}/sanzi/result/excel/b2020_ITA_linkj.xlsx",
             ig.parse_ita_link, "ita_monthly.txt", {"ita_o_link": "ita_o"})]
    for tag, page, stem, fixed, parse, fname, pairs in jobs:
        try:
            links = c.page(page, f"meti_{tag}_link")
        except RuntimeError as e:
            c.log(f"    （一覧ページが取れない：{e}）")
            links = []
        found = find(links, rf"/{stem}\.xlsx$")
        if not found:
            c.log(f"    （一覧ページに {stem}.xlsx のリンクがないため固定URLを使う）")
        new = parse(c.get_first(found + [fixed], f"接続指数 {stem}.xlsx"))
        cur = c.sfile(fname).series
        for k, ck in pairs.items():
            first12, later = ig.link_junction(new[k], cur[ck])
            if first12 > 0.051:
                raise RuntimeError(f"{k}：現行の {ck} とつなぎ目が合わない（最初の12か月の差の最大 {first12:.2f}）。取り込まない")
            c.notes.append(f"接続指数 {k}：{new[k].index[0]}〜{new[k].index[-1]}。現行の {ck} とつなぎ目が一致"
                           + (f"（その後の {later} か月は現行側が改定済み）" if later else ""))
        c.merge(fname, new, rev=0.5, jump=15.0)


def src_mk(c: Ctx):
    """毎勤：一覧（30-1a）→ 最新月のページ（確報があれば確報）→ 時系列第8表。"""
    links = ig.page_links("https://www.mhlw.go.jp/toukei/list/30-1a.html")
    pages = {}
    for u, _ in links:
        m = re.search(r"/monthly/r\d+/(\d{4})([pr])/\1\2\.html$", urlparse(u).path)
        if m:
            pages[(m.group(1), m.group(2) == "r")] = u
    if not pages:
        raise RuntimeError("毎勤の月別ページが見つからない")
    ym, conf = max(pages)
    page = pages[(ym, conf)]
    x = find(ig.page_links(page), r"/xls/\d{4}t08[pr]\.xlsx$")
    if not x:
        raise RuntimeError(f"毎勤 時系列第8表が見つからない：{page}")
    new = ig.parse_mk_t08(c.get(x[0]))
    c.merge("mk_sa_monthly.txt", new, rev=2.0, jump=5.0)
    c.notes.append(f"毎勤：20{ym[:2]}年{int(ym[2:])}月{'確報' if conf else '速報'}の時系列第8表（総実労働時間・常用雇用）。"
                   "製造業常用雇用（emfg_sa）は速報の表にないため手入力（推計では直近値で補う）")


def src_lfs(c: Ctx):
    b = c.get(f"{ESTAT}/file-download?statInfId=000031831358&fileKind=0", "lfs_lt01-a10.xlsx")
    c.merge("lfs_sa_quarterly.txt", ig.parse_lfs_1a1(b), rev=1.0, jump=2.0)
    c.notes.append("労調：長期時系列 1-a-1 季節調整値の四半期平均")


def src_stock(c: Ctx):
    links = ig.page_links(f"{ESRI}/kotei/files/files_kotei.html")
    cand = []
    for u, _ in links:
        m = re.search(r"/tables/(\d{4})/stock(\d{2})q(\d)_rn\.xlsx$", urlparse(u).path)
        if m:
            cand.append(((m.group(1), m.group(2), m.group(3)), u))
    if not cand:
        raise RuntimeError("固定資本ストック速報の計数表が見つからない")
    (base, yy, q), u = max(cand)
    df = ig.parse_stock(c.get(u))
    old = {}
    for r in (RAW / "stock_raw.txt").read_text().strip().strip(";").split(";"):
        a, p_, g_ = r.split(",")
        y = int(a[:2]); y = 1900 + y if y >= 80 else 2000 + y
        old[pd.Period(f"{y}Q{a[2]}", "Q")] = (float(p_), float(g_))
    for j, k in enumerate(("priv", "pub")):
        o = pd.Series({p: v[j] for p, v in old.items()}).sort_index()
        _, ch = ig.merge_series(o, df[k].round(0), 1.0, 2.0, "stock_raw.txt", k)
        c.changes.append(ch)
    body = ";".join(f"{p.year % 100:02d}{p.quarter},{int(round(r.priv))},{int(round(r.pub))}" for p, r in df.iterrows())
    c.pending[RAW / "stock_raw.txt"] = body + "\n"
    c.notes.append(f"資本ストック：{base}年基準 20{yy}年{q}Q 速報（stock{yy}q{q}_rn.xlsx）")


def src_tankan(c: Ctx):
    codes = ",".join(ig.TANKAN_CODES)
    b = c.get(f"https://www.stat-search.boj.or.jp/api/v1/getDataCode?format=json&lang=jp&db=CO&code={codes}",
              "boj_tankan.json")
    new = {k: s[s.index >= pd.Period("1980Q1", "Q")] for k, s in ig.parse_boj_tankan(b).items()}
    c.merge("tankan_di.txt", new, rev=0.5, jump=15.0, absolute=True, default_start="1980Q1")
    c.notes.append("短観：生産・営業用設備判断DI、雇用人員判断DI（全規模、製造業・非製造業、実績）")


def src_lfs_sexage(c: Ctx):
    """労調 性・年齢別（長期時系列、統計IDは固定）：労働力人口比率と15歳以上人口の四半期平均（日銀の労働力率ギャップ用）。"""
    new = {}
    for sid, nm, pre, nd in (("000031897991", "lfs_lfpr_sexage.xlsx", "lfpr", 1),
                             ("000031831366", "lfs_pop_sexage.xlsx", "pop", 0)):
        new.update(ig.parse_lfs_sexage(c.get(f"{ESTAT}/file-download?statInfId={sid}&fileKind=0", nm), pre, nd))
    c.merge("lfs_sexage_quarterly.txt", new, rev=1.0, jump=5.0, default_start="1980Q1")
    c.notes.append("労調 性・年齢別：労働力人口比率・15歳以上人口（原数値の四半期平均）")


def _mk_long_items(c: Ctx):
    return ig.estat_walk(f"{ESTAT}/files?page=1&toukei=00450071&tstat=000001011791&cycle=0&tclass1=000001035519"
                         f"&tclass2=000001144508&layout=datalist&tclass3val=0",
                         {"toukei": "00450071", "tclass2": "000001144508"}, max_pages=5, log=c.log)


def src_mk_days(c: Ctx):
    """毎勤 長期時系列 実数・指数累積データ「実数」→ 出勤日数（日銀の労働時間の分解用）。確報の公表時に更新される。"""
    items = _mk_long_items(c)
    hit = [it for it in items if "実数・指数累積データ 実数" in it.title and it.file_type == "CSV"]
    if not hit:
        raise RuntimeError("毎勤 実数・指数累積データ（実数）が一覧に見つからない")
    new = ig.parse_mk_days(c.get(hit[0].url, f"mk_long_jissu_{hit[0].stat_inf_id}.csv"))
    c.merge("mk_days_monthly.txt", new, rev=1.0, jump=25.0)
    c.notes.append(f"毎勤 出勤日数：実数・指数累積データ（調査年月 {hit[0].survey}）")


SHIHYO_WATCH = ("GDPギャップ", "需給ギャップ", "潜在", "推計方法", "見直し")   # 題名にあれば手法の告知かもしれない語


def shihyo_since(links, last_no: int):
    """今週の指標の一覧から、号数が last_no より後の号を [(号数, 題名, URL, 要確認か)] で返す（号数順）。
    要確認＝GDPギャップの定例の推計結果号ではないのに、題名に SHIHYO_WATCH の語がある号。
    2023年5月の No.1310（コロナ禍の暫定処理の見直し）のように、次の推計結果号より前に見直し後の値が
    公表されることがあり、見落とすと接続値の基準が古いままになる（2023Q1の誤差0.79の原因）。"""
    out = {}
    for u, t in links:
        m = re.search(r"/shihyo/\d{4}/\d{4}/(\d+)\.(?:pdf|html?)$", u)
        if m and int(m.group(1)) > last_no:
            title = ig.nfkc(t).strip().strip('"“”「」')
            watch = "GDPギャップの推計結果" not in title and any(w in title for w in SHIHYO_WATCH)
            out[int(m.group(1))] = (int(m.group(1)), title, u, watch)
    return [out[k] for k in sorted(out)]


def src_cao_pub(c: Ctx):
    """内閣府「今週の指標」の最新のGDPギャップ号 → 公表時点別の記録（cao_vintages_raw.txt）と、公表値（cao_q_raw.txt の GDPギャップ列）。"""
    links = ig.page_links("https://www5.cao.go.jp/keizai3/shihyo/index.html")
    g = [u for u, t in links if "GDPギャップの推計結果" in ig.nfkc(t) and u.endswith(".pdf")]
    if not g:
        raise RuntimeError("今週の指標の一覧にGDPギャップ号が見つからない")
    r = ig.parse_cao_gap_pdf(c.get(g[0], "cao_" + Path(urlparse(g[0]).path).name))
    pub = ROOT / "data" / "published"
    vraw = (pub / "cao_vintages_raw.txt").read_text(encoding="utf-8")
    have = {ln.split("|")[0] for ln in vraw.splitlines() if ln and not ln.startswith(("#", "R|"))}
    label = f"{r['no']}号（{r['date']}、{r['target']} {r['kind']}QE後、本文 {r['head']:+.1f}%）"
    # 前回記録したGDPギャップ号より後に出た号を並べる（手法見直しの告知を見落とさないため）
    last_no = max((int(h) for h in have if h.isdigit()), default=0)
    news = [x for x in shihyo_since(links, last_no) if x[0] != r["no"]]
    if news:
        c.notes.append(f"今週の指標：前回記録したGDPギャップ号（No.{last_no}）より後に {len(news)} 号 → "
                       + " ／ ".join(f"No.{n} {t}" for n, t, _, _ in news))
    for n, t, u, w in news:
        if w:
            c.notes.append(f"【要確認】今週の指標 No.{n}「{t}」（{u}）：手法見直しの告知の可能性。見直し後の値が載っていれば、"
                           "接続値の基準をその値にする（backtest_realtime2.py の ANCHOR_NOTES と同じ扱い）")
    if str(r["no"]) in have:
        c.notes.append(f"内閣府 公表値：最新は {label}。記録済みのため変更なし")
        return
    fv = lambda v: ",".join(f"{x:.1f}" for x in v)  # noqa: E731
    add = [f"{r['no']}|{r['date']}|{r['target']}|{r['kind']}|{r['head']:.1f}|update_data.py で自動取得",
           f"R|{r['no']}|今回|{r['now_start']}|{fv(r['now'])}"]
    if r["prev"]:
        add.append(f"R|{r['no']}|前回|{r['prev_start']}|{fv(r['prev'])}")
    lines = vraw.splitlines()
    i = next(k for k, ln in enumerate(lines) if ln and not ln.startswith("#"))
    c.pending[pub / "cao_vintages_raw.txt"] = "\n".join(lines[:i] + add + lines[i:]) + "\n"
    # 公表値（最新号の今回推計で GDPギャップ列を上書き。潜在成長率などの列は月例経済報告の資料で別途）
    rows = {}
    for part in (pub / "cao_q_raw.txt").read_text().strip().strip(";").split(";"):
        p = part.split(",")
        yy = int(p[0][:2]); y = 1900 + yy if yy >= 80 else 2000 + yy
        rows[pd.Period(f"{y}Q{p[0][2]}", "Q")] = p[1:]
    old = pd.Series({k: float(v[0]) for k, v in rows.items() if v[0] != "-"}).sort_index()
    now = pd.Series(r["now"], index=pd.period_range(r["now_start"], periods=len(r["now"]), freq="Q"))
    _, ch = ig.merge_series(old, now, 0.5, 2.0, "cao_q_raw.txt", "gap", absolute=True)
    c.changes.append(ch)
    for q, v in now.items():
        rows.setdefault(q, ["-"] * 6)
        rows[q][0] = f"{v:.1f}"
    c.pending[pub / "cao_q_raw.txt"] = ";".join(f"{q.year % 100:02d}{q.quarter}," + ",".join(v)
                                                  for q, v in sorted(rows.items())) + "\n"
    c.notes.append(f"内閣府 公表値：新しい号 {label} を記録し、公表GDPギャップを更新")


def src_pesa(c: Ctx):
    """一般職業紹介状況（e-Stat。統計IDが公表ごとに変わるので一覧からたどる）：表6 有効求人数・表8 就職件数（パート含む、季節調整値）。"""
    items = ig.estat_walk(f"{ESTAT}/files?page=1&toukei=00450222&tstat=000001020327",
                          {"toukei": "00450222", "tstat": "000001020327"}, max_pages=30, log=c.log)
    _catalog(items, "estat_pesa_catalog.tsv")
    got = []
    for key in ("表番号 6 有効求人数", "表番号 8 就職件数"):
        cand = sorted([it for it in items if key in it.title and "季節調整" in it.title and it.file_type == "EXCEL"],
                      key=lambda it: it.updated, reverse=True)
        if not cand:
            raise RuntimeError(f"一般職業紹介状況：「{key}」が一覧に見つからない")
        got.append(cand[0])
    b6, b8 = (c.get(it.url, f"pesa_{it.stat_inf_id}.xlsx") for it in got)
    c.merge("pesa_quarterly.txt", ig.parse_pesa(b6, b8), rev=2.0, jump=8.0)
    c.notes.append(f"一般職業紹介状況：表6・表8（調査年月 {got[0].survey}、パート含む季節調整値の四半期平均）")


def _estat_appid() -> str:
    f = ROOT / "estat_appid.txt"
    if not f.exists():
        raise RuntimeError("estat_appid.txt がありません（e-Stat のアプリケーションIDを1行で書いて GDPgap フォルダに置く）")
    a = f.read_text(encoding="utf-8-sig").strip()
    if not re.fullmatch(r"[0-9A-Za-z]{30,}", a):
        raise RuntimeError("estat_appid.txt の中身がアプリケーションIDの形（英数字40文字程度）になっていない")
    return a


def src_lfs_hours(c: Ctx):
    """労調 基本集計 表2-8-4 非農林業従業者の平均週間就業時間・延週間就業時間（e-Stat API、statsDataId 0003022751、2011年〜）。"""
    import json as _json
    app, sid = _estat_appid(), "0003022751"
    meta = _json.loads(ig.http_get(f"{ig.ESTAT_API}/getMetaInfo?appId={app}&statsDataId={sid}"))
    st = meta["GET_META_INFO"]["RESULT"]
    if int(st["STATUS"]) != 0:
        raise RuntimeError(f"e-Stat API：{st.get('ERROR_MSG')}")
    DL.mkdir(parents=True, exist_ok=True)
    (DL / f"estat_meta_{sid}.json").write_text(_json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    params, picked = ig.estat_pick_codes(meta, ["平均週間就業時間", "延週間就業時間"])
    q = "&".join(f"{k}={v}" for k, v in params.items())
    raw = ig.http_get(f"{ig.ESTAT_API}/getStatsData?appId={app}&statsDataId={sid}&{q}&metaGetFlg=Y&limit=100000")
    js = _json.loads(raw)
    st = js["GET_STATS_DATA"]["RESULT"]
    if int(st["STATUS"]) != 0:
        raise RuntimeError(f"e-Stat API：{st.get('ERROR_MSG')}")
    (DL / f"estat_data_{sid}.json").write_bytes(raw)
    c.log(f"    取得 {len(raw):>9,}B estat_data_{sid}.json  ← {ig.ESTAT_API}/getStatsData")
    c.log("    選んだ分類：" + " ／ ".join(picked))
    new = ig.parse_estat_values(js, {"平均週間就業時間": "ah", "延週間就業時間": "nh"})
    c.merge("lfs_hours_monthly.txt", new, rev=0.3, jump=8.0)
    c.notes.append("労調 就業時間：表2-8-4 非農林業（e-Stat API）。" + "／".join(picked))


def src_boj_pub(c: Ctx):
    """日銀「需給ギャップと潜在成長率」gap.xlsx（URL固定）→ data/published の boj_gap.csv（四半期）と boj_potential.csv（年度半期）。"""
    b = c.get("https://www.boj.or.jp/research/research_data/gap/gap.xlsx", "boj_gap.xlsx")
    Q, H = ig.parse_boj_gap_xlsx(b)
    pub = ROOT / "data" / "published"
    old = pd.read_csv(pub / "boj_gap.csv")
    old = old.set_index(pd.PeriodIndex(old.q, freq="Q")).drop(columns="q")
    for k in Q.columns:
        _, ch = ig.merge_series(old[k], Q[k], 0.05, 1.5, "boj_gap.csv", k, absolute=True)
        c.changes.append(ch)
    out = Q.round(3)
    out.index = out.index.astype(str)
    c.pending[pub / "boj_gap.csv"] = out.rename_axis("q").to_csv(lineterminator="\n")
    c.pending[pub / "boj_potential.csv"] = H.round(4).to_csv(index=False, lineterminator="\n")
    last = H.iloc[-1]
    c.notes.append(f"日銀 公表値：需給ギャップ {Q.dropna().index[-1]} まで、潜在成長率 {int(last.FY)}年度{'上' if last.H == 1 else '下'}期"
                   f"（{last['期間']}）まで {last.pot:.2f}%（TFP {last.tfp:.2f}／資本 {last.cap:.2f}／労働時間 {last.hours:.2f}／就業者 {last.emp:.2f}）")


def _catalog(items, name):
    DL.mkdir(parents=True, exist_ok=True)
    with open(DL / name, "w", encoding="utf-8") as f:
        for it in items:
            f.write(f"{it.stat_inf_id}\t{it.file_type}\t{it.survey}\t{it.updated}\t{it.title}\t{it.url}\n")


SOURCES = {"gdp": src_gdp, "iip": src_iip, "ita": src_ita, "link": src_link, "mk": src_mk, "lfs": src_lfs,
           "stock": src_stock, "tankan": src_tankan, "lfs_sexage": src_lfs_sexage, "mk_days": src_mk_days,
           "cao_pub": src_cao_pub, "pesa": src_pesa, "lfs_hours": src_lfs_hours, "boj_pub": src_boj_pub}


def main():
    ap = argparse.ArgumentParser(description="統計を取得して data/raw を更新する")
    ap.add_argument("--dry-run", action="store_true", help="data/raw を書き換えない")
    ap.add_argument("--only", default=None, help=f"一部だけ（{','.join(SOURCES)}）")
    a = ap.parse_args()
    names = a.only.split(",") if a.only else list(SOURCES)
    c = Ctx(a.dry_run)
    c.log(f"update_data {TS}  {'（確認のみ）' if a.dry_run else ''}")
    failed = []
    for n in names:
        c.log(f"[{n}]")
        try:
            SOURCES[n](c)
        except Exception as e:  # noqa: BLE001
            failed.append(n)
            c.log(f"    失敗：{type(e).__name__}: {e}")
            c.log("    " + traceback.format_exc().strip().splitlines()[-2].strip())

    # ---- 報告
    rep = ["", "=== 更新の内容 ==="]
    rep += [f"・{n}" for n in c.notes]
    rep += ["", f"{'ファイル':<26}{'系列':<12}{'最終期(前)':>11}{'→最終期(後)':>12}{'新しい期':>8}{'改定数':>7}{'最大改定':>9}"]
    warns = []
    for ch in c.changes:
        rep.append(f"{ch.file:<26}{ch.name:<12}{ch.old_last:>11}{ch.new_last:>12}{len(ch.added):>8}{ch.revised:>7}"
                   f"{ch.max_rev_pct:>8.2f}")
        warns += [f"{ch.file} {ch.name}：{w}" for w in ch.warn]
    rep += ["", "=== 要確認 ===" if warns else "=== 要確認：なし ==="] + [f"！ {w}" for w in warns]
    if failed:
        rep += ["", f"=== 失敗した統計：{', '.join(failed)}（既存のデータはそのまま）==="]
    for ln in rep:
        c.log(ln)

    # ---- 書き込み
    if a.dry_run:
        c.log("\n確認のみのため data/raw は変更していません。")
    elif c.files or c.pending:
        bk = ROOT / "data" / "backup" / f"raw_{TS}"
        shutil.copytree(RAW, bk)
        for sf in c.files.values():
            sf.write(f"{date.today()} update_data.py で更新")
        for p, body in c.pending.items():
            p.write_text(body, encoding="utf-8")
        c.log(f"\ndata/raw を更新しました（更新前のコピー：{bk.relative_to(ROOT)}）。")
    DL.mkdir(parents=True, exist_ok=True)
    (DL / "update_report.txt").write_text("\n".join(c.log_lines) + "\n", encoding="utf-8")
    c.log(f"報告：{(DL / 'update_report.txt').relative_to(ROOT)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
