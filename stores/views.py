import json
import csv  

import subprocess
import sys
import os
import re
from pathlib import Path
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.conf import settings
from django.core.paginator import Paginator
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import ImportStoresForm, SeleniumSearchForm
from .services import import_stores_from_content
from .models import Store, StoreAnalysis, NicheQueryTemplate, MessageTemplate


# ─── Costante stati "contattato" ─────────────────────────────────────────────
CONTACTED_STATUSES = ['contacted', 'replied', 'converted']


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
        'niche_queries': json.dumps(niche_queries),
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

    def render_body(body):
        def replace_var(m):
            var = m.group(1).strip()
            if var == 'store.name':
                return store.name or store.domain
            if var == 'store.domain':
                return store.domain
            if var == 'store.get_niche_display':
                return store.get_niche_display()
            return m.group(0)
        return re.sub(r'\{\{\s*([\w.]+)\s*\}\}', replace_var, body)

    msg_templates  = MessageTemplate.objects.filter(is_active=True)
    templates_json = json.dumps([
        {'id': t.pk, 'name': t.name, 'body': render_body(t.body), 'is_default': t.is_default}
        for t in msg_templates
    ])

    return render(request, 'stores/store_detail.html', {
        'store':          store,
        'analyses':       analyses,
        'contacts':       contacts,
        'latest':         analyses.first(),
        'msg_templates':  msg_templates,
        'templates_json': templates_json,
    })


def change_status(request, pk):
    """
    Cambia lo status di uno store.
    - Se richiesta normale (form) → redirect a store_detail
    - Se richiesta AJAX (X-Requested-With: XMLHttpRequest oppure Accept: application/json) → JsonResponse
    """
    store = get_object_or_404(Store, pk=pk)
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(Store.Status.choices):
            store.status = new_status
            store.save(update_fields=['status'])

            # Risposta AJAX
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or 'application/json' in request.headers.get('Accept', '')
            )
            if is_ajax:
                return JsonResponse({
                    'ok':          True,
                    'status':      store.status,
                    'status_label': store.get_status_display(),
                })

            messages.success(request, f"Stato aggiornato: {store.get_status_display()}")
        else:
            is_ajax = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or 'application/json' in request.headers.get('Accept', '')
            )
            if is_ajax:
                return JsonResponse({'ok': False, 'error': 'Stato non valido'}, status=400)

    return redirect('stores:store_detail', pk=pk)


# ─── CRUD MessageTemplate ────────────────────────────────────────────────────

def message_templates(request):
    templates = MessageTemplate.objects.all()
    return render(request, 'stores/message_templates.html', {
        'templates': templates,
    })


def message_template_create(request):
    if request.method == 'POST':
        name       = request.POST.get('name', '').strip()
        body       = request.POST.get('body', '').strip()
        is_default = request.POST.get('is_default') == 'on'
        is_active  = request.POST.get('is_active') == 'on'

        if not name or not body:
            messages.error(request, "Nome e testo sono obbligatori.")
        else:
            MessageTemplate.objects.create(
                name=name, body=body,
                is_default=is_default, is_active=is_active,
            )
            messages.success(request, f"Template \"{name}\" creato.")
            return redirect('stores:message_templates')

    return render(request, 'stores/message_template_form.html', {
        'action': 'Crea', 'template': None,
    })


def message_template_edit(request, pk):
    tmpl = get_object_or_404(MessageTemplate, pk=pk)

    if request.method == 'POST':
        name       = request.POST.get('name', '').strip()
        body       = request.POST.get('body', '').strip()
        is_default = request.POST.get('is_default') == 'on'
        is_active  = request.POST.get('is_active') == 'on'

        if not name or not body:
            messages.error(request, "Nome e testo sono obbligatori.")
        else:
            tmpl.name       = name
            tmpl.body       = body
            tmpl.is_default = is_default
            tmpl.is_active  = is_active
            tmpl.save()
            messages.success(request, f"Template \"{name}\" aggiornato.")
            return redirect('stores:message_templates')

    return render(request, 'stores/message_template_form.html', {
        'action': 'Modifica', 'template': tmpl,
    })


def message_template_delete(request, pk):
    tmpl = get_object_or_404(MessageTemplate, pk=pk)
    if request.method == 'POST':
        name = tmpl.name
        tmpl.delete()
        messages.success(request, f"Template \"{name}\" eliminato.")
    return redirect('stores:message_templates')


def message_template_set_default(request, pk):
    tmpl = get_object_or_404(MessageTemplate, pk=pk)
    if request.method == 'POST':
        tmpl.is_default = True
        tmpl.save()
        messages.success(request, f"\"{tmpl.name}\" impostato come default.")
    return redirect('stores:message_templates')


# ─── WhatsApp ────────────────────────────────────────────────────────────────

