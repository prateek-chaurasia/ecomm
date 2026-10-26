from django.conf import settings

from .models import GSTConfiguration


def gst_price_label(request):
    configuration = GSTConfiguration.get_solo()
    show_tax_information = settings.SHOW_TAX_INFORMATION
    return {
        'show_tax_information': show_tax_information,
        'gst_price_label': (
            'Including tax amount'
            if configuration.prices_include_tax
            else 'Excluding tax amount'
        ) if show_tax_information else '',
    }
