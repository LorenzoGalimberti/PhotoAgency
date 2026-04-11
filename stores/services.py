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
import time
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from django.conf import settings
from django.utils import timezone
from .models import Store, MetaAdsRun


def parse_urls_from_text(content: str, strict_filter: bool = True) -> list[str]:
    if strict_filter:
        pattern = r'https?://[a-zA-Z0-9\-\.]+(?:\.myshopify\.com|\.it)(?:/[^\s\'"]*)?'
    else:
        pattern = r'https?://[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}(?:/[^\s\'"]*)?'

    found = re.findall(pattern, content)

    BLACKLIST = {
        'www.google.it', 'google.it', 'support.google.it',
        'maps.google.it', 'translate.google.it',
        'web.archive.it', 'archive.it',
        'wikipedia.it', 'www.wikipedia.it',
        'facebook.it', 'instagram.it', 'twitter.it',
        'youtube.it', 'linkedin.it',
        'www.google.com', 'google.com', 'facebook.com', 'instagram.com',
        'twitter.com', 'youtube.com', 'linkedin.com', 'wikipedia.org',
        'web.archive.org', 'archive.org',
    }

    SKIP_SHOPIFY = {'apps', 'help', 'community', 'www', 'checkout', 'partners', 'accounts'}

    cleaned = []
    seen = set()

    for url in found:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc.lower()}"

        if base in seen or parsed.netloc.lower() in BLACKLIST:
            continue

        if '.myshopify.com' in base:
            subdomain = parsed.netloc.split('.')[0].lower()
            if subdomain in SKIP_SHOPIFY:
                continue

        seen.add(base)
        cleaned.append(base)

    return cleaned


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith('http'):
        url = 'https://' + url
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def extract_name_from_url(url: str) -> str:
    netloc = urlparse(url).netloc.lower()

    if '.myshopify.com' in netloc:
        subdomain = netloc.split('.myshopify.com')[0]
        return subdomain.replace('-', ' ').replace('_', ' ').title()
    else:
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


# ─────────────────────────────────────────────────────────────
# META ADS — COSTANTI E UTILITY
# ─────────────────────────────────────────────────────────────

SKIP_DOMAINS = {
    "facebook.com", "fb.com", "fb.me", "instagram.com", "whatsapp.com",
    "apple.com", "apps.apple.com", "play.google.com", "google.com",
    "youtube.com", "tiktok.com", "twitter.com", "x.com",
    "amazon.com", "amazon.it", "ebay.com", "ebay.it",
    "linktr.ee", "bit.ly", "t.co", "l.facebook.com",
    "short.io", "tinyurl.com", "fixbv.com",
    "temu.com", "aliexpress.com", "zalando.it", "sephora.it",
    "notino.it", "iherb.com", "calendly.com", "doubleclick.net",
}

BAD_TLDS = {"js", "css", "png", "jpg", "php", "json", "xml"}

NOT_STORE_PATTERNS = [
    r'^fb\.me$', r'^api\.whatsapp', r'^ad\.doubleclick',
    r'\.sh$', r'^go\.', r'^lp\.', r'^trk\.', r'\.ya$',
    r'search-helper',
]

META_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

API_VERSION   = "v19.0"
API_BASE      = f"https://graph.facebook.com/{API_VERSION}"
CHECK_WORKERS = 10
CHECK_TIMEOUT = 8

ADS_FIELDS = ",".join([
    "page_name", "page_id", "ad_delivery_start_time",
    "ad_delivery_stop_time", "ad_snapshot_url",
    "ad_creative_link_captions", "ad_creative_bodies",
    "ad_creative_link_titles", "ad_creative_link_descriptions",
    "languages",
])


def _clean_url(raw: str) -> str:
    try:
        raw = raw.strip().rstrip('/')
        raw = re.sub(r'[\[\]]', '', raw)
        raw = re.sub(r'[?&](?:fbclid|utm_[a-z_]+|ref|source|campaign)=[^\s&]*', '', raw)
        raw = raw.split('#')[0]
        if raw and not raw.startswith("http"):
            raw = "https://" + raw
        p = urlparse(raw)
        if not p.netloc:
            return ""
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""


def _extract_domain(url: str) -> str | None:
    try:
        d = urlparse(url).netloc.lower().lstrip("www.")
        return d if d and "." in d else None
    except Exception:
        return None


