"""Reproducible isolated Django tests; never select a configured business database."""
import argparse
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='portal.tests')
    parser.add_argument('--parallel', type=int, default=4, choices=range(1, 9))
    args = parser.parse_args()
    if not args.label.startswith('portal.tests'):
        parser.error('Only portal.tests labels are supported.')
    root = Path(__file__).resolve().parents[1]
    runtime = root / '.runtime' / 'intake-test-runs'
    runtime.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=runtime) as directory:
        os.environ.pop('PORTAL_DB_NAME', None)
        os.environ.update(DJANGO_SETTINGS_MODULE='config.settings', PORTAL_DEBUG='1', PORTAL_HTTPS='0',
            PORTAL_SECRET_KEY=secrets.token_urlsafe(48), PORTAL_SQLITE_PATH=str(Path(directory) / 'isolated.sqlite3'),
            PORTAL_PRODUCT_MODEL_CALLS_ALLOWED='0', PORTAL_PRODUCT_RETRIEVAL_ENABLED='0',
            PORTAL_MODEL_GATEWAY_URL='', PORTAL_BUSINESS_SUMMARY_URL='')
        sys.path.insert(0, str(root / 'backend'))
        import django
        django.setup()
        from django.core.management import call_command
        call_command('test', args.label, parallel=args.parallel, verbosity=1, interactive=False)


if __name__ == '__main__': main()
