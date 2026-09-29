from decimal import Decimal, InvalidOperation
from math import asin, cos, radians, sin, sqrt


def _coordinate(value, minimum, maximum):
    if value in (None, ''):
        return None
    try:
        coordinate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Location coordinates are invalid.')
    if not coordinate.is_finite() or not minimum <= coordinate <= maximum:
        raise ValueError('Location coordinates are invalid.')
    return coordinate


def calculate_distance_km(latitude_a, longitude_a, latitude_b, longitude_b):
    latitude_a, longitude_a, latitude_b, longitude_b = map(
        radians,
        (float(latitude_a), float(longitude_a), float(latitude_b), float(longitude_b)),
    )
    latitude_delta = latitude_b - latitude_a
    longitude_delta = longitude_b - longitude_a
    haversine = (
        sin(latitude_delta / 2) ** 2
        + cos(latitude_a) * cos(latitude_b) * sin(longitude_delta / 2) ** 2
    )
    return 6371 * 2 * asin(sqrt(haversine))


def get_delivery_fee(guideline, order_total):
    threshold = guideline.delivery_charge_threshold
    if threshold is not None and Decimal(str(order_total)) < threshold:
        return guideline.delivery_fee
    return Decimal('0.00')


def get_delivery_options(
    guideline, city, latitude=None, longitude=None, accuracy_meters=None, order_total=0,
):
    latitude = _coordinate(latitude, Decimal('-90'), Decimal('90'))
    longitude = _coordinate(longitude, Decimal('-180'), Decimal('180'))
    accuracy = _coordinate(accuracy_meters, Decimal('0'), Decimal('21000000'))
    customer_city = (city or '').strip().casefold()
    office_city = (guideline.operational_office_city or '').strip().casefold()
    threshold = guideline.delivery_charge_threshold
    below_delivery_charge_threshold = (
        threshold is not None and Decimal(str(order_total)) < threshold
    )
    distance = None
    free_delivery_available = False

    if (
        customer_city
        and customer_city == office_city
        and guideline.operational_office_latitude is not None
        and guideline.operational_office_longitude is not None
        and latitude is not None
        and longitude is not None
    ):
        distance = calculate_distance_km(
            guideline.operational_office_latitude,
            guideline.operational_office_longitude,
            latitude,
            longitude,
        )
        uncertainty_km = float(accuracy or 0) / 1000
        free_delivery_available = not below_delivery_charge_threshold and (
            distance + uncertainty_km <= float(guideline.free_delivery_radius_km)
        )

    options = []
    if free_delivery_available:
        options.append({
            'value': 'local_free',
            'label': 'Free local delivery',
            'description': (
                f'Within {guideline.free_delivery_radius_km:g} km of '
                f'{guideline.operational_office_city}.'
            ),
        })
    if guideline.express_delivery_enabled and free_delivery_available:
        options.append({
            'value': 'express',
            'label': (
                f'Express delivery in {guideline.express_delivery_min_hours}-'
                f'{guideline.express_delivery_max_hours} hours'
            ),
            'description': 'Subject to weather conditions.',
        })
    if guideline.next_day_delivery_enabled:
        options.append({
            'value': 'next_day',
            'label': 'Next-day delivery',
            'description': 'Delivery on the next day.',
        })

    return {
        'options': options,
        'free_delivery_available': free_delivery_available,
        'distance_km': round(distance, 2) if distance is not None else None,
        'location_checked': latitude is not None and longitude is not None,
        'below_delivery_charge_threshold': below_delivery_charge_threshold,
        'delivery_charge_threshold': str(threshold) if threshold is not None else None,
    }


def validate_delivery_selection(
    guideline, city, option, latitude=None, longitude=None, accuracy_meters=None,
    order_total=0,
):
    available = get_delivery_options(
        guideline,
        city,
        latitude,
        longitude,
        accuracy_meters,
        order_total,
    )
    selected = next(
        (item for item in available['options'] if item['value'] == option),
        None,
    )
    if not selected:
        raise ValueError('Please choose an available delivery option.')
    if option == 'local_free':
        return Decimal(str(available['distance_km']))
    return None


def get_automatic_delivery_selection(
    guideline, city, is_serviceable, latitude=None, longitude=None,
    accuracy_meters=None, order_total=0,
):
    if not is_serviceable:
        return '', None

    try:
        available = get_delivery_options(
            guideline,
            city,
            latitude,
            longitude,
            accuracy_meters,
            order_total,
        )
    except ValueError:
        available = {'options': [], 'distance_km': None}
    if any(option['value'] == 'express' for option in available['options']):
        return 'express', Decimal(str(available['distance_km']))
    if guideline.next_day_delivery_enabled:
        return 'next_day', None
    return '', None