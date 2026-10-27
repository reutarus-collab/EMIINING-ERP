"""Purchase unit rules. One place decides how many kg a purchase unit is, so the PO screen, the PO
service and goods receiving cannot disagree.

  KG   -> always 1 kg per unit
  TON  -> always 1000 kg per unit (metric)
  BAG  -> FIXED: the item's kg-per-bag (must be above 1), or VARIABLE: weighed on delivery
"""
import math

KG_PER_UOM = {'KG': 1.0, 'TON': 1000.0}
VALID_UOMS = ('BAG', 'KG', 'TON')
PRICE_BAND = 3.0          # a price per kg more than 3x above or below the current cost needs confirming


class PriceCheckError(ValueError):
    """The price per kg implied by a PO or receipt is far from the item's current cost."""


def normalise_rule(uom, ctype, factor, item_name='this item'):
    uom = str(uom or 'KG').strip().upper()
    if uom not in VALID_UOMS:
        raise ValueError(f'{item_name} has an unknown purchase unit "{uom}". Use BAG, KG or TON.')
    if uom in KG_PER_UOM:
        return uom, 'FIXED', KG_PER_UOM[uom]
    ctype = str(ctype or 'FIXED').strip().upper()
    if ctype == 'VARIABLE':
        return uom, 'VARIABLE', None
    if ctype != 'FIXED':
        raise ValueError(f'{item_name} has an unknown conversion type "{ctype}".')
    try:
        factor = float(factor)
    except (TypeError, ValueError):
        factor = 0.0
    if not math.isfinite(factor) or factor <= 1.0:
        raise ValueError(f'{item_name} is bought by the BAG but its kg per bag is not set (it is {factor:g}). '
                         f'Open the Item Master, press "Unit" on this item and enter the kg in one bag.')
    return uom, 'FIXED', factor


def rule_for_item(ing):
    return normalise_rule(ing.purchase_uom, ing.conversion_type, ing.conversion_factor, ing.name)


def rule_for_line(line, ing):
    """The rule written on the PO line wins. Lines from before the snapshot fall back to the item."""
    if line.purchase_uom:
        return normalise_rule(line.purchase_uom, line.conversion_type, line.conversion_factor,
                              ing.name if ing else 'this item')
    return rule_for_item(ing)


def expected_kg(factor, qty):
    return None if factor is None else qty * factor


def price_per_kg(factor, unit_cost):
    return None if not factor else unit_cost / factor


def check_price(item_name, per_kg, known, uom, factor):
    """Raise PriceCheckError when per_kg is outside 1/3x to 3x of the known cost per kg."""
    if per_kg is None or not known or known <= 0:
        return
    if not (known / PRICE_BAND <= per_kg <= known * PRICE_BAND):
        raise PriceCheckError(
            f'{item_name}: KSh {per_kg:.2f} per kg is far from its current cost of KSh {known:.2f} per kg. '
            f'The price must be per {uom}' + (f' ({factor:g} kg)' if factor and uom != 'KG' else '') +
            ', and the quantity a count of ' + uom + 's. Check both before continuing.')
