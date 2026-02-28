import os
import sys
import django
from pathlib import Path

# Setup Django
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'PhotoAgency.settings')
django.setup()

from stores.models import NicheQueryTemplate

NICHE_QUERIES = {
    'arredamento': """site:myshopify.com arredamento casa italia
site:myshopify.com home decor italia
site:myshopify.com candele profumate
site:myshopify.com oggetti casa design
site:myshopify.com complementi arredo""",

    'moda': """site:myshopify.com abbigliamento donna italia
site:myshopify.com moda italiana online
site:myshopify.com vestiti donna boutique
site:myshopify.com fashion brand italia""",

    'gioielli': """site:myshopify.com gioielli artigianali italia
site:myshopify.com bijoux handmade
site:myshopify.com collane bracciali italia
site:myshopify.com accessori moda donna""",

    'beauty': """site:myshopify.com cosmetici naturali italia
site:myshopify.com skincare biologica
site:myshopify.com make up italiano
site:myshopify.com profumi artigianali""",

    'food': """site:myshopify.com prodotti tipici italiani
site:myshopify.com olio extravergine italia
site:myshopify.com vino italiano online
site:myshopify.com food artigianale italia""",

    'sport': """site:myshopify.com abbigliamento sportivo italia
site:myshopify.com attrezzatura outdoor
site:myshopify.com sport equipment italia""",

    'tech': """site:myshopify.com accessori tech italia
site:myshopify.com gadget tecnologici
site:myshopify.com elettronica online italia""",

    'arte': """site:myshopify.com stampe artistiche italia
site:myshopify.com arte digitale print
site:myshopify.com quadri moderni online
site:myshopify.com poster design italiano""",

    'bambini': """site:myshopify.com giocattoli bambini italia
site:myshopify.com abbigliamento bambini
site:myshopify.com giochi educativi""",

    'animali': """site:myshopify.com accessori animali domestici
site:myshopify.com prodotti cani gatti italia
site:myshopify.com pet shop online italia""",
}


def run():
    created = 0
    updated = 0

    for niche, queries in NICHE_QUERIES.items():
        obj, is_new = NicheQueryTemplate.objects.update_or_create(
            niche=niche,
            defaults={
                'queries': queries.strip(),
                'active':  True,
            }
        )
        if is_new:
            created += 1
            print(f"[+] Creato: {obj.get_niche_display()}")
        else:
            updated += 1
            print(f"[~] Aggiornato: {obj.get_niche_display()}")

    print(f"\n✅ Fatto — {created} creati, {updated} aggiornati")


if __name__ == '__main__':
    run()