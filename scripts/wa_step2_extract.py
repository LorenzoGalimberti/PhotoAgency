"""
wa_step2_extract.py v7
═══════════════════════
Ottimizzazioni velocità:
  - Blocca risorse inutili (img, font, css, media)
  - Timeout aggressivi ma sicuri
  - domcontentloaded invece di networkidle (molto più veloce)
  - Early exit appena trova il numero
  - Ridotto sleep fissi
"""

import asyncio
import sys
import re
from urllib.parse import unquote
from playwright.async_api import async_playwright

HEADERS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Risorse da bloccare — non servono per trovare il numero WA
BLOCKED_RESOURCES = {'image', 'media', 'font', 'stylesheet'}

# Estensioni da ignorare nelle response
BLOCKED_EXTENSIONS = (
    '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg',
    '.woff', '.woff2', '.ttf', '.eot',
    '.css', '.mp4', '.mp3', '.ico',
)

WA_CLICK_SELECTORS = [
    "a[href*='wa.me']",
    "a[href*='api.whatsapp.com']",
    "a[href*='whatsapp.com/send']",
    "[id*='whatsapp']",
    "[class*='whatsapp']",
    "[class*='wa-btn']",
    "[class*='wa-button']",
    "[class*='czm']",
    "[id*='czm']",
]

COOKIE_SELECTORS = [
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    "#onetrust-accept-btn-handler",
    ".cc-accept",
    ".cc-btn.cc-allow",
    "button[id*='AcceptAll']",
    "button[id*='accept-all']",
    "button[id*='acceptAll']",
    "[class*='cookie'][class*='accept']",
    "[id*='cookie'][id*='accept']",
    "button[aria-label*='ccept']",
]


def extract_number(text: str) -> str | None:
    decoded = unquote(str(text))
    for pat in [
        r'phone=\+?(\d{10,15})',
        r'wa\.me/\+?(\d{10,15})',
        r'"phone"\s*:\s*"(\+?\d{10,15})"',
        r'"number"\s*:\s*"(\+?\d{10,15})"',
        r'"whatsapp"\s*:\s*"(\+?\d{10,15})"',
        r'\b(39\d{9,10})\b',
    ]:
        m = re.search(pat, decoded, re.IGNORECASE)
        if m:
            num = m.group(1)
            return '+' + num if not num.startswith('+') else num
    return None


async def accept_cookies(page) -> bool:
    for sel in COOKIE_SELECTORS:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click()
                await asyncio.sleep(1)   # era 2s → ora 1s
                return True
        except Exception:
            pass
    return False


async def try_click_widget(page, context) -> str | None:
    for selector in WA_CLICK_SELECTORS:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements:
                if not await el.is_visible():
                    continue

                href = await el.get_attribute("href") or ""
                if href:
                    n = extract_number(href)
                    if n:
                        return n

                try:
                    async with context.expect_page(timeout=3000) as new_page_info:  # era 4000
                        await el.click()
                    new_page = await new_page_info.value
                    await new_page.wait_for_load_state("domcontentloaded", timeout=4000)
                    new_url = new_page.url
                    n = extract_number(new_url)
                    await new_page.close()
                    if n:
                        return n
                except Exception:
                    await asyncio.sleep(0.5)  # era 1s
                    for wa_sel in ["a[href*='whatsapp']", "a[href*='wa.me']"]:
                        links = await page.query_selector_all(wa_sel)
                        for link in links:
                            href2 = await link.get_attribute("href") or ""
                            n = extract_number(href2)
                            if n:
                                return n
        except Exception:
            pass
    return None


async def extract_whatsapp_number(url: str) -> str | None:
    number_found = None
    found_event  = asyncio.Event()   # ← early exit flag

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",      # evita crash su macchine limitate
                "--disable-extensions",
                "--disable-background-networking",
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=HEADERS_UA,
            locale="it-IT",
        )

        # ── Blocca risorse inutili ─────────────────────────────────────────
        async def block_resources(route, request):
            if request.resource_type in BLOCKED_RESOURCES:
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", block_resources)

        page = await context.new_page()

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)

        # ── Strategia 1: intercetta risposte durante caricamento ───────────
        async def on_response(response):
            nonlocal number_found
            if number_found:
                return
            if response.url.lower().endswith(BLOCKED_EXTENSIONS):
                return
            try:
                body = await response.text()
                n = extract_number(body)
                if n:
                    number_found = n
                    found_event.set()   # ← segnala early exit
            except Exception:
                pass

        page.on("response", on_response)

        # ── Caricamento pagina con domcontentloaded (più veloce) ───────────
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass

        # Aspetta max 3s oppure finché non trova il numero
        try:
            await asyncio.wait_for(found_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

        if number_found:
            await browser.close()
            return number_found

        # ── Strategia 2: cerca nel DOM / clicca widget ─────────────────────
        number_found = await try_click_widget(page, context)

        if number_found:
            await browser.close()
            return number_found

        # ── Strategia 3: accetta cookie e riprova ──────────────────────────
        accepted = await accept_cookies(page)
        if accepted:
            await asyncio.sleep(2)   # era 4s

            found_event.clear()
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception:
                pass

            try:
                await asyncio.wait_for(found_event.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                pass

            if number_found:
                await browser.close()
                return number_found

            number_found = await try_click_widget(page, context)

        await browser.close()

    return number_found


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else input("Inserisci URL: ").strip()
    if not url.startswith("http"):
        url = "https://" + url
    number = asyncio.run(extract_whatsapp_number(url))
    print(number if number else "")
    