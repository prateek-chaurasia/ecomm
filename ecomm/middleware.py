from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponsePermanentRedirect


class CanonicalHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.IS_PRODUCTION:
            public_url = urlsplit(settings.PUBLIC_BASE_URL)
            if not public_url.scheme or not public_url.netloc:
                raise ImproperlyConfigured(
                    'PUBLIC_BASE_URL must include a scheme and hostname.'
                )

            if request.get_host().casefold() != public_url.netloc.casefold():
                canonical_url = f'{settings.PUBLIC_BASE_URL.rstrip("/")}{request.get_full_path()}'
                return HttpResponsePermanentRedirect(canonical_url)

        return self.get_response(request)
