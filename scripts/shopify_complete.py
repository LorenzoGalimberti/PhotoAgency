"""
============================================================================
SHOPIFY COMPLETE ANALYZER — integrato con Django PhotoAgency
============================================================================

Uso da Django (subprocess):
    python scripts/shopify_complete.py --url https://store.myshopify.com --output media/reports/

Uso manuale:
    python scripts/shopify_complete.py --url https://store.myshopify.com

Output:
    - report HTML in --output/report_<storename>_<timestamp>.html
    - JSON risultati su stdout (ultima riga) per Django
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

# Fix encoding Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding and sys.stderr.encoding.lower() != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

import requests
from bs4 import BeautifulSoup

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
TIMEOUT = 10
IMG_WORKERS = 6
IMG_TIMEOUT = 8
IMG_DOWNLOAD_PX = 400

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
        for t in ['dawn', 'debut', 'brooklyn', 'minimal', 'supply', 'narrative']:
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
        issues.append(f"Media immagini per prodotto bassa: {avg}")
    if single > len(products) * 0.3:
        score -= 25
        issues.append(f"{single} prodotti con 1 sola immagine")
    if low_res > 0:
        score -= 20
        issues.append(f"{low_res} immagini sotto 1000px")
    if no_alt > total_imgs * 0.5:
        score -= 15
        issues.append(f"{no_alt} immagini senza alt text")
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

        for pattern in [r'(?:Via|Viale|Corso|Piazza|Largo)\s+[A-Za-z\s\.]+,?\s*\d+',
                        r'\d{5}\s+[A-Za-z\s]+\s*\([A-Z]{2}\)']:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                result['address'] = m.group(0).strip()
                break

        piva = re.search(r'(?:P\.?\s?IVA|Partita\s+IVA)[:\s]*(\d{11})', text, re.IGNORECASE)
        if piva: result['piva'] = piva.group(1)

        social_patterns = {
            'instagram': r'instagram\.com/([A-Za-z0-9_.]+)',
            'facebook':  r'facebook\.com/([A-Za-z0-9_.]+)',
            'tiktok':    r'tiktok\.com/@([A-Za-z0-9_.]+)',
            'linkedin':  r'linkedin\.com/(?:company|in)/([A-Za-z0-9_\-]+)',
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
# LEAD SCORING
# ============================================================================

def calculate_lead_score(contacts, products_info, img_quality, social):
    score, breakdown = 0, {}

    if contacts.get('email'):
        score += 20
        breakdown['Email'] = {'score': 20, 'reason': f"trovata: {contacts['email']}"}
    else:
        breakdown['Email'] = {'score': 0, 'reason': 'Non trovata'}

    n = products_info.get('total_count', 0)
    ps = 15 if n >= 50 else 10 if n >= 20 else 5 if n >= 10 else 0
    score += ps
    breakdown['Prodotti'] = {'score': ps, 'reason': f"{n} prodotti"}

    iq = img_quality.get('quality_score', 100)
    ims = 35 if iq < 50 else 20 if iq < 70 else 5
    score += ims
    breakdown['Immagini'] = {'score': ims, 'reason': f"Score immagini: {iq}/100"}

    avg_p = products_info.get('price_avg', 0)
    prs = 15 if avg_p >= 100 else 10 if avg_p >= 50 else 5
    score += prs
    breakdown['Prezzo'] = {'score': prs, 'reason': f"Prezzo medio: {avg_p}"}

    sc = sum(1 for v in social.values() if v)
    ss = 15 if sc >= 3 else 8 if sc >= 1 else 0
    score += ss
    breakdown['Social'] = {'score': ss, 'reason': f"{sc} piattaforme"}

    if   score >= 70: priority, potential = "HOT",  "ALTO"
    elif score >= 50: priority, potential = "WARM", "MEDIO"
    else:             priority, potential = "COLD", "BASSO"

    return {
        'total_score': score,
        'priority':    priority,
        'potential':   potential,
        'breakdown':   breakdown,
    }


# ============================================================================
# REPORT HTML (versione compatta senza analisi immagini pesante)
# ============================================================================

def generate_html_report(store_url, store_name, basic_info, contacts,
                          products_info, img_quality_fast, lead_score):
    timestamp = datetime.now().strftime('%d/%m/%Y %H:%M')
    ls = lead_score['total_score']
    ls_color = '#22c55e' if ls >= 70 else '#f59e0b' if ls >= 50 else '#6366f1'

    breakdown_html = ''
    for crit, sc in lead_score['breakdown'].items():
        pct = sc['score'] / 35 * 100
        breakdown_html += f'''
        <div style="display:flex;justify-content:space-between;align-items:center;
                    padding:10px 0;border-bottom:1px solid #2a2a35">
          <span style="font-size:13px;font-weight:600;min-width:100px">{crit}</span>
          <span style="font-size:12px;color:#9ca3af;flex:1;text-align:right;margin-right:12px">{sc["reason"]}</span>
          <span style="font-size:13px;font-weight:700;color:{ls_color}">{sc["score"]}</span>
        </div>'''

    issues_html = ''.join(
        f'<li style="padding:6px 12px;margin:4px 0;background:rgba(245,158,11,.08);'
        f'border-left:3px solid #f59e0b;border-radius:4px;font-size:13px">{i}</li>'
        for i in img_quality_fast.get('issues', [])
    )

    social_html = ''.join(
        f'<a href="{u}" target="_blank" style="display:inline-block;padding:4px 12px;'
        f'background:#7c3aed;color:white;border-radius:6px;font-size:11px;'
        f'text-decoration:none;margin:2px">{p.capitalize()}</a>'
        for p, u in contacts.get('social', {}).items() if u
    )

    return f'''<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report — {store_name}</title>
<style>
  body{{margin:0;font-family:"Segoe UI",sans-serif;background:#0d0d0f;color:#e8e8f0}}
  .wrap{{max-width:900px;margin:0 auto;padding:40px 24px}}
  .header{{background:linear-gradient(135deg,#1a0a2e,#0d1a2e);padding:40px;
           border-radius:12px;margin-bottom:24px}}
  .card{{background:#16161a;border:1px solid #2a2a35;border-radius:10px;
         padding:24px;margin-bottom:16px}}
  .card h3{{font-size:14px;letter-spacing:2px;text-transform:uppercase;
            color:#06b6d4;margin:0 0 16px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
  .info-item{{background:#1e1e24;border-radius:8px;padding:12px 16px}}
  .info-label{{font-size:10px;letter-spacing:2px;text-transform:uppercase;
               color:#6b6b80;margin-bottom:4px}}
  .info-val{{font-size:14px}}
  .score-circle{{width:100px;height:100px;border-radius:50%;
                 border:3px solid {ls_color};display:flex;flex-direction:column;
                 align-items:center;justify-content:center;margin-right:24px}}
  .score-num{{font-size:36px;font-weight:800;color:{ls_color};line-height:1}}
  .score-lbl{{font-size:10px;letter-spacing:2px;color:#6b6b80}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div style="font-size:11px;letter-spacing:3px;color:#06b6d4;margin-bottom:12px">
      SHOPIFY ANALYZER — REPORT
    </div>
    <h1 style="font-size:32px;font-weight:800;margin:0 0 6px">{store_name}</h1>
    <div style="color:#9ca3af;font-size:13px;margin-bottom:6px">{store_url}</div>
    <div style="color:#6b6b80;font-size:12px">Generato il {timestamp}</div>
    <div style="display:flex;align-items:center;margin-top:28px">
      <div class="score-circle">
        <div class="score-num">{ls}</div>
        <div class="score-lbl">SCORE</div>
      </div>
      <div>
        <div style="font-size:26px;font-weight:800">{lead_score["priority"]}</div>
        <div style="color:#9ca3af;font-size:13px">Potenziale: {lead_score["potential"]}</div>
      </div>
    </div>
  </div>

  <div class="card">
    <h3>Info Store</h3>
    <div class="grid">
      <div class="info-item">
        <div class="info-label">Titolo</div>
        <div class="info-val">{basic_info.get("title") or "—"}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Lingua</div>
        <div class="info-val">{basic_info.get("language") or "—"}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Tema</div>
        <div class="info-val">{basic_info.get("theme") or "—"}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Analytics / Pixel</div>
        <div class="info-val">
          {"GA presente" if basic_info.get("has_analytics") else "GA assente"} /
          {"Pixel presente" if basic_info.get("has_facebook_pixel") else "Pixel assente"}
        </div>
      </div>
    </div>
  </div>

  <div class="card">
    <h3>Prodotti</h3>
    <div class="grid">
      <div class="info-item">
        <div class="info-label">Totale prodotti</div>
        <div class="info-val">{products_info.get("total_count", 0)}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Prezzo medio</div>
        <div class="info-val">€{products_info.get("price_avg", 0):.2f}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Range prezzi</div>
        <div class="info-val">€{products_info.get("price_min", 0):.0f} — €{products_info.get("price_max", 0):.0f}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Score immagini</div>
        <div class="info-val" style="color:{"#ef4444" if img_quality_fast["quality_score"]<50 else "#f59e0b" if img_quality_fast["quality_score"]<70 else "#22c55e"}">
          {img_quality_fast.get("quality_score", 0)}/100
        </div>
      </div>
    </div>
    <ul style="list-style:none;padding:0;margin:16px 0 0">{issues_html}</ul>
  </div>

  <div class="card">
    <h3>Contatti</h3>
    <div class="grid">
      <div class="info-item">
        <div class="info-label">Email</div>
        <div class="info-val">{contacts.get("email") or "—"}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Telefono</div>
        <div class="info-val">{contacts.get("phone") or "—"}</div>
      </div>
      <div class="info-item">
        <div class="info-label">P.IVA</div>
        <div class="info-val">{contacts.get("piva") or "—"}</div>
      </div>
      <div class="info-item">
        <div class="info-label">Indirizzo</div>
        <div class="info-val" style="font-size:12px">{contacts.get("address") or "—"}</div>
      </div>
    </div>
    {f'<div style="margin-top:16px">{social_html}</div>' if social_html else ''}
  </div>

  <div class="card">
    <h3>Lead Score Breakdown</h3>
    {breakdown_html}
  </div>

</div>
</body>
</html>'''


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url',    required=True, help='URL store Shopify')
    parser.add_argument('--output', default='media/reports',
                        help='Cartella output report HTML')
    args = parser.parse_args()

    store_url  = args.url.rstrip('/')
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    parsed     = urlparse(store_url)
    base_url   = f"{parsed.scheme}://{parsed.netloc}"
    store_name = parsed.netloc.split('.')[0].replace('-', ' ').title()

    log(f"Analisi: {store_url}")
    t0 = datetime.now()

    # 1. Verifica Shopify
    log("Verifica Shopify...")
    shopify = verify_shopify(base_url)
    if not shopify['is_shopify']:
        result = {'success': False, 'error': 'Non sembra uno store Shopify attivo'}
        print(f"RESULT_JSON:{json.dumps(result)}")
        sys.exit(1)

    products = shopify['products']
    log(f"OK — {len(products)} prodotti")

    # 2. Info base
    log("Info store...")
    basic_info = extract_basic_info(base_url)

    # 3. Prodotti
    log("Analisi prodotti...")
    products_info    = analyze_products(products)
    img_quality_fast = analyze_product_images_fast(products)

    # 4. Contatti
    log("Estrazione contatti...")
    contact_page = find_contact_page(base_url)
    contacts     = extract_contacts(contact_page)

    # 5. Lead scoring
    log("Lead scoring...")
    lead_score = calculate_lead_score(
        contacts, products_info, img_quality_fast, contacts.get('social', {})
    )

    # 6. Report HTML
    log("Generazione report...")
    html = generate_html_report(
        store_url, store_name, basic_info, contacts,
        products_info, img_quality_fast, lead_score
    )

    ts          = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_name   = re.sub(r'[^\w]', '_', store_name)
    report_name = f"report_{safe_name}_{ts}.html"
    report_path = output_dir / report_name

    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(html)

    duration = (datetime.now() - t0).seconds
    log(f"Completato in {duration}s — report: {report_path}")

    # Aggiorna contatti in store se trovati
    result = {
        'success':    True,
        'store_url':  store_url,
        'store_name': store_name,
        'report_file': report_name,
        'duration_s': duration,
        # Lead score
        'lead_score':     lead_score['total_score'],
        'lead_priority':  lead_score['priority'],
        'lead_potential': lead_score['potential'],
        # Prodotti
        'product_count': products_info['total_count'],
        'price_avg':     products_info['price_avg'],
        'price_min':     products_info['price_min'],
        'price_max':     products_info['price_max'],
        'categories':    ', '.join(products_info.get('categories', [])[:5]),
        'vendors':       ', '.join(products_info.get('vendors', [])[:3]),
        # Immagini
        'img_quality_score':   img_quality_fast['quality_score'],
        'img_total':           img_quality_fast['total_images'],
        'img_avg_per_product': img_quality_fast['avg_per_product'],
        'img_single_count':    img_quality_fast['single_image_count'],
        'img_low_res_count':   img_quality_fast['low_res_count'],
        'img_no_alt_count':    img_quality_fast['no_alt_count'],
        'img_issues':          json.dumps(img_quality_fast['issues']),
        # Store info
        'store_title':       basic_info.get('title', ''),
        'store_description': basic_info.get('description', '') or '',
        'store_language':    basic_info.get('language', '') or '',
        'store_theme':       basic_info.get('theme', '') or '',
        'has_analytics':     basic_info.get('has_analytics', False),
        'has_fb_pixel':      basic_info.get('has_facebook_pixel', False),
        # Contatti
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

    # Stampa JSON su ultima riga — Django lo legge
    print(f"RESULT_JSON:{json.dumps(result)}")


if __name__ == "__main__":
    main()