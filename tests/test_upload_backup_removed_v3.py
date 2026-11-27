"""Regression test: upload_backup was removed because Uptime Kuma 3.x has no
uploadBackup socket handler (checked at 3.0.0-beta.2 and 3.0.0-beta.3).
Refs #88."""
from uptime_kuma_api import UptimeKumaApi


def test_upload_backup_removed():
    assert not hasattr(UptimeKumaApi, "upload_backup")
