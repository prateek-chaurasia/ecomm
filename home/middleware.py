import ipaddress
import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db import DatabaseError
from geoip2.database import Reader
from geoip2.errors import AddressNotFoundError, GeoIP2Error
from maxminddb.errors import InvalidDatabaseError

from home.models import PageVisit

logger = logging.getLogger(__name__)

EXCLUDED_PATH_PREFIXES = (
    '/accounts/',
    '/admin/',
    '/cart/',
    '/checkout/',
    '/orders/',
    '/payment/',
    '/webhook/',
    '/wishlist/',
    '/product/wishlist/',
    '/product/product-reviews/edit/',
)


def _validated_ip(value):
    try:
        return str(ipaddress.ip_address(value.strip()))
    except (AttributeError, ValueError):
        return None


def get_client_ip(request):
    remote_addr = _validated_ip(request.META.get('REMOTE_ADDR', ''))
    trusted_proxy_count = settings.VISITOR_TRUSTED_PROXY_COUNT
    if trusted_proxy_count < 0:
        raise ValueError('VISITOR_TRUSTED_PROXY_COUNT cannot be negative.')
    if trusted_proxy_count == 0:
        return remote_addr

    forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if not forwarded_for or not remote_addr:
        return remote_addr

    forwarded_addresses = [
        _validated_ip(address) for address in forwarded_for.split(',')
    ]
    if any(address is None for address in forwarded_addresses):
        logger.warning('Invalid address in X-Forwarded-For; using REMOTE_ADDR.')
        return remote_addr

    address_chain = forwarded_addresses + [remote_addr]
    client_index = len(address_chain) - trusted_proxy_count - 1
    if client_index < 0:
        logger.warning(
            'X-Forwarded-For has fewer addresses than VISITOR_TRUSTED_PROXY_COUNT; '
            'using REMOTE_ADDR.'
        )
        return remote_addr
    return address_chain[client_index]


@lru_cache(maxsize=4)
def _open_geoip_reader(database_path):
    if not database_path:
        return None

    if not Path(database_path).is_file():
        logger.error(
            'Visitor GeoIP database is not available at the configured path: %s',
            database_path,
        )
        return None

    try:
        return Reader(database_path)
    except (InvalidDatabaseError, OSError):
        logger.exception('Could not open the configured visitor GeoIP database.')
        return None


def lookup_location(ip_address):
    database_path = settings.VISITOR_GEOIP_CITY_DATABASE
    reader = _open_geoip_reader(database_path)
    if reader is None:
        return {}

    try:
        result = reader.city(ip_address)
    except AddressNotFoundError:
        return {}
    except (GeoIP2Error, InvalidDatabaseError, OSError):
        logger.exception('Visitor GeoIP lookup failed.')
        return {}

    return {
        'country': (result.country.name or '')[:100],
        'region': (result.subdivisions.most_specific.name or '')[:100],
        'city': (result.city.name or '')[:100],
    }


class VisitorLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not settings.VISITOR_LOGGING_ENABLED:
            return response
        self._record_page_visit(request, response)
        return response

    @staticmethod
    def _record_page_visit(request, response):
        path = request.path
        if (
            request.method != 'GET'
            or not 200 <= response.status_code < 300
            or response.headers.get('Content-Type', '').split(';', 1)[0].lower()
            != 'text/html'
            or any(path.startswith(prefix) for prefix in EXCLUDED_PATH_PREFIXES)
        ):
            return

        ip_address = get_client_ip(request)
        if not ip_address:
            logger.warning('Visitor page view was not recorded because its IP is invalid.')
            return

        location = lookup_location(ip_address)
        try:
            PageVisit.objects.create(
                ip_address=ip_address,
                path=path[:2048],
                **location,
            )
        except DatabaseError:
            logger.exception('Could not save visitor page view.')
