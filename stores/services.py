"""
stores/services.py

Logica di import store da file .txt o .py generato dallo script Selenium.
Deduplicazione automatica per URL.

Formati URL supportati:
  1. https://store.myshopify.com     (myshopify diretto)
  2. https://mionegozio.it           (dominio .it root)
  3. https://www.mionegozio.it       (con www)
  4. https://shop.mionegozio.it      (sottodominio)

Con strict_filter=False accetta qualsiasi URL HTTP/S (es. .com, .eu, .de, ecc.)
"""

import re
from urllib.parse import urlparse
from .models import Store


def parse_urls_from_text(content: str, strict_filter: bool = True) -> list[str]:
    """
    Estrae tutti gli URL store da un testo.

    Se strict_filter=True (default):
      Accetta solo myshopify.com e domini .it
    Se strict_filter=False:
      Accetta qualsiasi URL HTTP/S valido
    """
    if strict_filter:
        pattern = r'https?://[a-zA-Z0-9\-\.]+(?:\.myshopify\.com|\.it)(?:/[^\s\'"]*)?'
    else:
        pattern = r'https?://[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}(?:/[^\s\'"]*)?'

    found = re.findall(pattern, content)

    # Domini da escludere — non sono store
    BLACKLIST = {
        'www.google.it', 'google.it', 'support.google.it',
        'maps.google.it', 'translate.google.it',
        'web.archive.it', 'archive.it',
        'wikipedia.it', 'www.wikipedia.it',
        'facebook.it', 'instagram.it', 'twitter.it',
        'youtube.it', 'linkedin.it',
        # blacklist generica (usata anche in modalità no-filter)
        'www.google.com', 'google.com', 'facebook.com', 'instagram.com',
        'twitter.com', 'youtube.com', 'linkedin.com', 'wikipedia.org',
        'web.archive.org', 'archive.org',
    }

    # Sottodomini Shopify interni da escludere
    SKIP_SHOPIFY = {'apps', 'help', 'community', 'www', 'checkout', 'partners', 'accounts'}

    cleaned = []
    seen = set()

    for url in found:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc.lower()}"

        # Salta duplicati e blacklist
        if base in seen or parsed.netloc.lower() in BLACKLIST:
            continue

        # Salta sottodomini interni di Shopify
        if '.myshopify.com' in base:
            subdomain = parsed.netloc.split('.')[0].lower()
            if subdomain in SKIP_SHOPIFY:
                continue

        seen.add(base)
        cleaned.append(base)

    return cleaned


def normalize_url(url: str) -> str:
    """Normalizza un URL: lowercase netloc, rimuove trailing slash."""
    url = url.strip()
    if not url.startswith('http'):
        url = 'https://' + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def extract_domain(url: str) -> str:
    """Estrae il dominio da un URL."""
    return urlparse(url).netloc.lower()


def extract_name_from_url(url: str) -> str:
    """
    Genera un nome leggibile dall'URL.

    Esempi:
      https://my-store-name.myshopify.com  → My Store Name
      https://mionegozio.it                → Mionegozio
      https://www.mio-negozio.it           → Mio Negozio
      https://shop.mionegozio.it           → Mionegozio
      https://mybrand.com                  → Mybrand
    """
    netloc = urlparse(url).netloc.lower()

    if '.myshopify.com' in netloc:
        subdomain = netloc.split('.myshopify.com')[0]
        return subdomain.replace('-', ' ').replace('_', ' ').title()
    else:
        # Rimuove sottodomini comuni (www, shop, store...)
        COMMON_SUBDOMAINS = {'www', 'shop', 'store', 'negozio', 'boutique'}
        parts = netloc.split('.')
        domain_parts = [p for p in parts[:-1] if p not in COMMON_SUBDOMAINS]
        name = domain_parts[-1] if domain_parts else parts[0]
        return name.replace('-', ' ').replace('_', ' ').title()


def import_stores_from_content(
    content: str,
    niche: str = 'altro',
    source_label: str = '',
    strict_filter: bool = True,
) -> dict:
    """
    Importa store da testo grezzo (contenuto di un file .txt o .py).

    Args:
      content:       testo grezzo con URL
      niche:         nicchia da assegnare agli store importati
      source_label:  etichetta sorgente (es. "Import Manuale")
      strict_filter: se True filtra solo .it e myshopify.com;
                     se False accetta qualsiasi URL HTTP/S

    Ritorna un dict con:
      - imported:    lista di Store creati
      - skipped:     lista di URL già esistenti
      - errors:      lista di URL non validi
      - total_found: totale URL trovati nel testo
    """
    urls = parse_urls_from_text(content, strict_filter=strict_filter)

    imported = []
    skipped  = []
    errors   = []

    for raw_url in urls:
        try:
            url = normalize_url(raw_url)

            store, created = Store.objects.get_or_create(
                url=url,
                defaults={
                    'domain':  extract_domain(url),
                    'name':    extract_name_from_url(url),
                    'niche':   niche,
                    'status':  Store.Status.NEW,
                    'notes':   f"Importato da: {source_label}" if source_label else '',
                }
            )

            if created:
                imported.append(store)
            else:
                skipped.append(url)

        except Exception as e:
            errors.append({'url': raw_url, 'error': str(e)})

    return {
        'imported':    imported,
        'skipped':     skipped,
        'errors':      errors,
        'total_found': len(urls),
    }