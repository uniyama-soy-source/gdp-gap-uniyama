"""統計の取得（fetch）→ 解析（parse）→ data/raw への反映（merge・検証）。

方針
- 取得先は「URLが変わらない一覧ページ」からリンクをたどって見つける（ファイル名・統計IDの変更に強くする）。
- 解析は「元ファイルのバイト列 → {系列名: pandas.Series}」の純粋な関数にする（取得方法と切り離す）。
- 反映は既存系列に上書き（重なる期間は新しい値、古い期間は残す）。書き込む前に差分を検証して報告する。
取得したファイルはすべて downloads/<日時>/ に保存し、GDP速報は data/vintages/esri/ にも残す。
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import numpy as np
import pandas as pd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gapmodel-update"


# ---------------------------------------------------------------- 取得
class _Links(HTMLParser):
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


def http_get(url: str, timeout: int = 60, retries: int = 2) -> bytes:
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ja"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"取得失敗 {url.split('?')[0]}: {type(last).__name__}: {str(last).split('?')[0]}")


def decode(b: bytes) -> str:
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "replace")


def page_links(url: str) -> list[tuple[str, str]]:
    p = _Links()
    p.feed(decode(http_get(url)))
    time.sleep(0.5)
    return [(urljoin(url, h), t) for h, t in p.links]


def nfkc(s) -> str:
    return unicodedata.normalize("NFKC", str(s)) if s is not None else ""


def num(x) -> float:
    """'465,071.2 ' や '<6234>' '(4122)' '…' を数値に。読めなければ NaN。"""
    if x is None:
        return np.nan
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"[^0-9.\-]", "", nfkc(x))
    try:
        return float(s) if s not in ("", "-", ".") else np.nan
    except ValueError:
        return np.nan


# ---------------------------------------------------------------- 解析（バイト列 → 系列）
def _xlsx(b: bytes):
    try:
        import openpyxl
    except ImportError as e:  # pragma: no cover
        raise SystemExit("openpyxl がありません。`pip install openpyxl` を実行してください。") from e
    import warnings
    warnings.filterwarnings("ignore", module="openpyxl")
    return openpyxl.load_workbook(BytesIO(b), read_only=True, data_only=True)


def _meti_rows(ws, key_col: int, keys: dict[str, str]) -> dict[str, pd.Series]:
    """経産省の時系列xlsx（3行目が YYYYMM の見出し）から、key_col列が keys の行を月次系列にする。"""
    rows = list(ws.iter_rows(values_only=True))
    hdr = None
    for r in rows[:10]:
        cells = [re.search(r"(\d{6})", nfkc(c)) for c in r]
        if sum(m is not None for m in cells) > 12 and any(nfkc(c) in ("品目番号", "品目名称") for c in r):
            hdr = r
            break
    if hdr is None:
        raise ValueError(f"{ws.title}: 年月の見出し行が見つからない")
    cols = [(j, pd.Period(f"{m.group(1)[:4]}-{m.group(1)[4:]}", "M"))
            for j, c in enumerate(hdr) if (m := re.search(r"(\d{6})", nfkc(c)))]
    out = {}
    for r in rows:
        k = nfkc(r[key_col]).strip() if len(r) > key_col else ""
        if k in keys and keys[k] not in out:
            out[keys[k]] = pd.Series({p: num(r[j]) for j, p in cols}).sort_index()
    miss = set(keys.values()) - set(out)
    if miss:
        raise ValueError(f"{ws.title}: 行が見つからない {miss}")
    return out


def parse_iip(gom1j: bytes, gsm1j: bytes, ngom1j: bytes | None) -> dict[str, pd.Series]:
    out = {}
    out.update(_meti_rows(_xlsx(gom1j)["生産"], 1, {"鉱工業": "ip_mm_o", "製造工業": "ip_mfg_o"}))
    out.update(_meti_rows(_xlsx(gsm1j)["生産"], 1, {"鉱工業": "ip_mm_s", "製造工業": "ip_mfg_s"}))
    if ngom1j is not None:
        wb = _xlsx(ngom1j)
        out.update(_meti_rows(wb["能力"], 1, {"製造工業": "cap_mfg_o"}))
        out.update(_meti_rows(wb["稼働率"], 1, {"製造工業": "util_mfg_o"}))
    return out


def _link_col(ws, head_row: int, name_or_code: str, ym_col: int, first_row: int) -> pd.Series:
    """経産省の接続指数xlsx（縦に年月、横に系列）から1系列を月次で取り出す。「－」は欠測。"""
    rows = list(ws.iter_rows(values_only=True))
    head = [nfkc(c).strip() for c in rows[head_row]]
    if name_or_code not in head:
        raise ValueError(f"{ws.title}: 列 {name_or_code} が見つからない")
    j = head.index(name_or_code)
    idx, val = [], []
    for r in rows[first_row:]:
        ym = nfkc(r[ym_col]).strip() if len(r) > ym_col else ""
        if re.fullmatch(r"\d{6}", ym):
            idx.append(pd.Period(f"{ym[:4]}-{ym[4:]}", "M"))
            val.append(num(r[j]))
    return pd.Series(val, index=pd.PeriodIndex(idx, freq="M")).dropna()


def parse_ita_link(b: bytes) -> dict[str, pd.Series]:
    """第3次産業活動指数の接続指数（b2020_ITA_linkj.xlsx）→ 総合の原指数（1988年～）。
    「第３次産業総合」（K1D000000I）の原指数は接続されていないが、「第３次産業総合（直接調整法）」（J000000I）の
    原指数が入っている。直接調整法は季節調整のやり方が違うだけで、原指数は総合と同じ値。"""
    return {"ita_o_link": _link_col(_xlsx(b)["原指数"], 1, "J000000I", 0, 6)}


def parse_iip_link(b: bytes) -> dict[str, pd.Series]:
    """鉱工業指数の接続指数・業種別季節調整済指数（b2020_sgs1j.xlsx）→ 鉱工業・製造工業の生産（1978年～）。"""
    ws = _xlsx(b)["生産付加価値額"]
    return {"ip_mm_s_link": _link_col(ws, 2, "鉱工業", 1, 4), "ip_mfg_s_link": _link_col(ws, 2, "製造工業", 1, 4)}


def link_junction(link: pd.Series, cur: pd.Series, tol: float = 0.051) -> tuple[float, int]:
    """接続指数と現行系列のつなぎ目の確認。戻り値：(重なりの最初の12か月の差の最大, その後の月で差がある月数)。
    最初の12か月が一致しなければ、基準や接続のしかたが変わった可能性があるので取り込まない。
    その後の差は現行系列の年間補正などで生じうる（使うのは現行系列の開始より前だけなので支障はない）。"""
    ov = link.index.intersection(cur.dropna().index)
    if len(ov) < 12:
        raise ValueError("接続指数と現行系列の重なりが12か月に満たない")
    d = (link.loc[ov] - cur.loc[ov]).abs()
    return float(d.iloc[:12].max()), int((d.iloc[12:] > tol).sum())


def parse_ita(komj: bytes, ksmj: bytes) -> dict[str, pd.Series]:
    out = {}
    out.update(_meti_rows(_xlsx(komj)["ITA"], 0, {"K1D000000I": "ita_o"}))
    out.update(_meti_rows(_xlsx(ksmj)["ITA"], 0, {"K1D000000I": "ita_s"}))
    return out


def parse_mk_t08(b: bytes) -> dict[str, pd.Series]:
    """毎勤 時系列第8表（季節調整済指数、5人以上）。G列=総実労働時間、M列=常用雇用。"""
    ws = _xlsx(b).worksheets[0]
    h, e, year = {}, {}, None
    for r in ws.iter_rows(values_only=True):
        lab = nfkc(r[0]).replace(" ", "")
        if "平均" in lab:
            continue
        my = re.search(r"(\d{4})年", lab)
        mm = re.search(r"(\d{1,2})月", lab)
        if my:
            year = int(my.group(1))
        if not (mm and year):
            continue
        p = pd.Period(f"{year}-{int(mm.group(1)):02d}", "M")
        if not np.isnan(num(r[6])):
            h[p] = num(r[6])
        if not np.isnan(num(r[12])):
            e[p] = num(r[12])
    if len(h) < 6:
        raise ValueError("毎勤 第8表：月次の行が読めない")
    return {"h_sa": pd.Series(h).sort_index(), "e_sa": pd.Series(e).sort_index()}


_ERA = {"昭和": 1925, "平成": 1988, "令和": 2018}


def _year_of(c) -> int | None:
    if isinstance(c, (int, float)) and 1900 < c < 2100:
        return int(c)
    s = nfkc(c)
    m = re.search(r"(昭和|平成|令和)\s*(\d+|元)\s*年", s)
    if m:
        return _ERA[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))
    m = re.search(r"(\d{4})年", s)
    return int(m.group(1)) if m else None


def parse_lfs_1a1(b: bytes) -> dict[str, pd.Series]:
    """労調 長期時系列 1-a-1 の「季節調整値」シート → 四半期平均（3か月そろった期のみ、万人）。"""
    ws = _xlsx(b)["季節調整値"]
    cols = {"lf": 4, "emp": 7, "ee": 10, "un": 13, "nilf": 16}
    m_vals = {k: {} for k in cols}
    year = None
    for r in ws.iter_rows(values_only=True):
        y = _year_of(r[0])
        if y:
            year = y
        mm = re.match(r"(\d{1,2})月", nfkc(r[1]))
        if not (mm and year):
            continue
        p = pd.Period(f"{year}-{int(mm.group(1)):02d}", "M")
        for k, j in cols.items():
            v = num(r[j])
            if not np.isnan(v):
                m_vals[k][p] = v
    out = {}
    for k, dct in m_vals.items():
        s = pd.Series(dct).sort_index()
        g = s.groupby(s.index.asfreq("Q"))
        q = g.mean()[g.count() == 3].round(1)
        out[k] = q
    if len(out["lf"]) < 100:
        raise ValueError("労調 1-a-1：季節調整値が読めない")
    return out


def parse_stock(b: bytes) -> pd.DataFrame:
    """固定資本ストック速報（実質原系列）→ DataFrame(q, priv, pub)、10億円。"""
    ws = _xlsx(b).worksheets[0]
    qmap = {"1": 1, "4": 2, "7": 3, "10": 4}
    rows, year = [], None
    for r in ws.iter_rows(values_only=True):
        if isinstance(r[0], (int, float)) and 1900 < r[0] < 2100:
            year = int(r[0])
        m = re.match(r"\s*(\d{1,2})\s*-", nfkc(r[1]))
        if not (m and year and m.group(1) in qmap):
            continue
        rows.append((pd.Period(f"{year}Q{qmap[m.group(1)]}", "Q"), num(r[3]), num(r[5])))
    df = pd.DataFrame(rows, columns=["q", "priv", "pub"]).set_index("q")
    if len(df) < 40:
        raise ValueError("資本ストック：四半期の行が読めない")
    return df


def parse_esri_gaku(b: bytes) -> pd.Series:
    """GDP速報 実質季節調整系列 gaku-jk*.csv → 国内総生産(支出側)、10億円。"""
    import csv
    txt = b.decode("cp932", "replace")
    qmap = {"1": 1, "4": 2, "7": 3, "10": 4}
    out, year, started = {}, None, False
    for r in csv.reader(txt.splitlines()):
        if not r:
            continue
        c0 = nfkc(r[0])
        my = re.match(r"\s*(\d{4})/", c0)
        if my:
            year = int(my.group(1))
        mq = re.search(r"(\d{1,2})\s*-\s*\d{1,2}\.", c0)
        if mq and year and mq.group(1) in qmap:
            started = True
            out[pd.Period(f"{year}Q{qmap[mq.group(1)]}", "Q")] = num(r[1])
        elif started:
            break
    s = pd.Series(out).sort_index()
    if len(s) < 40:
        raise ValueError("GDP速報CSV：四半期の行が読めない")
    return s


TANKAN_CODES = {
    "TK99F1000607GCQ00000": "equip_mfg",
    "TK99F2000607GCQ00000": "equip_nonmfg",
    "TK99F1000608GCQ00000": "emp_mfg",
    "TK99F2000608GCQ00000": "emp_nonmfg",
}


def parse_boj_tankan(b: bytes) -> dict[str, pd.Series]:
    d = json.loads(b.decode("utf-8"))
    out = {}
    for r in d["RESULTSET"]:
        k = TANKAN_CODES.get(r["SERIES_CODE"])
        if not k:
            continue
        v = r["VALUES"]
        idx = [pd.Period(f"{str(x)[:4]}Q{int(str(x)[4:])}", "Q") for x in v["SURVEY_DATES"]]
        out[k] = pd.Series([num(x) for x in v["VALUES"]], index=idx).sort_index()
    if set(out) != set(TANKAN_CODES.values()):
        raise ValueError(f"短観：系列が足りない {set(TANKAN_CODES.values()) - set(out)}")
    return out


# ---------------------------------------------------------------- e-Stat の一覧ページ
@dataclass
class EstatItem:
    title: str
    stat_inf_id: str
    url: str
    file_type: str
    survey: str = ""
    updated: str = ""


def _strip(html: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html)
    t = re.sub(r"&nbsp;", " ", t)
    return re.sub(r"\s+", " ", nfkc(t)).strip()


def estat_items(html: str, base: str) -> list[EstatItem]:
    items = []
    for art in html.split('<article class="stat-dataset_list-item">')[1:]:
        dls = re.findall(r'href="(/stat-search/file-download\?statInfId=(\d+)&(?:amp;)?fileKind=\d+)"[^>]*'
                         r'data-file_type="([A-Z]+)"', art, flags=re.S)
        if not dls:
            continue
        text = _strip(art)
        sv = re.search(r"調査年月\s*(\S+)", text)
        up = re.search(r"公開（更新）日\s*(\d{4}-\d{2}-\d{2})", text) or re.search(r"公開\(更新\)日\s*(\d{4}-\d{2}-\d{2})", text)
        for href, sid, ft in dls:
            items.append(EstatItem(text, sid, urljoin(base, href.replace("&amp;", "&")), ft,
                                   sv.group(1) if sv else "", up.group(1) if up else ""))
    return items


def estat_walk(start: str, must: dict[str, str], max_pages: int = 40, log=print) -> list[EstatItem]:
    """e-Stat のファイル一覧を、start から同じ toukei/tstat の下位ページへたどって集める。"""
    seen, queue, items = set(), [start], []
    while queue and len(seen) < max_pages:
        u = queue.pop(0)
        if u in seen:
            continue
        seen.add(u)
        html = decode(http_get(u))
        time.sleep(0.5)
        items += estat_items(html, u)
        p = _Links()
        p.feed(html)
        for h, _ in p.links:
            v = urljoin(u, h).split("#")[0]
            q = v.replace("&amp;", "&")
            if "/stat-search/files?" not in q or "/en/" in q or "layout=dataset" in q:
                continue
            if all(f"{k}={val}" in q for k, val in must.items()) and q not in seen:
                queue.append(q)
    log(f"    e-Stat一覧 {len(seen)}ページ、ファイル{len(items)}件")
    uniq = {}
    for it in items:
        uniq.setdefault((it.stat_inf_id, it.file_type), it)
    return list(uniq.values())


# ---------------------------------------------------------------- 反映と検証
@dataclass
class Change:
    file: str
    name: str
    old_last: str
    new_last: str
    added: list = field(default_factory=list)
    revised: int = 0
    max_rev_pct: float = 0.0
    warn: list = field(default_factory=list)


def merge_series(old: pd.Series | None, new: pd.Series, rev_warn_pct: float, jump_warn_pct: float,
                 file: str, name: str, absolute: bool = False) -> tuple[pd.Series, Change]:
    """absolute=True のとき（DIなど）、改定・変化は％ではなくポイント差で測る。"""
    new = new.dropna()
    if old is None or old.dropna().empty:
        ch = Change(file, name, "-", str(new.index[-1]), added=[str(p) for p in new.index])
        return new, ch
    o = old.dropna()
    new = new[new.index >= o.index[0]]          # 既存の開始期より前には伸ばさない（推計の標本期間を変えないため）
    comb = new.combine_first(o)
    full = pd.period_range(comb.index.min(), comb.index.max(), freq=comb.index.freq)
    comb = comb.reindex(full)
    ov = o.index.intersection(new.index)
    diff = (new[ov] - o[ov]).abs()
    rel = diff if absolute else (diff / o[ov].abs().replace(0, np.nan) * 100).fillna(diff)
    unit = "pt" if absolute else "%"
    ch = Change(file, name, str(o.index[-1]), str(comb.dropna().index[-1]),
                added=[str(p) for p in new.index if p > o.index[-1]],
                revised=int((rel > 1e-3).sum()), max_rev_pct=float(rel.max()) if len(rel) else 0.0)
    if ch.max_rev_pct > rev_warn_pct:
        p = rel.idxmax()
        ch.warn.append(f"重なる期間の改定が大きい：{p} {o[p]:g}→{new[p]:g}（{ch.max_rev_pct:.1f}{unit}）")
    s = comb.dropna()
    for p in [pd.Period(a, s.index.freq) for a in ch.added]:
        if name.endswith("_o"):   # 原系列は季節性があるので、前年同月比の変化で見る
            k = 12 if s.index.freqstr.startswith("M") else 4
            if all(x in s.index for x in (p - k, p - 1, p - 1 - k)):
                g = ((s[p] / s[p - k]) - (s[p - 1] / s[p - 1 - k])) * 100
                if abs(g) > jump_warn_pct:
                    ch.warn.append(f"新しい期の前年同月比が前月から大きく動いた：{p} {g:+.1f}pt")
            continue
        if p - 1 in s.index and (absolute or s[p - 1] != 0):
            g = (s[p] - s[p - 1]) if absolute else (s[p] / s[p - 1] - 1) * 100
            if abs(g) > jump_warn_pct:
                ch.warn.append(f"新しい期の変化が大きい：{p - 1}→{p} {g:+.1f}{unit}")
    if new.index[-1] < o.index[-1]:
        ch.warn.append(f"新しいファイルの最終期 {new.index[-1]} が既存 {o.index[-1]} より前（古いファイルの可能性）")
    return comb, ch


def fmt(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    s = f"{x:.4f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


def start_code(idx) -> str:
    p = idx[0]
    return f"{p.year}Q{p.quarter}" if idx.freqstr.startswith("Q") else f"{p.year}{p.month:02d}"


class SeriesFile:
    """`name|start|v1,...`（または `name|v1,...` ＋既定の開始期）形式のファイルを読み書きする。"""

    def __init__(self, path: Path, default_start: str | None = None):
        self.path, self.default_start = path, default_start
        self.lines = path.read_text(encoding="utf-8").splitlines()
        from .data import _read_series_file
        self.series = _read_series_file(path, default_start)
        self.three = {ln.split("|")[0]: ln.count("|") == 2 for ln in self.lines if "|" in ln and not ln.startswith("#")}

    def set(self, name: str, s: pd.Series):
        self.series[name] = s

    def write(self, note: str | None):
        out, done = [], set()
        comments = [ln for ln in self.lines if ln.startswith("#")]
        if note:
            comments.append(f"# {note}")
        for ln in self.lines:
            if ln.startswith("#") or "|" not in ln:
                continue
            name = ln.split("|")[0]
            out.append(self._line(name))
            done.add(name)
        for name in self.series:
            if name not in done:
                out.append(self._line(name))
        self.path.write_text("\n".join(comments + out) + "\n", encoding="utf-8")

    def _line(self, name: str) -> str:
        s = self.series[name]
        if self.three.get(name, True):
            return f"{name}|{start_code(s.index)}|" + ",".join(fmt(v) for v in s.values)
        st = pd.Period(self.default_start, s.index.freq)
        s = s.reindex(pd.period_range(st, s.index[-1], freq=s.index.freq))
        return f"{name}|" + ",".join(fmt(v) for v in s.values)


# ---------------------------------------------------------------- 追加の解析（日銀側の入力・公表値）
_SEXAGE_COLS = {"m": [15, 16, 17, 18, 19, 20], "f": [24, 25, 26, 27, 28, 29]}
_AGES = ["15", "25", "35", "45", "55", "65"]


def parse_lfs_sexage(b: bytes, prefix: str, ndigits: int) -> dict[str, pd.Series]:
    """労調 長期時系列（性・年齢階級別、原数値）→ 四半期平均。prefix='lfpr'（労働力人口比率）か 'pop'（15歳以上人口）。"""
    ws = _xlsx(b)["原数値"]
    mv, year = {}, None
    for r in ws.iter_rows(values_only=True):
        year = _year_of(r[0]) or year
        mm = re.match(r"(\d{1,2})月", nfkc(r[1]))
        if not (mm and year):
            continue
        p = pd.Period(f"{year}-{int(mm.group(1)):02d}", "M")
        for sx, cs in _SEXAGE_COLS.items():
            for a, j in zip(_AGES, cs):
                v = num(r[j])
                if not np.isnan(v):
                    mv.setdefault(f"{prefix}_{sx}{a}", {})[p] = v
    out = {}
    for k, dct in mv.items():
        s = pd.Series(dct).sort_index()
        g = s.groupby(s.index.asfreq("Q"))
        out[k] = g.mean()[g.count() == 3].round(ndigits)
    if len(out) != 12:
        raise ValueError(f"労調 性・年齢別（{prefix}）：列が読めない")
    return out


def parse_mk_days(b: bytes) -> dict[str, pd.Series]:
    """毎勤 長期時系列 実数・指数累積データ「実数」CSV → 出勤日数（調査産業計・就業形態計）。規模 T=5人以上、0=30人以上。"""
    import csv
    rd = csv.reader(b.decode("cp932", "replace").splitlines())
    hdr = next(rd)
    j = hdr.index("出勤日数")
    out = {"days_5": {}, "days_30": {}}
    for r in rd:
        if len(r) <= j or r[3].strip() != "TL" or r[5].strip() != "0" or not r[2].strip().isdigit():
            continue
        k = {"T": "days_5", "0": "days_30"}.get(r[4].strip())
        if k and r[j].strip():
            out[k][pd.Period(f"{int(r[1])}-{int(r[2]):02d}", "M")] = float(r[j])
    res = {k: pd.Series(v).sort_index() for k, v in out.items()}
    if min(len(v) for v in res.values()) < 100:
        raise ValueError("毎勤 実数CSV：出勤日数が読めない")
    return res


def parse_cao_gap_pdf(b: bytes) -> dict:
    """内閣府「今週の指標」GDPギャップ号のPDF → 号数・公表日・対象期・速報・本文の値・表1（今回・前回）。"""
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pypdf がありません。`pip install pypdf` を実行してください。") from e
    pages = [nfkc(p.extract_text()) for p in PdfReader(BytesIO(b)).pages]
    t0, txt = pages[0], "\n".join(pages)
    no = int(re.search(r"No\.\s*(\d+)", t0).group(1))
    dt = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", t0)
    pub = f"{dt.group(1)}-{int(dt.group(2)):02d}-{int(dt.group(3)):02d}"
    tg = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*-\s*(\d{1,2})\s*月期\s*GDP\s*([12])\s*次速報", t0)
    target = pd.Period(f"{tg.group(1)}Q{(int(tg.group(2)) + 2) // 3}", "Q")
    flat = re.sub(r"\s+", "", t0)
    hv = re.search(r"推計結果は([+\-▲]?)(\d+\.\d)%", flat)
    head = (-1 if hv and hv.group(1) in ("-", "▲") else 1) * float(hv.group(2)) if hv else np.nan
    blocks = {"今回": [], "前回": []}
    for ln in txt.splitlines():
        m = re.match(r"\s*(\d{2})年(\d{1,2})-(\d{1,2})月期([12])次QE後\s+(.*)$", ln)
        if not m:
            continue
        vals = [(-1 if s.strip() else 1) * float(v) for s, v in re.findall(r"(▲\s*)?(\d+\.\d)", m.group(5))]
        kind = "今回" if (pd.Period(f"20{m.group(1)}Q{(int(m.group(2)) + 2) // 3}", "Q"), m.group(4)) == (target, tg.group(4)) \
            and len(blocks["今回"]) <= len(blocks["前回"]) else "前回"
        blocks[kind].append(vals)
    now = [v for blk in blocks["今回"] for v in blk]
    prev = [v for blk in blocks["前回"] for v in blk]
    if not now:
        raise ValueError(f"今週の指標 {no}：表1が読めない")
    return {"no": no, "date": pub, "target": target, "kind": f"{tg.group(4)}次", "head": head,
            "now_start": target - (len(now) - 1), "now": now,
            "prev_start": (target - len(prev)) if len(prev) < len(now) else target - (len(prev) - 1), "prev": prev}


def parse_pesa(b6: bytes, b8: bytes) -> dict[str, pd.Series]:
    """一般職業紹介状況 表6（有効求人数）・表8（就職件数）の「パート含む」シート → 季節調整値の四半期平均（千人・千件）。
    列：年（西暦）＝A列、四半期平均＝見出しが「1～3月平均」…「10～12月平均」の列（季節調整値）。"""
    out = {}
    for b, name in ((b6, "vacancy_eff"), (b8, "placements")):
        wb = _xlsx(b)
        ws = next(w for w in wb.worksheets if "パート含む" in nfkc(w.title))
        rows = list(ws.iter_rows(values_only=True))
        qcol = {}
        for r in rows[:8]:
            for j, c in enumerate(r):
                m = re.match(r"(\d{1,2})[~～-](\d{1,2})月平均", nfkc(c).replace(" ", ""))
                if m:
                    qcol[(int(m.group(1)) + 2) // 3] = j
        if len(qcol) != 4:
            raise ValueError(f"{name}：四半期平均の列が見つからない")
        vals = {}
        for r in rows:
            m = re.match(r"(\d{4})年", nfkc(r[0]))
            if not m:
                continue
            for q, j in qcol.items():
                v = num(r[j]) if j < len(r) else np.nan
                if not np.isnan(v):
                    vals[pd.Period(f"{m.group(1)}Q{q}", "Q")] = round(v / 1000, 1)
        out[name] = pd.Series(vals).sort_index()
    return out


# ---------------------------------------------------------------- e-Stat API（アプリケーションIDが要る）
ESTAT_API = "https://api.e-stat.go.jp/rest/3.0/app/json"


def _as_list(x):
    return x if isinstance(x, list) else [x]


def estat_pick_codes(meta: dict, tab_words: list[str]) -> tuple[dict, list[str]]:
    """getMetaInfo の結果から、表章項目は tab_words を含むもの、その他の分類は
    「非農林業」「総数」などの集計項目を選ぶ。戻り値：({パラメータ名: コード}, 選んだ内容の説明)。"""
    objs = _as_list(meta["GET_META_INFO"]["METADATA_INF"]["CLASS_INF"]["CLASS_OBJ"])
    prefer = ["非農林業", "総数", "男女計", "15歳以上", "全国", "計", "総計"]
    params, notes = {}, []
    for o in objs:
        oid, oname = o["@id"], nfkc(o["@name"])
        cls = _as_list(o["CLASS"])
        if oid == "time":
            continue
        if oid == "tab":
            hit = [c for c in cls if any(w in nfkc(c["@name"]) for w in tab_words)]
            if len(hit) < len(tab_words):
                raise ValueError(f"表章項目に {tab_words} が見つからない：{[c['@name'] for c in cls]}")
            params["cdTab"] = ",".join(c["@code"] for c in hit)
            notes.append(f"{oname}：" + "、".join(nfkc(c["@name"]) for c in hit))
            continue
        pick = None
        for w in prefer:
            pick = next((c for c in cls if nfkc(c["@name"]).strip() == w), None)
            if pick:
                break
        if pick is None:
            pick = next((c for c in cls if str(c.get("@level", "1")) == "1"), cls[0])
        key = "cd" + oid[0].upper() + oid[1:]
        params[key] = pick["@code"]
        notes.append(f"{oname}：{nfkc(pick['@name'])}")
    return params, notes


def parse_estat_values(js: dict, tab_map: dict[str, str]) -> dict[str, pd.Series]:
    """getStatsData の結果 → {系列名: 月次系列}。tab_map は表章項目名に含まれる語 → 系列名。"""
    sd = js["GET_STATS_DATA"]["STATISTICAL_DATA"]
    objs = _as_list(sd["CLASS_INF"]["CLASS_OBJ"])
    names = {o["@id"]: {c["@code"]: nfkc(c["@name"]) for c in _as_list(o["CLASS"])} for o in objs}
    out = {k: {} for k in tab_map.values()}
    for v in _as_list(sd["DATA_INF"]["VALUE"]):
        tname = names["tab"].get(v.get("@tab"), "")
        key = next((k for w, k in tab_map.items() if w in tname), None)
        m = re.search(r"(\d{4})年\s*(\d{1,2})月", names["time"].get(v["@time"], ""))
        if key and m:
            x = num(v.get("$"))
            if not np.isnan(x):
                out[key][pd.Period(f"{m.group(1)}-{int(m.group(2)):02d}", "M")] = x
    return {k: pd.Series(d).sort_index() for k, d in out.items()}


def parse_boj_gap_xlsx(b: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    """日銀「需給ギャップと潜在成長率」gap.xlsx →
    (四半期：需給ギャップ・資本投入ギャップ・労働投入ギャップ, 年度半期：潜在成長率と寄与度)。"""
    wb = _xlsx(b)
    q = {}
    for r in wb["data1"].iter_rows(values_only=True):
        m = re.match(r"(\d{4})\.(\d)Q", nfkc(r[0]) if r and r[0] else "")
        if m:
            q[pd.Period(f"{m.group(1)}Q{m.group(2)}", "Q")] = [num(x) for x in r[1:4]]
    Q = pd.DataFrame.from_dict(q, orient="index", columns=["boj_gap", "boj_kgap", "boj_lgap"]).dropna(how="all")
    h = []
    for r in wb["data2"].iter_rows(values_only=True):
        m = re.match(r"(\d{4})\.([12])\s*:", nfkc(r[0]) if r and r[0] else "")
        if m:
            h.append((int(m.group(1)), int(m.group(2)), nfkc(r[0]).split(":")[1].strip(), *[num(x) for x in r[1:6]]))
    H = pd.DataFrame(h, columns=["FY", "H", "期間", "pot", "tfp", "cap", "hours", "emp"])
    if len(Q) < 100 or len(H) < 50:
        raise ValueError("日銀 gap.xlsx：data1／data2 が読めない")
    return Q, H
