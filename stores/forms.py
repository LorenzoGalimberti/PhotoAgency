from django import forms
from .models import Store


class ImportStoresForm(forms.Form):

    file = forms.FileField(
        label='File store (.txt o .py)',
        required=False,
        help_text='File generato dallo script Selenium (shopify_stores_*.txt o stores_list_*.py)',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.txt,.py'})
    )

    urls_text = forms.CharField(
        label='Oppure incolla URL direttamente',
        required=False,
        help_text='Uno URL per riga: https://store.myshopify.com',
        widget=forms.Textarea(attrs={
            'class': 'form-control font-monospace',
            'rows': 4,
            'placeholder': 'https://store-uno.myshopify.com\nhttps://store-due.myshopify.com'
        })
    )

    niche = forms.ChoiceField(
        label='Nicchia',
        choices=Store.Niche.choices,
        initial='altro',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    source_label = forms.CharField(
        label='Etichetta sorgente',
        max_length=100,
        required=False,
        initial='Import Manuale',
        help_text='Es: "arredamento italia - 19/02/2025"',
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    strict_filter = forms.BooleanField(
        label='Filtra solo .it e Shopify',
        required=False,
        initial=True,
        help_text='Deseleziona per importare qualsiasi URL (.com, .eu, ecc.)',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_strict_filter'})
    )


class SeleniumSearchForm(forms.Form):

    queries = forms.CharField(
        label='Query di ricerca',
        help_text='Una query per riga. Es: site:myshopify.com arredamento italia',
        initial=(
            'site:myshopify.com arredamento casa italia\n'
            'site:myshopify.com home decor italia\n'
            'site:myshopify.com candele profumate'
        ),
        widget=forms.Textarea(attrs={
            'class': 'form-control font-monospace',
            'rows': 6,
        })
    )

    niche = forms.ChoiceField(
        label='Nicchia',
        choices=Store.Niche.choices,
        initial='altro',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    page_from = forms.ChoiceField(
        label='Dalla pagina',
        initial='1',
        choices=[(str(i), f'Pagina {i}') for i in range(1, 11)],
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    page_to = forms.ChoiceField(
        label='Alla pagina',
        initial='3',
        choices=[(str(i), f'Pagina {i}') for i in range(1, 11)],
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    source_label = forms.CharField(
        label='Etichetta sorgente',
        max_length=100,
        required=False,
        help_text='Es: "arredamento - febbraio 2025"',
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    headless = forms.BooleanField(
        label='Modalita invisibile (headless)',
        required=False,
        initial=False,
        help_text='Se attivo, Chrome non si apre visualmente',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def clean(self):
        cleaned = super().clean()
        page_from = int(cleaned.get('page_from', 1))
        page_to   = int(cleaned.get('page_to', 3))
        if page_from > page_to:
            raise forms.ValidationError(
                '"Dalla pagina" non può essere maggiore di "Alla pagina".'
            )
        return cleaned
    
class MetaAdsSearchForm(forms.Form):

    keyword_list = forms.ChoiceField(
        label='Lista keyword salvata',
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    keyword = forms.CharField(
        label='Oppure keyword singola',
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={
            'class':       'form-control',
            'placeholder': 'es: gioielli artigianali, scarpe donna...',
        })
    )

    country = forms.ChoiceField(
        label='Paese',
        choices=[
            ('IT', 'Italia'),
            ('FR', 'Francia'),
            ('DE', 'Germania'),
            ('ES', 'Spagna'),
            ('US', 'USA'),
            ('GB', 'Regno Unito'),
        ],
        initial='IT',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    niche = forms.ChoiceField(
        label='Nicchia da assegnare agli store trovati',
        choices=Store.Niche.choices,
        initial='altro',
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    limit = forms.IntegerField(
        label='Max annunci per keyword',
        initial=500,
        min_value=50,
        max_value=5000,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )

    date_from = forms.DateField(
        label='Annunci attivi dal',
        required=False,
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type':  'date',
        })
    )

    date_to = forms.DateField(
        label='Annunci attivi fino al',
        required=False,
        widget=forms.DateInput(attrs={
            'class': 'form-control',
            'type':  'date',
        })
    )

    shopify_only = forms.BooleanField(
        label='Solo Shopify con prodotti (consigliato)',
        initial=True,
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Popola le scelte della lista keyword dinamicamente
        from .models import MetaAdsKeywordList
        lists = MetaAdsKeywordList.objects.filter(active=True).order_by('name')
        choices = [('', '— nessuna lista —')]
        choices += [(str(kl.pk), f"{kl.name} ({kl.keywords_count()} keywords)") for kl in lists]
        self.fields['keyword_list'].choices = choices

    def clean(self):
        cleaned = super().clean()
        keyword_list = cleaned.get('keyword_list')
        keyword      = cleaned.get('keyword', '').strip()
        date_from    = cleaned.get('date_from')
        date_to      = cleaned.get('date_to')

        if not keyword_list and not keyword:
            raise forms.ValidationError(
                "Inserisci almeno una keyword oppure seleziona una lista salvata."
            )
        if date_from and date_to and date_from > date_to:
            raise forms.ValidationError(
                "La data di inizio deve essere precedente alla data di fine."
            )
        return cleaned