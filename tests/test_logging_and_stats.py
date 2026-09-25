import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router_monitor.logging_utils import JsonlLogger
from router_monitor.stats import get_reboot_stats, format_stats_message


def test_log_event_writes_jsonl_line():
    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "events.jsonl"
        reboots_path = Path(tmp) / "reboots.jsonl"
        logger = JsonlLogger(str(events_path), str(reboots_path))

        logger.log_event("router_down_confirmed", fail_count=6)

        lines = events_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "router_down_confirmed"
        assert entry["fail_count"] == 6
        assert "timestamp" in entry


def test_log_reboot_writes_separate_file():
    with tempfile.TemporaryDirectory() as tmp:
        events_path = Path(tmp) / "events.jsonl"
        reboots_path = Path(tmp) / "reboots.jsonl"
        logger = JsonlLogger(str(events_path), str(reboots_path))

        logger.log_reboot(attempt=1)
        logger.log_event("some_other_event")

        reboot_lines = reboots_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(reboot_lines) == 1
        assert not events_path.exists() or "reboot" not in [
            json.loads(l)["event"] for l in events_path.read_text().strip().splitlines()
        ]


def test_creates_parent_directories():
    with tempfile.TemporaryDirectory() as tmp:
        nested = Path(tmp) / "a" / "b" / "events.jsonl"
        reboots = Path(tmp) / "a" / "b" / "reboots.jsonl"
        logger = JsonlLogger(str(nested), str(reboots))
        logger.log_event("x")
        assert nested.exists()


def test_get_reboot_stats_empty_when_file_missing():
    stats = get_reboot_stats("/nonexistent/path/reboots.jsonl")
    assert stats == {"today": 0, "month": 0, "total": 0}


def test_get_reboot_stats_counts_correctly():
    with tempfile.TemporaryDirectory() as tmp:
        reboots_path = Path(tmp) / "reboots.jsonl"
        now = datetime(2026, 6, 20, 15, 0, 0)

        entries = [
            {"timestamp": "2026-06-20T10:00:00+09:00"},   # today
            {"timestamp": "2026-06-20T11:00:00+09:00"},   # today
            {"timestamp": "2026-06-15T09:00:00+09:00"},   # this month, not today
            {"timestamp": "2026-05-01T09:00:00+09:00"},   # not this month
            {"timestamp": "2025-01-01T09:00:00+09:00"},   # last year
        ]
        with open(reboots_path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        stats = get_reboot_stats(str(reboots_path), now=now)
        assert stats == {"today": 2, "month": 3, "total": 5}


def test_get_reboot_stats_skips_malformed_lines():
    with tempfile.TemporaryDirectory() as tmp:
        reboots_path = Path(tmp) / "reboots.jsonl"
        with open(reboots_path, "w", encoding="utf-8") as f:
            f.write('{"timestamp": "2026-06-20T10:00:00+09:00"}\n')
            f.write("not valid json\n")
            f.write('{"timestamp": "2026-06-20T11:00:00+09:00"}\n')

        now = datetime(2026, 6, 20, 15, 0, 0)
        stats = get_reboot_stats(str(reboots_path), now=now)
        assert stats["total"] == 2


def test_format_stats_message():
    msg = format_stats_message({"today": 2, "month": 7, "total": 53})
    assert "2" in msg and "7" in msg and "53" in msg


def test_logger_handles_write_failure_gracefully(tmp_path):
    bad_path = str(tmp_path)
    logger = JsonlLogger(bad_path, bad_path)
    # 例外が送出されずに処理が継続すること
    logger.log_event("test_event", foo="bar")
    logger.log_reboot(foo="bar")

