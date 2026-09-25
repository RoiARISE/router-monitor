import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from router_monitor.config import load_config


ENV_KEYS = ["HA_TOKEN", "DISCORD_WEBHOOK_URL", "MISSKEY_API_TOKEN", "MISSKEY_INSTANCE_URL"]


@pytest.fixture(autouse=True)
def clean_env():
    """load_dotenv mutates the process environment, so each test must
    start from a clean slate regardless of execution order."""
    saved = {k: os.environ.pop(k, None) for k in ENV_KEYS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v



VALID_YAML = """
ping_targets:
  router_lan_ip: "192.168.0.1"
  external:
    - "1.1.1.1"
    - "8.8.8.8"

thresholds:
  fail_threshold: 6
  cooldown_seconds: 90
  reboot_lock_seconds: 300
  max_retry: 3
  verify_success_required: 3

interval_seconds: 30

power_cycle:
  off_duration_seconds: 30

home_assistant:
  base_url: "http://localhost:8123"
  entity_id: "switch.switchbot_plug_router"
  request_timeout_seconds: 10
  retries: 3
  retry_interval_seconds: 5

notify:
  discord_enabled: true
  misskey_enabled: false

logging:
  events_path: "data/events.jsonl"
  reboots_path: "data/reboots.jsonl"

arp:
  enabled: true
  interface: "wlan0"
"""

VALID_ENV = """
HA_TOKEN=test_token_123
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/test
"""


def _write(tmp, name, content):
    path = Path(tmp) / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_load_valid_config():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write(tmp, "config.yaml", VALID_YAML)
        env_path = _write(tmp, ".env", VALID_ENV)

        config = load_config(config_path, env_path)

        assert config.ping_targets.router_lan_ip == "192.168.0.1"
        assert config.ping_targets.external == ["1.1.1.1", "8.8.8.8"]
        assert config.thresholds.fail_threshold == 6
        assert config.thresholds.failed_resume_seconds == 300  # default applied
        assert config.interval_seconds == 30
        assert config.home_assistant.entity_id == "switch.switchbot_plug_router"
        assert config.home_assistant.token == "test_token_123"
        assert config.notify.discord_enabled is True
        assert config.notify.discord_webhook_url == "https://discord.com/api/webhooks/test"
        assert config.arp.enabled is True
        assert config.arp.interface == "wlan0"


def test_missing_required_key_raises():
    broken_yaml = VALID_YAML.replace("fail_threshold: 6", "wrong_key: 6")
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write(tmp, "config.yaml", broken_yaml)
        env_path = _write(tmp, ".env", VALID_ENV)

        try:
            load_config(config_path, env_path)
            assert False, "expected ValueError"
        except ValueError as e:
            assert "必須の設定キーが不足しています" in str(e)


def test_missing_ha_token_raises():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write(tmp, "config.yaml", VALID_YAML)
        env_path = _write(tmp, ".env", "")  # no HA_TOKEN

        try:
            load_config(config_path, env_path)
            assert False, "expected ValueError"
        except ValueError as e:
            assert "HA_TOKEN" in str(e)


def test_discord_enabled_without_webhook_does_not_raise():
    """discord_enabled=true でも DISCORD_WEBHOOK_URL 未設定はエラーにならない。
    Notifier.from_config() がスキップするため config 読み込み自体は成功する。"""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write(tmp, "config.yaml", VALID_YAML)
        env_path = _write(tmp, ".env", "HA_TOKEN=abc\n")  # no webhook url

        config = load_config(config_path, env_path)
        # 読み込みは成功し、webhook_url が空文字のまま
        assert config.notify.discord_enabled is True
        assert config.notify.discord_webhook_url == ""


def test_arp_defaults_when_section_missing():
    yaml_without_arp = VALID_YAML.replace(
        """
arp:
  enabled: true
  interface: "wlan0"
""",
        "",
    )
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write(tmp, "config.yaml", yaml_without_arp)
        env_path = _write(tmp, ".env", VALID_ENV)

        config = load_config(config_path, env_path)
        assert config.arp.enabled is False
        assert config.arp.interface == "eth0"


def test_empty_config_file_raises():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write(tmp, "config.yaml", "")
        env_path = _write(tmp, ".env", VALID_ENV)

        try:
            load_config(config_path, env_path)
            assert False, "expected ValueError"
        except ValueError as e:
            assert "空" in str(e) or "empty" in str(e)


def test_validation_rejects_non_positive_numbers():
    invalid_yaml = VALID_YAML.replace("interval_seconds: 30", "interval_seconds: 0")
    with tempfile.TemporaryDirectory() as tmp:
        config_path = _write(tmp, "config.yaml", invalid_yaml)
        env_path = _write(tmp, ".env", VALID_ENV)
        with pytest.raises(ValueError, match="interval_seconds"):
            load_config(config_path, env_path)

