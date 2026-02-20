"""
============================================================================
SHOPIFY URL EXTRACTOR - SELENIUM AUTOMATICO
============================================================================

Versione integrata con Django PhotoAgency.
Salva sempre in: media/selenium_output.txt (path fisso)

Uso da linea di comando:
    python scripts/selenium_extractor.py

Uso da Django (subprocess):
    python scripts/selenium_extractor.py --queries "query1|query2" --niche arredamento

============================================================================
"""

import time
import re
import sys
import os
import argparse
from datetime import datetime
from pathlib import Path

# Fix encoding Windows — deve stare prima di qualsiasi print
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

# ============================================================================
# CONFIGURAZIONE DEFAULT
# ============================================================================

DEFAULT_QUERIES = [
    "site:myshopify.com arredamento casa italia",
    "site:myshopify.com home decor italia",
    "site:myshopify.com candele profumate",
    "site:myshopify.com complementi arredo italiano",
    "site:myshopify.com mobili design",
]

DELAY_BETWEEN_QUERIES = 5
HEADLESS = False

# Path fisso output
SCRIPT_DIR  = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
OUTPUT_FILE = PROJECT_DIR / 'media' / 'selenium_output.txt'


# ============================================================================
# SETUP SELENIUM
# ============================================================================

def setup_driver(headless=False):
    print("[*] Configurazione Selenium...")

    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError:
        print("[!] Librerie mancanti! Installa con: pip install selenium webdriver-manager")
        return None

    options = Options()
    if headless:
        options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                         'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    print("[*] Avvio Chrome...")
    try:
        service = Service(ChromeDriverManager().install())
        driver  = webdriver.Chrome(service=service, options=options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print("[OK] Chrome avviato!")
        return driver
    except Exception as e:
        print(f"[!] Errore avvio Chrome: {e}")
        return None


# ============================================================================
# RICERCA GOOGLE
# ============================================================================

def search_google(driver, query):
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys

    print(f"\n[>] Cerco: {query}")
    urls_found = []

    try:
        driver.get("https://www.google.com")
        time.sleep(2)

        try:
            btn = driver.find_element(
                By.XPATH, "//button[contains(., 'Accetta') or contains(., 'Accept')]"
            )
            btn.click()
            time.sleep(1)
        except:
            pass

        try:
            box = driver.find_element(By.NAME, "q")
        except:
            box = driver.find_element(By.CSS_SELECTOR, "textarea[name='q']")

        box.clear()
        box.send_keys(query)
        box.send_keys(Keys.RETURN)
        time.sleep(3)

        for link in driver.find_elements(By.TAG_NAME, "a"):
            try:
                url = link.get_attribute("href")
                if url and 'myshopify.com' in url:
                    clean = extract_clean_url(url)
                    if clean and clean not in urls_found:
                        urls_found.append(clean)
                        print(f"   [+] {clean}")
            except:
                continue

        print(f"   [=] {len(urls_found)} store da questa query")

    except Exception as e:
        print(f"   [!] Errore: {e}")

    return urls_found


def extract_clean_url(url):
    match = re.search(r'https://([a-zA-Z0-9-]+)\.myshopify\.com', url)
    if match:
        name = match.group(1)
        if name not in ['apps', 'help', 'community', 'www', 'checkout']:
            return f"https://{name}.myshopify.com"
    return None


# ============================================================================
# SALVATAGGIO
# ============================================================================

def save_results(urls, output_file=OUTPUT_FILE):
    if not urls:
        print("[!] Nessun URL da salvare")
        return None

    output_file = Path(output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# Generato: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Store trovati: {len(urls)}\n")
        for url in sorted(urls):
            f.write(f"{url}\n")

    print(f"\n[OK] Salvato in: {output_file}")
    return str(output_file)


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--queries', type=str, default=None,
                        help='Query separate da | es: "query1|query2"')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--output', type=str, default=str(OUTPUT_FILE))
    args = parser.parse_args()

    if args.queries:
        queries = [q.strip() for q in args.queries.split('|') if q.strip()]
    else:
        queries = DEFAULT_QUERIES

    print("=" * 60)
    print("SHOPIFY URL EXTRACTOR")
    print("=" * 60)
    print(f"Query:    {len(queries)}")
    print(f"Modalita: {'Headless' if args.headless else 'Visibile'}")
    print(f"Output:   {args.output}")
    print()

    driver = setup_driver(headless=args.headless)
    if not driver:
        sys.exit(1)

    all_urls = set()

    try:
        for i, query in enumerate(queries, 1):
            print(f"\n-- QUERY {i}/{len(queries)} --")
            urls = search_google(driver, query)
            all_urls.update(urls)
            print(f"[=] Totale progressivo: {len(all_urls)} store unici")

            if i < len(queries):
                print(f"[*] Pausa {DELAY_BETWEEN_QUERIES}s...")
                time.sleep(DELAY_BETWEEN_QUERIES)

        print(f"\n[OK] Completato — {len(all_urls)} store unici trovati")
        save_results(list(all_urls), output_file=args.output)

    except KeyboardInterrupt:
        print("\n[!] Interrotto — salvo quello che ho trovato...")
        save_results(list(all_urls), output_file=args.output)

    except Exception as e:
        print(f"\n[!] Errore: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    finally:
        print("\n[*] Chiudo browser...")
        driver.quit()


if __name__ == "__main__":
    main()