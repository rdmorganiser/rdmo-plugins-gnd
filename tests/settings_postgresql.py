import os

from .settings import *  # noqa: F403

database = {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': os.environ.get('PGDATABASE', 'rdmo_gnd_tests'),
    'USER': os.environ.get('PGUSER', ''),
    'PASSWORD': os.environ.get('PGPASSWORD', ''),
    'HOST': os.environ.get('PGHOST', 'localhost'),
    'PORT': os.environ.get('PGPORT', '5432'),
}
DATABASES = {
    'default': database,
    'cache': {**database, 'NAME': f'{database["NAME"]}_cache', 'TEST': {'MIGRATE': False}},
}
