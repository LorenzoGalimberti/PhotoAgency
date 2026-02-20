"""
analyzer/services.py

Lancia shopify_complete.py come subprocess e salva i risultati nel DB.
"""

import subprocess
import sys
import os
import json
from pathlib import Path
from django.conf import settings
from stores.models import Store, StoreAnalysis


def run_analysis(store: Store) -> dict:
    """
    Lancia l'analisi su un singolo store.
    Ritorna dict con success, messaggio e StoreAnalysis creata.
    """
    script_path = Path(settings.BASE_DIR) / 'scripts' / 'shopify_complete.py'
    output_dir  = Path(settings.MEDIA_ROOT) / 'reports'
    output_dir.mkdir(parents=True, exist_ok=True)

    if not script_path.exists():
        return {'success': False, 'error': f"Script non trovato: {script_path}"}

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'

    cmd = [
        sys.executable,
        str(script_path),
        '--url',    store.url,
        '--output', str(output_dir),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=120,
            env=env,
        )

        # Cerca la riga RESULT_JSON nell'output
        result_data = None
        for line in proc.stdout.splitlines():
            if line.startswith('RESULT_JSON:'):
                result_data = json.loads(line[len('RESULT_JSON:'):])
                break

        if result_data is None:
            err = proc.stderr[-400:] if proc.stderr else 'Nessun output JSON trovato'
            return {'success': False, 'error': err}

        if not result_data.get('success'):
            return {'success': False, 'error': result_data.get('error', 'Errore sconosciuto')}

        # Salva StoreAnalysis nel DB
        analysis = StoreAnalysis.objects.create(
            store            = store,
            lead_score       = result_data['lead_score'],
            lead_priority    = result_data['lead_priority'],
            lead_potential   = result_data['lead_potential'],
            product_count    = result_data['product_count'],
            price_avg        = result_data['price_avg'],
            price_min        = result_data['price_min'],
            price_max        = result_data['price_max'],
            categories       = result_data['categories'],
            vendors          = result_data['vendors'],
            img_quality_score   = result_data['img_quality_score'],
            img_total           = result_data['img_total'],
            img_avg_per_product = result_data['img_avg_per_product'],
            img_single_count    = result_data['img_single_count'],
            img_low_res_count   = result_data['img_low_res_count'],
            img_no_alt_count    = result_data['img_no_alt_count'],
            img_issues          = result_data['img_issues'],
            store_title         = result_data['store_title'],
            store_description   = result_data['store_description'],
            store_language      = result_data['store_language'],
            store_theme         = result_data['store_theme'],
            has_analytics       = result_data['has_analytics'],
            has_fb_pixel        = result_data['has_fb_pixel'],
            report_file         = result_data['report_file'],
            duration_s          = result_data['duration_s'],
            raw_json            = result_data,
        )

        # Aggiorna Store con i contatti trovati e status
        updated_fields = ['status']
        store.status = Store.Status.ANALYZED

        for field in ['email', 'phone', 'whatsapp_url', 'piva', 'address',
                      'instagram', 'facebook', 'tiktok', 'linkedin']:
            val = result_data.get(field, '')
            if val and not getattr(store, field):
                setattr(store, field, val)
                updated_fields.append(field)

        if result_data.get('store_title') and not store.name:
            store.name = result_data['store_title']
            updated_fields.append('name')

        store.save(update_fields=updated_fields)

        return {
            'success':  True,
            'analysis': analysis,
            'store':    store,
        }

    except subprocess.TimeoutExpired:
        return {'success': False, 'error': 'Timeout — analisi oltre 2 minuti'}
    except Exception as e:
        return {'success': False, 'error': str(e)}