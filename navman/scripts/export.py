"""
XLS export using openpyxl.

Generates two files:
  assignments.xlsx — one row per navigation task
  pairings.xlsx    — one row per pair
"""
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_SI_FILL = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")
_IF_FILL = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
_RTL = Alignment(horizontal="right", vertical="center", readingOrder=2)
_CENTER = Alignment(horizontal="center", vertical="center")


def _header_row(ws, headers: list[str]) -> None:
    ws.append(headers)
    for ci, cell in enumerate(ws[ws.max_row], start=1):
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _RTL


def _auto_width(ws) -> None:
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=8)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max_len + 4, 40)


def _rtl_sheet(ws) -> None:
    """Enable RTL display for the sheet."""
    ws.sheet_view.rightToLeft = True


# ---------------------------------------------------------------------------
# Assignments export
# ---------------------------------------------------------------------------

def export_assignments(
    assignments: list[dict],
    points_db: list[dict],
    output_dir: str,
) -> str:
    """Write assignments.xlsx. Returns file path."""
    pt_map = {p["id"]: p for p in points_db}
    n_pts = max((len(a["points"]) for a in assignments), default=0)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "משימות ניווט"
    _rtl_sheet(ws)

    headers = ["מס' משימה", "מקטע"] + [f"נקודה {i+1}" for i in range(n_pts)] + ["אורך (ק\"מ)"]
    _header_row(ws, headers)

    for a in sorted(assignments, key=lambda x: x["index"]):
        fill = _SI_FILL if "ס→" in a["section"] else _IF_FILL
        pts_cells = []
        for pid in a["points"]:
            pt = pt_map.get(pid)
            label = f"{pid}" + (f" – {pt['description']}" if pt and pt.get("description") else "")
            pts_cells.append(label)
        # Pad to n_pts
        pts_cells += [""] * (n_pts - len(pts_cells))

        row_data = [a["index"], a["section"]] + pts_cells + [a["length_km"]]
        ws.append(row_data)

        for cell in ws[ws.max_row]:
            cell.fill = fill
            cell.alignment = _RTL

    _auto_width(ws)

    out_path = Path(output_dir) / "assignments.xlsx"
    wb.save(str(out_path))
    return str(out_path)


# ---------------------------------------------------------------------------
# Pairings export
# ---------------------------------------------------------------------------

def export_pairings(
    pairings: list[dict],
    output_dir: str,
) -> str:
    """Write pairings.xlsx. Returns file path."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "שיבוץ זוגות"
    _rtl_sheet(ws)

    headers = [
        "מס' זוג",
        "משתתף א", "ציון א", "מס' משימה א", "מקטע א", "אורך א (ק\"מ)",
        "משתתף ב", "ציון ב", "מס' משימה ב", "מקטע ב", "אורך ב (ק\"מ)",
    ]
    _header_row(ws, headers)

    for pr in sorted(pairings, key=lambda x: x["pair_index"]):
        row_data = [
            pr["pair_index"],
            pr["p1_name"],
            round(pr["p1_score"], 1),
            pr["p1_task_index"] or "",
            pr["p1_section"],
            pr["p1_length_km"],
            pr["p2_name"],
            round(pr["p2_score"], 1) if pr["p2_name"] else "",
            pr["p2_task_index"] or "",
            pr["p2_section"],
            pr["p2_length_km"] if pr["p2_name"] else "",
        ]
        ws.append(row_data)
        for cell in ws[ws.max_row]:
            cell.alignment = _RTL

    _auto_width(ws)

    out_path = Path(output_dir) / "pairings.xlsx"
    wb.save(str(out_path))
    return str(out_path)
