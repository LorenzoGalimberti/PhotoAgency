from django.contrib import admin
from .models import Store, StoreAnalysis, ContactLog, NicheQueryTemplate


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display    = ['name', 'domain', 'niche', 'status', 'email', 'lead_score', 'discovered_at']
    list_filter     = ['status', 'niche']
    search_fields   = ['name', 'domain', 'email', 'url']
    list_editable   = ['status', 'niche']
    readonly_fields = ['discovered_at', 'updated_at']
    ordering        = ['-discovered_at']

    fieldsets = (
        ('Identificazione', {
            'fields': ('url', 'myshopify_url', 'name', 'domain')
        }),
        ('Classificazione', {
            'fields': ('niche', 'status', 'tags')
        }),
        ('Contatti', {
            'fields': ('email', 'phone', 'whatsapp_url', 'piva', 'address')
        }),
        ('Social', {
            'fields': ('instagram', 'facebook', 'tiktok', 'linkedin')
        }),
        ('Note', {
            'fields': ('notes',)
        }),
        ('Timestamp', {
            'fields': ('discovered_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(StoreAnalysis)
class StoreAnalysisAdmin(admin.ModelAdmin):
    list_display    = ['store', 'lead_score', 'lead_priority', 'product_count',
                       'img_quality_score', 'created_at']
    list_filter     = ['lead_priority']
    search_fields   = ['store__name', 'store__domain']
    readonly_fields = ['created_at']
    ordering        = ['-created_at']


@admin.register(ContactLog)
class ContactLogAdmin(admin.ModelAdmin):
    list_display  = ['store', 'contact_type', 'outcome', 'subject', 'sent_at']
    list_filter   = ['contact_type', 'outcome']
    search_fields = ['store__name', 'subject']
    ordering      = ['-sent_at']


# ✅ NUOVO
@admin.register(NicheQueryTemplate)
class NicheQueryTemplateAdmin(admin.ModelAdmin):
    list_display  = ['niche_display', 'active', 'query_count']
    list_filter   = ['active']
    list_editable = ['active']
    ordering      = ['niche']

    def niche_display(self, obj):
        return obj.get_niche_display()
    niche_display.short_description = 'Nicchia'

    def query_count(self, obj):
        return len(obj.queries_list())
    query_count.short_description = 'N. Query'