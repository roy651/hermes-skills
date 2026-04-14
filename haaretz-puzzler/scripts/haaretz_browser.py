#!/usr/bin/env python3
"""Haaretz Crossword Puzzle Fetcher — Playwright Browser Automation

Uses Playwright to:
1. Search for the latest puzzle on haaretz.co.il
2. Navigate to the puzzle article
3. Login with email + password (2-step flow)
4. Find the Nth puzzle image by alt text pattern: "<N>תשבץ <DATE>"
5. Download and save the JPEG

Exit:
    stdout: MEDIA:<local_path>\nALT_INFO:<article_title>
    stderr: Progress messages
    exit code 0 on success, 1 on error
"""

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    print("ERROR: playwright not installed", file=sys.stderr)
    sys.exit(1)

try:
    import aiohttp
except ImportError:
    print("ERROR: aiohttp not installed", file=sys.stderr)
    sys.exit(1)

HAARETZ_BASE = "https://www.haaretz.co.il"
SEARCH_URLS = {
    "puzzle": "https://www.haaretz.co.il/ty-search?q=%D7%A2%D7%95%D7%A9%D7%94+%D7%A9%D7%9B%D7%9C",
    "logic":  "https://www.haaretz.co.il/ty-search?q=%D7%AA%D7%A9%D7%91%D7%A5%20%D7%94%D7%99%D7%92%D7%99%D7%95%D7%9F",
}
LOGIN_URL = "https://login.haaretz.co.il/?htm_source=site&htm_medium=MidPage&htm_campaign=register&htm_content=login"


def _env_defaults():
    """Load .env file."""
    dotenv = Path(__file__).parent.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip())


def _best_image_url(src: str, srcset: str) -> str:
    """Pick highest-resolution URL from srcset, or strip resize params from src."""
    if srcset:
        # srcset format: "url1 1x, url2 2x" or "url1 300w, url2 600w, url3 1200w"
        best_url = src
        best_w = 0
        for entry in srcset.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.rsplit(None, 1)
            url_part = parts[0].strip()
            desc = parts[1].strip() if len(parts) > 1 else "1x"
            try:
                if desc.endswith("w"):
                    w = int(desc[:-1])
                elif desc.endswith("x"):
                    w = int(float(desc[:-1]) * 1000)
                else:
                    w = 0
            except ValueError:
                w = 0
            if w > best_w:
                best_w = w
                best_url = url_part
        if best_url and best_url != src:
            return best_url

    # No usable srcset — try stripping CDN resize query params from src
    # e.g. remove ?width=NNN&quality=NN or similar
    if src and ("?" in src or "&" in src):
        from urllib.parse import urlparse as _up, urlencode, parse_qs, urlunparse
        parsed = _up(src)
        params = parse_qs(parsed.query)
        # Strip common resize params; keep none (original) if only resize params exist
        strip_keys = {"width", "w", "height", "h", "quality", "q", "fit", "f", "auto", "format", "dpr", "resize"}
        remaining = {k: v for k, v in params.items() if k.lower() not in strip_keys}
        clean_query = urlencode(remaining, doseq=True)
        src = urlunparse(parsed._replace(query=clean_query))

    return src