def _is_valid_domain(domain: str) -> bool:
    if not domain or len(domain) < 5:
        return False
    if any(domain == s or domain.endswith("." + s) for s in SKIP_DOMAINS):
        return False
    if domain.split(".")[-1] in BAD_TLDS:
        return False
    if re.search(r'play\.google|apps\.apple|onelink|\.app$', domain):
        return False
    if any(re.search(p, domain) for p in NOT_STORE_PATTERNS):
        return False
    return True


def _url_from_captions(ad: dict) -> str | None:
    for c in (ad.get("ad_creative_link_captions") or []):
        if not c:
            continue
        m = re.search(r'(?:https?://)?(?:www\.)?([a-z0-9\-]+\.[a-z]{2,}(?:\.[a-z]{2})?)', c.lower())
        if m:
            u = _clean_url("https://" + m.group(0))
            d = _extract_domain(u)
            if d and _is_valid_domain(d):
                return u
    return None


def _url_from_body(ad: dict) -> str | None:
    for body in (ad.get("ad_creative_bodies") or []):
        for m in re.findall(r'https?://[^\s"\'<>\[\]]+', str(body)):
            u = _clean_url(m)
            if not u:
                continue
            d = _extract_domain(u)
            if d and _is_valid_domain(d):
                return u
    return None


def _make_store_dict(ad: dict, url: str) -> dict:
    return {
        "url":          url,
        "domain":       _extract_domain(url),
        "page_name":    ad.get("page_name", ""),
        "page_id":      ad.get("page_id", ""),
        "active_since": ad.get("ad_delivery_start_time", ""),
        "active_until": ad.get("ad_delivery_stop_time", ""),
        "snapshot_url": ad.get("ad_snapshot_url", ""),
    }


# ─────────────────────────────────────────────────────────────
# META ADS — FETCH ADS DALL'API
# ─────────────────────────────────────────────────────────────

def _fetch_page_websites_bulk(page_ids: list, token: str) -> dict:
    results = {}
    for i in range(0, len(page_ids), 50):
        batch = page_ids[i:i+50]
        batch_req = json.dumps([
            {"method": "GET", "relative_url": f"{pid}?fields=website"}
            for pid in batch
        ])
        try:
            r = requests.post(API_BASE, data={
                "access_token": token,
                "batch":        batch_req,
            }, timeout=30)
            if r.status_code != 200:
                continue
            for pid, resp in zip(batch, r.json()):
                if not resp or resp.get("code") != 200:
                    continue
                body = json.loads(resp.get("body", "{}"))
                website = body.get("website", "").strip()
                if website and "." in website:
                    u = _clean_url(website)
                    d = _extract_domain(u)
                    if d and _is_valid_domain(d):
                        results[pid] = u
        except Exception:
            continue
        time.sleep(0.5)
    return results


