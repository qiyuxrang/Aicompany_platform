from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from portal.tender_recovery import RecoveryRejected, confirm_batch, confirm_source


class Command(BaseCommand):
    help = '仅在操作员已确认旧采集进程停止后解除过期运行；此命令不停止进程、不采集'

    def add_arguments(self, parser):
        parser.add_argument('--operator-id', required=True, type=int)
        parser.add_argument('--reason', required=True)
        parser.add_argument('--confirm-process-stopped', '--confirm-stopped',
                            dest='confirm_process_stopped', action='store_true')
        parser.add_argument('--source-run', action='append', type=int, default=[])
        parser.add_argument('--batch')

    def handle(self, *args, **options):
        if not options['confirm_process_stopped']:
            raise CommandError('须先确认旧进程已停止，再显式传入 --confirm-process-stopped')
        if not options['source_run'] and not options['batch']:
            raise CommandError('必须指定 --source-run 或 --batch')
        try:
            actor = get_user_model().objects.get(pk=options['operator_id'])
            for run_id in options['source_run']:
                run = confirm_source(run_id, actor, options['reason'])
                self.stdout.write(f'source_run={run.pk} state={run.state}')
            if options['batch']:
                batch = confirm_batch(options['batch'], actor, options['reason'])
                self.stdout.write(f'batch={batch.pk} state={batch.state}')
        except (get_user_model().DoesNotExist, RecoveryRejected) as error:
            raise CommandError(str(error)) from None
