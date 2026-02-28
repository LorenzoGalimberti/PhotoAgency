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

    # ✅ NUOVO: range pagine
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