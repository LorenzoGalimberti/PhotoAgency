import json
import subprocess
import sys
import os
from pathlib import Path

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.conf import settings
from django.core.paginator import Paginator

from .forms import ImportStoresForm, SeleniumSearchForm
from .services import import_stores_from_content
from .models import Store, StoreAnalysis, NicheQueryTemplate


def dashboard(request):
    total         = Store.objects.count()
    hot           = StoreAnalysis.objects.filter(lead_priority__icontains='HOT').values('store').distinct().count()
    analyzed      = Store.objects.filter(status=Store.Status.ANALYZED).count()
    contacted     = Store.objects.filter(status=Store.Status.CONTACTED).count()
    recent_stores = Store.objects.order_by('-discovered_at')[:5]

    return render(request, 'stores/dashboard.html', {
        'total':         total,
        'hot':           hot,
        'analyzed':      analyzed,
        'contacted':     contacted,
        'recent_stores': recent_stores,
    })


def import_stores(request):
    if request.method == 'POST':
        form = ImportStoresForm(request.POST, request.FILES)
        if form.is_valid():
            niche        = form.cleaned_data['niche']
            source_label = form.cleaned_data.get('source_label', '')
            content      = ''

            if request.FILES.get('file'):
                uploaded_file = request.FILES['file']
                try:
                    content = uploaded_file.read().decode('utf-8')
                except UnicodeDecodeError:
                    content = uploaded_file.read().decode('latin-1')

            if form.cleaned_data.get('urls_text'):
                content += '\n' + form.cleaned_data['urls_text']

            if not content.strip():
                messages.error(request, "Inserisci un file oppure incolla almeno un URL.")
                return redirect('stores:import_stores')

            result = import_stores_from_content(
                content=content,
                niche=niche,
                source_label=source_label or 'Import Manuale',
            )

            n_imported = len(result['imported'])
            n_skipped  = len(result['skipped'])
            n_errors   = len(result['errors'])

            if n_imported > 0:
                messages.success(request, f"{n_imported} store importati con successo.")
            if n_skipped > 0:
                messages.info(request, f"{n_skipped} store gia presenti (saltati).")
            if n_errors > 0:
                messages.warning(request, f"{n_errors} URL non validi o errori.")
            if result['total_found'] == 0:
                messages.error(request,
                    "Nessun URL Shopify trovato. "
                    "Il file deve contenere URL tipo https://store.myshopify.com")

            request.session['import_result'] = {
                'imported':    [s.url for s in result['imported']],
                'skipped':     result['skipped'],
                'errors':      result['errors'],
                'total_found': result['total_found'],
            }
            return redirect('stores:import_stores')
    else:
        form = ImportStoresForm()

    import_result = request.session.pop('import_result', None)
    return render(request, 'stores/import_stores.html', {
        'form': form, 'import_result': import_result,
    })


