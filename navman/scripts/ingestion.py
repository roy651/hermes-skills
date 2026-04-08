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
import hashlib
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


def _image_hash(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


_PARSE_CACHE_FILE = Path(__file__).parent.parent / "data" / "parse_cache.json"


def _cache_load() -> dict:
    if _PARSE_CACHE_FILE.exists():
        try:
            return json.loads(_PARSE_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _cache_save(cache: dict) -> None:
    _PARSE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PARSE_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_nav_points(pts: list) -> list[dict]:
    """Keep only rows with plausible ITM coordinates."""
    valid = []
    for p in pts:
        if not isinstance(p, dict):
            continue
        try:
            pid = int(p.get("id", 0))
            x = float(p.get("x", 0))
            y = float(p.get("y", 0))
        except (TypeError, ValueError):
            continue
        if 0 < pid < 10_000 and 620_000 <= x <= 900_000 and 3_300_000 <= y <= 3_700_000:
            valid.append({**p, "id": pid, "x": x, "y": y})
    return valid


def parse_nav_images(image_paths: list[str], api_cfg: dict) -> tuple[list[dict], list[str]]:
    """
    Parse navigation point table image(s).
    Returns (points, failed_image_names).

    Strategy per image:
    1. Cache hit — return previously parsed result instantly.
    2. Docling — extracts table structure deterministically.
    3. LLM batch — all cache-missing images sent in ONE call.

    Results are cached by image content hash to avoid redundant LLM calls.
    """
    if _DoclingConverter is None and not (api_cfg and api_cfg.get("key")):
        raise RuntimeError("docling not installed and no LLM configured — cannot parse image")

    cache = _cache_load()
    all_points: list[dict] = []
    needs_llm: list[str] = []
    cache_dirty = False

    for path in image_paths:
        key = _image_hash(path)

        # Cache hit
        if key in cache:
            print(f"[ingestion] cache hit: {Path(path).name} ({len(cache[key])} pts)", file=sys.stderr)
            all_points.extend(cache[key])
            continue

        # Docling
        rows = _docling_extract_rows(path)
        if rows is not None:
            rows = _skip_header(rows)
            cols = _detect_nav_columns(rows)
            if cols is not None:
                pts = _validate_nav_points(_parse_nav_rows(rows, cols))
                if pts:
                    if api_cfg and api_cfg.get("key"):
                        garbled = [p for p in pts if p.get("description") and not _is_hebrew(p["description"])]
                        if garbled:
                            _fix_nav_descriptions_llm(pts, path, api_cfg)
                    all_points.extend(pts)
                    cache[key] = pts
                    cache_dirty = True
                    continue

        needs_llm.append(path)

    # Batch LLM call for all images that docling couldn't handle
    failed_names: list[str] = []
    if needs_llm:
        if not (api_cfg and api_cfg.get("key")):
            if not all_points:
                raise RuntimeError("docling לא הצליח לזהות טבלה ואין LLM מוגדר — נסה להעלות קובץ CSV/XLS")
        else:
            batch_key = "batch:" + hashlib.sha256(
                "".join(sorted(_image_hash(p) for p in needs_llm)).encode()
            ).hexdigest()[:16]

            if batch_key in cache and cache[batch_key]:
                print(f"[ingestion] batch cache hit ({len(cache[batch_key])} pts)", file=sys.stderr)
                all_points.extend(cache[batch_key])
            else:
                llm_pts, failed_names = _llm_parse_nav_batch(needs_llm, api_cfg)
                all_points.extend(llm_pts)
                if llm_pts:  # only cache successful results
                    cache[batch_key] = llm_pts
                    cache_dirty = True
                if failed_names:
                    print(f"[ingestion] Warning: LLM failed for {failed_names}", file=sys.stderr)

    if cache_dirty:
        _cache_save(cache)

    if not all_points:
        raise ValueError("לא הצלחתי לפרסר את הטבלה — נסה להעלות קובץ CSV/XLS")
    return all_points, failed_names


def _llm_parse_nav_batch(image_paths: list[str], api_cfg: dict) -> tuple[list[dict], list[str]]:
    """
    Send all images in ONE LLM call, trying models in priority order.
    Free models first; falls back to paid models on 429 or connection errors.
    Returns (points, failed_names).
    """
    import time
    import requests

    prompt = (
        "These images show one or more pages of a navigation points table. "
        "The table has 3 or 4 columns in any order:\n"
        "- Point number: small integer (typically 1–200)\n"
        "- Easting (X): 6-digit number, roughly 620000–900000, may have decimal (e.g. 663917.4)\n"
        "- Northing (Y): 7-digit number, roughly 3300000–3700000 (e.g. 3405245)\n"
        "- Description: optional Hebrew text — may be absent\n\n"
        "Extract ALL points from ALL pages and return ONE JSON array.\n"
        "Required keys: id (int), x (float), y (float). Add 'description' only if present.\n"
        "STRICT RULES — skip any row where:\n"
        "- The point number is not a plain integer (e.g. contains letters like 'D', is blank, or is garbled)\n"
        "- The X or Y coordinate is missing, non-numeric, or outside the valid ranges above\n"
        "- You are guessing or inferring a value that is not clearly visible — do NOT hallucinate\n"
        "Fix obvious single-character OCR errors (e.g. '0' vs 'O', '1' vs 'I') only when the correct "
        "value is unambiguous from context. When in doubt, skip the row.\n"
        "Example: [{\"id\":1,\"x\":663917.4,\"y\":3405245},{\"id\":2,\"x\":663687.4,\"y\":3405200}]"
    )

    content = []
    for path in image_paths:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = Path(path).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    content.append({"type": "text", "text": prompt})

    headers = {"Authorization": f"Bearer {api_cfg['key']}", "Content-Type": "application/json"}

    # Try each model in priority order (free first, then paid fallbacks)
    models = api_cfg.get("models") or [api_cfg["model"]]
    last_error = None

    for model in models:
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": content}],
        }
        print(f"[ingestion] trying model: {model}", file=sys.stderr)

        try:
            delays = [5, 15, 45]
            resp = None
            for attempt, delay in enumerate(delays + [None]):
                resp = requests.post(api_cfg["url"], headers=headers, json=payload, timeout=120)
                if resp.status_code == 429 and delay is not None:
                    print(f"[ingestion] {model} rate-limited, retrying in {delay}s ({attempt+1}/{len(delays)})...", file=sys.stderr)
                    time.sleep(delay)
                    continue
                if resp.status_code in (429, 404, 400, 503):
                    # Not retrying further — move to next model
                    print(f"[ingestion] {model} returned {resp.status_code}, trying next model...", file=sys.stderr)
                    raise requests.HTTPError(response=resp)
                resp.raise_for_status()
                break

            data = resp.json()
            if "choices" not in data:
                print(f"[ingestion] {model} returned no choices, trying next...", file=sys.stderr)
                continue

            text = (data["choices"][0]["message"].get("content") or "").strip()
            if not text:
                print(f"[ingestion] {model} returned empty/null content, trying next...", file=sys.stderr)
                continue
            match = re.search(r"\[[\s\S]*\]", text)
            if not match:
                print(f"[ingestion] {model} returned no JSON array, trying next...", file=sys.stderr)
                continue

            pts = _validate_nav_points(json.loads(match.group()))
            if pts:
                print(f"[ingestion] success with model: {model}", file=sys.stderr)
                return pts, []

            print(f"[ingestion] {model} returned 0 valid points, trying next...", file=sys.stderr)

        except Exception as e:
            last_error = e
            print(f"[ingestion] {model} failed: {e}", file=sys.stderr)
            continue

    print(f"[ingestion] all models exhausted. Last error: {last_error}", file=sys.stderr)
    return [], [Path(p).name for p in image_paths]


def parse_special_image(image_path: str, api_cfg: dict) -> list[dict]:
    """
    Parse a special-points image (start, intermediate, finish).

    Handles any coordinate format: X\\Y, X,Y, X Y, X/Y etc.
    Returns ordered list of up to 3 dicts: [{x, y, label?}, ...]
    Order in image is assumed: start first, intermediate second, finish third.
    """
    cache_key = "special:" + _image_hash(image_path)
    cache = _cache_load()
    if cache_key in cache:
        print(f"[ingestion] special cache hit: {Path(image_path).name}", file=sys.stderr)
        return cache[cache_key]

    prompt = (
        "This image shows special navigation points (start, intermediate, finish). "
        "Each point has a coordinate pair. The format may be X\\Y, X,Y, X/Y, or two separate numbers. "
        "X (easting) is a 6-digit number ~620000–900000. Y (northing) is a 7-digit number ~3300000–3700000.\n\n"
        "Extract up to 3 points IN THE ORDER THEY APPEAR (top to bottom = start, intermediate, finish). "
        "Return ONLY a JSON array:\n"
        "[{\"order\":1,\"x\":664792,\"y\":3409517,\"label\":\"optional Hebrew label\"},...]\n"
        "Include label only if clearly visible. Fix OCR errors in numbers."
    )
    resp = call_vision_llm(image_path, prompt, api_cfg)
    match = re.search(r"\[[\s\S]*\]", resp)
    if not match:
        raise ValueError("LLM לא הצליח לזהות נקודות מיוחדות בתמונה")
    pts = json.loads(match.group())
    result = []
    for p in pts:
        try:
            x = float(p.get("x", 0))
            y = float(p.get("y", 0))
            if 620_000 <= x <= 900_000 and 3_300_000 <= y <= 3_700_000:
                result.append({"order": int(p.get("order", len(result) + 1)),
                                "x": x, "y": y,
                                "label": p.get("label", "")})
        except (TypeError, ValueError):
            continue
    result.sort(key=lambda p: p["order"])
    cache[cache_key] = result
    _cache_save(cache)
    return result


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
        lines.append(f"  {p['id']}: ({p['x']:.0f}, {p['y']:.0f}) — {p.get('description', '')}")
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
