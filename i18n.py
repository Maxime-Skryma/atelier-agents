"""Interface language. One process serves one user, so a module-level setting is enough."""

LANGUAGES = ('en', 'fr')
NAMES = {'en': 'English', 'fr': 'French'}
current = 'en'


def set_language(lang):
    global current
    current = lang if lang in LANGUAGES else 'en'


def L(fr, en, lang=None):
    """Pick the French or English text, for the given language or the interface language."""
    return fr if (lang or current) == 'fr' else en
