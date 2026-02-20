from django import forms
from .models import Store


class ImportStoresForm(forms.Form):
    """Form per importare store da file .txt o .py."""

    file = forms.FileField(
        label='File store (.txt o .py)',
        help_text='File generato dallo script Selenium (shopify_stores_*.txt o stores_list_*.py)',
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.txt,.py'})
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
        initial='Selenium Import',
        help_text='Es: "arredamento italia - 19/02/2025"',
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )


class SeleniumSearchForm(forms.Form):
    """Form per lanciare lo script Selenium dalla web app."""

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