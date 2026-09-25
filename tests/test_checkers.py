import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router_monitor.checkers import ping, arp_check


def _completed(returncode):
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    return cp


def test_ping_success():
    with patch("router_monitor.checkers.subprocess.run", return_value=_completed(0)) as mock_run:
        assert ping("1.1.1.1") is True
        args = mock_run.call_args[0][0]
        assert args[0] == "ping"
        assert "1.1.1.1" in args


def test_ping_failure():
    with patch("router_monitor.checkers.subprocess.run", return_value=_completed(1)):
        assert ping("1.1.1.1") is False


def test_ping_command_missing_raises():
    with patch("router_monitor.checkers.subprocess.run", side_effect=FileNotFoundError()):
        try:
            ping("1.1.1.1")
            assert False, "expected RuntimeError"
        except RuntimeError:
            pass


def test_ping_unexpected_exception_returns_false():
    with patch("router_monitor.checkers.subprocess.run", side_effect=OSError("boom")):
        assert ping("1.1.1.1") is False


def test_arp_check_success():
    with patch("router_monitor.checkers.subprocess.run", return_value=_completed(0)):
        assert arp_check("192.168.0.1", interface="eth0") is True


def test_arp_check_failure_is_false_not_none():
    with patch("router_monitor.checkers.subprocess.run", return_value=_completed(1)):
        assert arp_check("192.168.0.1", interface="eth0") is False


def test_arp_check_missing_binary_returns_none():
    with patch("router_monitor.checkers.subprocess.run", side_effect=FileNotFoundError()):
        assert arp_check("192.168.0.1", interface="eth0") is None


def test_arp_check_unexpected_exception_returns_none():
    with patch("router_monitor.checkers.subprocess.run", side_effect=OSError("boom")):
        assert arp_check("192.168.0.1", interface="eth0") is None