def _fetch_ads_from_api(
    keyword: str,
    country: str,
    token: str,
    limit: int,
    date_from: str | None,
    date_to: str | None,
    existing_domains: set,
    job_id: str | None = None,
    seen_this_run: set | None = None,
) -> tuple[list[dict], int]:
    from analyzer import job_manager

    def log(msg, level='info'):
        if job_id:
            job_manager.add_log(job_id, msg, level)

    if seen_this_run is None:
        seen_this_run = set()

    skipped_db  = 0
    raw_ads     = []
    ads_fetched = 0

    params = {
        "search_terms":         keyword,
        "ad_type":              "ALL",
        "ad_reached_countries": country,
        "fields":               ADS_FIELDS,
        "limit":                min(limit, 100),
        "access_token":         token,
    }
    if date_from:
        params["ad_delivery_date_min"] = date_from
    if date_to:
        params["ad_delivery_date_max"] = date_to

    url      = f"{API_BASE}/ads_archive"
    page_num = 1

    while ads_fetched < limit:
        log(f"Pagina {page_num} — scarico annunci...")
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                log("Rate limit — attendo 60s...", 'warn')
                time.sleep(60)
                continue
            if r.status_code in (400, 401):
                err = r.json().get("error", {}).get("message", "Errore API")
                log(f"Errore API Meta: {err}", 'error')
                raise Exception(err)
            if r.status_code != 200:
                break
            data = r.json()
        except Exception as e:
            log(f"Errore fetch: {e}", 'error')
            raise

        ads = data.get("data", [])
        if not ads:
            log("Nessun altro annuncio disponibile.")
            break

        raw_ads.extend(ads)
        ads_fetched += len(ads)
        log(f"+{len(ads)} annunci → totale {ads_fetched}")

        paging = data.get("paging", {})
        after  = paging.get("cursors", {}).get("after")
        if not after or not paging.get("next") or ads_fetched >= limit:
            break
        params["after"] = after
        page_num += 1
        time.sleep(1.0)

    log(f"{len(raw_ads)} annunci scaricati — estraggo URL...")

    results        = {}
    needs_page_api = []

    for ad in raw_ads:
        page_id = ad.get("page_id", "")
        u = _url_from_captions(ad) or _url_from_body(ad)
        if u:
            d = _extract_domain(u)
            if not d or not _is_valid_domain(d):
                continue
            if d in existing_domains:
                skipped_db += 1
                continue
            if d in seen_this_run:
                continue
            seen_this_run.add(d)
            results[d] = _make_store_dict(ad, u)
        else:
            if page_id and page_id not in {a.get("page_id") for a in needs_page_api}:
                needs_page_api.append(ad)

    if needs_page_api:
        page_ids = list({ad.get("page_id") for ad in needs_page_api if ad.get("page_id")})
        log(f"Page API per {len(page_ids)} pagine senza URL diretto...")
        page_websites = _fetch_page_websites_bulk(page_ids, token)
        for ad in needs_page_api:
            u = page_websites.get(ad.get("page_id", ""))
            if not u:
                continue
            d = _extract_domain(u)
            if not d or not _is_valid_domain(d):
                continue
            if d in existing_domains or d in seen_this_run:
                skipped_db += 1
                continue
            seen_this_run.add(d)
            results[d] = _make_store_dict(ad, u)

    log(f"{len(results)} domini nuovi | {skipped_db} già in DB")
    return list(results.values()), skipped_db


# ─────────────────────────────────────────────────────────────
# META ADS — CHECK SHOPIFY
# ─────────────────────────────────────────────────────────────

def _check_shopify(url: str) -> dict:
    result = {
        "is_ecommerce":  False,
        "platform":      "shopify",
        "product_count": 0,
        "error":         None,
    }
    try:
        r = requests.get(
            f"{url}/products.json?limit=250",
            headers=META_HEADERS,
            timeout=CHECK_TIMEOUT,
            allow_redirects=True,
        )
        if r.status_code == 200:
            data = r.json()
            if "products" in data and len(data["products"]) > 0:
                result["is_ecommerce"]  = True
                result["product_count"] = len(data["products"])
        elif r.status_code == 429:
            time.sleep(3)
            r2 = requests.get(
                f"{url}/products.json?limit=1",
                headers=META_HEADERS,
                timeout=CHECK_TIMEOUT,
                allow_redirects=True,
            )
            if r2.status_code == 200 and "products" in r2.json():
                result["is_ecommerce"]  = True
                result["product_count"] = -1
            elif r2.status_code == 429:
                result["is_ecommerce"]  = True
                result["product_count"] = -1
        elif r.status_code == 404:
            result["error"] = "404 — non è Shopify"
        else:
            result["error"] = f"HTTP {r.status_code}"
    except Exception as e:
        result["error"] = str(e)[:80]
    return result


# ─────────────────────────────────────────────────────────────
# META ADS — CHECK E-COMMERCE GENERICO
# ─────────────────────────────────────────────────────────────

