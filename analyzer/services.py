"""
analyzer/services.py

Lancia shopify_complete.py come subprocess e salva i risultati nel DB.
Versione asincrona: supporta job_id per log live via job_manager.
"""

import subprocess
import sys
import os
import json
from pathlib import Path
from django.conf import settings
from stores.models import Store, StoreAnalysis
from . import job_manager


def run_analysis(store: Store, job_id: str = None) -> dict:
    """
    Lancia l'analisi su un singolo store.
    Se job_id è fornito, logga in tempo reale nel job_manager.
    Ritorna dict con success, messaggio e StoreAnalysis creata.
    """

    def log(msg, level='info'):
        if job_id:
            job_manager.add_log(job_id, msg, level)

    script_path = Path(settings.BASE_DIR) / 'scripts' / 'shopify_complete.py'
    output_dir  = Path(settings.MEDIA_ROOT) / 'reports'
    output_dir.mkdir(parents=True, exist_ok=True)

    if not script_path.exists():
        err = f"Script non trovato: {script_path}"
        log(err, 'error')
        return {'success': False, 'error': err}

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'

    cmd = [
        sys.executable,
        str(script_path),
        '--url',    store.url,
        '--output', str(output_dir),
    ]

    log(f'Avvio analisi: {store.url}')

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=120,
            env=env,
        )

        # Parsa i log dello script e li riversa nel job_manager
        result_data = None
        for line in proc.stdout.splitlines():
            if line.startswith('RESULT_JSON:'):
                result_data = json.loads(line[len('RESULT_JSON:'):])
            elif line.startswith('[analyzer]'):
                # Log live dallo script
                msg = line.replace('[analyzer]', '').strip()
                level = 'error' if 'errore' in msg.lower() or 'error' in msg.lower() else 'info'
                log(msg, level)

        if result_data is None:
            err = proc.stderr[-400:] if proc.stderr else 'Nessun output JSON trovato'
            log(f'Analisi fallita: {err}', 'error')
            return {'success': False, 'error': err}

        if not result_data.get('success'):
            err = result_data.get('error', 'Errore sconosciuto')
            log(f'Store non valido: {err}', 'warn')
            return {'success': False, 'error': err}

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

        score    = result_data['lead_score']
        priority = result_data['lead_priority']
        log(f"Completato — Score: {score}/100 ({priority}) — {result_data['duration_s']}s", 'success')

        return {
            'success':  True,
            'analysis': analysis,
            'store':    store,
        }

    except subprocess.TimeoutExpired:
        err = 'Timeout — analisi oltre 2 minuti'
        log(err, 'error')
        return {'success': False, 'error': err}
    except Exception as e:
        err = str(e)
        log(f'Eccezione: {err}', 'error')
        return {'success': False, 'error': err}


def run_bulk_analysis_thread(stores_qs, job_id: str):
    """
    Esegue l'analisi bulk in un thread separato.
    Aggiorna job_manager ad ogni store completato.
    """
    stores = list(stores_qs)

    job_manager.add_log(job_id, f'Avvio analisi bulk: {len(stores)} store da processare', 'info')

    for store in stores:
        name = store.name or store.domain or store.url
        job_manager.set_current(job_id, store.url, name)
        job_manager.add_log(job_id, f'→ {name} ({store.url})', 'info')

        result = run_analysis(store, job_id=job_id)

        if result['success']:
            analysis = result['analysis']
            job_manager.mark_store_done(
                job_id,
                url=store.url,
                name=name,
                success=True,
                score=analysis.lead_score,
                priority=analysis.lead_priority,
            )
        else:
            job_manager.mark_store_done(
                job_id,
                url=store.url,
                name=name,
                success=False,
                error=result['error'],
            )

    job_manager.complete_job(job_id)
    job_manager.add_log(job_id, 'Analisi bulk completata.', 'success')
    job_manager.cleanup_old_jobs()


def run_single_analysis_thread(store: Store, job_id: str):
    """
    Esegue l'analisi di un singolo store in un thread separato.
    """
    name = store.name or store.domain or store.url
    job_manager.set_current(job_id, store.url, name)
    job_manager.add_log(job_id, f'Analisi store: {name}', 'info')

    result = run_analysis(store, job_id=job_id)

    if result['success']:
        analysis = result['analysis']
        job_manager.mark_store_done(
            job_id,
            url=store.url,
            name=name,
            success=True,
            score=analysis.lead_score,
            priority=analysis.lead_priority,
        )
        job_manager.complete_job(job_id, analysis_pk=analysis.pk)
    else:
        job_manager.mark_store_done(
            job_id,
            url=store.url,
            name=name,
            success=False,
            error=result['error'],
        )
        job_manager.fail_job(job_id, result['error'])
        