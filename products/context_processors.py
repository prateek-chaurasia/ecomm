from .models import GSTConfiguration


def gst_price_label(request):
    configuration = GSTConfiguration.get_solo()
    return {
        'gst_price_label': (
            'Including tax amount'
            if configuration.prices_include_tax
            else 'Excluding tax amount'
        ),
    }