def _detect_ecommerce(url: str) -> dict:
    result = {
        "is_ecommerce":  False,
        "platform":      "unknown",
        "product_count": 0,
        "signals":       [],
        "error":         None,
    }

    # Prima tenta Shopify via products.json
    try:
        r = requests.get(
            f"{url}/products.json?limit=250",
            headers=META_HEADERS, timeout=CHECK_TIMEOUT, allow_redirects=True
        )
        if r.status_code == 200:
            data = r.json()
            if "products" in data:
                result["is_ecommerce"]  = True
                result["platform"]      = "shopify"
                result["product_count"] = len(data["products"])
                result["signals"].append("products.json ✓")
                return result
        elif r.status_code == 429:
            time.sleep(3)
            r2 = requests.get(
                f"{url}/products.json?limit=1",
                headers=META_HEADERS, timeout=CHECK_TIMEOUT, allow_redirects=True
            )
            if r2.status_code == 200 and "products" in r2.json():
                result["is_ecommerce"]  = True
                result["platform"]      = "shopify"
                result["product_count"] = -1
                result["signals"].append("products.json ✓ (retry)")
                return result
    except Exception:
        pass

    # Poi analizza HTML per altri e-commerce
    try:
        r = requests.get(
            url, headers=META_HEADERS,
            timeout=CHECK_TIMEOUT, allow_redirects=True
        )
        if r.status_code != 200:
            result["error"] = f"HTTP {r.status_code}"
            return result

        html = r.text.lower()

        # Rilevamento piattaforma
        platform = "unknown"
        if "woocommerce" in html or "wc-cart" in html or "wc_add_to_cart" in html:
            platform = "woocommerce"
        elif "prestashop" in html or "prestashop" in r.headers.get("x-powered-by", "").lower():
            platform = "prestashop"
        elif "magento" in html or "mage/" in html:
            platform = "magento"
        elif "bigcommerce" in html:
            platform = "bigcommerce"
        elif "squarespace" in html:
            platform = "squarespace"
        elif "shopify" in html or "cdn.shopify.com" in html:
            platform = "shopify"
            result["signals"].append("shopify JS ✓")
        elif "wix.com" in html:
            platform = "wix"

        result["platform"] = platform

        score = 0

        cart_signals = [
            "/cart", "add-to-cart", "add_to_cart", "addtocart",
            "/checkout", "carrello", "aggiungi al carrello",
            "acquista ora", "aggiungi", "basket", "buy now",
        ]
        cart_hits = [s for s in cart_signals if s in html]
        if cart_hits:
            score += 3
            result["signals"].append(f"cart: {cart_hits[0]}")

        price_patterns = [r'€\s*\d+', r'\d+[.,]\d{2}\s*€', r'eur\s*\d+', r'price.*\d+']
        price_hits = [p for p in price_patterns if re.search(p, html)]
        if price_hits:
            score += 2
            result["signals"].append("prezzi trovati")

        if '"product"' in html or 'itemtype="https://schema.org/product"' in html:
            score += 3
            result["signals"].append("schema Product ✓")

        if 'og:type" content="product' in html:
            score += 2
            result["signals"].append("og:product ✓")

        shop_keywords = ["shop", "store", "negozio", "boutique", "acquista", "ordina"]
        shop_hits = [k for k in shop_keywords if k in html]
        if len(shop_hits) >= 2:
            score += 1
            result["signals"].append(f"keywords: {', '.join(shop_hits[:2])}")

        if platform != "unknown":
            score += 2
            result["signals"].append(f"platform: {platform}")

        if score >= 4:
            result["is_ecommerce"] = True

    except Exception as e:
        result["error"] = str(e)[:60]

    return result


# ─────────────────────────────────────────────────────────────
# META ADS — THREAD PRINCIPALE
# ─────────────────────────────────────────────────────────────

