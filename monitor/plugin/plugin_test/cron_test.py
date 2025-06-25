from datetime import datetime

from lib.plugins.cron import CronPlugin


class TestCronPerMin(CronPlugin, cron_expression="*/1 * * * *", run_on_startup=True):
    """Test cron job that runs every 1 minutes with startup execution."""

    def start(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info(f"TestCronPerMin job started at {current_time}")


class TestCron2Min(CronPlugin, cron_expression="*/2 * * * *", run_on_startup=True):
    """Test cron job that runs every 2 minutes with startup execution."""

    def start(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info(f"TestCron2Min job started at {current_time}")
