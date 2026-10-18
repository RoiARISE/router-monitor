import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router_monitor.notify.discord import DiscordNotifier
from router_monitor.notify.misskey import MisskeyNotifier
from router_monitor.notify import Notifier
from router_monitor.config import NotifyConfig


def _resp(status_code=200):
    r = MagicMock(spec=requests.Response)
    r.status_code = status_code
    r.ok = 200 <= status_code < 300
    return r


# -- DiscordNotifier --------------------------------------------------

def test_discord_send_success():
    notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
    with patch("router_monitor.notify.discord.requests.post", return_value=_resp(200)) as mock_post:
        ok = notifier.send("hello")
        assert ok is True
        assert mock_post.call_args.kwargs["json"] == {"content": "hello"}


def test_discord_send_http_failure_returns_false_no_raise():
    notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
    with patch("router_monitor.notify.discord.requests.post", return_value=_resp(500)):
        ok = notifier.send("hello")
        assert ok is False


def test_discord_send_network_failure_returns_false_no_raise():
    notifier = DiscordNotifier("https://discord.com/api/webhooks/test")
    with patch(
        "router_monitor.notify.discord.requests.post",
        side_effect=requests.ConnectionError("refused"),
    ):
        ok = notifier.send("hello")
        assert ok is False


# -- MisskeyNotifier ----------------------------------------------------

def test_misskey_send_success():
    notifier = MisskeyNotifier("https://misskey.example", "tok123")
    with patch("router_monitor.notify.misskey.requests.post", return_value=_resp(200)) as mock_post:
        ok = notifier.send("hello")
        assert ok is True
        body = mock_post.call_args.kwargs["json"]
        assert body["i"] == "tok123"
        assert body["text"] == "hello"


def test_misskey_send_failure_returns_false_no_raise():
    notifier = MisskeyNotifier("https://misskey.example", "tok123")
    with patch("router_monitor.notify.misskey.requests.post", return_value=_resp(403)):
        ok = notifier.send("hello")
        assert ok is False


# -- Notifier fan-out -----------------------------------------------------

def test_notifier_sends_to_all_enabled_channels():
    sent = []

    class FakeChannel:
        def send(self, message):
            sent.append(message)
            return True

    notifier = Notifier([FakeChannel(), FakeChannel()])
    notifier.send("hi")
    assert sent == ["hi", "hi"]


def test_notifier_continues_after_one_channel_fails():
    sent = []

    class FailingChannel:
        def send(self, message):
            raise RuntimeError("should not propagate")

    class WorkingChannel:
        def send(self, message):
            sent.append(message)
            return True

    # FailingChannel が例外を送出しても Notifier.send() 内で捕捉され、
    # 後続の WorkingChannel に正常に通知が届くことを検証する
    notifier = Notifier([FailingChannel(), WorkingChannel()])
    notifier.send("hi")
    assert sent == ["hi"]


def test_notifier_from_config_only_builds_enabled_channels():
    config = NotifyConfig(
        discord_enabled=True,
        misskey_enabled=False,
        discord_webhook_url="https://discord.com/api/webhooks/test",
    )
    notifier = Notifier.from_config(config)
    assert len(notifier._channels) == 1
    assert isinstance(notifier._channels[0], DiscordNotifier)


def test_notifier_from_config_builds_both_when_both_enabled():
    config = NotifyConfig(
        discord_enabled=True,
        misskey_enabled=True,
        discord_webhook_url="https://discord.com/api/webhooks/test",
        misskey_api_token="tok",
        misskey_instance_url="https://misskey.example",
    )
    notifier = Notifier.from_config(config)
    assert len(notifier._channels) == 2


def test_quiet_hours_detection():
    from datetime import time as dtime
    from router_monitor.notify import is_in_quiet_hours

    # 23:00 〜 07:00 (日付またぎ)
    assert is_in_quiet_hours(dtime(23, 30), "23:00", "07:00") is True
    assert is_in_quiet_hours(dtime(4, 0), "23:00", "07:00") is True
    assert is_in_quiet_hours(dtime(6, 59), "23:00", "07:00") is True
    assert is_in_quiet_hours(dtime(7, 0), "23:00", "07:00") is False
    assert is_in_quiet_hours(dtime(12, 0), "23:00", "07:00") is False
    assert is_in_quiet_hours(dtime(22, 59), "23:00", "07:00") is False


def test_notifier_suppresses_during_quiet_hours():
    from datetime import time as dtime
    from unittest.mock import MagicMock

    sent = []
    ch = MagicMock()
    ch.send = lambda msg: sent.append(msg)

    current_time = [dtime(4, 0)]  # 04:00 (夜間)

    notifier = Notifier(
        [ch],
        quiet_hours_enabled=True,
        quiet_hours_start="23:00",
        quiet_hours_end="07:00",
        now_fn=lambda: current_time[0],
    )

    # 夜間は抑制される
    notifier.send("alert at 4am")
    assert len(sent) == 0

    # force=True なら夜間でも送信される
    notifier.send("daily report", force=True)
    assert len(sent) == 1
    assert sent[0] == "daily report"

    # 昼間 (12:00) になれば通常通り送信される
    current_time[0] = dtime(12, 0)
    notifier.send("daytime alert")
    assert len(sent) == 2
    assert sent[1] == "daytime alert"


