"""
Map image parser — extract point numbers visible inside a drawn boundary.

Sends the map image to a vision LLM and asks it to identify which navigation
point numbers appear inside the hand-drawn enclosed region.
"""
import base64
import json
import re
import sys
import time
from pathlib import Path


def _resize_for_api(image_path: str, max_kb: int = 500) -> tuple[bytes, str]:
    """Return (bytes, mime) — resized/compressed if over max_kb."""
    raw = Path(image_path).read_bytes()
    ext = Path(image_path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
    if len(raw) <= max_kb * 1024:
        return raw, mime
    try:
        from PIL import Image
        import io
        img = Image.open(image_path).convert("RGB")
        w, h = img.size
        if max(w, h) > 1500:
            scale = 1500 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        for quality in (85, 70, 55, 40):
            buf.seek(0); buf.truncate()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= max_kb * 1024:
                break
        print(f"[map_parser] resized {len(raw)//1024}KB → {buf.tell()//1024}KB (q={quality})", file=sys.stderr)
        buf.seek(0)
        return buf.read(), "image/jpeg"
    except ImportError:
        print("[map_parser] Pillow not available — sending original image", file=sys.stderr)
        return raw, mime


def parse_map_image(image_path: str, all_point_ids: list[int], api_cfg: dict) -> list[int]:
    """
    Given a map image with a hand-drawn closed boundary, return the list of
    navigation point IDs that appear INSIDE the boundary.

    Falls back to returning all_point_ids on error so the user can correct
    with /edit_map.
    """
    if not api_cfg or not api_cfg.get("key"):
        raise ValueError("VISION_API_KEY לא מוגדר — לא ניתן לנתח תמונת מפה")

    import requests

    img_bytes, mime = _resize_for_api(image_path)
    b64 = base64.b64encode(img_bytes).decode()

    known_ids_str = ", ".join(str(i) for i in sorted(all_point_ids))

    prompt = (
        "This is a topographic navigation map. There is a hand-drawn closed boundary line "
        "(drawn in pen or marker) enclosing a specific area on the map.\n\n"
        "Inside the map you can see numbered navigation points (small dots with numbers next to them).\n\n"
        f"The full list of possible point numbers is: {known_ids_str}\n\n"
        "Your task: list ONLY the point numbers that appear INSIDE the drawn boundary (not outside it).\n\n"
        "Return ONLY a JSON array of integers, for example: [240, 241, 243, 244, 265]\n"
        "Do not include any explanation, just the JSON array."
    )

    msg_content = [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        {"type": "text", "text": prompt},
    ]

    headers = {
        "Authorization": f"Bearer {api_cfg['key']}",
        "Content-Type": "application/json",
    }

    models = api_cfg.get("models") or [api_cfg["model"]]
    all_point_ids_set = set(all_point_ids)
    last_error = None

    for model in models:
        payload = {
            "model": model,
            "max_tokens": 512,
            "messages": [{"role": "user", "content": msg_content}],
        }
        print(f"[map_parser] trying model: {model}", file=sys.stderr)
        try:
            delays = [5, 15, 45]
            for attempt, delay in enumerate(delays + [None]):
                resp = requests.post(api_cfg["url"], headers=headers, json=payload, timeout=90)
                if resp.status_code == 429 and delay is not None:
                    print(f"[map_parser] {model} rate-limited, retrying in {delay}s ({attempt+1}/{len(delays)})...", file=sys.stderr)
                    time.sleep(delay)
                    continue
                if resp.status_code in (429, 404, 400, 503):
                    print(f"[map_parser] {model} returned {resp.status_code}, trying next...", file=sys.stderr)
                    raise requests.HTTPError(response=resp)
                resp.raise_for_status()
                break

            data = resp.json()
            if "choices" not in data:
                print(f"[map_parser] {model} no choices, trying next...", file=sys.stderr)
                continue

            content_str = data["choices"][0]["message"]["content"].strip()
            print(f"[map_parser] LLM response: {content_str[:200]}", file=sys.stderr)

            match = re.search(r"\[[\d\s,]+\]", content_str)
            if not match:
                print(f"[map_parser] {model} no JSON array, trying next...", file=sys.stderr)
                continue

            ids = json.loads(match.group())
            valid_ids = [i for i in ids if i in all_point_ids_set]
            if not valid_ids:
                print(f"[map_parser] {model} returned 0 valid IDs, trying next...", file=sys.stderr)
                continue

            return sorted(valid_ids)

        except Exception as e:
            last_error = e
            print(f"[map_parser] {model} failed: {e}", file=sys.stderr)
            continue

    raise ValueError(f"כל המודלים נכשלו — השתמש ב-/edit_map להגדרה ידנית. שגיאה: {last_error}")


def format_map_preview(filtered_ids: list[int], total_points: int) -> str:
    ids_str = ", ".join(str(i) for i in filtered_ids)
    return (
        f"נקודות שזוהו בתוך הגבול ({len(filtered_ids)} מתוך {total_points}):\n"
        f"{ids_str}\n\n"
        "אשר עם /cm (confirm_map) או ערוך עם /em <מספרים מופרדים בפסיק>"
    )
