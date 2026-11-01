from datetime import timedelta
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from home.middleware import VisitorLoggingMiddleware, get_client_ip, lookup_location
from home.models import PageVisit


class VisitorLoggingMiddlewareTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(
        self,
        path='/product/example/',
        *,
        method='get',
        status=200,
        content_type='text/html',
        **headers,
    ):
        request = self.factory.generic(
            method,
            path,
            REMOTE_ADDR='203.0.113.10',
            **headers,
        )
        middleware = VisitorLoggingMiddleware(
            lambda request: HttpResponse(status=status, content_type=content_type)
        )
        return middleware(request)

    @override_settings(
        VISITOR_LOGGING_ENABLED=True,
        VISITOR_TRUSTED_PROXY_COUNT=0,
        VISITOR_GEOIP_CITY_DATABASE='',
    )
    def test_records_successful_public_html_get_without_query_string(self):
        self._request('/product/example/?token=must-not-be-saved')

        visit = PageVisit.objects.get()
        self.assertEqual(visit.ip_address, '203.0.113.10')
        self.assertEqual(visit.path, '/product/example/')
        self.assertEqual(visit.country, '')

    @override_settings(VISITOR_LOGGING_ENABLED=False)
    def test_disabled_logging_does_not_record_visit(self):
        self._request()

        self.assertEqual(PageVisit.objects.count(), 0)

    @override_settings(
        VISITOR_LOGGING_ENABLED=True,
        VISITOR_TRUSTED_PROXY_COUNT=0,
    )
    def test_ignores_non_get_non_html_non_success_and_private_pages(self):
        self._request(method='post')
        self._request(content_type='image/jpeg')
        self._request(status=302)
        self._request('/accounts/login/')
        self._request('/admin/')
        self._request('/product/wishlist/')

        self.assertEqual(PageVisit.objects.count(), 0)

    @override_settings(VISITOR_TRUSTED_PROXY_COUNT=0)
    def test_untrusted_forwarded_header_is_not_used(self):
        request = self.factory.get(
            '/',
            REMOTE_ADDR='203.0.113.20',
            HTTP_X_FORWARDED_FOR='198.51.100.99',
        )

        self.assertEqual(get_client_ip(request), '203.0.113.20')

    @override_settings(VISITOR_TRUSTED_PROXY_COUNT=1)
    def test_uses_forwarded_address_only_for_configured_proxy_hop(self):
        request = self.factory.get(
            '/',
            REMOTE_ADDR='10.0.0.1',
            HTTP_X_FORWARDED_FOR='198.51.100.99',
        )

        self.assertEqual(get_client_ip(request), '198.51.100.99')

    @override_settings(VISITOR_GEOIP_CITY_DATABASE='')
    def test_missing_local_geoip_database_keeps_visit_location_blank(self):
        self.assertEqual(lookup_location('203.0.113.10'), {})

    def test_local_geoip_database_resolves_city_without_remote_lookup(self):
        location = SimpleNamespace(
            country=SimpleNamespace(name='India'),
            subdivisions=SimpleNamespace(
                most_specific=SimpleNamespace(name='Uttarakhand')
            ),
            city=SimpleNamespace(name='Dehradun'),
        )
        with TemporaryDirectory() as directory:
            database_path = Path(directory) / 'GeoLite2-City.mmdb'
            database_path.touch()
            with override_settings(
                VISITOR_GEOIP_CITY_DATABASE=str(database_path)
            ):
                with patch('home.middleware.Reader') as reader:
                    reader.return_value.city.return_value = location

                    resolved = lookup_location('203.0.113.10')

        self.assertEqual(
            resolved,
            {
                'country': 'India',
                'region': 'Uttarakhand',
                'city': 'Dehradun',
            },
        )


class PageVisitAdminTests(TestCase):
    def test_staff_without_view_permission_cannot_access_report(self):
        staff_user = User.objects.create_user(
            username='staff-user',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse('admin:home_pagevisit_changelist'))

        self.assertNotEqual(response.status_code, 200)

    def test_staff_with_view_permission_can_read_report(self):
        staff_user = User.objects.create_user(
            username='report-viewer',
            password='test-password',
            is_staff=True,
        )
        permission = Permission.objects.get(
            content_type__app_label='home',
            codename='view_pagevisit',
        )
        staff_user.user_permissions.add(permission)
        PageVisit.objects.create(
            ip_address='203.0.113.10',
            path='/product/example/',
            country='India',
        )
        self.client.force_login(staff_user)

        response = self.client.get(reverse('admin:home_pagevisit_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '203.0.113.10')
        self.assertContains(response, '/product/example/')


class PurgePageVisitsCommandTests(TestCase):
    def test_purge_command_removes_records_older_than_90_days_only(self):
        expired = PageVisit.objects.create(
            ip_address='203.0.113.10',
            path='/old/',
        )
        PageVisit.objects.filter(pk=expired.pk).update(
            visited_at=timezone.now() - timedelta(days=91)
        )
        recent = PageVisit.objects.create(
            ip_address='203.0.113.11',
            path='/recent/',
        )

        call_command('purge_page_visits', stdout=StringIO())

        self.assertFalse(PageVisit.objects.filter(pk=expired.pk).exists())
        self.assertTrue(PageVisit.objects.filter(pk=recent.pk).exists())
