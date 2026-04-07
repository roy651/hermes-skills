"""
Table ingestion: parse navigation points and participant tables.

Supported input formats:
  - CSV (auto-detect delimiter)
  - XLS/XLSX (openpyxl)
  - Images (docling table extraction → LLM description correction)

Navigation points table schema (4 columns, any order detected):
  id (int), x (float), y (float), description (Hebrew text)

Participant table schema (3 columns):
  index (int), name (Hebrew text), score (str — various formats)
"""
import base64
import csv
import json
import re
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    openpyxl = None

try:
    from docling.document_converter import DocumentConverter as _DoclingConverter
    _docling_conv = None  # lazy-init singleton
except ImportError:
    _DoclingConverter = None
    _docling_conv = None


def _get_docling():
    global _docling_conv
    if _DoclingConverter is None:
        return None
    if _docling_conv is None:
        _docling_conv = _DoclingConverter()
    return _docling_conv


def release_models() -> None:
    """Release docling model weights from memory. Call after a session completes."""
    global _docling_conv
    if _docling_conv is not None:
        _docling_conv = None
        import gc
        gc.collect()
        print("[ingestion] docling models released from memory", file=sys.stderr)


# ---------------------------------------------------------------------------
# LLM vision helper
# ---------------------------------------------------------------------------

def call_vision_llm(image_path: str, prompt: str, api_cfg: dict) -> str:
    """Call an OpenAI-compatible vision endpoint; return response text.

    Retries on 429 with exponential backoff (5s, 15s, 45s).
    """
    import time
    import requests

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = Path(image_path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")

    payload = {
        "model": api_cfg["model"],
        "max_tokens": 2048,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_cfg['key']}",
        "Content-Type": "application/json",
    }

    delays = [5, 15, 45]
    for attempt, delay in enumerate(delays + [None]):
        resp = requests.post(api_cfg["url"], headers=headers, json=payload, timeout=60)
        if resp.status_code == 429 and delay is not None:
            print(f"[ingestion] rate-limited (429), retrying in {delay}s (attempt {attempt+1}/{len(delays)})...", file=sys.stderr)
            time.sleep(delay)
            continue
        resp.raise_for_status()
        break

    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# Docling table extraction
# ---------------------------------------------------------------------------