async def download_image(url: str, referer: str, output_dir: str) -> tuple[str, int]:
    """Download an image with referer header."""
    os.makedirs(output_dir, exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": referer,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=60), allow_redirects=True) as resp:
            if resp.status != 200:
                raise Exception(f"HTTP {resp.status}")
            data = await resp.read()
            if len(data) < 1024:
                raise Exception(f"Too small ({len(data)} bytes)")
            ct = resp.headers.get("Content-Type", "")
            if "image" not in ct and "octet-stream" not in ct:
                raise Exception(f"Not an image: {ct}")

    parsed = urlparse(url)
    fname = unquote(parsed.path.split("/")[-1])
    if not fname or "." not in fname:
        fname = f"puzzle_{int(time.time())}.jpg"
    dest = os.path.join(output_dir, fname)
    counter = 1
    while os.path.exists(dest):
        name, ext = os.path.splitext(fname)
        dest = os.path.join(output_dir, f"{name}_{counter}{ext}")
        counter += 1
    with open(dest, "wb") as f:
        f.write(data)
    return dest, len(data)


async def run(email: str, password: str, puzzle_index: int, output_dir: str, puzzle_type: str = "puzzle"):
    """Main browser automation flow."""
    search_url = SEARCH_URLS.get(puzzle_type, SEARCH_URLS["puzzle"])
    is_logic = puzzle_type == "logic"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="he-IL",
            timezone_id="Asia/Jerusalem",
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['he', 'en']});
            window.chrome = { runtime: {} };
        """)
        page = await context.new_page()

        try:
            # ── Step 1: Search for latest puzzle ──
            label = "תשבץ היגיון" if is_logic else "תשבץ"
            print(f"[1/6] Searching for latest {label}...", file=sys.stderr)
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            articles = page.locator("article")
            if await articles.count() == 0:
                raise Exception("No search results found")

            first = articles.first
            link_el = first.locator("a").first
            href = await link_el.get_attribute("href", timeout=10000)
            title_text = await link_el.inner_text(timeout=10000)

            if not href or not title_text:
                heading = first.locator("h3, h2, h1").first
                try:
                    title_text = await heading.inner_text(timeout=5000)
                except Exception:
                    title_text = "תשבץ"
                if not href:
                    raise Exception("Could not find article URL")

            article_url = urljoin(HAARETZ_BASE, href)
            print(f"  Found: {title_text}", file=sys.stderr)
            print(f"  URL: {article_url}", file=sys.stderr)

            # ── Step 2: Login ──
            print("[2/6] Logging in...", file=sys.stderr)
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            current_url = page.url
            if "login" not in current_url.lower():
                print(f"  Already logged in (URL: {current_url})", file=sys.stderr)
            else:
                # Find email input
                email_input = page.locator('input[type="email"], input[placeholder*="example"]')
                await email_input.first.wait_for(state="visible", timeout=15000)
                await email_input.first.fill(email, timeout=5000)
                print("  Email entered", file=sys.stderr)

                # Click "המשך" button
                continue_btn = page.locator("button", has_text="המשך").first
                await continue_btn.click()
                print("  Clicked המשך, waiting for password screen...", file=sys.stderr)

                # Wait for password input to appear
                pw_input = page.locator('input[type="password"]')
                await pw_input.wait_for(state="visible", timeout=15000)

                # Small delay before filling (mimics human)
                await page.wait_for_timeout(500)
                await pw_input.fill(password, timeout=5000)
                print("  Password entered", file=sys.stderr)

                await page.wait_for_timeout(300)

                # Must use exact match — "(להתחברות עם מייל אחר)" also contains "התחברות"
                login_btn = page.get_by_role("button", name="התחברות", exact=True)
                await login_btn.wait_for(state="visible", timeout=10000)
                btn_text = await login_btn.inner_text(timeout=5000)
                print(f"  Clicking button: '{btn_text}'", file=sys.stderr)
                await login_btn.click()
                print("  Waiting for redirect...", file=sys.stderr)

                # Wait for the page to navigate away from login
                try:
                    await page.wait_for_url(lambda u: "login.haaretz.co.il" not in u, timeout=20000)
                except Exception:
                    await page.wait_for_timeout(5000)

                current_url = page.url
                print(f"  After login: {current_url}", file=sys.stderr)

                # Verify login succeeded
                if "login.haaretz" in current_url:
                    # Check if there's an error message
                    try:
                        body_text = await page.inner_text("body", timeout=5000)
                    except Exception:
                        body_text = "could not read"
                    raise Exception(f"Login failed. Still on login page.\nBody: {body_text[:300]}")
                print("  Login successful", file=sys.stderr)

            # ── Step 3: Navigate to puzzle article ──
            print("[3/6] Navigating to puzzle article...", file=sys.stderr)
            await page.goto(article_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

            # ── Step 4: Scroll to trigger lazy loading ──
            print("[4/6] Scrolling...", file=sys.stderr)
            await page.evaluate("window.scrollBy(0, 600)")
            await page.wait_for_timeout(2000)
            await page.evaluate("window.scrollBy(0, 600)")
            await page.wait_for_timeout(2000)

            # ── Step 5: Find puzzle image ──
            all_imgs = page.locator("img")
            total = await all_imgs.count()
            print(f"[5/6] Scanning {total} images...", file=sys.stderr)

            if is_logic:
                # Logic puzzle: one image per article — find first with "תשבץ" in alt,
                # or fall back to first sufficiently large image.
                image_url = None
                alt_for_log = ""
                fallback_url = None
                for i in range(total):
                    img = all_imgs.nth(i)
                    try:
                        alt = await img.get_attribute("alt", timeout=2000) or ""
                        src = await img.get_attribute("src", timeout=2000) or ""
                        if not src:
                            continue
                        raw_src = await img.get_attribute("src", timeout=2000) or ""
                        raw_srcset = (await img.get_attribute("srcset", timeout=2000) or
                                      await img.get_attribute("data-srcset", timeout=2000) or "")
                        if not raw_src:
                            raw_src = await img.get_attribute("data-src", timeout=2000) or ""
                        if not raw_src:
                            continue
                        def _abs(u):
                            if u.startswith("//"):
                                return "https:" + u
                            if u.startswith("/"):
                                return urljoin(HAARETZ_BASE, u)
                            return u
                        raw_src = _abs(raw_src)
                        raw_srcset = ", ".join(_abs(e.strip()) if e.strip().startswith("/") else e for e in raw_srcset.split(","))
                        best = _best_image_url(raw_src, raw_srcset)
                        # Skip tiny icons/logos
                        if re.search(r'[_-](16|24|32|48|64|80)x', best):
                            continue
                        if "תשבץ" in alt:
                            image_url = best
                            alt_for_log = alt.strip()
                            print(f"  Logic image (by alt): '{alt_for_log}' → {best}", file=sys.stderr)
                            break
                        if fallback_url is None and best and ("cdn" in best or "img" in best or "media" in best):
                            fallback_url = best
                    except Exception:
                        continue

                if not image_url:
                    if fallback_url:
                        image_url = fallback_url
                        print(f"  Logic image (fallback): {fallback_url}", file=sys.stderr)
                    else:
                        print("  No logic image found. Alt texts:", file=sys.stderr)
                        for i in range(min(total, 30)):
                            try:
                                alt = await all_imgs.nth(i).get_attribute("alt", timeout=1000)
                                if alt:
                                    print(f"    [{i}] '{alt}'", file=sys.stderr)
                            except Exception:
                                continue
                        raise Exception("No image found in logic puzzle article")
            else:
                # Regular puzzle: find by numbered alt "NתשבץDATE"
                found = []
                for i in range(total):
                    img = all_imgs.nth(i)
                    try:
                        alt = await img.get_attribute("alt", timeout=2000)
                        if not alt or "תשבץ" not in alt:
                            continue
                        raw_src = await img.get_attribute("src", timeout=2000) or ""
                        raw_srcset = (await img.get_attribute("srcset", timeout=2000) or
                                      await img.get_attribute("data-srcset", timeout=2000) or "")
                        if not raw_src:
                            raw_src = await img.get_attribute("data-src", timeout=2000) or ""
                        if not raw_src:
                            continue
                        if raw_src.startswith("//"):
                            raw_src = "https:" + raw_src
                        elif raw_src.startswith("/"):
                            raw_src = urljoin(HAARETZ_BASE, raw_src)
                        best = _best_image_url(raw_src, raw_srcset)
                        m = re.match(r'^(\d+)תשבץ\s', alt.strip())
                        if m:
                            idx = int(m.group(1))
                            found.append((idx, alt.strip(), best))
                            print(f"  Puzzle #{idx}: '{alt.strip()}' → {best}", file=sys.stderr)
                    except Exception:
                        continue

                if not found:
                    print("  No puzzle images found. Alt texts:", file=sys.stderr)
                    for i in range(min(total, 30)):
                        try:
                            alt = await all_imgs.nth(i).get_attribute("alt", timeout=1000)
                            if alt:
                                print(f"    [{i}] '{alt}'", file=sys.stderr)
                        except Exception:
                            continue
                    raise Exception("No puzzle images with 'תשבץ' in alt")

                target = None
                for idx, alt, src in found:
                    if idx == puzzle_index:
                        target = (idx, alt, src)
                        break

                if not target:
                    available = ", ".join(str(x[0]) for x in found)
                    raise Exception(f"Puzzle #{puzzle_index} not found. Available: {available}")

                print(f"  Selected: #{target[0]} | {target[1]}", file=sys.stderr)
                image_url = target[2]

            # ── Step 6: Download ──
            print("[6/6] Downloading...", file=sys.stderr)
            local_path, size = await download_image(image_url, article_url, output_dir)
            print(f"  Saved: {local_path} ({size:,} bytes)", file=sys.stderr)

            print(f"MEDIA:{local_path}")
            print(f"ALT_INFO:{title_text}")

        except PlaywrightTimeout as e:
            print(f"ERROR: Timeout — {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(1)
        finally:
            await browser.close()


def main():
    import argparse
    _env_defaults()
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", default=os.environ.get("HAARETZ_EMAIL", ""))
    parser.add_argument("--password", default=os.environ.get("HAARETZ_PASSWORD", ""))
    parser.add_argument("--index", type=int, default=int(os.environ.get("PUZZLE_INDEX", "3")))
    parser.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", str(Path(__file__).parent.parent / "output")))
    parser.add_argument("--type", default=os.environ.get("PUZZLE_TYPE", "puzzle"), choices=["puzzle", "logic"])
    args = parser.parse_args()

    if not args.email or not args.password:
        print("ERROR: HAARETZ_EMAIL and HAARETZ_PASSWORD required.", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run(args.email, args.password, args.index, args.output_dir, args.type))


if __name__ == "__main__":
    main()