def run_meta_ads_thread(run: MetaAdsRun, params: dict, job_id: str):
    from analyzer import job_manager

    def log(msg, level='info'):
        job_manager.add_log(job_id, msg, level)

    try:
        # ── 1. Costruisci lista keyword ───────────────────────
        keywords = params.get('keywords', [])
        if not keywords:
            keywords = [params['keyword']]

        log(f"Keyword da processare: {len(keywords)} — {', '.join(keywords)}")

        # ── 2. Dedup da DB ───────────────────────────────────
        log("Carico domini esistenti dal DB...")
        existing_domains = set(Store.objects.values_list('domain', flat=True))
        log(f"{len(existing_domains)} domini già in DB")

        # ── 3. Fetch annunci per ogni keyword ─────────────────
        token = getattr(settings, 'META_ADS_TOKEN', '')
        if not token:
            raise Exception("META_ADS_TOKEN non configurato in settings.py")

        all_stores_raw = []
        total_skipped  = 0
        seen_this_run  = set()

        for idx, kw in enumerate(keywords, 1):
            log(f"[{idx}/{len(keywords)}] Keyword: '{kw}'")

            stores_raw, skipped_db = _fetch_ads_from_api(
                keyword          = kw,
                country          = params['country'],
                token            = token,
                limit            = params['limit'],
                date_from        = str(params['date_from']) if params.get('date_from') else None,
                date_to          = str(params['date_to'])   if params.get('date_to')   else None,
                existing_domains = existing_domains,
                job_id           = job_id,
                seen_this_run    = seen_this_run,
            )

            all_stores_raw.extend(stores_raw)
            total_skipped += skipped_db
            log(f"  → {len(stores_raw)} nuovi store | {skipped_db} saltati")

            if idx < len(keywords):
                time.sleep(2.0)

        run.ads_fetched    = len(all_stores_raw) + total_skipped
        run.stores_skipped = total_skipped
        run.save(update_fields=['ads_fetched', 'stores_skipped'])

        if not all_stores_raw:
            log("Nessun nuovo store da verificare.", 'warn')
            run.status       = MetaAdsRun.Status.COMPLETED
            run.completed_at = timezone.now()
            run.save(update_fields=['status', 'completed_at'])
            job_manager.complete_job(job_id)
            return
        # ── 3.5 DEDUP FINALE (rete di sicurezza) ─────────────
        seen_domains = set()
        unique_stores = []
        dupes_removed = 0
        for s in all_stores_raw:
            d = s.get("domain", "")
            if d and d not in seen_domains:
                seen_domains.add(d)
                unique_stores.append(s)
            else:
                dupes_removed += 1
        if dupes_removed:
            log(f"🧹 Rimossi {dupes_removed} duplicati residui")
        all_stores_raw = unique_stores
        # ── 4. Check in parallelo ─────────────────────────────
        shopify_only = params.get('shopify_only', True)
        check_label  = "Shopify" if shopify_only else "e-commerce"
        log(f"Verifico {len(all_stores_raw)} store in parallelo ({CHECK_WORKERS} workers) — modalità: {check_label}...")

        confirmed = []
        checked   = 0
        total     = len(all_stores_raw)

        def check_one(store_dict):
            if shopify_only:
                result = _check_shopify(store_dict['url'])
            else:
                result = _detect_ecommerce(store_dict['url'])
            store_dict.update(result)
            return store_dict

        with ThreadPoolExecutor(max_workers=CHECK_WORKERS) as executor:
            futures = {executor.submit(check_one, s): s for s in all_stores_raw}
            for future in as_completed(futures):
                checked += 1
                store  = future.result()
                domain = store.get('domain', '')

                if store.get('is_ecommerce'):
                    confirmed.append(store)
                    platform = store.get('platform', '?')
                    n        = store.get('product_count', 0)
                    prod_str = f" ({n} prodotti)" if n and n > 0 else ""
                    log(f"✅ {domain} [{platform}]{prod_str}", 'success')
                else:
                    err = store.get('error', '')
                    log(f"❌ {domain} — {err}" if err else f"❌ {domain}")

                if checked % 10 == 0 or checked == total:
                    job_manager.set_current(job_id, '', f"{checked}/{total} verificati")

        run.stores_checked = checked
        run.save(update_fields=['stores_checked'])

        # ── 5. Salva store confermati nel DB ──────────────────
        log(f"{len(confirmed)} store confermati — salvo nel DB...")
        new_count = 0

        for s in confirmed:
            try:
                platform = s.get('platform', 'unknown')
                _, created = Store.objects.get_or_create(
                    domain=s['domain'],
                    defaults={
                        'url':    s['url'],
                        'name':   s.get('page_name', '') or extract_name_from_url(s['url']),
                        'niche':  params['niche'],
                        'status': Store.Status.NEW,
                        'notes':  f"Meta Ads - {', '.join(keywords)}",
                        'tags':   f"meta_ads,{platform}",
                    }
                )
                if created:
                    new_count += 1
            except Exception as e:
                log(f"Errore salvataggio {s['domain']}: {e}", 'error')

        # ── 6. Aggiorna MetaAdsRun ────────────────────────────
        run.stores_new   = new_count
        run.status       = MetaAdsRun.Status.COMPLETED
        run.completed_at = timezone.now()
        run.save(update_fields=['stores_new', 'status', 'completed_at'])

        log(f"Completato — {new_count} nuovi store salvati", 'success')
        job_manager.complete_job(job_id)

    except Exception as e:
        log(f"Errore fatale: {e}", 'error')
        run.status        = MetaAdsRun.Status.ERROR
        run.error_message = str(e)
        run.completed_at  = timezone.now()
        run.save(update_fields=['status', 'error_message', 'completed_at'])
        job_manager.fail_job(job_id, str(e))