def run_selenium(request):
    # ✅ Costruisce mappa nicchia → query dal DB
    niche_queries = {}
    for t in NicheQueryTemplate.objects.filter(active=True):
        niche_queries[t.niche] = t.queries_list()

    if request.method == 'POST':
        form = SeleniumSearchForm(request.POST)
        if form.is_valid():
            queries      = form.cleaned_data['queries']
            niche        = form.cleaned_data['niche']
            headless     = form.cleaned_data['headless']
            source_label = form.cleaned_data.get('source_label', '')
            page_from    = int(form.cleaned_data['page_from'])
            page_to      = int(form.cleaned_data['page_to'])

            query_list  = [q.strip() for q in queries.splitlines() if q.strip()]
            queries_arg = '|'.join(query_list)

            output_file = Path(settings.MEDIA_ROOT) / 'selenium_output.txt'
            script_path = Path(settings.BASE_DIR) / 'scripts' / 'selenium_extractor.py'

            if not script_path.exists():
                messages.error(request, f"Script non trovato in: {script_path}")
                return redirect('stores:run_selenium')

            cmd = [sys.executable, str(script_path),
                   '--queries',   queries_arg,
                   '--output',    str(output_file),
                   '--page-from', str(page_from),
                   '--page-to',   str(page_to)]
            if headless:
                cmd.append('--headless')

            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    encoding='utf-8', timeout=300, env=env,
                )

                if proc.returncode != 0:
                    err_msg = proc.stderr[-300:] if proc.stderr else 'Errore sconosciuto'
                    messages.error(request, f"Selenium terminato con errore: {err_msg}")
                    return redirect('stores:run_selenium')

                if not output_file.exists():
                    messages.error(request, "File output non trovato dopo l'esecuzione.")
                    return redirect('stores:run_selenium')

                content = output_file.read_text(encoding='utf-8')
                result  = import_stores_from_content(
                    content=content, niche=niche,
                    source_label=source_label or f"Selenium - {', '.join(query_list[:2])}",
                )

                n_imported = len(result['imported'])
                n_skipped  = len(result['skipped'])
                messages.success(request,
                    f"Completato — {n_imported} nuovi store, {n_skipped} gia presenti.")

                request.session['import_result'] = {
                    'imported':    [s.url for s in result['imported']],
                    'skipped':     result['skipped'],
                    'errors':      result['errors'],
                    'total_found': result['total_found'],
                }

            except subprocess.TimeoutExpired:
                messages.error(request, "Timeout — script oltre 5 minuti.")
            except Exception as e:
                messages.error(request, f"Errore: {e}")

            return redirect('stores:run_selenium')
    else:
        form = SeleniumSearchForm()

    import_result = request.session.pop('import_result', None)
    return render(request, 'stores/run_selenium.html', {
        'form':          form,
        'import_result': import_result,
        'niche_queries': json.dumps(niche_queries),  # ✅ passa al template come JSON
    })


def store_list(request):
    qs = Store.objects.prefetch_related('analyses').order_by('-discovered_at')

    status = request.GET.get('status', '')
    niche  = request.GET.get('niche', '')
    email  = request.GET.get('email', '')
    sort   = request.GET.get('sort', '-discovered_at')

    if status:
        qs = qs.filter(status=status)
    if niche:
        qs = qs.filter(niche=niche)
    if email == 'yes':
        qs = qs.exclude(email='')
    if email == 'no':
        qs = qs.filter(email='')

    allowed_sorts = {
        '-discovered_at': '-discovered_at',
        'discovered_at':  'discovered_at',
        'name':           'name',
        'status':         'status',
    }
    qs = qs.order_by(allowed_sorts.get(sort, '-discovered_at'))

    paginator = Paginator(qs, 20)
    stores    = paginator.get_page(request.GET.get('page', 1))

    page_range_start = max(1, stores.number - 3)
    page_range_end   = min(paginator.num_pages, stores.number + 3)

    counts = {
        'total':      Store.objects.count(),
        'new':        Store.objects.filter(status='new').count(),
        'analyzed':   Store.objects.filter(status='analyzed').count(),
        'contacted':  Store.objects.filter(status='contacted').count(),
        'converted':  Store.objects.filter(status='converted').count(),
        'with_email': Store.objects.exclude(email='').count(),
    }

    return render(request, 'stores/store_list.html', {
        'stores':           stores,
        'counts':           counts,
        'status_choices':   Store.Status.choices,
        'niche_choices':    Store.Niche.choices,
        'filter_status':    status,
        'filter_niche':     niche,
        'filter_email':     email,
        'filter_sort':      sort,
        'page_range_start': page_range_start,
        'page_range_end':   page_range_end,
    })


def store_detail(request, pk):
    store    = get_object_or_404(Store, pk=pk)
    analyses = store.analyses.order_by('-created_at')
    contacts = store.contact_logs.order_by('-sent_at')

    return render(request, 'stores/store_detail.html', {
        'store':    store,
        'analyses': analyses,
        'contacts': contacts,
        'latest':   analyses.first(),
    })


def change_status(request, pk):
    store = get_object_or_404(Store, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(Store.Status.choices):
            store.status = new_status
            store.save(update_fields=['status'])
            messages.success(request, f"Stato aggiornato: {store.get_status_display()}")
    return redirect('stores:store_detail', pk=pk)