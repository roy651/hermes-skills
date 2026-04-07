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

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    ext = Path(image_path).suffix.lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")

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

    payload = {
        "model": api_cfg["model"],
        "max_tokens": 512,
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
        resp = requests.post(api_cfg["url"], headers=headers, json=payload, timeout=90)
        if resp.status_code == 429 and delay is not None:
            print(f"[map_parser] rate-limited (429), retrying in {delay}s (attempt {attempt+1}/{len(delays)})...", file=sys.stderr)
            time.sleep(delay)
            continue
        resp.raise_for_status()
        break

    data = resp.json()
    if "choices" not in data:
        raise ValueError(f"תגובת LLM לא תקינה: {str(data)[:200]}")
    content = data["choices"][0]["message"]["content"].strip()

    print(f"[map_parser] LLM response: {content[:200]}", file=sys.stderr)

    match = re.search(r"\[[\d\s,]+\]", content)
    if not match:
        raise ValueError(f"LLM החזיר תשובה לא תקינה: {content[:100]}")

    ids = json.loads(match.group())
    valid_ids = [i for i in ids if i in all_point_ids]

    if not valid_ids:
        raise ValueError("לא זוהו נקודות תקינות בתוך הגבול — השתמש ב-/edit_map להגדרה ידנית")

    return sorted(valid_ids)


def format_map_preview(filtered_ids: list[int], total_points: int) -> str:
    ids_str = ", ".join(str(i) for i in filtered_ids)
    return (
        f"נקודות שזוהו בתוך הגבול ({len(filtered_ids)} מתוך {total_points}):\n"
        f"{ids_str}\n\n"
        "אשר עם /cm (confirm_map) או ערוך עם /em <מספרים מופרדים בפסיק>"
    )
