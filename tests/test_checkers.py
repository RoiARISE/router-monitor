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


def test_host_link_ok_up_and_down():
    from router_monitor.checkers import host_link_ok
    from unittest.mock import mock_open

    # operstate が "up"
    with patch("builtins.open", mock_open(read_data="up\n")):
        assert host_link_ok("wlp1s0") is True

    # operstate が "down"
    with patch("builtins.open", mock_open(read_data="down\n")):
        assert host_link_ok("wlp1s0") is False

    # ファイル読み込み失敗時は安全に True
    with patch("builtins.open", side_effect=FileNotFoundError()):
        assert host_link_ok("wlp1s0") is True


def test_nm_connectivity():
    from router_monitor.checkers import nm_connectivity

    cp_limited = MagicMock(spec=subprocess.CompletedProcess)
    cp_limited.returncode = 0
    cp_limited.stdout = "limited\n"

    with patch("router_monitor.checkers.subprocess.run", return_value=cp_limited):
        assert nm_connectivity() == "limited"

    cp_fail = MagicMock(spec=subprocess.CompletedProcess)
    cp_fail.returncode = 1
    cp_fail.stdout = ""

    with patch("router_monitor.checkers.subprocess.run", return_value=cp_fail):
        assert nm_connectivity() is None

    with patch("router_monitor.checkers.subprocess.run", side_effect=FileNotFoundError()):
        assert nm_connectivity() is None


def test_bluetooth_powered():
    from router_monitor.checkers import bluetooth_powered

    cp_yes = MagicMock(spec=subprocess.CompletedProcess)
    cp_yes.returncode = 0
    cp_yes.stdout = "Controller 3C:3B:AD:6B:F8:0F\n\tName: Test\n\tPowered: yes\n"

    with patch("router_monitor.checkers.subprocess.run", return_value=cp_yes):
        assert bluetooth_powered() is True

    cp_no = MagicMock(spec=subprocess.CompletedProcess)
    cp_no.returncode = 0
    cp_no.stdout = "Controller 3C:3B:AD:6B:F8:0F\n\tName: Test\n\tPowered: no\n"

    with patch("router_monitor.checkers.subprocess.run", return_value=cp_no):
        assert bluetooth_powered() is False

    cp_fail = MagicMock(spec=subprocess.CompletedProcess)
    cp_fail.returncode = 1
    cp_fail.stdout = ""

    with patch("router_monitor.checkers.subprocess.run", return_value=cp_fail):
        assert bluetooth_powered() is None

    with patch("router_monitor.checkers.subprocess.run", side_effect=FileNotFoundError()):
        assert bluetooth_powered() is None


