"""
analyzer/views.py

Views aggiornate con analisi asincrona.
analyze_store e analyze_all lanciano thread in background
e reindirizzano alla pagina job_status con log live.
"""

import json
import threading
from django.shortcuts import redirect, get_object_or_404, render
from django.http import JsonResponse
from stores.models import Store, StoreAnalysis
from . import job_manager
from .services import run_single_analysis_thread, run_bulk_analysis_thread


# ─────────────────────────────────────────────
# ANALISI SINGOLO STORE (asincrona)
# ─────────────────────────────────────────────

def analyze_store(request, pk):
    store = get_object_or_404(Store, pk=pk)
    if request.method != 'POST':
        return redirect('stores:store_detail', pk=pk)

    job_id = job_manager.create_job(job_type='single', total=1)

    # Salva store_pk nel job per il redirect finale
    job = job_manager._jobs.get(job_id)
    if job:
        job['store_pk'] = store.pk

    # Lancia il thread
    t = threading.Thread(
        target=run_single_analysis_thread,
        args=(store, job_id),
        daemon=True,
    )
    t.start()

    return redirect('analyzer:job_status', job_id=job_id)


# ─────────────────────────────────────────────
# ANALISI BULK (asincrona)
# ─────────────────────────────────────────────

def analyze_all(request):
    if request.method != 'POST':
        return redirect('stores:store_list')

    stores_qs = Store.objects.filter(status=Store.Status.NEW)
    total     = stores_qs.count()

    if total == 0:
        from django.contrib import messages
        messages.info(request, "Nessuno store da analizzare.")
        return redirect('stores:store_list')

    job_id = job_manager.create_job(job_type='bulk', total=total)

    t = threading.Thread(
        target=run_bulk_analysis_thread,
        args=(stores_qs, job_id),
        daemon=True,
    )
    t.start()

    return redirect('analyzer:job_status', job_id=job_id)


# ─────────────────────────────────────────────
# PAGINA STATUS (con polling live)
# ─────────────────────────────────────────────

def job_status(request, job_id):
    job = job_manager.get_job(job_id)
    if job is None:
        return render(request, 'analyzer/job_status.html', {
            'job': None,
            'job_id': job_id,
        })
    return render(request, 'analyzer/job_status.html', {
        'job':    job,
        'job_id': job_id,
    })


# ─────────────────────────────────────────────
# API POLLING (JSON)
# ─────────────────────────────────────────────

def job_status_api(request, job_id):
    """Endpoint JSON per il polling JS."""
    job = job_manager.get_job(job_id)
    if job is None:
        return JsonResponse({'error': 'Job non trovato'}, status=404)

    # Calcola redirect URL per quando il job è completato
    redirect_url = None
    if job['status'] == 'completed':
        if job['type'] == 'single' and job.get('analysis_pk'):
            redirect_url = f"/analyzer/report/{job['analysis_pk']}/"
        elif job['type'] == 'single' and job.get('store_pk'):
            redirect_url = f"/stores/{job['store_pk']}/"
        else:
            redirect_url = '/stores/'

    return JsonResponse({
        'status':       job['status'],
        'type':         job['type'],
        'total':        job['total'],
        'completed':    job['completed'],
        'success':      job['success'],
        'errors':       job['errors'],
        'current_url':  job['current_url'],
        'current_name': job['current_name'],
        'logs':         job['logs'][-50:],   # ultimi 50 log
        'results':      job['results'],
        'redirect_url': redirect_url,
        'finished_at':  job['finished_at'],
    })


# ─────────────────────────────────────────────
# REPORT ANALISI (invariato)
# ─────────────────────────────────────────────

def analysis_report(request, pk):
    analysis = get_object_or_404(StoreAnalysis, pk=pk)
    store    = analysis.store

    raw            = analysis.raw_json or {}
    img_results    = raw.get('img_results', [])
    lead_breakdown = raw.get('lead_breakdown', {})

    prod_imgs  = [r for r in img_results if r and not r.get('is_extra') and not r.get('error')]
    extra_imgs = [r for r in img_results if r and r.get('is_extra')     and not r.get('error')]

    avg_prod  = round(sum(r['overall_score'] for r in prod_imgs)  / len(prod_imgs))  if prod_imgs  else 0
    avg_extra = round(sum(r['overall_score'] for r in extra_imgs) / len(extra_imgs)) if extra_imgs else 0

    grade_counts = {'A': 0, 'B': 0, 'C': 0, 'D': 0}
    for r in prod_imgs:
        g = r.get('grade', 'D')
        if g in grade_counts:
            grade_counts[g] += 1

    by_source_prod  = {}
    by_source_extra = {}
    for r in prod_imgs:
        by_source_prod.setdefault(r.get('source', 'Prodotti'), []).append(r)
    for r in extra_imgs:
        by_source_extra.setdefault(r.get('source', 'Pagine'), []).append(r)

    try:
        img_issues = json.loads(analysis.img_issues) if analysis.img_issues else []
    except Exception:
        img_issues = []

    return render(request, 'analyzer/analysis_report.html', {
        'analysis':        analysis,
        'store':           store,
        'raw':             raw,
        'lead_breakdown':  lead_breakdown,
        'prod_imgs':       prod_imgs,
        'extra_imgs':      extra_imgs,
        'by_source_prod':  by_source_prod,
        'by_source_extra': by_source_extra,
        'avg_prod':        avg_prod,
        'avg_extra':       avg_extra,
        'grade_counts':    grade_counts,
        'img_issues':      img_issues,
    })