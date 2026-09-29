from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from home.delivery import (
    get_automatic_delivery_selection, get_delivery_fee, get_delivery_options,
    validate_delivery_selection,
)
from home.models import DeliveryGuideline, HomeBanner, ReturnRefundPolicy
from products.models import Category, Product


class ContactFormTests(TestCase):
    def test_contact_name_fields_load_blur_validation(self):
        response = self.client.get(reverse('contact'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-validate-on-blur')
        self.assertContains(response, '/static/js/form-validation.js')

    @override_settings(
        ADMIN_EMAIL='support@example.com,orders@example.com',
        DEFAULT_FROM_EMAIL='noreply@example.com',
    )
    def test_valid_contact_form_emails_admins_and_sets_reply_to(self):
        response = self.client.post(reverse('contact'), {
            'first_name': 'Asha',
            'last_name': 'Sharma',
            'email': 'asha@example.com',
            'subject': 'order',
            'message': 'Please help with my order.',
            'newsletter': 'on',
        })

        self.assertRedirects(response, reverse('contact'))
        self.assertEqual(len(mail.outbox), 1)
        sent_email = mail.outbox[0]
        self.assertEqual(
            sent_email.to,
            ['support@example.com', 'orders@example.com'],
        )
        self.assertEqual(sent_email.reply_to, ['asha@example.com'])
        self.assertEqual(sent_email.from_email, 'noreply@example.com')
        self.assertIn('Order Support', sent_email.subject)
        self.assertIn('Please help with my order.', sent_email.body)
        self.assertIn('Newsletter updates requested: Yes', sent_email.body)

    @override_settings(ADMIN_EMAIL='support@example.com')
    def test_invalid_contact_form_does_not_send_email(self):
        response = self.client.post(reverse('contact'), {
            'first_name': 'Asha',
            'email': 'not-an-email',
            'subject': 'unknown',
            'message': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'Enter a valid email address.')
        self.assertContains(response, 'This field is required.')

    @override_settings(ADMIN_EMAIL='support@example.com')
    def test_contact_form_displays_name_validation_error(self):
        for field_name in ('first_name', 'last_name'):
            response = self.client.post(reverse('contact'), {
                'first_name': 'Asha2' if field_name == 'first_name' else 'Asha',
                'last_name': 'Sharm2' if field_name == 'last_name' else 'Sharma',
                'email': 'asha@example.com',
                'subject': 'general',
                'message': 'A question',
            })

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Numbers are not allowed in names.')
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(ADMIN_EMAIL='')
    def test_contact_form_reports_missing_admin_recipient(self):
        response = self.client.post(reverse('contact'), {
            'first_name': 'Asha',
            'email': 'asha@example.com',
            'subject': 'general',
            'message': 'A question',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, 'Contact email is temporarily unavailable.')


class DeliveryGuidelineTests(TestCase):
    def test_delivery_options_check_city_radius_accuracy_and_order_threshold(self):
        guideline = DeliveryGuideline(
            operational_office_city='Dehradun',
            operational_office_latitude=Decimal('30.316500'),
            operational_office_longitude=Decimal('78.032200'),
            free_delivery_radius_km=Decimal('5.00'),
            delivery_charge_threshold=Decimal('500.00'),
        )

        nearby = get_delivery_options(
            guideline,
            ' dehradun ',
            30.32,
            78.03,
            200,
            Decimal('400'),
        )
        self.assertTrue(nearby['below_delivery_charge_threshold'])
        self.assertFalse(nearby['free_delivery_available'])
        self.assertEqual(
            [option['value'] for option in nearby['options']],
            ['next_day'],
        )

        at_threshold = get_delivery_options(
            guideline,
            'Dehradun',
            30.32,
            78.03,
            200,
            Decimal('500'),
        )
        self.assertTrue(at_threshold['free_delivery_available'])
        self.assertFalse(at_threshold['below_delivery_charge_threshold'])

        above_threshold = get_delivery_options(
            guideline,
            'Dehradun',
            30.32,
            78.03,
            200,
            Decimal('500.01'),
        )
        self.assertTrue(above_threshold['free_delivery_available'])
        self.assertEqual(
            [option['value'] for option in above_threshold['options']],
            ['local_free', 'express', 'next_day'],
        )

        outside_radius = get_delivery_options(
            guideline, 'Dehradun', 30.5, 78.03, 0, Decimal('600'))
        other_city = get_delivery_options(
            guideline, 'New Delhi', 30.32, 78.03, 0, Decimal('600'))
        self.assertFalse(outside_radius['free_delivery_available'])
        self.assertFalse(other_city['free_delivery_available'])
        self.assertFalse(outside_radius['below_delivery_charge_threshold'])
        self.assertNotIn('express', [option['value'] for option in outside_radius['options']])

        automatic_express = get_automatic_delivery_selection(
            guideline, 'Dehradun', True, 30.32, 78.03, 200, Decimal('500.01'))
        automatic_next_day = get_automatic_delivery_selection(
            guideline, 'Dehradun', True, 30.5, 78.03, 0, Decimal('400'))
        outside_service_area = get_automatic_delivery_selection(
            guideline, 'Dehradun', False, 30.32, 78.03, 0, Decimal('400'))
        self.assertEqual(automatic_express[0], 'express')
        self.assertEqual(automatic_next_day, ('next_day', None))
        self.assertEqual(outside_service_area, ('', None))

    def test_delivery_fee_applies_below_threshold_and_is_free_at_threshold(self):
        guideline = DeliveryGuideline(
            delivery_charge_threshold=Decimal('500.00'),
            delivery_fee=Decimal('80.00'),
        )

        self.assertEqual(get_delivery_fee(guideline, Decimal('499.99')), Decimal('80.00'))
        self.assertEqual(get_delivery_fee(guideline, Decimal('500.00')), Decimal('0.00'))
        self.assertEqual(get_delivery_fee(guideline, Decimal('500.01')), Decimal('0.00'))

    def test_free_delivery_selection_is_rechecked_server_side(self):
        guideline = DeliveryGuideline(
            operational_office_city='Dehradun',
            operational_office_latitude=Decimal('30.316500'),
            operational_office_longitude=Decimal('78.032200'),
            free_delivery_radius_km=Decimal('5.00'),
        )

        with self.assertRaisesMessage(ValueError, 'available delivery option'):
            validate_delivery_selection(
                guideline, 'New Delhi', 'local_free', 30.32, 78.03, 0)

    def test_guideline_is_available_on_site_pages(self):
        guideline = DeliveryGuideline.get_solo()
        guideline.summary = 'Delivery details set by the store team.'
        guideline.save()

        response = self.client.get(reverse('about'))

        self.assertEqual(response.context['delivery_guideline'], guideline)
        self.assertContains(response, guideline.summary)

    def test_only_one_guideline_can_be_saved(self):
        DeliveryGuideline.objects.create(title='First version')
        DeliveryGuideline.objects.create(title='Updated version')

        self.assertEqual(DeliveryGuideline.objects.count(), 1)
        self.assertEqual(DeliveryGuideline.objects.get(pk=1).title, 'Updated version')


class ReturnRefundPolicyTests(TestCase):
    def test_policy_is_available_on_site_pages(self):
        policy = ReturnRefundPolicy.get_solo()
        policy.return_summary = 'Returns are reviewed under the current store policy.'
        policy.refund_summary = 'Refunds are handled by the support team.'
        policy.save()

        response = self.client.get(reverse('about'))

        self.assertEqual(response.context['return_refund_policy'], policy)
        self.assertContains(response, policy.return_summary)
        self.assertContains(response, policy.refund_summary)

    def test_terms_display_configured_cancellation_window(self):
        policy = ReturnRefundPolicy.get_solo()
        policy.cancellation_window_hours = 36
        policy.save()

        response = self.client.get(reverse('terms-and-conditions'))

        self.assertContains(response, 'within 36 hours of placement')

    def test_only_one_policy_can_be_saved(self):
        ReturnRefundPolicy.objects.create(return_title='First policy')
        ReturnRefundPolicy.objects.create(return_title='Updated policy')

        self.assertEqual(ReturnRefundPolicy.objects.count(), 1)
        self.assertEqual(
            ReturnRefundPolicy.objects.get(pk=1).return_title,
            'Updated policy',
        )


class HomeBannerTests(TestCase):
    def test_active_banners_are_shown_on_home_page(self):
        banner = HomeBanner.objects.create(
            title='Diwali Sale',
            subtitle='Flat 30% off on festive essentials',
            button_text='Shop Now',
            link_url='https://example.com/diwali',
            image=SimpleUploadedFile(
                'banner.png',
                b'fake-image-data',
                content_type='image/png',
            ),
            is_active=True,
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=7),
        )

        response = self.client.get(reverse('index'))

        self.assertEqual(list(response.context['banners']), [banner])
        self.assertContains(response, 'Diwali Sale')


class InfiniteProductLoadingTests(TestCase):
    def test_home_page_returns_next_product_batch_for_ajax_requests(self):
        category = Category.objects.create(category_name='Toys')
        for product_number in range(21):
            Product.objects.create(
                product_name=f'Toy {product_number:02d}',
                category=category,
                price=100,
                product_desription='A toy',
            )

        response = self.client.get(
            reverse('index') + '?page=2',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['html'])
        self.assertFalse(response.json()['has_next'])
        self.assertIn('Toy 20', response.json()['html'])
