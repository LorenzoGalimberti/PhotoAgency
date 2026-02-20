from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from stores.models import Store
from .services import run_analysis


def analyze_store(request, pk):
    """
    Analizza un singolo store (da pagina dettaglio).
    Funziona sempre — crea un nuovo StoreAnalysis anche se già analizzato.
    """
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
    """
    Analizza tutti gli store con status=new.
    Gli store già analizzati (status != new) vengono saltati.
    """
    if request.method != 'POST':
        return redirect('stores:store_list')

    stores_to_analyze = Store.objects.filter(status=Store.Status.NEW)
    total   = stores_to_analyze.count()

    if total == 0:
        messages.info(request,
            "Nessuno store da analizzare. "
            "Tutti gli store sono gia stati analizzati.")
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
            f"Analisi massiva completata — "
            f"{success_count} analizzati, {error_count} errori su {total} totali.")
    if errors:
        messages.warning(request,
            f"Errori: " + " | ".join(errors[:3]))

    return redirect('stores:store_list')