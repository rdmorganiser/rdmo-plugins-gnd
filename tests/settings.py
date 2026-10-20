from rdmo.core.settings import *  # noqa: F403

SECRET_KEY = 'gnd-tests-only'
DATABASES = {
    'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'},
    # Historical RDMO data migrations assume the default database. The second
    # alias only exercises cache routing, so create its current model tables.
    'cache': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:', 'TEST': {'MIGRATE': False}},
}
INSTALLED_APPS += ['rdmo_gnd']  # noqa: F405
ROOT_URLCONF = 'tests.urls'
STATIC_ROOT = '/tmp/rdmo-gnd-tests-static'
USE_TZ = True
FORMS_URLFIELD_ASSUME_HTTPS = True

# Cache tests opt in explicitly; production defaults are checked separately.
GND_CACHE_CONFIG = {'alias': 'default'}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'gnd-tests',
    },
    'gnd': {
        'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
        'LOCATION': 'test_gnd_cache',
        'KEY_PREFIX': 'gnd-tests',
    },
}