def whatsapp_list(request):
    # Base queryset store con numero trovato
    found_qs = (
        Store.objects
        .filter(whatsapp_analyzed_at__isnull=False)
        .exclude(whatsapp_url='')
        .order_by('-whatsapp_analyzed_at')
    )

    # Filtro contattati: '' = tutti | 'yes' = contattati | 'no' = non contattati
    contacted_filter = request.GET.get('contacted', '')
    if contacted_filter == 'yes':
        found = found_qs.filter(status__in=CONTACTED_STATUSES)
    elif contacted_filter == 'no':
        found = found_qs.exclude(status__in=CONTACTED_STATUSES)
    else:
        found = found_qs

    not_found = (
        Store.objects
        .filter(whatsapp_analyzed_at__isnull=False, whatsapp_url='')
        .order_by('-whatsapp_analyzed_at')
    )
    pending = (
        Store.objects
        .filter(whatsapp_analyzed_at__isnull=True)
        .order_by('-discovered_at')
    )

    # Contatori fissi (non dipendono dal filtro) per i badge tab
    total_found     = found_qs.count()
    total_contacted     = found_qs.filter(status__in=CONTACTED_STATUSES).count()
    total_not_contacted = found_qs.exclude(status__in=CONTACTED_STATUSES).count()

    return render(request, 'stores/whatsapp_list.html', {
        'found':                found,
        'not_found':            not_found,
        'pending':              pending,
        'total_found':          total_found,
        'total_not_found':      not_found.count(),
        'total_pending':        pending.count(),
        'contacted_filter':     contacted_filter,
        'total_contacted':      total_contacted,
        'total_not_contacted':  total_not_contacted,
    })


@csrf_exempt
@require_POST
def analyze_whatsapp_ajax(request, pk):
    import traceback
    try:
        store = get_object_or_404(Store, pk=pk)

        # Skip se già analizzato (a meno che non sia force)
        force = request.GET.get('force') == '1'
        if store.whatsapp_analyzed_at is not None and not force:
            return JsonResponse({
                'status':       'skipped',
                'number':       store.whatsapp_url or '',
                'whatsapp_url': store.whatsapp_url or '',
                'message':      'Già analizzato',
            })

        script_path = Path(settings.BASE_DIR) / 'scripts' / 'wa_step2_extract.py'
        if not script_path.exists():
            return JsonResponse({
                'status':  'error',
                'message': f'Script non trovato: {script_path}',
            }, status=500)

        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8']       = '1'

        proc = subprocess.run(
            [sys.executable, str(script_path), store.url],
            capture_output=True,
            text=True,
            encoding='utf-8',
            timeout=60,
            env=env,
        )
        raw_number = (proc.stdout or '').strip()

        whatsapp_url = ''
        if raw_number:
            clean = raw_number.replace('+', '').replace(' ', '').replace('-', '')
            whatsapp_url = f'https://wa.me/{clean}'

        store.whatsapp_url         = whatsapp_url
        store.whatsapp_analyzed_at = timezone.now()
        store.save(update_fields=['whatsapp_url', 'whatsapp_analyzed_at'])

        if whatsapp_url:
            return JsonResponse({
                'status':       'found',
                'number':       raw_number,
                'whatsapp_url': whatsapp_url,
                'message':      f'Numero trovato: {raw_number}',
            })
        else:
            return JsonResponse({
                'status':       'not_found',
                'number':       '',
                'whatsapp_url': '',
                'message':      'Nessun widget WhatsApp trovato',
            })

    except subprocess.TimeoutExpired:
        store.whatsapp_analyzed_at = timezone.now()
        store.save(update_fields=['whatsapp_analyzed_at'])
        return JsonResponse({
            'status':  'timeout',
            'message': 'Timeout (>60s) — store troppo lento',
        })

    except Exception as e:
        tb = traceback.format_exc()
        print("=== WA ANALYZE ERROR ===")
        print(tb)
        print("========================")
        return JsonResponse({
            'status':  'error',
            'message': str(e),
            'traceback': tb,
        }, status=500)


def export_stores_urls(request):
    """
    Esporta tutti gli URL degli store in un file .txt (uno per riga).
    Opzionale: filtro per status e/o nicchia via query param.
    """
    qs = Store.objects.all()

    status = request.GET.get('status')
    niche  = request.GET.get('niche')
    if status:
        qs = qs.filter(status=status)
    if niche:
        qs = qs.filter(niche=niche)

    response = HttpResponse(content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stores_urls.txt"'

    for store in qs.values_list('url', flat=True):
        response.write(store + '\n')

    return response


def export_stores_csv(request):
    """
    Esporta gli store in CSV con url, email, nicchia, status, lead_score.
    """
    qs = Store.objects.all()

    status = request.GET.get('status')
    niche  = request.GET.get('niche')
    if status:
        qs = qs.filter(status=status)
    if niche:
        qs = qs.filter(niche=niche)

    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stores.csv"'

    writer = csv.writer(response)
    writer.writerow(['url', 'name', 'email', 'niche', 'status', 'phone', 'instagram', 'discovered_at'])

    for s in qs.select_related():
        writer.writerow([
            s.url,
            s.name,
            s.email,
            s.niche,
            s.status,
            s.phone,
            s.instagram,
            s.discovered_at.strftime('%Y-%m-%d') if s.discovered_at else '',
        ])

    return response