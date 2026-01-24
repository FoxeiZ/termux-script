from datetime import datetime

from lib.plugin.cron import CronPlugin


class TestCronPerMin(CronPlugin, cron_expression="*/1 * * * *", run_on_startup=False):
    """Test cron job that runs every 1 minutes with startup execution."""

    def start(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info(f"test cron per min job started at {current_time}")


class TestCron2Min(CronPlugin, cron_expression="*/2 * * * *", run_on_startup=True):
    """Test cron job that runs every 2 minutes with startup execution."""

    def start(self):
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.logger.info(f"test cron 2 min job started at {current_time}")
