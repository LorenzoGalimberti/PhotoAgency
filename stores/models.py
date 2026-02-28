from django.db import models
from django.utils import timezone


class Store(models.Model):
    """
    Rappresenta uno store Shopify scoperto.
    È il nodo centrale del CRM.
    """

    class Status(models.TextChoices):
        NEW        = 'new',        'Nuovo'
        ANALYZED   = 'analyzed',   'Analizzato'
        CONTACTED  = 'contacted',  'Contattato'
        REPLIED    = 'replied',    'Ha risposto'
        CONVERTED  = 'converted',  'Convertito'
        REJECTED   = 'rejected',   'Non interessato'
        SKIP       = 'skip',       'Da saltare'

    class Niche(models.TextChoices):
        ARREDAMENTO  = 'arredamento',  'Arredamento & Casa'
        MODA         = 'moda',         'Moda & Abbigliamento'
        GIOIELLI     = 'gioielli',     'Gioielli & Accessori'
        BEAUTY       = 'beauty',       'Beauty & Cosmetica'
        FOOD         = 'food',         'Food & Beverage'
        SPORT        = 'sport',        'Sport & Outdoor'
        TECH         = 'tech',         'Tech & Elettronica'
        ARTE         = 'arte',         'Arte & Stampe'
        BAMBINI      = 'bambini',      'Bambini & Giochi'
        ANIMALI      = 'animali',      'Animali'
        ALTRO        = 'altro',        'Altro'

    url           = models.URLField(unique=True, verbose_name='URL Store')
    myshopify_url = models.URLField(blank=True, verbose_name='URL myshopify.com')
    name          = models.CharField(max_length=255, blank=True, verbose_name='Nome brand')
    domain        = models.CharField(max_length=255, blank=True, verbose_name='Dominio')
    niche         = models.CharField(max_length=50, choices=Niche.choices, default=Niche.ALTRO)
    status        = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    tags          = models.CharField(max_length=500, blank=True, help_text='Tag separati da virgola')
    email         = models.EmailField(blank=True, verbose_name='Email principale')
    phone         = models.CharField(max_length=50, blank=True)
    whatsapp_url  = models.URLField(blank=True)
    piva          = models.CharField(max_length=20, blank=True, verbose_name='P.IVA')
    address       = models.TextField(blank=True, verbose_name='Indirizzo')
    instagram     = models.URLField(blank=True)
    facebook      = models.URLField(blank=True)
    tiktok        = models.URLField(blank=True)
    linkedin      = models.URLField(blank=True)
    notes         = models.TextField(blank=True, verbose_name='Note interne')
    discovered_at = models.DateTimeField(default=timezone.now, verbose_name='Scoperto il')
    updated_at    = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Store'
        verbose_name_plural = 'Store'
        ordering            = ['-discovered_at']

    def __str__(self):
        return self.name or self.url

    @property
    def latest_analysis(self):
        return self.analyses.order_by('-created_at').first()

    @property
    def lead_score(self):
        a = self.latest_analysis
        return a.lead_score if a else None

    @property
    def has_email(self):
        return bool(self.email)

    @property
    def social_count(self):
        return sum(1 for s in [self.instagram, self.facebook, self.tiktok, self.linkedin] if s)


