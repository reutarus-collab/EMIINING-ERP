"""Item grouping and ordering shared by every screen that lists items."""
from services.db import db
from services.models import FeedIngredient

# Display order of the groups in the Item Master and stock lists.
GROUPS = (('finished', 'Finished products'), ('raw', 'Raw materials'), ('mineral', 'Mineral salts'),
          ('additive', 'Additives'), ('packaging', 'Packaging'), ('other', 'Other'))
GROUP_LABEL = dict(GROUPS)
GROUP_RANK = {code: i for i, (code, _) in enumerate(GROUPS)}

# What the category dropdown offers. 'Raw - Mineral' is the old combined category: it stays under Mineral salts
# until someone moves an additive to 'Raw - Additive'.
CATEGORY_CHOICES = ('Finished Feed', 'Raw - Energy', 'Raw - Protein', 'Raw - Mineral', 'Raw - Additive', 'Packaging')


def group_for(category):
    c = (category or '').strip().lower()
    if 'additive' in c:
        return 'additive'
    if 'mineral' in c or 'salt' in c:
        return 'mineral'
    if 'finished' in c:
        return 'finished'
    if 'packag' in c:
        return 'packaging'
    if 'raw' in c:
        return 'raw'
    return 'other'


def name_order():
    """Case-insensitive A-Z. Plain ORDER BY name puts every capital letter before every lower-case one."""
    return db.func.lower(FeedIngredient.name)
