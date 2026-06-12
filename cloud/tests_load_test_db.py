from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from cloud.management.commands.prepare_load_test_db import _loadtest_ip
from cloud.management.commands.stress_lifecycle_plans import _stress_ip


class PrepareLoadTestDbCommandTestCase(SimpleTestCase):
    def test_dry_run_reports_isolated_sqlite_env_without_writing(self):
        output = StringIO()

        call_command(
            'prepare_load_test_db',
            '--sqlite-name',
            '.shop-load-tests/shop-loadtest-dryrun.sqlite3',
            stdout=output,
        )

        text = output.getvalue()
        self.assertIn('DB_ENGINE=sqlite', text)
        self.assertIn('SQLITE_NAME=.shop-load-tests/shop-loadtest-dryrun.sqlite3', text)
        self.assertIn('SHOP_LOAD_TEST_DB=1', text)
        self.assertIn('dry-run 通过', text)

    def test_rejects_sqlite_path_outside_loadtest_directory(self):
        with self.assertRaisesMessage(CommandError, '必须位于 .shop-load-tests/'):
            call_command(
                'prepare_load_test_db',
                '--sqlite-name',
                'db.sqlite3',
                '--migrate',
                '--confirm-isolated',
                stdout=StringIO(),
            )

    def test_rejects_mutation_without_explicit_confirmation(self):
        with self.assertRaisesMessage(CommandError, '必须传入 --confirm-isolated'):
            call_command(
                'prepare_load_test_db',
                '--sqlite-name',
                '.shop-load-tests/shop-loadtest-unconfirmed.sqlite3',
                '--seed-assets',
                '1',
                stdout=StringIO(),
            )

    def test_loadtest_ip_keeps_octets_in_private_range(self):
        self.assertEqual(_loadtest_ip(1), '10.64.0.1')
        self.assertEqual(_loadtest_ip(256), '10.64.1.1')
        self.assertTrue(_loadtest_ip(100000).startswith('10.'))

    def test_stress_ip_keeps_large_table_values_unique(self):
        values = {_stress_ip(10, index) for index in [1, 255, 256, 65025, 65026, 100000]}
        self.assertEqual(len(values), 6)
        self.assertTrue(all(value.startswith('10.') for value in values))