def _docling_extract_rows(image_path: str) -> list[list[str]] | None:
    """Extract table rows from an image using docling. Returns None if unavailable or no table found."""
    conv = _get_docling()
    if conv is None:
        return None
    try:
        result = conv.convert(image_path)
        tables = result.document.tables
        if not tables:
            return None
        rows = []
        for row in tables[0].data.grid:
            cells = [cell.text.strip() if cell and cell.text else "" for cell in row]
            if any(c for c in cells):
                rows.append(cells)
        return rows if rows else None
    except Exception as e:
        print(f"[ingestion] docling failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Row parsing helpers
# ---------------------------------------------------------------------------

def _is_float(s: str) -> bool:
    try:
        float(str(s).replace(",", "."))
        return True
    except ValueError:
        return False


def _to_float(s: str) -> float:
    return float(str(s).replace(",", ".").strip())


def _is_hebrew(text: str) -> bool:
    return any("\u05d0" <= c <= "\u05ea" for c in text)


def _detect_nav_columns(rows: list[list[str]]) -> tuple[int, int, int, int] | None:
    """
    Auto-detect column indices: (id_col, x_col, y_col, desc_col).

    Heuristics:
      - ID col: small positive int (< 1000)
      - X col: ITM easting (600_000 – 900_000), detected via median
      - Y col: ITM northing (3_300_000 – 3_700_000), detected via median
      - Desc col: remaining
    """
    if not rows:
        return None
    ncols = max(len(r) for r in rows)
    if ncols < 3:
        return None

    sample = [r for r in rows if len(r) >= 3][:10]
    if not sample:
        return None

    col_stats = {}
    for ci in range(ncols):
        vals = [r[ci].strip() for r in sample if ci < len(r)]
        floats = sorted([_to_float(v) for v in vals if _is_float(v)])
        median = floats[len(floats) // 2] if floats else 0
        float_ratio = len(floats) / len(vals) if vals else 0
        col_stats[ci] = {
            "mostly_float": float_ratio >= 0.6,
            "median": median,
        }

    easting_col = northing_col = id_col = desc_col = None

    for ci, s in col_stats.items():
        if not s["mostly_float"]:
            continue
        if 600_000 <= s["median"] <= 900_000:
            easting_col = ci
        elif 3_300_000 <= s["median"] <= 3_700_000:
            northing_col = ci
        elif 0 < s["median"] < 10_000:
            id_col = ci

    if easting_col is None or northing_col is None:
        return None

    remaining = [ci for ci in range(ncols) if ci not in (easting_col, northing_col, id_col)]
    desc_col = remaining[0] if remaining else None

    if id_col is None:
        for ci in remaining:
            s = col_stats.get(ci, {})
            if s.get("mostly_float") and 0 < s.get("median", 0) < 10_000:
                id_col = ci
                remaining = [r for r in remaining if r != ci]
                desc_col = remaining[0] if remaining else None
                break

    return id_col, easting_col, northing_col, desc_col


def _parse_nav_rows(rows: list[list[str]], cols: tuple) -> list[dict]:
    id_col, x_col, y_col, desc_col = cols
    points = []
    for row in rows:
        try:
            if len(row) <= max(c for c in cols if c is not None):
                continue
            pt_id = int(float(str(row[id_col]).strip()))
            x = _to_float(row[x_col])
            y = _to_float(row[y_col])
            # Reject obviously garbled coordinates (missing decimal)
            if not (600_000 <= x <= 900_000) or not (3_300_000 <= y <= 3_700_000):
                continue
            if not (0 < pt_id < 10_000):
                continue
            desc = row[desc_col].strip() if desc_col is not None and desc_col < len(row) else ""
            points.append({"id": pt_id, "x": x, "y": y, "description": desc})
        except (ValueError, IndexError):
            continue
    return points


def _detect_participant_columns(rows: list[list[str]]) -> tuple[int, int, int] | None:
    """Detect (index_col, name_col, score_col) for participant table."""
    if not rows:
        return None
    sample = rows[:10]
    ncols = max(len(r) for r in sample)
    if ncols < 2:
        return None

    score_pat = re.compile(r"^\d+(\.\d+)?([/]\d+(\.\d+)?|%)?$")
    id_col = name_col = score_col = None

    col_score_hits = {}
    for ci in range(ncols):
        hits = sum(
            1 for r in sample
            if ci < len(r) and score_pat.match(r[ci].strip().replace(" ", ""))
        )
        col_score_hits[ci] = hits

    score_col = max(col_score_hits, key=lambda k: col_score_hits[k])
    if col_score_hits[score_col] == 0:
        score_col = ncols - 1

    for ci in range(ncols):
        if ci == score_col:
            continue
        vals = [r[ci].strip() for r in sample if ci < len(r)]
        try:
            ids = [int(v) for v in vals if v]
            if ids and max(ids) < 500:
                id_col = ci
                break
        except ValueError:
            pass

    for ci in range(ncols):
        if ci not in (score_col, id_col):
            name_col = ci
            break

    if name_col is None or score_col is None:
        return None

    return id_col, name_col, score_col


def _parse_participant_rows(rows: list[list[str]], cols: tuple) -> list[dict]:
    id_col, name_col, score_col = cols
    participants = []
    idx = 1
    for row in rows:
        try:
            if len(row) <= score_col:
                continue
            name = row[name_col].strip() if name_col is not None and name_col < len(row) else ""
            score_raw = row[score_col].strip() if score_col < len(row) else ""
            if not name or not score_raw:
                continue
            row_idx = int(float(row[id_col].strip())) if id_col is not None and id_col < len(row) else idx
            participants.append({
                "index": row_idx,
                "name": name,
                "score_raw": score_raw,
            })
            idx += 1
        except (ValueError, IndexError):
            continue
    return participants


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------

def _skip_header(rows: list[list[str]]) -> list[list[str]]:
    """Drop first row if it looks like a header (mostly non-numeric)."""
    if not rows:
        return rows
    first = rows[0]
    numeric_count = sum(1 for c in first if _is_float(c))
    if numeric_count < len(first) // 2:
        return rows[1:]
    return rows


# ---------------------------------------------------------------------------
# Public: parse navigation points
# ---------------------------------------------------------------------------

def parse_nav_file(file_path: str) -> list[dict]:
    """Parse CSV or XLS(X) navigation points file. Returns list of point dicts."""
    ext = Path(file_path).suffix.lower()
    if ext in (".csv", ".txt", ".tsv"):
        rows = _read_csv(file_path)
    elif ext in (".xlsx", ".xls"):
        rows = _read_xlsx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    rows = _skip_header(rows)
    cols = _detect_nav_columns(rows)
    if cols is None:
        raise ValueError("לא הצלחתי לזהות עמודות נקודות ניווט בקובץ (צפוי: מספר, X, Y, תיאור)")
    return _parse_nav_rows(rows, cols)


def parse_nav_images(image_paths: list[str], api_cfg: dict) -> list[dict]:
    """
    Parse navigation point table image(s).

    Strategy:
    1. Docling — extracts table structure; coordinates and IDs are exact.
       If descriptions are garbled (non-Hebrew) and api_cfg is set, LLM corrects them.
    2. LLM only — send the raw image and ask for the full table as JSON.
       Used when docling finds no table.
    """
    if _DoclingConverter is None and not (api_cfg and api_cfg.get("key")):
        raise RuntimeError("docling not installed and no LLM configured — cannot parse image")

    # --- Strategy 1: Docling ---
    all_points: list[dict] = []
    for path in image_paths:
        rows = _docling_extract_rows(path)
        if rows is None:
            continue
        rows = _skip_header(rows)
        cols = _detect_nav_columns(rows)
        if cols is None:
            continue
        pts = _parse_nav_rows(rows, cols)
        all_points.extend(pts)

    if all_points:
        if api_cfg and api_cfg.get("key"):
            garbled = [p for p in all_points if p.get("description") and not _is_hebrew(p["description"])]
            if garbled:
                _fix_nav_descriptions_llm(all_points, image_paths[-1], api_cfg)
        return all_points

    # --- Strategy 2: LLM only ---
    if not (api_cfg and api_cfg.get("key")):
        raise RuntimeError("docling לא הצליח לזהות טבלה ואין LLM מוגדר — נסה להעלות קובץ CSV/XLS")

    prompt = (
        "This image contains a navigation points table with 4 columns: "
        "point number (integer < 1000), X coordinate (ITM easting, 6-digit number ~668000), "
        "Y coordinate (ITM northing, 7-digit number ~3390000), and a Hebrew description.\n\n"
        "Return ONLY a JSON array of objects with keys: id (int), x (float), y (float), description (string).\n"
        "Fix any obvious OCR errors in numbers. Ignore header rows. Example:\n"
        '[{"id":240,"x":668237.376,"y":3390075,"description":"כיפה סתיה"},...]'
    )
    all_points = []
    for path in image_paths:
        try:
            resp = call_vision_llm(path, prompt, api_cfg)
            match = re.search(r"\[.*\]", resp, re.DOTALL)
            if match:
                all_points.extend(json.loads(match.group()))
        except Exception as e:
            print(f"[ingestion] LLM nav parse failed for {path}: {e}", file=sys.stderr)

    if not all_points:
        raise ValueError("לא הצלחתי לפרסר את הטבלה — נסה להעלות קובץ CSV/XLS")
    return all_points


def _fix_nav_descriptions_llm(points: list[dict], image_path: str, api_cfg: dict) -> None:
    """Use vision LLM to correct Hebrew descriptions in-place."""
    prompt = (
        "This is a navigation points table image. The columns are: Hebrew description, X coordinate, Y coordinate, point number.\n"
        "Return ONLY a JSON object mapping point_number (as string key) to its Hebrew description. Example:\n"
        '{"20": "פיצול נחל", "21": "אוכף", "22": "כיפה"}\n'
        "Include only points visible in the image."
    )
    try:
        resp = call_vision_llm(image_path, prompt, api_cfg)
        match = re.search(r"\{.*\}", resp, re.DOTALL)
        if not match:
            return
        id_to_desc = {int(k): v for k, v in json.loads(match.group()).items()}
        for p in points:
            if p["id"] in id_to_desc:
                p["description"] = id_to_desc[p["id"]]
    except Exception as e:
        print(f"[ingestion] _fix_nav_descriptions_llm: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public: parse participants
# ---------------------------------------------------------------------------

def parse_participant_file(file_path: str) -> list[dict]:
    """Parse CSV or XLS(X) participant table. Returns list of participant dicts."""
    ext = Path(file_path).suffix.lower()
    if ext in (".csv", ".txt", ".tsv"):
        rows = _read_csv(file_path)
    elif ext in (".xlsx", ".xls"):
        rows = _read_xlsx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    rows = _skip_header(rows)
    cols = _detect_participant_columns(rows)
    if cols is None:
        raise ValueError("לא הצלחתי לזהות עמודות משתתפים (צפוי: מספר, שם, ציון)")
    return _parse_participant_rows(rows, cols)


def parse_participant_images(image_paths: list[str], api_cfg: dict) -> list[dict]:
    """
    Parse participant table image(s).

    Strategy:
    1. Docling → column detection → parse. If names are garbled, LLM corrects.
    2. LLM only — send image, ask for full table as JSON.
    """
    if _DoclingConverter is None and not (api_cfg and api_cfg.get("key")):
        raise RuntimeError("docling not installed and no LLM configured — cannot parse image")

    # --- Strategy 1: Docling ---
    all_participants: list[dict] = []
    for path in image_paths:
        rows = _docling_extract_rows(path)
        if rows is None:
            continue
        rows = _skip_header(rows)
        cols = _detect_participant_columns(rows)
        if cols is None:
            continue
        all_participants.extend(_parse_participant_rows(rows, cols))

    if all_participants:
        if api_cfg and api_cfg.get("key"):
            garbled = [p for p in all_participants if not _is_hebrew(p.get("name", ""))]
            if garbled:
                try:
                    prompt = (
                        "This is a participants table image with columns: index, Hebrew name, score.\n"
                        "Return ONLY a JSON array: [{\"index\":int, \"name\":str, \"score_raw\":str}, ...]\n"
                        "Preserve the score format exactly (e.g. '85', '7/10', '85%')."
                    )
                    resp = call_vision_llm(image_paths[-1], prompt, api_cfg)
                    match = re.search(r"\[.*\]", resp, re.DOTALL)
                    if match:
                        return json.loads(match.group())
                except Exception as e:
                    print(f"[ingestion] LLM participant name fix failed: {e}", file=sys.stderr)
        return all_participants

    # --- Strategy 2: LLM only ---
    if not (api_cfg and api_cfg.get("key")):
        raise RuntimeError("docling לא הצליח לזהות טבלה ואין LLM מוגדר — נסה להעלות קובץ CSV/XLS")

    prompt = (
        "This image contains a participants table with 3 columns: index (integer), Hebrew name, score.\n"
        "The score may be a number, fraction (7/10), decimal, or percentage.\n"
        "Return ONLY a JSON array: [{\"index\":int, \"name\":str, \"score_raw\":str}, ...]\n"
        "Ignore header rows."
    )
    all_participants = []
    for path in image_paths:
        try:
            resp = call_vision_llm(path, prompt, api_cfg)
            match = re.search(r"\[.*\]", resp, re.DOTALL)
            if match:
                all_participants.extend(json.loads(match.group()))
        except Exception as e:
            print(f"[ingestion] LLM participant parse failed for {path}: {e}", file=sys.stderr)

    if not all_participants:
        raise ValueError("לא הצלחתי לפרסר את טבלת המשתתפים — נסה להעלות קובץ CSV/XLS")
    return all_participants


# ---------------------------------------------------------------------------
# Preview helpers
# ---------------------------------------------------------------------------

def format_nav_preview(points: list[dict], max_rows: int = 8) -> str:
    lines = [f"נמצאו {len(points)} נקודות ניווט:"]
    for p in points[:max_rows]:
        lines.append(f"  {p['id']}: ({p['x']:.0f}, {p['y']:.0f}) — {p['description']}")
    if len(points) > max_rows:
        lines.append(f"  ... ועוד {len(points) - max_rows} נקודות")
    return "\n".join(lines)


def format_participant_preview(participants: list[dict], max_rows: int = 8) -> str:
    lines = [f"נמצאו {len(participants)} משתתפים:"]
    for p in participants[:max_rows]:
        lines.append(f"  {p['index']}. {p['name']} — ציון: {p['score_raw']}")
    if len(participants) > max_rows:
        lines.append(f"  ... ועוד {len(participants) - max_rows} משתתפים")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal: file readers
# ---------------------------------------------------------------------------

def _read_csv(file_path: str) -> list[list[str]]:
    with open(file_path, encoding="utf-8-sig", errors="replace") as f:
        sample = f.read(4096)
    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|") if sample.strip() else None
    with open(file_path, encoding="utf-8-sig", errors="replace") as f:
        reader = csv.reader(f, dialect=dialect) if dialect else csv.reader(f)
        return [[str(c).strip() for c in row] for row in reader if any(c.strip() for c in row)]


def _read_xlsx(file_path: str) -> list[list[str]]:
    if openpyxl is None:
        raise RuntimeError("openpyxl not installed")
    wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if any(c for c in cells):
            rows.append(cells)
    wb.close()
    return rows
