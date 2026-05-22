def apply_discount(price, pct):
    saved = price * pct / 100
    final = price - saved
    return final


def checkout(items, coupon):
    subtotal = 0
    for item in items:
        subtotal += item
    if coupon:
        total = apply_discount(subtotal, 10)
    else:
        total = subtotal
    return total


receipt = checkout([100, 50, 25], True)