class StoreAnalysis(models.Model):
    """
    Risultato dell'analisi completa di uno store.
    Ogni store può avere più analisi nel tempo (storico).
    """

    store            = models.ForeignKey(Store, on_delete=models.CASCADE,
                                          related_name='analyses', verbose_name='Store')
    lead_score       = models.IntegerField(default=0, verbose_name='Lead Score')
    lead_priority    = models.CharField(max_length=20, blank=True)
    lead_potential   = models.CharField(max_length=20, blank=True)
    product_count    = models.IntegerField(default=0)
    price_avg        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_min        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price_max        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    categories       = models.CharField(max_length=500, blank=True)
    vendors          = models.CharField(max_length=500, blank=True)
    img_quality_score    = models.IntegerField(default=0, verbose_name='Score immagini')
    img_total            = models.IntegerField(default=0)
    img_avg_per_product  = models.FloatField(default=0)
    img_single_count     = models.IntegerField(default=0)
    img_low_res_count    = models.IntegerField(default=0)
    img_no_alt_count     = models.IntegerField(default=0)
    img_issues           = models.TextField(blank=True)
    store_title          = models.CharField(max_length=255, blank=True)
    store_description    = models.TextField(blank=True)
    store_language       = models.CharField(max_length=10, blank=True)
    store_theme          = models.CharField(max_length=50, blank=True)
    has_analytics        = models.BooleanField(default=False)
    has_fb_pixel         = models.BooleanField(default=False)
    report_file          = models.CharField(max_length=500, blank=True)
    raw_json             = models.JSONField(null=True, blank=True)
    created_at           = models.DateTimeField(default=timezone.now)
    duration_s           = models.IntegerField(default=0)

    class Meta:
        verbose_name        = 'Analisi Store'
        verbose_name_plural = 'Analisi Store'
        ordering            = ['-created_at']

    def __str__(self):
        return f"Analisi {self.store} — {self.created_at.strftime('%d/%m/%Y %H:%M')}"

    @property
    def priority_emoji(self):
        mapping = {'HOT': '🔥', 'WARM': '🌟', 'COLD': '❄️'}
        for k, v in mapping.items():
            if k in self.lead_priority.upper():
                return v
        return '—'


class ContactLog(models.Model):
    """
    Traccia ogni tentativo di contatto verso uno store.
    """

    class ContactType(models.TextChoices):
        EMAIL   = 'email',   'Email'
        MANUAL  = 'manual',  'Manuale (DM/altro)'
        CALL    = 'call',    'Telefonata'

    class Outcome(models.TextChoices):
        SENT       = 'sent',       'Inviato'
        OPENED     = 'opened',     'Aperto'
        REPLIED    = 'replied',    'Risposta ricevuta'
        INTERESTED = 'interested', 'Interessato'
        NOT_NOW    = 'not_now',    'Non ora'
        REJECTED   = 'rejected',   'Non interessato'
        BOUNCED    = 'bounced',    'Email bounced'

    store        = models.ForeignKey(Store, on_delete=models.CASCADE,
                                      related_name='contact_logs')
    contact_type = models.CharField(max_length=20, choices=ContactType.choices,
                                     default=ContactType.EMAIL)
    outcome      = models.CharField(max_length=20, choices=Outcome.choices,
                                     default=Outcome.SENT)
    subject      = models.CharField(max_length=255, blank=True)
    body         = models.TextField(blank=True, verbose_name='Corpo messaggio')
    notes        = models.TextField(blank=True, verbose_name='Note follow-up')
    sent_at      = models.DateTimeField(default=timezone.now)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = 'Log Contatto'
        verbose_name_plural = 'Log Contatti'
        ordering            = ['-sent_at']

    def __str__(self):
        return f"{self.get_contact_type_display()} → {self.store} ({self.get_outcome_display()})"


# ✅ NUOVO
class NicheQueryTemplate(models.Model):
    """
    Query di ricerca Google associate a una nicchia.
    Usate per popolare automaticamente il form di ricerca Selenium.
    """
    niche = models.CharField(
        max_length=50,
        choices=Store.Niche.choices,
        unique=True,
        verbose_name='Nicchia'
    )
    queries = models.TextField(
        verbose_name='Query di ricerca',
        help_text='Una query per riga. Es: site:myshopify.com arredamento casa italia'
    )
    active = models.BooleanField(default=True, verbose_name='Attivo')

    class Meta:
        verbose_name        = 'Template Query Nicchia'
        verbose_name_plural = 'Template Query Nicchie'
        ordering            = ['niche']

    def __str__(self):
        return self.get_niche_display()

    def queries_list(self):
        return [q.strip() for q in self.queries.splitlines() if q.strip()]