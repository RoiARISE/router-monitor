import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router_monitor.relay.home_assistant import (
    RelayController,
    HomeAssistantUnreachable,
    HomeAssistantAuthError,
    RelayOperationFailed,
)


def make_controller(**overrides):
    defaults = dict(
        base_url="http://localhost:8123",
        token="test-token",
        entity_id="switch.switchbot_plug_router",
        timeout_seconds=5,
        retries=3,
        retry_interval_seconds=0,  # no real waiting in tests
    )
    defaults.update(overrides)
    return RelayController(**defaults)


def _resp(status_code=200, json_body=None):
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.ok = 200 <= status_code < 300
    r.json.return_value = json_body or {}
    if not r.ok:
        r.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status_code}")
    else:
        r.raise_for_status.side_effect = None
    return r


# -- check_connectivity ---------------------------------------------------

def test_check_connectivity_ok():
    rc = make_controller()
    with patch("router_monitor.relay.home_assistant.requests.get", return_value=_resp(200)):
        rc.check_connectivity()  # should not raise


def test_check_connectivity_auth_error():
    rc = make_controller()
    with patch("router_monitor.relay.home_assistant.requests.get", return_value=_resp(401)):
        with pytest.raises(HomeAssistantAuthError):
            rc.check_connectivity()


def test_check_connectivity_404_is_unreachable():
    rc = make_controller()
    with patch("router_monitor.relay.home_assistant.requests.get", return_value=_resp(404)):
        with pytest.raises(HomeAssistantUnreachable):
            rc.check_connectivity()


def test_check_connectivity_network_error():
    rc = make_controller()
    with patch(
        "router_monitor.relay.home_assistant.requests.get",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(HomeAssistantUnreachable):
            rc.check_connectivity()


# -- power_cycle happy path ----------------------------------------------

def test_power_cycle_success():
    rc = make_controller()
    sleeps = []

    get_responses = [
        _resp(200),                              # check_connectivity
        _resp(200, {"state": "off"}),             # after turn_off
        _resp(200, {"state": "on"}),              # after turn_on
    ]

    with patch("router_monitor.relay.home_assistant.requests.get", side_effect=get_responses), \
         patch("router_monitor.relay.home_assistant.requests.post", return_value=_resp(200)):
        rc.power_cycle(off_duration=30, sleep_fn=lambda s: sleeps.append(s))

    assert 30 in sleeps  # the off_duration wait actually happened


# -- power_cycle failure modes --------------------------------------------

def test_power_cycle_fails_when_ha_unreachable():
    rc = make_controller()
    with patch(
        "router_monitor.relay.home_assistant.requests.get",
        side_effect=requests.ConnectionError("refused"),
    ):
        with pytest.raises(HomeAssistantUnreachable):
            rc.power_cycle(off_duration=30, sleep_fn=lambda s: None)


def test_power_cycle_fails_when_state_never_matches():
    rc = make_controller(retries=2)

    get_responses = [
        _resp(200),                          # check_connectivity
        _resp(200, {"state": "on"}),         # after turn_off attempt 1 - wrong state
        _resp(200, {"state": "on"}),         # after turn_off attempt 2 - still wrong
    ]

    with patch("router_monitor.relay.home_assistant.requests.get", side_effect=get_responses), \
         patch("router_monitor.relay.home_assistant.requests.post", return_value=_resp(200)):
        with pytest.raises(RelayOperationFailed):
            rc.power_cycle(off_duration=30, sleep_fn=lambda s: None)


def test_power_cycle_retries_then_succeeds():
    rc = make_controller(retries=3)

    get_responses = [
        _resp(200),                          # check_connectivity
        _resp(200, {"state": "on"}),         # turn_off attempt 1 fails
        _resp(200, {"state": "off"}),        # turn_off attempt 2 succeeds
        _resp(200, {"state": "on"}),         # turn_on attempt 1 succeeds
    ]

    with patch("router_monitor.relay.home_assistant.requests.get", side_effect=get_responses), \
         patch("router_monitor.relay.home_assistant.requests.post", return_value=_resp(200)):
        rc.power_cycle(off_duration=30, sleep_fn=lambda s: None)  # should not raise


def test_get_state_returns_string():
    rc = make_controller()
    with patch(
        "router_monitor.relay.home_assistant.requests.get",
        return_value=_resp(200, {"state": "off"}),
    ):
        assert rc.get_state() == "off"


def test_ensure_on_turns_on_when_off():
    rc = make_controller()
    calls = []

    def mock_post(url, **kwargs):
        calls.append(url)
        return _resp(200)

    # get_state returns "off", then after turn_on returns "on"
    get_responses = [_resp(200, {"state": "off"}), _resp(200, {"state": "on"})]
    with patch("router_monitor.relay.home_assistant.requests.get", side_effect=get_responses), \
         patch("router_monitor.relay.home_assistant.requests.post", side_effect=mock_post):
        rc.ensure_on(sleep_fn=lambda s: None)

    assert any("turn_on" in c for c in calls)


def test_ensure_on_noop_when_already_on():
    rc = make_controller()
    with patch("router_monitor.relay.home_assistant.requests.get", return_value=_resp(200, {"state": "on"})), \
         patch("router_monitor.relay.home_assistant.requests.post") as mock_post:
        rc.ensure_on(sleep_fn=lambda s: None)
        mock_post.assert_not_called()


def test_power_cycle_ensures_on_even_if_sleep_raises():
    rc = make_controller(retries=1)

    get_responses = [
        _resp(200),                          # check_connectivity
        _resp(200, {"state": "off"}),        # after turn_off attempt 1 -> succeeds!
        _resp(200, {"state": "on"}),         # after turn_on attempt 1 in finally -> succeeds!
    ]

    calls = []
    def mock_post(url, **kwargs):
        calls.append(url)
        return _resp(200)

    def sleep_raising(dur):
        if dur == 30:  # off_duration
            raise RuntimeError("unexpected error during sleep")

    with patch("router_monitor.relay.home_assistant.requests.get", side_effect=get_responses), \
         patch("router_monitor.relay.home_assistant.requests.post", side_effect=mock_post):
        with pytest.raises(RuntimeError, match="unexpected error during sleep"):
            rc.power_cycle(off_duration=30, sleep_fn=sleep_raising)

    assert any("turn_off" in c for c in calls)
    assert any("turn_on" in c for c in calls)

