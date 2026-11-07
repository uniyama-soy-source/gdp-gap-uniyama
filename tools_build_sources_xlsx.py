"""data_sources.xlsx（算出に必要な統計・公表日・リンク・早期化の余地）を作る。内容は sources.json。"""
import json
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent
S = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))

FONT = "Arial"
HDR = PatternFill("solid", fgColor="1F4E79")
CRIT = PatternFill("solid", fgColor="FCE4D6")
thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)


def style_header(ws, row, ncol):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c)
        cell.font = Font(name=FONT, bold=True, color="FFFFFF")
        cell.fill = HDR
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = BORDER


def body(cell, wrap=True):
    cell.font = Font(name=FONT, size=10)
    cell.alignment = Alignment(wrap_text=wrap, vertical="top")
    cell.border = BORDER


def to_dt(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.strptime(s, "%Y-%m-%d")


wb = Workbook()

# ---------------- シート1：統計一覧 ----------------
ws = wb.active
ws.title = "統計一覧"
ws["A1"] = "GDPギャップ（内閣府）・需給ギャップ（日銀）の算出に必要な統計（2026年4-6月期の推計を例に）"
ws["A1"].font = Font(name=FONT, bold=True, size=13)
ws["A2"] = ("公表日時＝2026Q2の推計に必要な最終データ（6月分・4-6月期分など）が最初に公表された日時。"
            "確認度：確認＝公表元ページで確認／推定＝例年の慣行などから推定。四半期末からの日数は数式（=公表日−2026/6/30）。"
            "ボトルネック列：◎＝そのギャップを実績だけで自前推計できる最早日を決める統計、○＝改定値・同日内の順番で効く統計、△＝時期ではなく精度を左右する統計。")
ws["A2"].font = Font(name=FONT, size=9, italic=True)
ws.merge_cells("A2:Q2")
ws.row_dimensions[2].height = 42
ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
ws["A3"] = "四半期末"
ws["B3"] = date(2026, 6, 30)
ws["B3"].number_format = "yyyy-mm-dd"
for c in ("A3", "B3"):
    ws[c].font = Font(name=FONT, size=10, bold=True)

cols = ["No", "使用先", "統計名", "作成機関", "使う系列", "ギャップ算出での役割", "2026Q2推計に必要なデータ",
        "公表日時（初出）", "確認度", "四半期末からの日数", "ボトルネック", "リンク",
        "推計で補う方法", "補えば何日早まるか（目安）", "補う場合の誤差・注意", "本モデルでの扱い", "メモ"]
HR = 5
for j, h in enumerate(cols, 1):
    ws.cell(row=HR, column=j, value=h)
style_header(ws, HR, len(cols))
for i, r in enumerate(S["stats"], 1):
    row = HR + i
    vals = [i, r["use"], r["name"], r["org"], r["series"], r["role"], r["need"], to_dt(r.get("date")),
            r.get("conf", ""), None, r.get("bottleneck", ""), r.get("url", ""),
            r.get("fill", ""), r.get("gain", ""), r.get("risk", ""), r.get("model", ""), r.get("memo", "")]
    for j, v in enumerate(vals, 1):
        cell = ws.cell(row=row, column=j, value=v)
        body(cell)
    dcell = ws.cell(row=row, column=8)
    if r.get("date"):
        dcell.number_format = "yyyy-mm-dd hh:mm" if " " in r["date"] else "yyyy-mm-dd"
        ws.cell(row=row, column=10, value=f"=IF(H{row}=\"\",\"\",INT(H{row})-$B$3)")
    else:
        dcell.value = r.get("date_text", "")
        ws.cell(row=row, column=10, value="")
    ws.cell(row=row, column=10).number_format = "0"
    url = r.get("url")
    if url:
        ws.cell(row=row, column=12).hyperlink = url
        ws.cell(row=row, column=12).font = Font(name=FONT, size=10, color="0563C1", underline="single")
    if str(r.get("bottleneck", "")).startswith("◎"):
        for j in range(1, len(cols) + 1):
            ws.cell(row=row, column=j).fill = CRIT
widths = [8, 12, 26, 12, 30, 34, 22, 15, 10, 9, 12, 40, 38, 18, 34, 30, 30]
for j, w in enumerate(widths, 1):
    ws.column_dimensions[get_column_letter(j)].width = w
ws.freeze_panes = ws.cell(row=HR + 1, column=4)
ws.auto_filter.ref = f"A{HR}:{get_column_letter(len(cols))}{HR + len(S['stats'])}"

# ---------------- シート2：タイムライン ----------------
wt = wb.create_sheet("タイムライン")
wt["A1"] = "2026年4-6月期分の公表順（日付のある統計のみ）"
wt["A1"].font = Font(name=FONT, bold=True, size=13)
hdr = ["公表日時", "四半期末からの日数", "統計名", "使用先", "ボトルネック"]
for j, h in enumerate(hdr, 1):
    wt.cell(row=3, column=j, value=h)
style_header(wt, 3, len(hdr))
dated = sorted([r for r in S["stats"] if r.get("date")], key=lambda r: to_dt(r["date"]))
for i, r in enumerate(dated, 4):
    vals = [to_dt(r["date"]), None, r["name"], r["use"], r.get("bottleneck", "")]
    for j, v in enumerate(vals, 1):
        body(wt.cell(row=i, column=j, value=v), wrap=False)
    wt.cell(row=i, column=1).number_format = "yyyy-mm-dd hh:mm" if " " in r["date"] else "yyyy-mm-dd"
    wt.cell(row=i, column=2, value=f"=INT(A{i})-統計一覧!$B$3").number_format = "0"
    if str(r.get("bottleneck", "")).startswith("◎"):
        for j in range(1, len(hdr) + 1):
            wt.cell(row=i, column=j).fill = CRIT
for j, w in enumerate([17, 10, 46, 14, 16], 1):
    wt.column_dimensions[get_column_letter(j)].width = w

# ---------------- シート3：早期化シナリオ ----------------
wsn = wb.create_sheet("早期化シナリオ")
wsn["A1"] = "自前推計を出せる最早日（2026年4-6月期の例）"
wsn["A1"].font = Font(name=FONT, bold=True, size=13)
hdr = ["ギャップ", "シナリオ", "最早日", "四半期末からの日数", "公式公表日", "公式公表より何日早いか", "待つ統計／補う統計", "精度への影響（目安）"]
for j, h in enumerate(hdr, 1):
    wsn.cell(row=3, column=j, value=h)
style_header(wsn, 3, len(hdr))
for i, r in enumerate(S["scenarios"], 4):
    vals = [r["gap"], r["name"], to_dt(r["date"]), None, to_dt(r["official"]), None, r["wait"], r["acc"]]
    for j, v in enumerate(vals, 1):
        body(wsn.cell(row=i, column=j, value=v))
    wsn.cell(row=i, column=3).number_format = "yyyy-mm-dd"
    wsn.cell(row=i, column=5).number_format = "yyyy-mm-dd"
    wsn.cell(row=i, column=4, value=f"=INT(C{i})-統計一覧!$B$3").number_format = "0"
    wsn.cell(row=i, column=6, value=f"=INT(E{i})-INT(C{i})").number_format = "0"
for j, w in enumerate([10, 34, 12, 10, 12, 12, 60, 46], 1):
    wsn.column_dimensions[get_column_letter(j)].width = w
n = 4 + len(S["scenarios"])
wsn.cell(row=n + 1, column=1, value="注：日付は統計一覧シートの公表日から機械的に組んだもの。各統計の取得・処理時間（数時間）は含まない。").font = Font(name=FONT, size=9, italic=True)

# ---------------- シート4：出典 ----------------
wsrc = wb.create_sheet("出典・注記")
wsrc["A1"] = "出典・注記"
wsrc["A1"].font = Font(name=FONT, bold=True, size=13)
for i, t in enumerate(S["notes"], 3):
    c = wsrc.cell(row=i, column=1, value=t)
    c.font = Font(name=FONT, size=10)
    c.alignment = Alignment(wrap_text=True, vertical="top")
wsrc.column_dimensions["A"].width = 140

out = ROOT / "data_sources.xlsx"
wb.save(out)
print(out)
