"""Run Django tests without reading or mutating an existing portal environment."""
import os
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# Child process only: never use the operator's database, secrets or remote endpoints.
for key in list(os.environ):
    if key.startswith('PORTAL_') or key == 'DJANGO_SETTINGS_MODULE':
        del os.environ[key]
with tempfile.TemporaryDirectory(prefix='portal-prd-tests-') as temporary:
    os.environ.update({
        'DJANGO_SETTINGS_MODULE': 'config.settings',
        'PORTAL_SECRET_KEY': secrets.token_urlsafe(48),
        'PORTAL_DEBUG': '1', 'PORTAL_HTTPS': '0',
        'PORTAL_SQLITE_PATH': str(Path(temporary) / 'test-base.sqlite3'),
        'PORTAL_PRODUCT_STORAGE_ROOT': str(Path(temporary) / 'product'),
        'PORTAL_HR_STORAGE_ROOT': str(Path(temporary) / 'hr'),
        'PORTAL_ALLOWED_HOSTS': 'testserver,localhost,127.0.0.1',
    })
    sys.path.insert(0, str(ROOT / 'backend'))
    from django.core.management import execute_from_command_line
    args = sys.argv[1:]
    if args == ['--check-schema']:
        execute_from_command_line(['manage.py', 'check'])
        execute_from_command_line(['manage.py', 'makemigrations', '--check', '--dry-run'])
        execute_from_command_line(['manage.py', 'migrate', '--noinput'])
    else:
        if '--fast-passwords' in args:
            # Optional test-only speedup. Default runs retain the real password hasher.
            from django.conf import settings
            settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
            args.remove('--fast-passwords')
        execute_from_command_line(['manage.py', 'test', *(args or ['portal.tests']), '--noinput'])
