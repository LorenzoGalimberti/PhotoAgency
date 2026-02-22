"""
============================================================================
SHOPIFY COMPLETE ANALYZER — integrato con Django PhotoAgency
============================================================================
"""

import sys
import os
import json
import argparse
import threading
import statistics
import re
import base64
from io import BytesIO
from datetime import datetime
from urllib.parse import urlparse, urljoin
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

import requests
from bs4 import BeautifulSoup

try:
    import cv2
    import numpy as np
    from PIL import Image
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
TIMEOUT          = 10
IMG_WORKERS      = 6
IMG_TIMEOUT      = 8
IMG_DOWNLOAD_PX  = 400
MAX_PER_PRODUCT  = 4
MAX_PRODUCT_IMGS = 100  # limite immagini prodotto
MAX_EXTRA_IMGS   = 50   # limite immagini pagine extra (separato)

_print_lock = threading.Lock()

def log(msg):
    with _print_lock:
        print(f"[analyzer] {msg}", flush=True)


# ============================================================================
# MODULO STORE
# ============================================================================

def verify_shopify(store_url):
    try:
        r = requests.get(f"{store_url}/products.json?limit=250", headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            if "products" in data:
                return {'is_shopify': True, 'products': data['products']}
    except Exception:
        pass
    return {'is_shopify': False, 'products': []}


def extract_basic_info(store_url):
    try:
        r = requests.get(store_url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, 'html.parser')
        html = r.text
        title_tag = soup.find('title')
        meta_desc = soup.find('meta', {'name': 'description'})
        html_tag  = soup.find('html')
        theme = 'Unknown'
        for t in ['dawn', 'debut', 'brooklyn', 'minimal', 'supply', 'narrative', 'boundless']:
            if t in html.lower():
                theme = t.capitalize()
                break
        return {
            'title':              title_tag.text.strip() if title_tag else None,
            'description':        meta_desc.get('content') if meta_desc else None,
            'language':           html_tag.get('lang') if html_tag else None,
            'theme':              theme,
            'has_analytics':      'gtag' in html or 'google-analytics' in html,
            'has_facebook_pixel': 'fbevents' in html or 'facebook.net' in html,
        }
    except Exception:
        return {}


def analyze_products(products):
    if not products:
        return {'total_count': 0, 'categories': [], 'vendors': [],
                'price_min': 0, 'price_max': 0, 'price_avg': 0}
    prices, categories, vendors = [], [], []
    for p in products:
        for v in p.get('variants', []):
            price = float(v.get('price', 0))
            if price > 0:
                prices.append(price)
        if p.get('product_type'): categories.append(p['product_type'])
        if p.get('vendor'):       vendors.append(p['vendor'])
    return {
        'total_count': len(products),
        'categories':  [c for c, _ in Counter(categories).most_common()],
        'vendors':     [v for v, _ in Counter(vendors).most_common()],
        'price_min':   round(min(prices), 2) if prices else 0,
        'price_max':   round(max(prices), 2) if prices else 0,
        'price_avg':   round(statistics.mean(prices), 2) if prices else 0,
    }


def analyze_product_images_fast(products):
    if not products:
        return {'quality_score': 0, 'total_images': 0, 'avg_per_product': 0,
                'single_image_count': 0, 'low_res_count': 0, 'no_alt_count': 0,
                'issues': ['Nessun prodotto trovato']}
    total_imgs, low_res, no_alt, single, per_product = 0, 0, 0, 0, []
    for p in products:
        imgs = p.get('images', [])
        n = len(imgs)
        per_product.append(n)
        total_imgs += n
        if n == 1: single += 1
        for img in imgs:
            w, h = img.get('width', 0), img.get('height', 0)
            if w and h and (w < 1000 or h < 1000): low_res += 1
            if not img.get('alt'): no_alt += 1
    avg = round(statistics.mean(per_product), 1) if per_product else 0
    score, issues = 100, []
    if avg < 3:
        score -= 30
        issues.append(f"Media immagini per prodotto bassa: {avg} (ottimale: 5+)")
    if single > len(products) * 0.3:
        score -= 25
        issues.append(f"{single} prodotti con 1 sola immagine")
    if low_res > 0:
        score -= 20
        issues.append(f"{low_res} immagini sotto 1000px")
    if no_alt > total_imgs * 0.5:
        score -= 15
        issues.append(f"{no_alt} immagini senza alt text (SEO)")
    return {
        'quality_score':      max(0, score),
        'total_images':       total_imgs,
        'avg_per_product':    avg,
        'single_image_count': single,
        'low_res_count':      low_res,
        'no_alt_count':       no_alt,
        'issues':             issues or ['Nessun problema rilevato'],
    }


# ============================================================================
# MODULO CONTATTI
# ============================================================================

CONTACT_CANDIDATES = [
    '/pages/contact', '/pages/contact-us', '/pages/contacts',
    '/pages/contatti', '/pages/contattaci', '/pages/support',
    '/pages/supporto', '/pages/info', '/pages/scrivici',
    '/pages/help', '/pages/assistenza', '/pages/customer-service',
]

def find_contact_page(base_url):
    try:
        r = requests.get(base_url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(r.text, 'html.parser')
        contact_kw = ['contact', 'contatt', 'support', 'aiuto', 'info', 'scrivici']
        for link in soup.find_all('a', href=True):
            href = link['href'].lower()
            text = link.get_text(strip=True).lower()
            if '/pages/' in href and any(k in href or k in text for k in contact_kw):
                return urljoin(base_url, link['href'])
    except Exception:
        pass
    for candidate in CONTACT_CANDIDATES:
        url = base_url.rstrip('/') + candidate
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                return url
        except Exception:
            continue
    return base_url


def extract_contacts(page_url):
    result = {
        'email': None, 'emails_all': [], 'phone': None,
        'address': None, 'piva': None, 'cf': None,
        'whatsapp': None, 'social': {},
    }
    try:
        r = requests.get(page_url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return result
        soup = BeautifulSoup(r.text, 'html.parser')
        html = r.text
        text = soup.get_text(separator=' ', strip=True)

        blacklist = ['myshopify.com', 'example.com', 'google.com', 'shopify.com',
                     'sentry.io', 'cloudflare.com', 'schema.org']
        emails, seen = [], set()
        for e in re.findall(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b', html):
            e = e.lower()
            if e not in seen and not any(b in e for b in blacklist):
                emails.append(e)
                seen.add(e)
        result['emails_all'] = emails
        result['email']      = emails[0] if emails else None

        phones = []
        for pattern in [r'\+39[\s\-]?[\d][\d\s\-]{7,12}',
                        r'\b0\d{1,4}[\s\-]?\d{5,8}\b',
                        r'\b3\d{2}[\s\-]?\d{3}[\s\-]?\d{4}\b']:
            for p in re.findall(pattern, html):
                clean = re.sub(r'\s+', ' ', p).strip()
                if clean not in phones:
                    phones.append(clean)
        result['phone'] = phones[0] if phones else None

        wa = re.search(r'wa\.me/(\+?[\d]+)', html)
        if wa: result['whatsapp'] = 'https://' + wa.group(0)

        for pattern in [r'(?:Via|Viale|Corso|Piazza|Largo)\s+[A-Za-zÀ-ú\s\.]+,?\s*\d+',
                        r'\d{5}\s+[A-Za-zÀ-ú\s]+\s*\([A-Z]{2}\)']:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                result['address'] = m.group(0).strip()
                break

        piva = re.search(r'(?:P\.?\s?IVA|Partita\s+IVA)[:\s]*(\d{11})', text, re.IGNORECASE)
        if piva: result['piva'] = piva.group(1)

        cf = re.search(
            r'(?:C\.?F\.?|Codice\s+Fiscale)[:\s]*([A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z])',
            text, re.IGNORECASE
        )
        if cf: result['cf'] = cf.group(1)

        social_patterns = {
            'instagram': r'instagram\.com/([A-Za-z0-9_.]+)',
            'facebook':  r'facebook\.com/([A-Za-z0-9_.]+)',
            'tiktok':    r'tiktok\.com/@([A-Za-z0-9_.]+)',
            'linkedin':  r'linkedin\.com/(?:company|in)/([A-Za-z0-9_\-]+)',
            'twitter':   r'(?:twitter|x)\.com/([A-Za-z0-9_]+)',
            'youtube':   r'youtube\.com/(?:channel|@|c)/([A-Za-z0-9_\-]+)',
        }
        for platform, pat in social_patterns.items():
            m = re.search(pat, html, re.IGNORECASE)
            if m:
                full = m.group(0)
                result['social'][platform] = full if full.startswith('http') else 'https://' + full

    except Exception:
        pass
    return result


# ============================================================================
# MODULO IMMAGINI
# ============================================================================

def build_small_url(url, size=IMG_DOWNLOAD_PX):
    url = re.sub(r'_\d+x\d*(?=\.(jpg|jpeg|png|webp))', '', url, flags=re.I)
    url = re.sub(r'\?.*$', '', url)
    url = re.sub(r'\.(jpg|jpeg|png|webp)$', f'_{size}x{size}.\\1', url, flags=re.I)
    return url


def download_image_pil(url):
    if not HAS_CV2:
        return None
    for attempt in [build_small_url(url), url]:
        try:
            r = requests.get(attempt, headers=HEADERS, timeout=IMG_TIMEOUT, stream=True)
            if r.status_code == 200:
                ct = r.headers.get('Content-Type', '')
                if 'image' in ct or attempt.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    img = Image.open(BytesIO(r.content)).convert('RGB')
                    return img
        except Exception:
            continue
    return None


def make_thumbnail_b64(pil_img, size=280):
    try:
        thumb = pil_img.copy()
        thumb.thumbnail((size, size), Image.LANCZOS)
        buf = BytesIO()
        thumb.save(buf, format='JPEG', quality=70)
        return base64.b64encode(buf.getvalue()).decode('utf-8')
    except Exception:
        return None


def analyze_resolution(pil_img, w_hint=None, h_hint=None):
    w = w_hint or pil_img.size[0]
    h = h_hint or pil_img.size[1]
    if   w >= 2000 and h >= 2000: score, label = 100, "Eccellente"
    elif w >= 1000 and h >= 1000: score, label = 70,  "Buona"
    elif w >= 600  and h >= 600:  score, label = 40,  "Media"
    else:                          score, label = 10,  "Bassa"
    return {'width': w, 'height': h, 'score': score, 'label': label}


def analyze_resolution_fast(w, h):
    if   w >= 2000 and h >= 2000: score, label = 100, "Eccellente"
    elif w >= 1000 and h >= 1000: score, label = 70,  "Buona"
    elif w >= 600  and h >= 600:  score, label = 40,  "Media"
    elif w > 0 and h > 0:         score, label = 10,  "Bassa"
    else:                          score, label = 50,  "Sconosciuta"
    return {'width': w, 'height': h, 'score': score, 'label': label}


def analyze_blur(pil_img):
    try:
        gray = np.array(pil_img.convert('L'))
        if max(gray.shape) > 400:
            s = 400 / max(gray.shape)
            gray = cv2.resize(gray, (int(gray.shape[1]*s), int(gray.shape[0]*s)))
        value = round(cv2.Laplacian(gray, cv2.CV_64F).var(), 1)
        if   value >= 500: score, label = 100, "Nitida"
        elif value >= 200: score, label = 75,  "Accettabile"
        elif value >= 100: score, label = 45,  "Leggermente sfocata"
        else:              score, label = 10,  "Sfocata ⚠️"
        return {'laplacian': value, 'score': score, 'label': label}
    except Exception:
        return {'laplacian': 0, 'score': 50, 'label': 'N/A'}


def analyze_background(pil_img):
    try:
        arr = np.array(pil_img)
        b   = max(5, int(min(pil_img.size) * 0.05))
        border = np.concatenate([
            arr[:b, :].reshape(-1, 3), arr[-b:, :].reshape(-1, 3),
            arr[:, :b].reshape(-1, 3), arr[:, -b:].reshape(-1, 3)
        ])
        avg = border.mean(axis=0)
        std = border.std(axis=0).mean()
        is_white   = all(c > 230 for c in avg)
        is_uniform = std < 20
        if   is_white:    label, score = "Sfondo bianco ✓", 90
        elif is_uniform:  label, score = "Sfondo uniforme",  70
        else:             label, score = "Sfondo complesso", 50
        return {'is_white': is_white, 'label': label, 'score': score}
    except Exception:
        return {'label': 'N/A', 'score': 60, 'is_white': False}


def analyze_alt_text(alt):
    if not alt or not alt.strip():   return {'score': 0,   'label': 'Mancante ⚠️'}
    elif len(alt) < 10:              return {'score': 40,  'label': 'Troppo corto'}
    elif len(alt) > 120:             return {'score': 70,  'label': 'Troppo lungo'}
    else:                            return {'score': 100, 'label': 'OK'}


def img_overall_score(res, blur, bg, alt):
    return round(res['score'] * 0.30 + blur['score'] * 0.40 + bg['score'] * 0.20 + alt['score'] * 0.10)


def score_to_grade(score):
    if   score >= 80: return 'A', '#22c55e', 'Ottima'
    elif score >= 60: return 'B', '#84cc16', 'Buona'
    elif score >= 40: return 'C', '#f59e0b', 'Media'
    else:             return 'D', '#ef4444', 'Scarsa'


def collect_product_img_urls(products, max_per_product=MAX_PER_PRODUCT):
    imgs = []
    for p in products:
        for img in p.get('images', [])[:max_per_product]:
            imgs.append({
                'url': img.get('src'), 'alt': img.get('alt') or '',
                'source': 'Prodotti', 'context': p.get('title', ''),
                'w_hint': img.get('width'), 'h_hint': img.get('height'),
                'is_extra': False,
            })
    return imgs


def collect_page_img_urls(url, label):
    imgs = []
    SKIP = ['data:image', '.svg', 'icon', 'pixel', 'tracking', '1x1', 'spinner', 'logo']
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200: return imgs
        soup = BeautifulSoup(r.text, 'html.parser')
        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src') or img.get('data-lazy-src')
            if not src: continue
            src = urljoin(url, src)
            if any(x in src for x in SKIP): continue
            imgs.append({
                'url': src, 'alt': img.get('alt') or '',
                'source': label, 'context': label,
                'w_hint': None, 'h_hint': None,
                'is_extra': True,
            })
    except Exception:
        pass
    return imgs


def collect_all_img_urls(base_url, products):
    """
    Raccoglie immagini con limiti SEPARATI:
    - Prodotti: MAX_PRODUCT_IMGS
    - Pagine extra: MAX_EXTRA_IMGS (indipendente dai prodotti)
    """
    all_imgs, seen = [], set()

    def add(items, limit=None):
        count = 0
        for img in items:
            if limit and count >= limit: break
            if not img.get('url'): continue
            key = re.sub(r'_\d+x\d*\.', '.', re.sub(r'\?.*$', '', img['url']))
            if key not in seen:
                seen.add(key)
                img['url_clean'] = key
                all_imgs.append(img)
                count += 1
        return count

    # Prodotti — limite indipendente
    log("Raccolta immagini prodotto...")
    prod_added = add(collect_product_img_urls(products), limit=MAX_PRODUCT_IMGS)
    log(f"  → {prod_added} immagini prodotto")

    # Pagine extra — limite SEPARATO e indipendente
    extra_pages = [
        (base_url,                             'Homepage'),
        (f"{base_url}/collections",            'Collections'),
        (f"{base_url}/pages/about",            'About'),
        (f"{base_url}/pages/about-us",         'About Us'),
        (f"{base_url}/pages/chi-siamo",        'Chi Siamo'),
        (f"{base_url}/blogs/news",             'Blog'),
        (f"{base_url}/pages/lookbook",         'Lookbook'),
        (f"{base_url}/pages/gallery",          'Gallery'),
        (f"{base_url}/pages/our-story",        'Our Story'),
        (f"{base_url}/pages/la-nostra-storia", 'La Nostra Storia'),
    ]

    extra_total = 0
    log("Raccolta immagini pagine extra...")
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(collect_page_img_urls, pu, lbl): lbl
                   for pu, lbl in extra_pages}
        for future in as_completed(futures):
            lbl = futures[future]
            remaining = MAX_EXTRA_IMGS - extra_total
            if remaining <= 0:
                continue
            try:
                result = future.result()
                added = add(result, limit=remaining)
                extra_total += added
                if added > 0:
                    log(f"  → +{added} da {lbl}")
            except Exception:
                pass

    log(f"Totale: {prod_added} prodotto + {extra_total} extra = {len(all_imgs)} immagini")
    return all_imgs


def analyze_single_image(img_data, idx, total):
    url = img_data.get('url_clean') or img_data['url']
    result = {
        'url': img_data['url'], 'url_clean': url,
        'alt': img_data.get('alt', ''),
        'source': img_data.get('source', ''),
        'context': img_data.get('context', ''),
        'is_extra': img_data.get('is_extra', False),
        'thumbnail_b64': None,
        'resolution': None, 'blur': None, 'background': None, 'alt_analysis': None,
        'overall_score': 0,
        'grade': 'D', 'grade_color': '#ef4444', 'grade_label': 'Scarsa',
        'error': None
    }

    if not HAS_CV2:
        w = img_data.get('w_hint', 0) or 0
        h = img_data.get('h_hint', 0) or 0
        result['resolution']   = analyze_resolution_fast(w, h)
        result['alt_analysis'] = analyze_alt_text(img_data.get('alt', ''))
        result['blur']         = {'score': 60, 'label': 'N/A (cv2 non installato)', 'laplacian': 0}
        result['background']   = {'score': 60, 'label': 'N/A', 'is_white': False}
        score = round(result['resolution']['score'] * 0.5 + result['alt_analysis']['score'] * 0.5)
        result['overall_score'] = score
        result['grade'], result['grade_color'], result['grade_label'] = score_to_grade(score)
        return result

    pil_img = download_image_pil(url)
    if pil_img is None:
        result['error'] = 'Download fallito'
        return result

    result['thumbnail_b64'] = make_thumbnail_b64(pil_img)
    result['resolution']    = analyze_resolution(pil_img, img_data.get('w_hint'), img_data.get('h_hint'))
    result['blur']          = analyze_blur(pil_img)
    result['background']    = analyze_background(pil_img)
    result['alt_analysis']  = analyze_alt_text(img_data.get('alt', ''))

    score = img_overall_score(result['resolution'], result['blur'],
                              result['background'], result['alt_analysis'])
    result['overall_score'] = score
    result['grade'], result['grade_color'], result['grade_label'] = score_to_grade(score)

    log(f"  [{idx+1}/{total}] Grade {result['grade']} {score}/100 — {img_data.get('context','')[:40]}")
    return result


def analyze_images_parallel(all_imgs):
    total   = len(all_imgs)
    results = [None] * total
    log(f"Analisi {total} immagini con {IMG_WORKERS} worker...")
    with ThreadPoolExecutor(max_workers=IMG_WORKERS) as ex:
        futures = {ex.submit(analyze_single_image, img, i, total): i
                   for i, img in enumerate(all_imgs)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = future.result()
            except Exception as e:
                results[i] = {**all_imgs[i], 'error': str(e), 'overall_score': 0,
                              'grade': 'D', 'grade_color': '#ef4444', 'grade_label': 'Errore'}
    return results


# ============================================================================
# LEAD SCORING
# ============================================================================

def calculate_lead_score(contacts, products_info, img_quality, social):
    score, breakdown = 0, {}

    if contacts.get('email'):
        score += 20
        breakdown['Email'] = {'score': 20, 'max': 20, 'reason': f"trovata: {contacts['email']}"}
    else:
        breakdown['Email'] = {'score': 0, 'max': 20, 'reason': 'Non trovata'}

    n = products_info.get('total_count', 0)
    ps = 15 if n >= 50 else 10 if n >= 20 else 5 if n >= 10 else 0
    score += ps
    breakdown['Prodotti'] = {'score': ps, 'max': 15, 'reason': f"{n} prodotti"}

    iq = img_quality.get('quality_score', 100)
    ims = 35 if iq < 50 else 20 if iq < 70 else 5
    score += ims
    breakdown['Qualità Immagini'] = {
        'score': ims, 'max': 35,
        'reason': f"Score immagini: {iq}/100 {'— ALTA OPPORTUNITÀ' if iq < 50 else ''}"
    }

    avg_p = products_info.get('price_avg', 0)
    prs = 15 if avg_p >= 100 else 10 if avg_p >= 50 else 5
    score += prs
    breakdown['Prezzo Medio'] = {'score': prs, 'max': 15, 'reason': f"€{avg_p:.0f} medio"}

    sc = sum(1 for v in social.values() if v)
    ss = 15 if sc >= 3 else 8 if sc >= 1 else 0
    score += ss
    breakdown['Social Media'] = {'score': ss, 'max': 15, 'reason': f"{sc} piattaforme"}

    if   score >= 70: priority, potential = "HOT",  "ALTO"
    elif score >= 50: priority, potential = "WARM", "MEDIO"
    else:             priority, potential = "COLD", "BASSO"

    return {'total_score': score, 'priority': priority, 'potential': potential, 'breakdown': breakdown}


# ============================================================================
# REPORT HTML
# ============================================================================

def _bar(score):
    if score >= 70: return '#22c55e'
    if score >= 40: return '#f59e0b'
    return '#ef4444'


def _img_card(r):
    thumb   = r.get('thumbnail_b64')
    img_tag = (f'<img src="data:image/jpeg;base64,{thumb}" alt="{r.get("alt","")}" loading="lazy">'
               if thumb else '<div class="no-img">📷</div>')

    res, blur, bg, alt_a = r.get('resolution'), r.get('blur'), r.get('background'), r.get('alt_analysis')

    badges = ''
    if blur  and blur.get('score', 100)  < 45: badges += '<span class="badge badge-red">SFOCATA</span>'
    if res   and res.get('score', 100)   < 40: badges += '<span class="badge badge-orange">LOW RES</span>'
    if alt_a and alt_a.get('score', 100) == 0: badges += '<span class="badge badge-yellow">NO ALT</span>'

    res_str  = f"{res.get('width',0)}×{res.get('height',0)} — {res.get('label','?')}" if res else '?'
    blur_str = f"{blur.get('laplacian',0)} — {blur.get('label','?')}"                  if blur else '?'
    bg_str   = bg.get('label', '?')    if bg   else '?'
    alt_str  = alt_a.get('label', '?') if alt_a else '?'
    src      = r.get('source', '')
    ctx      = (r.get('context') or '')[:40]
    gc       = r.get('grade_color', '#ef4444')
    grade    = r.get('grade', '?')
    oscore   = r.get('overall_score', 0)

    return f'''
    <div class="img-card" data-score="{oscore}" data-source="{src}">
        <div class="img-wrap">
            {img_tag}
            <div class="grade-badge" style="background:{gc}">{grade}</div>
            <div class="score-overlay">{oscore}/100</div>
        </div>
        <div class="card-body">
            <div class="card-source">{src}</div>
            <div class="card-ctx">{ctx}</div>
            <div class="badges">{badges}</div>
            <div class="metrics">
                <div class="metric"><span class="ml">Risoluzione</span>
                    <span class="mv" style="color:{_bar(res.get('score',50) if res else 50)}">{res_str}</span></div>
                <div class="metric"><span class="ml">Nitidezza</span>
                    <span class="mv" style="color:{_bar(blur.get('score',50) if blur else 50)}">{blur_str}</span></div>
                <div class="metric"><span class="ml">Sfondo</span><span class="mv">{bg_str}</span></div>
                <div class="metric"><span class="ml">Alt text</span><span class="mv">{alt_str}</span></div>
            </div>
        </div>
    </div>'''


def _build_img_section(source, imgs):
    avg = round(sum(r['overall_score'] for r in imgs) / len(imgs)) if imgs else 0
    g, gc, _ = score_to_grade(avg)
    cards = ''.join(_img_card(r) for r in imgs)
    return f'''
    <div class="source-section">
        <div class="source-header">
            <div class="source-title">
                <h3>{source}</h3>
                <span class="badge-count">{len(imgs)} immagini</span>
            </div>
            <div style="font-size:13px;color:#6b6b80">
                Score medio: <strong style="color:{gc}">{avg}/100 ({g})</strong>
            </div>
        </div>
        <div class="cards-grid">{cards}</div>
    </div>'''


def generate_html_report(store_url, store_name, basic_info, contacts,
                          contact_page, products_info, img_quality_fast,
                          lead_score, img_results):

    analyzed = [r for r in img_results if r and not r.get('error')]
    errors   = [r for r in img_results if r and r.get('error')]

    # Separa prodotti da extra
    prod_imgs  = [r for r in analyzed if not r.get('is_extra')]
    extra_imgs = [r for r in analyzed if r.get('is_extra')]

    avg_prod  = round(sum(r['overall_score'] for r in prod_imgs)  / len(prod_imgs))  if prod_imgs  else 0
    avg_extra = round(sum(r['overall_score'] for r in extra_imgs) / len(extra_imgs)) if extra_imgs else 0
    avg_all   = round(sum(r['overall_score'] for r in analyzed)   / len(analyzed))   if analyzed   else 0

    _, avg_prod_color,  _ = score_to_grade(avg_prod)
    _, avg_extra_color, _ = score_to_grade(avg_extra)
    _, avg_all_color,   _ = score_to_grade(avg_all)

    # Raggruppa per source
    by_source_prod  = {}
    by_source_extra = {}
    for r in prod_imgs:
        by_source_prod.setdefault(r['source'], []).append(r)
    for r in extra_imgs:
        by_source_extra.setdefault(r['source'], []).append(r)

    grade_counts = {'A': 0, 'B': 0, 'C': 0, 'D': 0}
    for r in analyzed:
        grade_counts[r['grade']] += 1

    blurry  = [r for r in analyzed if r.get('blur') and r['blur'].get('score', 100) < 45]
    low_res = [r for r in analyzed if r.get('resolution') and r['resolution'].get('score', 100) < 40]
    no_alt  = [r for r in analyzed if r.get('alt_analysis') and r['alt_analysis'].get('score', 100) == 0]

    timestamp = datetime.now().strftime('%d/%m/%Y %H:%M')
    ls        = lead_score['total_score']
    ls_color  = '#22c55e' if ls >= 70 else '#f59e0b' if ls >= 50 else '#6366f1'
    priority_emoji = '🔥' if ls >= 70 else '🌟' if ls >= 50 else '❄️'

    # Sezioni HTML immagini prodotto
    prod_sections_html = ''.join(
        _build_img_section(src, imgs)
        for src, imgs in by_source_prod.items()
    ) or '<p style="color:#6b6b80">Nessuna immagine prodotto analizzata.</p>'

    # Sezioni HTML immagini extra
    extra_sections_html = ''.join(
        _build_img_section(src, imgs)
        for src, imgs in by_source_extra.items()
    ) or '<p style="color:#6b6b80">Nessuna immagine extra trovata.</p>'

    # Lead score breakdown
    breakdown_html = ''
    for crit, sc in lead_score['breakdown'].items():
        pct = sc['score'] / sc['max'] * 100 if sc.get('max') else 0
        breakdown_html += f'''
        <div class="bd-row">
            <div class="bd-label">{crit}</div>
            <div class="bd-right">
                <span class="bd-pts">{sc["score"]}/{sc.get("max","?")}</span>
                <div class="bd-bar"><div class="bd-fill" style="width:{pct}%"></div></div>
                <span class="bd-reason">{sc["reason"]}</span>
            </div>
        </div>'''

    social_html = ''.join(
        f'<a href="{u}" class="social-btn" target="_blank">{p.capitalize()}</a>'
        for p, u in contacts.get('social', {}).items() if u
    )

    issue_chips = ''
    if blurry:  issue_chips += f'<span class="chip chip-red">{len(blurry)} sfocate</span>'
    if low_res: issue_chips += f'<span class="chip chip-orange">{len(low_res)} low-res</span>'
    if no_alt:  issue_chips += f'<span class="chip chip-yellow">{len(no_alt)} senza alt</span>'
    if errors:  issue_chips += f'<span class="chip chip-red">{len(errors)} errori download</span>'
    if not issue_chips:
        issue_chips = '<span style="color:#22c55e;font-size:13px;">✓ Nessun problema rilevato</span>'

    img_issues_html = ''.join(
        f'<li class="issue-li">{i}</li>'
        for i in img_quality_fast.get('issues', [])
    )

    return f'''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report — {store_name}</title>
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Mono:wght@300;400;500&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0d0d0f;--surface:#16161a;--s2:#1e1e24;--border:#2a2a35;--text:#e8e8f0;--muted:#6b6b80;--accent:#7c3aed;--accent2:#06b6d4;--green:#22c55e;--yellow:#f59e0b;--red:#ef4444;--orange:#f97316;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{font-family:'DM Mono',monospace;background:var(--bg);color:var(--text);}}
a{{color:var(--accent2);text-decoration:none;}}
.header{{background:linear-gradient(135deg,#0d0d0f,#1a0a2e 50%,#0d1a2e);padding:60px 40px 50px;border-bottom:1px solid var(--border);position:relative;overflow:hidden;}}
.header::before{{content:'';position:absolute;top:-50%;left:-20%;width:600px;height:600px;background:radial-gradient(circle,rgba(124,58,237,.15),transparent 70%);pointer-events:none;}}
.h-inner{{max-width:1400px;margin:0 auto;position:relative;z-index:1;}}
.h-label{{font-size:11px;letter-spacing:4px;color:var(--accent2);text-transform:uppercase;margin-bottom:16px;}}
.h-title{{font-family:'Syne',sans-serif;font-size:clamp(26px,4vw,48px);font-weight:800;line-height:1.1;margin-bottom:8px;}}
.h-title span{{color:var(--accent2);}}
.h-url{{color:var(--muted);font-size:13px;margin-bottom:4px;}}
.h-meta{{font-size:12px;color:var(--muted);}}
.lead-hero{{display:flex;align-items:center;gap:30px;margin-top:40px;flex-wrap:wrap;}}
.lead-circle{{width:120px;height:120px;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center;border:3px solid {ls_color};box-shadow:0 0 30px {ls_color}40;background:var(--s2);flex-shrink:0;}}
.lead-num{{font-family:'Syne',sans-serif;font-size:38px;font-weight:800;color:{ls_color};line-height:1;}}
.lead-sub{{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);}}
.lead-priority{{font-family:'Syne',sans-serif;font-size:28px;font-weight:800;margin-bottom:6px;}}
.nav{{background:var(--surface);border-bottom:1px solid var(--border);padding:0 40px;position:sticky;top:0;z-index:200;}}
.nav-inner{{max-width:1400px;margin:0 auto;display:flex;gap:0;overflow-x:auto;}}
.nav-tab{{padding:16px 20px;font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);cursor:pointer;border:none;background:none;border-bottom:2px solid transparent;transition:all .15s;white-space:nowrap;font-family:'DM Mono',monospace;}}
.nav-tab:hover{{color:var(--text);}}
.nav-tab.active{{color:var(--accent2);border-bottom-color:var(--accent2);}}
.content{{max-width:1400px;margin:0 auto;padding:40px;}}
.tab-panel{{display:none;}}
.tab-panel.active{{display:block;}}
.sec-title{{font-family:'Syne',sans-serif;font-size:20px;font-weight:700;margin-bottom:24px;padding-bottom:16px;border-bottom:1px solid var(--border);}}
.info-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px;}}
.info-card{{background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:18px 20px;border-left:3px solid var(--accent);}}
.info-card-label{{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:6px;}}
.info-card-val{{font-size:14px;}}
.stat-row{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:30px;}}
.stat-pill{{background:var(--s2);border:1px solid var(--border);border-radius:12px;padding:18px 22px;min-width:110px;}}
.stat-num{{font-family:'Syne',sans-serif;font-size:28px;font-weight:700;}}
.stat-lbl{{font-size:10px;text-transform:uppercase;letter-spacing:2px;color:var(--muted);margin-top:3px;}}
.contact-block{{background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:20px;}}
.contact-item .cl{{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--accent2);margin-bottom:4px;}}
.contact-item .cv{{font-size:13px;}}
.social-btn{{display:inline-block;padding:7px 14px;background:var(--accent);color:white;border-radius:6px;font-size:12px;margin:4px 4px 0 0;}}
.bd-row{{display:flex;justify-content:space-between;align-items:center;padding:12px 0;border-bottom:1px solid var(--border);}}
.bd-row:last-child{{border:none;}}
.bd-label{{font-size:13px;font-weight:600;min-width:140px;}}
.bd-right{{display:flex;align-items:center;gap:10px;flex:1;justify-content:flex-end;flex-wrap:wrap;}}
.bd-pts{{font-size:13px;min-width:40px;text-align:right;}}
.bd-bar{{width:140px;height:6px;background:var(--border);border-radius:3px;overflow:hidden;}}
.bd-fill{{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent2));border-radius:3px;}}
.bd-reason{{font-size:11px;color:var(--muted);min-width:160px;text-align:right;}}
.issue-li{{padding:9px 14px;margin:8px 0;background:rgba(245,158,11,.08);border-left:3px solid var(--yellow);border-radius:4px;font-size:13px;}}
.subtab-nav{{display:flex;gap:8px;margin-bottom:24px;flex-wrap:wrap;}}
.subtab-btn{{background:var(--s2);border:1px solid var(--border);color:var(--muted);padding:8px 18px;border-radius:8px;font-family:'DM Mono',monospace;font-size:12px;cursor:pointer;transition:all .15s;}}
.subtab-btn:hover,.subtab-btn.active{{background:var(--accent);border-color:var(--accent);color:white;}}
.subtab-panel{{display:none;}}
.subtab-panel.active{{display:block;}}
.source-section{{margin-bottom:48px;}}
.source-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;padding-bottom:12px;border-bottom:1px solid var(--border);}}
.source-title{{display:flex;align-items:baseline;gap:10px;}}
.source-title h3{{font-family:'Syne',sans-serif;font-size:17px;font-weight:700;}}
.badge-count{{font-size:11px;color:var(--muted);background:var(--s2);padding:2px 9px;border-radius:16px;}}
.cards-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px;}}
.img-card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;overflow:hidden;transition:transform .2s,border-color .2s;}}
.img-card:hover{{transform:translateY(-3px);border-color:var(--accent);}}
.img-wrap{{position:relative;aspect-ratio:1;overflow:hidden;background:var(--s2);}}
.img-wrap img{{width:100%;height:100%;object-fit:cover;display:block;}}
.no-img{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:32px;}}
.grade-badge{{position:absolute;top:8px;left:8px;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-family:'Syne',sans-serif;font-weight:800;font-size:12px;color:white;box-shadow:0 2px 8px rgba(0,0,0,.5);}}
.score-overlay{{position:absolute;bottom:0;right:0;background:rgba(0,0,0,.75);padding:3px 9px;font-size:11px;border-top-left-radius:7px;}}
.card-body{{padding:12px;}}
.card-source{{font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--accent2);margin-bottom:2px;}}
.card-ctx{{font-size:11px;color:var(--muted);margin-bottom:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
.badges{{display:flex;gap:3px;flex-wrap:wrap;margin-bottom:8px;min-height:18px;}}
.badge{{padding:2px 7px;border-radius:10px;font-size:10px;font-weight:600;letter-spacing:1px;}}
.badge-red{{background:rgba(239,68,68,.2);color:#ef4444;}}
.badge-orange{{background:rgba(249,115,22,.2);color:#f97316;}}
.badge-yellow{{background:rgba(245,158,11,.2);color:#f59e0b;}}
.metrics{{display:flex;flex-direction:column;gap:4px;}}
.metric{{display:flex;justify-content:space-between;gap:6px;font-size:10px;}}
.ml{{color:var(--muted);flex-shrink:0;}}.mv{{text-align:right;}}
.chip{{padding:5px 12px;border-radius:16px;font-size:12px;display:inline-block;margin:4px 4px 0 0;}}
.chip-red{{background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);color:#ef4444;}}
.chip-orange{{background:rgba(249,115,22,.1);border:1px solid rgba(249,115,22,.3);color:#f97316;}}
.chip-yellow{{background:rgba(245,158,11,.1);border:1px solid rgba(245,158,11,.3);color:#f59e0b;}}
.grade-row{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:24px;}}
.grade-block{{display:flex;align-items:center;gap:8px;background:var(--s2);border:1px solid var(--border);padding:8px 14px;border-radius:8px;}}
.grade-letter{{font-family:'Syne',sans-serif;font-size:18px;font-weight:800;}}
.grade-cnt{{font-size:12px;color:var(--muted);}}
.footer{{text-align:center;padding:40px;color:var(--muted);font-size:11px;border-top:1px solid var(--border);margin-top:40px;}}
</style>
</head>
<body>

<div class="header">
  <div class="h-inner">
    <div class="h-label">🔍 Shopify Analyzer — Report Completo</div>
    <div class="h-title">Report — <span>{store_name}</span></div>
    <div class="h-url">{store_url}</div>
    <div class="h-meta">Generato il {timestamp} · {products_info.get('total_count',0)} prodotti · {len(prod_imgs)} img prodotto + {len(extra_imgs)} img extra analizzate</div>
    <div class="lead-hero">
      <div class="lead-circle">
        <div class="lead-num">{ls}</div>
        <div class="lead-sub">Lead Score</div>
      </div>
      <div>
        <div class="lead-priority">{priority_emoji} {lead_score['priority']}</div>
        <div style="color:var(--muted);font-size:13px">Potenziale: <strong>{lead_score['potential']}</strong></div>
      </div>
    </div>
  </div>
</div>

<div class="nav">
  <div class="nav-inner">
    <button class="nav-tab active" onclick="showTab('lead',this)">🎯 Lead Score</button>
    <button class="nav-tab" onclick="showTab('store',this)">🏪 Store</button>
    <button class="nav-tab" onclick="showTab('contacts',this)">📞 Contatti</button>
    <button class="nav-tab" onclick="showTab('products',this)">🏷️ Prodotti</button>
    <button class="nav-tab" onclick="showTab('images',this)">📸 Img Prodotto ({len(prod_imgs)})</button>
    <button class="nav-tab" onclick="showTab('extra',this)">🖼️ Img Extra ({len(extra_imgs)})</button>
  </div>
</div>

<div class="content">

  <div id="tab-lead" class="tab-panel active">
    <div class="sec-title">🎯 Lead Score & Breakdown</div>
    <div class="stat-row">
      <div class="stat-pill"><div class="stat-num" style="color:{ls_color}">{ls}/100</div><div class="stat-lbl">Score totale</div></div>
      <div class="stat-pill"><div class="stat-num">{lead_score['priority']}</div><div class="stat-lbl">Priorità</div></div>
      <div class="stat-pill"><div class="stat-num">{lead_score['potential']}</div><div class="stat-lbl">Potenziale</div></div>
    </div>
    <div style="background:var(--s2);border:1px solid var(--border);border-radius:10px;padding:24px;">{breakdown_html}</div>
  </div>

  <div id="tab-store" class="tab-panel">
    <div class="sec-title">🏪 Informazioni Store</div>
    <div class="info-grid">
      <div class="info-card"><div class="info-card-label">Titolo</div><div class="info-card-val">{basic_info.get('title','N/A')}</div></div>
      <div class="info-card"><div class="info-card-label">Lingua</div><div class="info-card-val">{basic_info.get('language','N/A')}</div></div>
      <div class="info-card"><div class="info-card-label">Tema</div><div class="info-card-val">{basic_info.get('theme','Unknown')}</div></div>
      <div class="info-card"><div class="info-card-label">Google Analytics</div><div class="info-card-val">{'✅ Presente' if basic_info.get('has_analytics') else '❌ Assente'}</div></div>
      <div class="info-card"><div class="info-card-label">Facebook Pixel</div><div class="info-card-val">{'✅ Presente' if basic_info.get('has_facebook_pixel') else '❌ Assente'}</div></div>
    </div>
  </div>

  <div id="tab-contacts" class="tab-panel">
    <div class="sec-title">📞 Contatti</div>
    <div style="font-size:12px;color:var(--muted);margin-bottom:16px">Estratto da: <a href="{contact_page}" target="_blank">{contact_page}</a></div>
    <div class="contact-block">
      <div class="contact-item"><div class="cl">Email</div><div class="cv">{contacts.get('email') or '—'}</div></div>
      <div class="contact-item"><div class="cl">Telefono</div><div class="cv">{contacts.get('phone') or '—'}</div></div>
      <div class="contact-item"><div class="cl">WhatsApp</div><div class="cv">{contacts.get('whatsapp') or '—'}</div></div>
      <div class="contact-item"><div class="cl">P.IVA</div><div class="cv">{contacts.get('piva') or '—'}</div></div>
      <div class="contact-item"><div class="cl">Codice Fiscale</div><div class="cv">{contacts.get('cf') or '—'}</div></div>
      <div class="contact-item"><div class="cl">Indirizzo</div><div class="cv">{contacts.get('address') or '—'}</div></div>
    </div>
    {('<div style="margin-top:20px">' + social_html + '</div>') if social_html else ''}
  </div>

  <div id="tab-products" class="tab-panel">
    <div class="sec-title">🏷️ Prodotti</div>
    <div class="stat-row">
      <div class="stat-pill"><div class="stat-num">{products_info.get('total_count',0)}</div><div class="stat-lbl">Prodotti</div></div>
      <div class="stat-pill"><div class="stat-num" style="color:#06b6d4">€{products_info.get('price_avg',0):.0f}</div><div class="stat-lbl">Prezzo medio</div></div>
      <div class="stat-pill"><div class="stat-num" style="color:{'#ef4444' if img_quality_fast['quality_score']<50 else '#f59e0b' if img_quality_fast['quality_score']<70 else '#22c55e'}">{img_quality_fast['quality_score']}/100</div><div class="stat-lbl">Score img</div></div>
    </div>
    <div class="info-grid">
      <div class="info-card"><div class="info-card-label">Categorie</div><div class="info-card-val" style="font-size:13px">{', '.join(products_info.get('categories',['N/A'])[:4]) or 'N/A'}</div></div>
      <div class="info-card"><div class="info-card-label">Vendor</div><div class="info-card-val">{(products_info.get('vendors') or ['N/A'])[0]}</div></div>
      <div class="info-card"><div class="info-card-label">Immagini totali</div><div class="info-card-val">{img_quality_fast.get('total_images',0)}</div></div>
      <div class="info-card"><div class="info-card-label">Media img / prodotto</div><div class="info-card-val">{img_quality_fast.get('avg_per_product',0)}</div></div>
      <div class="info-card"><div class="info-card-label">Prodotti con 1 foto</div><div class="info-card-val" style="color:var(--orange)">{img_quality_fast.get('single_image_count',0)}</div></div>
      <div class="info-card"><div class="info-card-label">Senza alt text</div><div class="info-card-val" style="color:var(--yellow)">{img_quality_fast.get('no_alt_count',0)}</div></div>
    </div>
    <ul style="list-style:none">{img_issues_html}</ul>
  </div>

  <div id="tab-images" class="tab-panel">
    <div class="sec-title">📸 Immagini Prodotto ({len(prod_imgs)})</div>
    <div class="stat-row">
      <div class="stat-pill"><div class="stat-num" style="color:{avg_prod_color}">{avg_prod}/100</div><div class="stat-lbl">Score medio</div></div>
      <div class="stat-pill"><div class="stat-num">{len(prod_imgs)}</div><div class="stat-lbl">Analizzate</div></div>
      <div class="stat-pill"><div class="stat-num" style="color:var(--red)">{len([r for r in prod_imgs if r.get('blur') and r['blur'].get('score',100)<45])}</div><div class="stat-lbl">Sfocate</div></div>
      <div class="stat-pill"><div class="stat-num" style="color:var(--orange)">{len([r for r in prod_imgs if r.get('resolution') and r['resolution'].get('score',100)<40])}</div><div class="stat-lbl">Low-res</div></div>
    </div>
    <div style="margin-bottom:16px">{issue_chips}</div>
    <div class="grade-row">
      <div class="grade-block"><span class="grade-letter" style="color:#22c55e">A</span><span class="grade-cnt">{grade_counts['A']}</span></div>
      <div class="grade-block"><span class="grade-letter" style="color:#84cc16">B</span><span class="grade-cnt">{grade_counts['B']}</span></div>
      <div class="grade-block"><span class="grade-letter" style="color:#f59e0b">C</span><span class="grade-cnt">{grade_counts['C']}</span></div>
      <div class="grade-block"><span class="grade-letter" style="color:#ef4444">D</span><span class="grade-cnt">{grade_counts['D']}</span></div>
    </div>
    {'<p style="color:var(--muted);font-size:12px;margin-bottom:16px">⚠️ cv2 non installato — analisi nitidezza non disponibile.</p>' if not HAS_CV2 else ''}
    {prod_sections_html}
  </div>

  <div id="tab-extra" class="tab-panel">
    <div class="sec-title">🖼️ Immagini Extra — Pagine Sito ({len(extra_imgs)})</div>
    <div style="font-size:13px;color:var(--muted);margin-bottom:24px;padding:12px 16px;background:var(--s2);border:1px solid var(--border);border-radius:8px;">
      Immagini raccolte da Homepage, Collections, About, Blog e altre pagine del sito.
      Conteggio <strong>separato</strong> dalle immagini prodotto (max {MAX_EXTRA_IMGS}).
    </div>
    <div class="stat-row">
      <div class="stat-pill"><div class="stat-num" style="color:{avg_extra_color}">{avg_extra}/100</div><div class="stat-lbl">Score medio</div></div>
      <div class="stat-pill"><div class="stat-num">{len(extra_imgs)}</div><div class="stat-lbl">Analizzate</div></div>
      <div class="stat-pill"><div class="stat-num" style="color:var(--red)">{len([r for r in extra_imgs if r.get('blur') and r['blur'].get('score',100)<45])}</div><div class="stat-lbl">Sfocate</div></div>
      <div class="stat-pill"><div class="stat-num" style="color:var(--orange)">{len([r for r in extra_imgs if r.get('resolution') and r['resolution'].get('score',100)<40])}</div><div class="stat-lbl">Low-res</div></div>
    </div>
    {extra_sections_html}
  </div>

</div>

<div class="footer">Shopify Analyzer · {timestamp} · {store_url}</div>

<script>
function showTab(name, btn) {{
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}}
</script>
</body>
</html>'''


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',    default=None)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    store_url = args.url
    if not store_url:
        if len(sys.argv) > 1 and sys.argv[1].startswith('http'):
            store_url = sys.argv[1].strip()
        else:
            store_url = input("Store URL: ").strip()

    if not store_url.startswith('http'):
        store_url = 'https://' + store_url
    store_url = store_url.rstrip('/')

    output_dir = Path(args.output) if args.output else Path(__file__).resolve().parent
    output_dir.mkdir(parents=True, exist_ok=True)

    parsed     = urlparse(store_url)
    base_url   = f"{parsed.scheme}://{parsed.netloc}"
    store_name = parsed.netloc.split('.')[0].replace('-', ' ').title()

    log(f"Analisi: {store_url}")
    t0 = datetime.now()

    log("Verifica Shopify...")
    shopify = verify_shopify(base_url)
    if not shopify['is_shopify']:
        result = {'success': False, 'error': 'Non sembra uno store Shopify attivo'}
        print(f"RESULT_JSON:{json.dumps(result)}")
        sys.exit(1)

    products = shopify['products']
    log(f"OK — {len(products)} prodotti")

    log("Info store...")
    basic_info = extract_basic_info(base_url)

    log("Analisi prodotti...")
    products_info    = analyze_products(products)
    img_quality_fast = analyze_product_images_fast(products)

    log("Estrazione contatti...")
    contact_page = find_contact_page(base_url)
    contacts     = extract_contacts(contact_page)

    log("Lead scoring...")
    lead_score = calculate_lead_score(
        contacts, products_info, img_quality_fast, contacts.get('social', {})
    )

    log(f"Analisi immagini {'(cv2)' if HAS_CV2 else '(fast mode)'}...")
    all_imgs    = collect_all_img_urls(base_url, products)
    img_results = analyze_images_parallel(all_imgs)

    analyzed   = [r for r in img_results if r and not r.get('error')]
    prod_imgs  = [r for r in analyzed if not r.get('is_extra')]
    extra_imgs = [r for r in analyzed if r.get('is_extra')]
    avg_img    = round(sum(r['overall_score'] for r in analyzed) / len(analyzed)) if analyzed else 0

    duration = (datetime.now() - t0).seconds
    log(f"Completato in {duration}s — {len(prod_imgs)} img prodotto + {len(extra_imgs)} img extra")

    def safe_img(r):
        if r is None:
            return None
        return {k: v for k, v in r.items() if k != 'pil_img'}

    result = {
        'success':    True,
        'store_url':  store_url,
        'store_name': store_name,
        'report_file': '',
        'duration_s': duration,
        'lead_score':     lead_score['total_score'],
        'lead_priority':  lead_score['priority'],
        'lead_potential': lead_score['potential'],
        'lead_breakdown': lead_score['breakdown'],
        'product_count': products_info['total_count'],
        'price_avg':     products_info['price_avg'],
        'price_min':     products_info['price_min'],
        'price_max':     products_info['price_max'],
        'categories':    ', '.join(products_info.get('categories', [])[:5]),
        'vendors':       ', '.join(products_info.get('vendors', [])[:3]),
        'img_quality_score':   img_quality_fast['quality_score'],
        'img_total':           img_quality_fast['total_images'],
        'img_avg_per_product': img_quality_fast['avg_per_product'],
        'img_single_count':    img_quality_fast['single_image_count'],
        'img_low_res_count':   img_quality_fast['low_res_count'],
        'img_no_alt_count':    img_quality_fast['no_alt_count'],
        'img_issues':          json.dumps(img_quality_fast['issues']),
        'img_analyzed_count':  len(analyzed),
        'img_avg_score':       avg_img,
        'img_results':         [safe_img(r) for r in img_results],
        'store_title':       basic_info.get('title', '') or '',
        'store_description': basic_info.get('description', '') or '',
        'store_language':    basic_info.get('language', '') or '',
        'store_theme':       basic_info.get('theme', '') or '',
        'has_analytics':     basic_info.get('has_analytics', False),
        'has_fb_pixel':      basic_info.get('has_facebook_pixel', False),
        'email':        contacts.get('email') or '',
        'phone':        contacts.get('phone') or '',
        'whatsapp_url': contacts.get('whatsapp') or '',
        'piva':         contacts.get('piva') or '',
        'address':      contacts.get('address') or '',
        'instagram':    contacts.get('social', {}).get('instagram') or '',
        'facebook':     contacts.get('social', {}).get('facebook') or '',
        'tiktok':       contacts.get('social', {}).get('tiktok') or '',
        'linkedin':     contacts.get('social', {}).get('linkedin') or '',
    }

    print(f"RESULT_JSON:{json.dumps(result)}")


if __name__ == "__main__":
    main()