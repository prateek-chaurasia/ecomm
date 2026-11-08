from datetime import datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image
from axes.utils import reset as reset_axes_attempts
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core import mail
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from home.admin import HomeBannerAdminForm
from home.delivery import (
    get_automatic_delivery_selection, get_delivery_fee, get_delivery_options,
    is_before_local_delivery_cutoff, validate_delivery_selection,
)
from home.models import (
    City, DeliveryGuideline, HomeBanner, MarqueeAnnouncement,
    NewsletterSubscriber, ReplacePolicy, ReturnRefundPolicy, SiteTheme, State,
)
from products.models import Category, Coupon, Product, ProductReview


class HeadingLevelParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.levels = []

    def handle_starttag(self, tag, attrs):
        if len(tag) == 2 and tag[0] == 'h' and tag[1] in '123456':
            self.levels.append(int(tag[1]))


class ContactFormTests(TestCase):
    def test_contact_name_fields_load_blur_validation(self):
        response = self.client.get(reverse('contact'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-validate-on-blur')
        self.assertContains(response, '/static/js/form-validation.js')
        self.assertContains(response, '/static/css/contact.min.css')
        self.assertNotContains(response, '/static/css/register.min.css')
        self.assertContains(response, 'Just Started')
        self.assertContains(response, 'Growing with You')
        self.assertNotContains(response, '50K+')
        self.assertNotContains(response, 'Happy Customers')
        self.assertNotContains(response, 'Live Chat')
        self.assertContains(
            response,
            'rel="icon" type="image/svg+xml" href="/static/images/bundleofgifts-icon.svg"',
        )
        self.assertContains(
            response,
            'rel="icon" type="image/png" href="/static/images/bundleofgifts-icon.png"',
        )
        self.assertContains(
            response,
            'class="brand-icon"',
        )

    @override_settings(
        ADMIN_EMAIL='support@example.com,orders@example.com',
        DEFAULT_FROM_EMAIL='noreply@example.com',
    )
    def test_valid_contact_form_emails_admins_and_acknowledges_sender(self):
        response = self.client.post(reverse('contact'), {
            'first_name': 'Asha',
            'last_name': 'Sharma',
            'email': 'asha@example.com',
            'subject': 'order',
            'message': 'Please help with my order.',
            'newsletter': 'on',
        })

        self.assertRedirects(response, reverse('contact'))
        self.assertEqual(len(mail.outbox), 3)
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

        acknowledgement = mail.outbox[1]
        self.assertEqual(
            acknowledgement.subject,
            'Acknowledgement to your query',
        )
        self.assertEqual(acknowledgement.to, ['asha@example.com'])
        self.assertEqual(acknowledgement.from_email, 'noreply@example.com')
        self.assertIn(
            'Thank you for reaching out to us. Your query has been received '
            'and is currently being reviewed by our team.',
            acknowledgement.body,
        )
        self.assertIn('Best Regards,\nTeam BundleofGifts', acknowledgement.body)
        self.assertEqual(len(acknowledgement.alternatives), 1)
        acknowledgement_html, mimetype = acknowledgement.alternatives[0]
        self.assertEqual(mimetype, 'text/html')
        self.assertIn('Acknowledgement to your query', acknowledgement_html)
        self.assertIn('src="cid:bundleofgifts-brand-icon"', acknowledgement_html)
        self.assertIn('Best Regards,<br><strong>Team BundleofGifts</strong>', acknowledgement_html)

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
    def test_standard_delivery_option_uses_approved_copy(self):
        guideline = DeliveryGuideline()

        option = next(
            option for option in get_delivery_options(guideline, 'Dehradun')['options']
            if option['value'] == 'next_day'
        )

        self.assertEqual(option['label'], 'Standard delivery')
        self.assertEqual(
            option['description'],
            'Most orders will arrive in 2–3 business days, subject to location '
            'and service availability.',
        )
        self.assertNotIn('next day', option['label'].casefold())
        self.assertNotIn('next day', option['description'].casefold())

    def test_delivery_estimates_follow_local_daily_cutoff(self):
        guideline = DeliveryGuideline(
            operational_office_city='Dehradun',
            operational_office_latitude=Decimal('30.316500'),
            operational_office_longitude=Decimal('78.032200'),
            free_delivery_radius_km=Decimal('5.00'),
        )
        local_timezone = ZoneInfo('Asia/Kolkata')
        before_cutoff = timezone.make_aware(datetime(2025, 1, 1, 11, 59), local_timezone)
        after_cutoff = timezone.make_aware(datetime(2025, 1, 1, 12, 0), local_timezone)

        with patch('home.delivery.timezone.localtime', return_value=before_cutoff):
            before_options = get_delivery_options(
                guideline, 'Dehradun', 30.32, 78.03, 0, Decimal('600'))
        with patch('home.delivery.timezone.localtime', return_value=after_cutoff):
            after_options = get_delivery_options(
                guideline, 'Dehradun', 30.32, 78.03, 0, Decimal('600'))

        local_before = next(
            option for option in before_options['options']
            if option['value'] == 'local_free'
        )
        local_after = next(
            option for option in after_options['options']
            if option['value'] == 'local_free'
        )
        self.assertIn('within 4–5 hours', local_before['description'])
        self.assertNotIn('within 4–5 hours', local_after['description'])
        self.assertTrue(before_options['local_delivery_promise_available'])
        self.assertFalse(after_options['local_delivery_promise_available'])
        self.assertTrue(is_before_local_delivery_cutoff(
            guideline, now=before_cutoff,
        ))
        self.assertFalse(is_before_local_delivery_cutoff(
            guideline, now=after_cutoff,
        ))

    def test_guideline_defaults_use_approved_delivery_copy(self):
        approved_copy = (
            'We strive to deliver your order at the earliest possible opportunity. '
            'Most orders are delivered within 2-3 business days, subject to location '
            'and service availability.'
        )
        guideline = DeliveryGuideline()

        self.assertEqual(guideline.summary, approved_copy)
        self.assertEqual(guideline.details, approved_copy)
        self.assertContains(self.client.get(reverse('about')), approved_copy)
        self.assertContains(
            self.client.get(reverse('terms-and-conditions')),
            approved_copy,
        )

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
        automatic_standard = get_automatic_delivery_selection(
            guideline, 'Dehradun', True, 30.5, 78.03, 0, Decimal('400'))
        outside_service_area = get_automatic_delivery_selection(
            guideline, 'Dehradun', False, 30.32, 78.03, 0, Decimal('400'))
        self.assertEqual(automatic_express[0], 'express')
        self.assertEqual(automatic_standard, ('next_day', None))
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


class CityDirectoryTests(TestCase):
    def test_city_normalization_collapses_case_spacing_and_diacritics(self):
        self.assertEqual(City.normalize_name('Dehra dūn'), 'dehradun')
        self.assertEqual(City.normalize_name('DEHRA-DUN'), 'dehradun')

    def test_import_cities_skips_normalized_duplicates(self):
        state = State.objects.get(country_id='IN', name='Uttarakhand')
        with TemporaryDirectory() as temp_directory:
            csv_path = Path(temp_directory) / 'cities.csv'
            csv_path.write_text(
                'state,city\nUttarakhand,Dehradun\nUttarakhand,Dehra dun\n',
                encoding='utf-8',
            )
            output = StringIO()
            repeated_output = StringIO()

            call_command('import_cities', file=csv_path, stdout=output)
            call_command('import_cities', file=csv_path, stdout=repeated_output)

        self.assertEqual(City.objects.filter(state=state).count(), 1)
        self.assertEqual(City.objects.get(state=state).name, 'Dehradun')
        self.assertIn('Imported 1 cities; skipped 1 normalized duplicates.', output.getvalue())
        self.assertIn('Imported 0 cities; skipped 2 normalized duplicates.', repeated_output.getvalue())


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

    def test_terms_display_cancellation_until_dispatch(self):
        response = self.client.get(reverse('terms-and-conditions'))

        self.assertContains(response, 'at any time before they are dispatched')
        self.assertContains(response, 'order details page')

    def test_only_one_policy_can_be_saved(self):
        ReturnRefundPolicy.objects.create(return_title='First policy')
        ReturnRefundPolicy.objects.create(return_title='Updated policy')

        self.assertEqual(ReturnRefundPolicy.objects.count(), 1)
        self.assertEqual(
            ReturnRefundPolicy.objects.get(pk=1).return_title,
            'Updated policy',
        )

    def test_replace_policy_is_available_on_site_pages(self):
        policy = ReplacePolicy.get_solo()
        policy.replace_summary = 'Replacement terms for eligible products.'
        policy.replace_details = 'Contact support to request a replacement.'
        policy.save()

        response = self.client.get(reverse('about'))

        self.assertEqual(response.context['replace_policy'], policy)
        self.assertEqual(ReplacePolicy.objects.count(), 1)

    def test_only_one_replace_policy_can_be_saved(self):
        ReplacePolicy.objects.create(replace_summary='First policy')
        ReplacePolicy.objects.create(replace_summary='Updated policy')

        self.assertEqual(ReplacePolicy.objects.count(), 1)
        self.assertEqual(
            ReplacePolicy.objects.get(pk=1).replace_summary,
            'Updated policy',
        )

    def test_replace_policy_is_managed_from_admin(self):
        admin_user = User.objects.create_superuser(
            username='policy-admin',
            email='policy-admin@example.com',
            password='secret123',
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse('admin:home_replacepolicy_add'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Replace summary')
        self.assertContains(response, 'Replace details')

    def test_customer_policy_page_renders_centralized_policy_html(self):
        return_policy = ReturnRefundPolicy.get_solo()
        return_policy.return_title = 'Our Returns'
        return_policy.return_summary = '<p><strong>Return summary from database.</strong></p>'
        return_policy.return_details = '<ul><li>Return details from database.</li></ul>'
        return_policy.refund_title = 'Our Refunds'
        return_policy.refund_summary = '<p>Refund summary from database.</p>'
        return_policy.refund_details = '<p>Refund details from database.</p>'
        return_policy.return_shipping_address = '<p>Return address from database.</p>'
        return_policy.save()
        replace_policy = ReplacePolicy.get_solo()
        replace_policy.replace_summary = '<p>Replace summary from database.</p>'
        replace_policy.replace_details = '<p>Replace details from database.</p>'
        replace_policy.save()

        response = self.client.get(reverse('return-refund-replace-policy'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['return_refund_policy'], return_policy)
        self.assertEqual(response.context['replace_policy'], replace_policy)
        for content in (
            'Our Returns',
            '<p><strong>Return summary from database.</strong></p>',
            '<ul><li>Return details from database.</li></ul>',
            'Our Refunds',
            '<p>Refund summary from database.</p>',
            '<p>Refund details from database.</p>',
            '<p>Return address from database.</p>',
            'Replace Policy',
            '<p>Replace summary from database.</p>',
            '<p>Replace details from database.</p>',
        ):
            with self.subTest(content=content):
                self.assertContains(response, content)

    def test_site_pages_render_saved_return_refund_and_replace_html(self):
        return_policy = ReturnRefundPolicy.get_solo()
        return_policy.return_summary = '<ul><li>Return summary HTML</li></ul>'
        return_policy.return_details = '<ul><li>Return details HTML</li></ul>'
        return_policy.refund_summary = '<ul><li>Refund summary HTML</li></ul>'
        return_policy.refund_details = '<ul><li>Refund details HTML</li></ul>'
        return_policy.save()

        replace_policy = ReplacePolicy.get_solo()
        replace_policy.replace_summary = '<ul><li>Replace summary HTML</li></ul>'
        replace_policy.replace_details = '<ul><li>Replace details HTML</li></ul>'
        replace_policy.save()

        for page_name in ('terms-and-conditions', 'about', 'contact'):
            response = self.client.get(reverse(page_name))
            self.assertEqual(response.status_code, 200)
            for policy_html in (
                '<ul><li>Return summary HTML</li></ul>',
                '<ul><li>Return details HTML</li></ul>',
                '<ul><li>Refund summary HTML</li></ul>',
                '<ul><li>Refund details HTML</li></ul>',
                '<ul><li>Replace summary HTML</li></ul>',
                '<ul><li>Replace details HTML</li></ul>',
            ):
                with self.subTest(page=page_name, content=policy_html):
                    self.assertContains(response, policy_html)
            self.assertNotContains(response, '&lt;ul&gt;')

    def test_policy_page_is_linked_from_footer(self):
        response = self.client.get(reverse('index'))

        self.assertContains(
            response,
            f'href="{reverse("return-refund-replace-policy")}"',
        )


class DehradunShoppingGuideTests(TestCase):
    def test_guide_shows_seo_content_and_links_all_database_categories(self):
        toys = Category.objects.create(category_name='Toys')
        hidden_category = Category.objects.create(category_name='Hidden products')
        empty_category = Category.objects.create(category_name='Empty category')
        Product.objects.create(
            product_name='Remote Control Car',
            category=toys,
            price=1500,
            product_desription='A remote control toy car.',
        )
        Product.objects.create(
            product_name='Unlisted item',
            category=hidden_category,
            price=500,
            product_desription='Not shown in the catalog.',
            show_in_catalog=False,
        )

        response = self.client.get(reverse('dehradun_shopping_guide'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<title>Online Shopping in Dehradun: Gifts, Toys & More | BundleofGifts.com</title>',
        )
        self.assertContains(response, 'Open-Box Inspection in the Local Service Range')
        self.assertContains(response, 'decline it immediately')
        self.assertContains(response, 'Cash on Delivery (COD)')
        self.assertContains(response, 'UPI at the time of delivery')
        self.assertContains(response, 'Toys')
        self.assertEqual(
            list(response.context['guide_categories']),
            [empty_category, hidden_category, toys],
        )
        self.assertContains(
            response,
            f'href="{reverse("category_products", kwargs={"slug": toys.slug})}"',
        )
        self.assertContains(
            response,
            f'href="{reverse("category_products", kwargs={"slug": empty_category.slug})}"',
        )

    def test_guide_is_linked_from_footer_and_included_in_sitemap(self):
        guide_url = reverse('dehradun_shopping_guide')

        page_response = self.client.get(guide_url)
        sitemap_response = self.client.get(reverse('sitemap'))

        self.assertContains(page_response, f'href="{guide_url}"')
        home_response = self.client.get(reverse('index'))
        self.assertContains(home_response, f'href="{guide_url}"')
        self.assertContains(
            sitemap_response,
            guide_url,
        )

    def test_guide_shows_approved_delivery_copy_from_guideline(self):
        response = self.client.get(reverse('dehradun_shopping_guide'))
        approved_copy = (
            'We strive to deliver your order at the earliest possible opportunity. '
            'Most orders are delivered within 2-3 business days, subject to location '
            'and service availability.'
        )

        self.assertContains(response, approved_copy)
        self.assertEqual(response.content.count(approved_copy.encode()), 1)


class HomeBannerTests(TestCase):
    def test_public_pages_do_not_skip_heading_levels(self):
        for route_name in (
            'index',
            'about',
            'dehradun_shopping_guide',
            'contact',
            'privacy-policy',
            'terms-and-conditions',
            'return-refund-replace-policy',
            'categories',
            'product_search',
        ):
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))

                self.assertEqual(response.status_code, 200)
                parser = HeadingLevelParser()
                parser.feed(response.content.decode())
                for previous, current in zip(parser.levels, parser.levels[1:]):
                    self.assertLessEqual(
                        current,
                        previous + 1,
                        f'{route_name} skips from h{previous} to h{current}',
                    )

    def test_admin_accepts_webp_and_avif_banner_uploads(self):
        for image_format, extension, content_type in (
            ('WEBP', 'webp', 'image/webp'),
            ('AVIF', 'avif', 'image/avif'),
        ):
            with self.subTest(image_format=image_format):
                image_data = BytesIO()
                Image.new('RGB', (2, 2), 'blue').save(
                    image_data,
                    format=image_format,
                )
                form = HomeBannerAdminForm(
                    data={
                        'title': f'{image_format} banner',
                        'button_text': 'Shop Now',
                        'starts_at': timezone.now(),
                    },
                    files={
                        'image': SimpleUploadedFile(
                            f'banner.{extension}',
                            image_data.getvalue(),
                            content_type=content_type,
                        ),
                    },
                )

                self.assertIn(
                    f'.{extension}',
                    form.fields['image'].widget.attrs['accept'],
                )
                self.assertTrue(form.is_valid(), form.errors)

    def test_fontawesome_and_footer_styles_load_with_initial_page_styles(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<button class="btn btn-primary" type="submit" aria-label="Search products">',
        )
        self.assertContains(
            response,
            '<i class="fa fa-search" aria-hidden="true"></i>',
        )
        self.assertContains(response, 'aria-label="Shopping cart"')
        self.assertContains(response, 'aria-label="Sign in to your account"')
        self.assertContains(
            response,
            'href="/static/fonts/fontawesome/css/all.min.css" type="text/css" rel="stylesheet"',
        )
        self.assertContains(
            response,
            'href="/static/css/footer.min.css" rel="stylesheet" type="text/css"',
        )
        self.assertContains(
            response,
            'href="/static/css/bootstrap.min.css" rel="stylesheet"',
        )
        self.assertContains(
            response,
            'href="/static/css/ui.min.css" rel="stylesheet"',
        )

    def test_home_page_includes_search_relevant_seo_metadata(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<meta name="keywords" content="toys, kitchenware and households, '
            'kitchenware, 2.4 ghz cars, climber cross country 2.4 ghz, '
            'online gifts and toys delivery dehradun">',
        )
        self.assertContains(
            response,
            '<meta name="description" content="Shop toys, kitchenware, household essentials and gifts online at BundleofGifts.com, with delivery options in Dehradun.">',
        )

    def test_authenticated_nav_profile_link_has_a_unique_accessible_name(self):
        user = User.objects.create_user(username='screen-reader-user')
        self.client.force_login(user)

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "aria-label=\"View screen-reader-user's profile\"",
        )

    def test_home_page_shop_by_category_button_opens_categories(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Shop by Category')
        self.assertContains(
            response,
            f'href="{reverse("categories")}"',
        )
        self.assertNotContains(response, 'Explore gifts')

    def test_active_banners_are_shown_on_home_page(self):
        banner = HomeBanner.objects.create(
            title='Diwali Sale',
            subtitle='Flat 30% off on festive essentials',
            button_text='Shop Now',
            link_url='https://bundleofgifts.com/diwali',
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

    def test_homepage_prioritizes_only_the_first_banner_image(self):
        first_banner = HomeBanner.objects.create(
            title='Current banner',
            image='banners/current.jpg',
            starts_at=timezone.now(),
            ends_at=timezone.now() + timedelta(days=1),
        )
        later_banner = HomeBanner.objects.create(
            title='Next banner',
            image='banners/next.jpg',
            starts_at=first_banner.starts_at - timedelta(minutes=1),
            ends_at=timezone.now() + timedelta(days=1),
        )

        response = self.client.get(reverse('index'))

        response_html = ' '.join(response.content.decode().split())
        self.assertContains(response, 'data-bs-ride="carousel"')
        self.assertIn(
            'src="/media/banners/current.jpg" loading="eager" class="d-block w-100 home-banner-image" '
            'alt="Current banner" fetchpriority="high" decoding="async"',
            response_html,
        )
        self.assertIn(
            'data-src="/media/banners/next.jpg" class="d-block w-100 home-banner-image" '
            'alt="Next banner" decoding="async"',
            response_html,
        )

    def test_active_marquee_announcements_render_in_order(self):
        MarqueeAnnouncement.objects.all().update(is_active=False)
        second = MarqueeAnnouncement.objects.create(
            text='Second announcement', display_order=2,
        )
        first = MarqueeAnnouncement.objects.create(
            text='First announcement', display_order=1,
        )
        MarqueeAnnouncement.objects.create(
            text='Inactive announcement', display_order=0, is_active=False,
        )

        response = self.client.get(reverse('index'))

        self.assertEqual(
            list(response.context['marquee_announcements']), [first, second],
        )
        self.assertContains(response, 'First announcement')
        self.assertContains(response, 'Second announcement')
        self.assertNotContains(response, 'Inactive announcement')


class SitemapTests(TestCase):
    @override_settings(PUBLIC_BASE_URL='https://bundleofgifts.com/')
    def test_robots_txt_allows_public_pages_and_uses_configured_sitemap_url(self):
        response = self.client.get(
            reverse('robots_txt'),
            HTTP_HOST='testserver',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertContains(response, 'User-agent: *')
        self.assertContains(response, 'Allow: /')
        self.assertContains(response, 'Disallow: /admin/')
        self.assertContains(response, 'Disallow: /accounts/')
        self.assertContains(response, 'Allow: /accounts/login/')
        self.assertContains(response, 'Disallow: /product/wishlist/')
        self.assertContains(response, 'Disallow: /product/add-to-cart/')
        self.assertContains(
            response,
            'Sitemap: https://bundleofgifts.com/sitemap.xml',
        )
        self.assertNotContains(response, 'testserver')

    @override_settings(PUBLIC_BASE_URL='https://bundleofgifts.com')
    def test_sitemap_uses_public_domain_instead_of_sites_framework_domain(self):
        response = self.client.get(reverse('sitemap'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<loc>https://bundleofgifts.com/product/bundles/</loc>',
        )
        self.assertNotContains(response, 'https://example.com/')


class CanonicalHostMiddlewareTests(TestCase):
    @override_settings(
        IS_PRODUCTION=True,
        PUBLIC_BASE_URL='https://bundleofgifts.com',
        ALLOWED_HOSTS=['bundleofgifts.com', 'www.bundleofgifts.com'],
    )
    def test_www_host_redirects_to_apex_and_preserves_path_and_query(self):
        response = self.client.get(
            '/terms-and-conditions/?sort=lowest',
            HTTP_HOST='www.bundleofgifts.com',
            secure=True,
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            'https://bundleofgifts.com/terms-and-conditions/?sort=lowest',
        )

    @override_settings(
        IS_PRODUCTION=True,
        PUBLIC_BASE_URL='https://bundleofgifts.com',
        ALLOWED_HOSTS=['bundleofgifts.com', 'www.bundleofgifts.com'],
    )
    def test_apex_host_is_not_redirected(self):
        response = self.client.get(
            reverse('robots_txt'),
            HTTP_HOST='bundleofgifts.com',
            secure=True,
        )

        self.assertEqual(response.status_code, 200)


class RenderBlockingResourceTests(TestCase):
    def test_base_template_defers_scripts_and_noncritical_stylesheets(self):
        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'code.jquery.com/jquery-3.5.1.min.js')
        self.assertNotContains(response, 'select2@4.0.13/dist/js/select2.min.js')
        self.assertNotContains(response, 'stackpath.bootstrapcdn.com/bootstrap/4.5.2')
        self.assertNotContains(response, '@popperjs/core')
        self.assertContains(
            response,
            '/static/js/vendor/bootstrap-5.3.0-alpha1.bundle.min.js',
        )
        self.assertContains(
            response,
            '/static/fonts/local-fonts.min.css',
        )
        self.assertContains(response, '/static/css/vendor/bootstrap-5.3.0-alpha1.min.css')
        self.assertNotContains(
            response,
            '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/',
            html=False,
        )

    def test_about_and_contact_animation_assets_are_local(self):
        for url_name in ('about', 'contact'):
            with self.subTest(page=url_name):
                response = self.client.get(reverse(url_name))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, '/static/css/vendor/aos-2.3.1.css')
                self.assertContains(response, '/static/js/vendor/aos-2.3.1.js')
                self.assertNotContains(response, 'unpkg.com/aos')

    def test_error_templates_use_local_assets(self):
        not_found_page = render_to_string('404.html')
        server_error_page = render_to_string('500.html')

        self.assertIn('/static/fonts/local-fonts.min.css', not_found_page)
        self.assertNotIn('cdnjs.cloudflare.com', not_found_page)
        self.assertIn('/static/js/vendor/TweenLite-2.1.3.min.js', server_error_page)
        self.assertIn('/static/js/vendor/CSSPlugin-2.1.3.min.js', server_error_page)
        self.assertNotIn('cdnjs.cloudflare.com', server_error_page)
        self.assertNotIn('fonts.googleapis.com', server_error_page)


class InfiniteProductLoadingTests(TestCase):
    def test_home_product_cards_show_average_review_rating_before_price(self):
        category = Category.objects.create(category_name='Toys')
        product = Product.objects.create(
            product_name='Rated toy',
            category=category,
            price=900,
            product_desription='Popular toy',
            stock_quantity=2,
        )
        first_reviewer = User.objects.create_user(username='rating-reviewer-one')
        second_reviewer = User.objects.create_user(username='rating-reviewer-two')
        ProductReview.objects.create(
            product=product, user=first_reviewer, stars=5,
        )
        ProductReview.objects.create(
            product=product, user=second_reviewer, stars=4,
        )

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'aria-label="Rated 4.5 out of 5 from 2 reviews"',
        )
        self.assertContains(response, '<span>4.5</span>', html=False)
        page_html = response.content.decode()
        self.assertLess(
            page_html.index('aria-label="Rated 4.5 out of 5 from 2 reviews"'),
            page_html.index('₹900.00'),
        )

    def test_home_page_places_out_of_stock_products_after_in_stock_products(self):
        category = Category.objects.create(category_name='Toys')
        in_stock = Product.objects.create(
            product_name='Zebra in stock',
            category=category,
            price=900,
            product_desription='Available toy',
            stock_quantity=2,
        )
        out_of_stock = Product.objects.create(
            product_name='Apple out of stock',
            category=category,
            price=10,
            product_desription='Unavailable toy',
            stock_quantity=0,
        )

        default_response = self.client.get(reverse('index'))
        self.assertEqual(
            list(default_response.context['products'].object_list),
            [in_stock, out_of_stock],
        )

        price_response = self.client.get(reverse('index'), {'sort': 'priceAsc'})
        self.assertEqual(
            list(price_response.context['products'].object_list),
            [in_stock, out_of_stock],
        )

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
        self.assertTrue(response.json()['has_next'])
        self.assertIn('Toy 08', response.json()['html'])

        response = self.client.get(
            reverse('index') + '?page=3',
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['has_next'])
        self.assertIn('Toy 20', response.json()['html'])


class SiteThemeTests(TestCase):
    def test_active_theme_is_selected_only_inside_its_schedule(self):
        now = timezone.now()
        theme = SiteTheme.objects.create(
            name='Summer',
            is_active=True,
            starts_at=now - timedelta(hours=1),
            ends_at=now + timedelta(hours=1),
        )

        with patch('home.models.timezone.now', return_value=now):
            self.assertEqual(SiteTheme.get_active(), theme)

        with patch('home.models.timezone.now', return_value=now + timedelta(hours=1)):
            self.assertIsNone(SiteTheme.get_active())

    def test_disabled_and_future_themes_are_not_active(self):
        now = timezone.now()
        SiteTheme.objects.create(
            name='Disabled',
            is_active=False,
            starts_at=now - timedelta(days=1),
        )
        SiteTheme.objects.create(
            name='Future',
            is_active=True,
            starts_at=now + timedelta(days=1),
        )

        with patch('home.models.timezone.now', return_value=now):
            self.assertIsNone(SiteTheme.get_active())

    def test_theme_without_dates_is_active_indefinitely(self):
        theme = SiteTheme.objects.create(name='Always on', is_active=True)

        with patch('home.models.timezone.now', return_value=timezone.now()):
            self.assertEqual(SiteTheme.get_active(), theme)

    def test_active_schedules_cannot_overlap(self):
        now = timezone.now()
        SiteTheme.objects.create(
            name='First',
            is_active=True,
            starts_at=now,
            ends_at=now + timedelta(days=2),
        )
        overlapping_theme = SiteTheme(
            name='Overlapping',
            is_active=True,
            starts_at=now + timedelta(days=1),
            ends_at=now + timedelta(days=3),
        )

        with self.assertRaises(ValidationError):
            overlapping_theme.full_clean()

    def test_adjacent_schedules_are_valid(self):
        transition = timezone.now() + timedelta(days=1)
        SiteTheme.objects.create(
            name='First',
            is_active=True,
            starts_at=None,
            ends_at=transition,
        )
        next_theme = SiteTheme(
            name='Next',
            is_active=True,
            starts_at=transition,
        )

        next_theme.full_clean()

    def test_invalid_schedule_and_color_values_are_rejected(self):
        now = timezone.now()
        theme = SiteTheme(
            name='Invalid',
            starts_at=now + timedelta(days=1),
            ends_at=now,
            primary_color='red',
        )

        with self.assertRaises(ValidationError) as error:
            theme.full_clean()

        self.assertIn('ends_at', error.exception.message_dict)
        self.assertIn('primary_color', error.exception.message_dict)

    def test_active_theme_is_applied_without_changing_seo_content(self):
        theme = SiteTheme.objects.create(
            name='Autumn',
            is_active=True,
            primary_color='#123456',
            primary_text_color='#EEEEEE',
            starts_at=timezone.now() - timedelta(hours=1),
            ends_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.get(reverse('index'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_site_theme'], theme)
        self.assertContains(response, '/static/css/theme.css')
        self.assertContains(response, '--site-primary: #123456;')
        self.assertContains(response, '<title>', html=False)
        self.assertContains(response, 'rel="canonical"', html=False)

    def test_admin_theme_form_uses_color_pickers(self):
        admin_user = User.objects.create_superuser(
            username='theme-admin',
            email='theme-admin@example.com',
            password='test-password',
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse('admin:home_sitetheme_add'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="color"')
        self.assertContains(response, 'name="starts_at_0"')
        self.assertContains(response, 'name="ends_at_0"')


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='newsletter@example.com',
)
class NewsletterSubscriptionTests(TestCase):
    def subscribe(self, email='shopper@example.com'):
        return self.client.post(reverse('newsletter_subscribe'), {
            'email': email,
            'consent': 'on',
            'website': '',
        })

    @override_settings(NEWSLETTER_ENABLED=False)
    def test_newsletter_can_be_disabled_without_blocking_unsubscribe(self):
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="newsletter-popup"')
        self.assertNotContains(response, '/static/js/newsletter-popup.js')
        self.assertNotContains(response, '/static/css/newsletter-popup.css')

        response = self.subscribe()
        self.assertEqual(response.status_code, 404)
        self.assertFalse(NewsletterSubscriber.objects.exists())

        subscriber = NewsletterSubscriber.objects.create(
            email='shopper@example.com',
            consented_at=timezone.now(),
            is_subscribed=True,
            confirmed_at=timezone.now(),
        )
        response = self.client.get(reverse(
            'newsletter_confirm',
            args=[subscriber.confirmation_token],
        ))
        self.assertEqual(response.status_code, 404)
        response = self.client.post(reverse(
            'newsletter_confirm',
            args=[subscriber.confirmation_token],
        ))
        self.assertEqual(response.status_code, 404)
        subscriber.refresh_from_db()
        self.assertTrue(subscriber.is_subscribed)
        self.assertIsNone(subscriber.coupon)

        response = self.client.post(reverse(
            'newsletter_unsubscribe',
            args=[subscriber.unsubscribe_token],
        ))
        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertFalse(subscriber.is_subscribed)

    def test_popup_is_loaded_site_wide_and_confirmation_pages_hide_it(self):
        for page in (reverse('index'), reverse('contact')):
            with self.subTest(page=page):
                response = self.client.get(page)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'id="newsletter-popup"')
                self.assertContains(response, '/static/js/newsletter-popup.js')
                self.assertContains(response, '/static/css/newsletter-popup.css')

        self.subscribe()
        subscriber = NewsletterSubscriber.objects.get()
        response = self.client.get(reverse(
            'newsletter_confirm',
            args=[subscriber.confirmation_token],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="newsletter-popup"')

    def test_consent_is_required_and_honeypot_submissions_are_rejected(self):
        response = self.client.post(reverse('newsletter_subscribe'), {
            'email': 'shopper@example.com',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(NewsletterSubscriber.objects.exists())

        response = self.client.post(reverse('newsletter_subscribe'), {
            'email': 'shopper@example.com',
            'consent': 'on',
            'website': 'bot-filled-this',
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(NewsletterSubscriber.objects.exists())

    def test_signup_saves_pending_consent_and_sends_confirmation_without_coupon(self):
        response = self.subscribe(' Shopper@Example.com ')

        self.assertEqual(response.status_code, 200)
        self.assertIn('confirm your email', response.json()['message'])
        subscriber = NewsletterSubscriber.objects.get()
        self.assertEqual(subscriber.email, 'shopper@example.com')
        self.assertFalse(subscriber.is_subscribed)
        self.assertIsNotNone(subscriber.consented_at)
        self.assertEqual(mail.outbox[0].to, ['shopper@example.com'])
        self.assertIn(str(subscriber.confirmation_token), mail.outbox[0].body)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        confirmation_html, content_type = mail.outbox[0].alternatives[0]
        self.assertEqual(content_type, 'text/html')
        self.assertIn('cid:bundleofgifts-brand-icon', confirmation_html)
        self.assertIn('BundleofGifts', confirmation_html)
        self.assertFalse(Coupon.objects.exists())

    def test_confirmation_is_required_before_creating_and_revealing_discount(self):
        self.subscribe()
        subscriber = NewsletterSubscriber.objects.get()
        confirmation_url = reverse(
            'newsletter_confirm',
            args=[subscriber.confirmation_token],
        )

        response = self.client.get(confirmation_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(NewsletterSubscriber.objects.get().is_subscribed)
        self.assertFalse(Coupon.objects.exists())

        response = self.client.post(confirmation_url)

        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertTrue(subscriber.is_subscribed)
        self.assertIsNotNone(subscriber.confirmed_at)
        self.assertContains(response, subscriber.coupon.coupon_code)
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn(subscriber.coupon.coupon_code, mail.outbox[1].body)
        self.assertIn('newsletter/unsubscribe/', mail.outbox[1].body)
        self.assertEqual(len(mail.outbox[1].alternatives), 1)
        welcome_html, content_type = mail.outbox[1].alternatives[0]
        self.assertEqual(content_type, 'text/html')
        self.assertIn('cid:bundleofgifts-brand-icon', welcome_html)
        self.assertIn(subscriber.coupon.coupon_code, welcome_html)
        self.assertIn('BundleofGifts', welcome_html)
        self.assertEqual(subscriber.coupon.discount_percentage, 5)
        self.assertEqual(subscriber.coupon.minimum_amount, 0)
        self.assertIsNone(subscriber.coupon.expiry_date)
        self.assertTrue(subscriber.coupon.is_available)
        self.assertTrue(
            Coupon.objects.available().filter(pk=subscriber.coupon.pk).exists(),
        )
        self.assertFalse(
            Coupon.objects.available().filter(subscriber_email='').exists(),
        )

        self.client.post(confirmation_url)
        self.assertEqual(Coupon.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_pending_signup_confirmation_email_is_throttled(self):
        self.subscribe()
        response = self.subscribe('SHOPPER@example.com')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(NewsletterSubscriber.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_confirmed_subscriber_can_unsubscribe_with_a_post(self):
        self.subscribe()
        subscriber = NewsletterSubscriber.objects.get()
        self.client.post(reverse(
            'newsletter_confirm',
            args=[subscriber.confirmation_token],
        ))
        subscriber.refresh_from_db()

        unsubscribe_url = reverse(
            'newsletter_unsubscribe',
            args=[subscriber.unsubscribe_token],
        )
        response = self.client.get(unsubscribe_url)
        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertTrue(subscriber.is_subscribed)

        response = self.client.post(unsubscribe_url)

        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertFalse(subscriber.is_subscribed)
        self.assertIsNotNone(subscriber.unsubscribed_at)
        self.assertIsNotNone(subscriber.coupon)

    def test_unsubscribed_visitor_can_subscribe_again(self):
        self.subscribe()
        subscriber = NewsletterSubscriber.objects.get()
        old_confirmation_token = subscriber.confirmation_token
        old_unsubscribe_token = subscriber.unsubscribe_token
        self.client.post(reverse(
            'newsletter_confirm',
            args=[subscriber.confirmation_token],
        ))
        subscriber.refresh_from_db()
        self.client.post(reverse(
            'newsletter_unsubscribe',
            args=[subscriber.unsubscribe_token],
        ))

        response = self.subscribe()

        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertFalse(subscriber.is_subscribed)
        self.assertIsNone(subscriber.unsubscribed_at)
        self.assertIsNone(subscriber.confirmed_at)
        self.assertNotEqual(subscriber.confirmation_token, old_confirmation_token)
        self.assertNotEqual(subscriber.unsubscribe_token, old_unsubscribe_token)
        self.assertEqual(len(mail.outbox), 3)

        response = self.client.post(reverse(
            'newsletter_confirm',
            args=[subscriber.confirmation_token],
        ))

        self.assertEqual(response.status_code, 200)
        subscriber.refresh_from_db()
        self.assertTrue(subscriber.is_subscribed)
        self.assertIsNotNone(subscriber.coupon)
