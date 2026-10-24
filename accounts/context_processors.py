from .models import CartItem


def cart_count(request):
    cart_items = CartItem.objects.filter(
        cart__is_paid=False,
        cart__is_buy_now=False,
    )
    if request.user.is_authenticated:
        cart_items = cart_items.filter(cart__user=request.user)
    elif request.session.session_key:
        cart_items = cart_items.filter(
            cart__session_key=request.session.session_key,
        )
    else:
        return {'cart_count': 0}

    return {'cart_count': cart_items.count()}
