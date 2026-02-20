"""
stores/services.py

Logica di import store da file .txt o .py generato dallo script Selenium.
Deduplicazione automatica per URL.
"""

import re
from urllib.parse import urlparse
from .models import Store


def parse_urls_from_text(content: str) -> list[str]:
    """
    Estrae tutti gli URL myshopify.com o store generici da un testo.
    Funziona sia con file .txt (uno per riga) che con file .py (SEED_STORES = [...]).
    """
    # Cerca tutti gli URL che contengono myshopify.com o sono URL generici di store
    pattern = r'https?://[a-zA-Z0-9\-]+\.myshopify\.com[^\s\'"]*'
    found = re.findall(pattern, content)

    # Pulizia: rimuove slash finali, parametri query, path extra
    cleaned = []
    seen = set()
    for url in found:
        # Tieni solo schema + netloc (es. https://store.myshopify.com)
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in seen:
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
    Genera un nome leggibile dal subdomain myshopify.
    es. https://my-store-name.myshopify.com → My Store Name
    """
    netloc = urlparse(url).netloc
    subdomain = netloc.split('.')[0]
    return subdomain.replace('-', ' ').replace('_', ' ').title()


def import_stores_from_content(content: str, niche: str = 'altro', source_label: str = '') -> dict:
    """
    Importa store da testo grezzo (contenuto di un file .txt o .py).

    Ritorna un dict con:
      - imported: lista di Store creati
      - skipped:  lista di URL già esistenti
      - errors:   lista di URL non validi
    """
    urls = parse_urls_from_text(content)

    imported = []
    skipped  = []
    errors   = []

    for raw_url in urls:
        try:
            url = normalize_url(raw_url)

            # Deduplicazione: get_or_create per URL
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
        'imported': imported,
        'skipped':  skipped,
        'errors':   errors,
        'total_found': len(urls),
    }