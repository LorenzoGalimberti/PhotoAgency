import json
from django.shortcuts import redirect, get_object_or_404, render
from django.contrib import messages
from stores.models import Store, StoreAnalysis
from .services import run_analysis


def analyze_store(request, pk):
    store = get_object_or_404(Store, pk=pk)
    if request.method == 'POST':
        result = run_analysis(store)
        if result['success']:
            messages.success(request,
                f"Analisi completata — Lead Score: {result['analysis'].lead_score}/100 "
                f"({result['analysis'].lead_priority})")
        else:
            messages.error(request, f"Errore analisi: {result['error']}")
    return redirect('stores:store_detail', pk=pk)


def analyze_all(request):
    if request.method != 'POST':
        return redirect('stores:store_list')

    stores_to_analyze = Store.objects.filter(status=Store.Status.NEW)
    total = stores_to_analyze.count()

    if total == 0:
        messages.info(request, "Nessuno store da analizzare.")
        return redirect('stores:store_list')

    success_count = 0
    error_count   = 0
    errors        = []

    for store in stores_to_analyze:
        result = run_analysis(store)
        if result['success']:
            success_count += 1
        else:
            error_count += 1
            errors.append(f"{store.url}: {result['error']}")

    if success_count > 0:
        messages.success(request,
            f"Analisi completata — {success_count} analizzati, {error_count} errori su {total} totali.")
    if errors:
        messages.warning(request, "Errori: " + " | ".join(errors[:3]))

    return redirect('stores:store_list')


def analysis_report(request, pk):
    """
    Pagina report completa per una singola analisi — sostituisce il vecchio HTML.
    """
    analysis = get_object_or_404(StoreAnalysis, pk=pk)
    store    = analysis.store

    raw            = analysis.raw_json or {}
    img_results    = raw.get('img_results', [])
    lead_breakdown = raw.get('lead_breakdown', {})
    # DEBUG — rimuovi dopo
    print(f"raw keys: {list(raw.keys())}")
    print(f"img_results count: {len(img_results)}")
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