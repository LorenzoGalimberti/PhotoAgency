"""
analyzer/job_manager.py

Gestione job asincroni in memoria.
Traccia stato, log e progressione di analisi singole e bulk.
"""

import uuid
import threading
from datetime import datetime


# Dizionario globale: job_id → stato
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def create_job(job_type: str = 'single', total: int = 1) -> str:
    """Crea un nuovo job e restituisce il job_id."""
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = {
            'id':           job_id,
            'type':         job_type,   # 'single' | 'bulk'
            'status':       'running',  # 'running' | 'completed' | 'error'
            'total':        total,
            'completed':    0,
            'success':      0,
            'errors':       0,
            'current_url':  None,
            'current_name': None,
            'logs':         [],         # lista di {time, level, msg}
            'results':      [],         # lista di {url, name, score, priority, success}
            'started_at':   datetime.now().isoformat(),
            'finished_at':  None,
            # per singolo store
            'store_pk':     None,
            'analysis_pk':  None,
        }
    return job_id


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        # Restituisce una copia per evitare race conditions
        return {
            **job,
            'logs':    list(job['logs']),
            'results': list(job['results']),
        }


def add_log(job_id: str, msg: str, level: str = 'info'):
    """Aggiunge una riga di log al job."""
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job['logs'].append({
                'time':  datetime.now().strftime('%H:%M:%S'),
                'level': level,   # 'info' | 'success' | 'error' | 'warn'
                'msg':   msg,
            })


def set_current(job_id: str, url: str, name: str):
    """Aggiorna lo store attualmente in analisi."""
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job['current_url']  = url
            job['current_name'] = name


def mark_store_done(job_id: str, url: str, name: str, success: bool,
                    score: int = 0, priority: str = '', error: str = ''):
    """Segna uno store come completato e aggiorna i contatori."""
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job['completed'] += 1
            if success:
                job['success'] += 1
            else:
                job['errors'] += 1
            job['results'].append({
                'url':      url,
                'name':     name,
                'success':  success,
                'score':    score,
                'priority': priority,
                'error':    error,
            })


def complete_job(job_id: str, analysis_pk: int = None):
    """Segna il job come completato."""
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job['status']      = 'completed'
            job['finished_at'] = datetime.now().isoformat()
            job['current_url']  = None
            job['current_name'] = None
            if analysis_pk:
                job['analysis_pk'] = analysis_pk


def fail_job(job_id: str, error: str):
    """Segna il job come fallito."""
    with _lock:
        job = _jobs.get(job_id)
        if job:
            job['status']      = 'error'
            job['finished_at'] = datetime.now().isoformat()
            job['logs'].append({
                'time':  datetime.now().strftime('%H:%M:%S'),
                'level': 'error',
                'msg':   f'Errore fatale: {error}',
            })


def cleanup_old_jobs(max_jobs: int = 50):
    """Rimuove i job più vecchi se superano il limite."""
    with _lock:
        if len(_jobs) > max_jobs:
            # Rimuove i job completati più vecchi
            completed = [
                (jid, j) for jid, j in _jobs.items()
                if j['status'] != 'running'
            ]
            completed.sort(key=lambda x: x[1]['started_at'])
            for jid, _ in completed[:len(_jobs) - max_jobs]:
                del _jobs[jid]