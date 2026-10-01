"""
config.yaml (非機密設定) と .env (機密情報: トークン、Webhook URL) を
読み込み、単一の型付けされた Config オブジェクトを生成します。
2つのソースを分離しておくことで、リポジトリの構成（config.yaml はテンプレート経由でコミットされ、
.env は決してコミットされない）を反映しています。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(dotenv_path=None, override=True):
        if not dotenv_path:
            return
        p = Path(dotenv_path)
        if not p.is_file():
            return
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                if override or k not in os.environ:
                    os.environ[k] = v


@dataclass
class PingTargets:
    router_lan_ip: str
    external: List[str]


@dataclass
class Thresholds:
    fail_threshold: int
    cooldown_seconds: int
    reboot_lock_seconds: int
    max_retry: int
    verify_success_required: int
    failed_resume_seconds: int = 300


@dataclass
class PowerCycle:
    off_duration_seconds: int


@dataclass
class HomeAssistantConfig:
    base_url: str
    entity_id: str
    request_timeout_seconds: int
    retries: int
    retry_interval_seconds: int
    token: str = ""  # populated from .env


@dataclass
class NotifyConfig:
    discord_enabled: bool
    misskey_enabled: bool
    discord_webhook_url: str = ""       # from .env
    misskey_api_token: str = ""         # from .env
    misskey_instance_url: str = ""      # from .env


@dataclass
class LoggingConfig:
    events_path: str
    reboots_path: str


@dataclass
class ArpConfig:
    enabled: bool = False
    interface: str = "eth0"


@dataclass
class Config:
    ping_targets: PingTargets
    thresholds: Thresholds
    interval_seconds: int
    power_cycle: PowerCycle
    home_assistant: HomeAssistantConfig
    notify: NotifyConfig
    logging: LoggingConfig
    arp: ArpConfig = field(default_factory=ArpConfig)


def _resolve_path(path_str: str, base_dir: Optional[Path] = None) -> Path:
    p = Path(path_str)
    if p.is_file():
        return p.resolve()
    if not p.is_absolute():
        if base_dir and (base_dir / p).is_file():
            return (base_dir / p).resolve()
        project_root = Path(__file__).resolve().parent.parent
        if (project_root / p).is_file():
            return (project_root / p).resolve()
    return p.resolve()


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> Config:
    project_root = Path(__file__).resolve().parent.parent

    resolved_env = _resolve_path(env_path, project_root)
    load_dotenv(dotenv_path=resolved_env, override=True)

    resolved_config = _resolve_path(config_path, project_root)
    if not resolved_config.is_file():
        raise FileNotFoundError(f"設定ファイルが見つかりません: {config_path}")

    with open(resolved_config, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError(f"{config_path} が空であるか、不正な形式です")

    base_dir = resolved_config.parent

    try:
        ping_targets = PingTargets(
            router_lan_ip=raw["ping_targets"]["router_lan_ip"],
            external=list(raw["ping_targets"]["external"]),
        )

        thresholds_raw = raw["thresholds"]
        thresholds = Thresholds(
            fail_threshold=thresholds_raw["fail_threshold"],
            cooldown_seconds=thresholds_raw["cooldown_seconds"],
            reboot_lock_seconds=thresholds_raw["reboot_lock_seconds"],
            max_retry=thresholds_raw["max_retry"],
            verify_success_required=thresholds_raw["verify_success_required"],
            failed_resume_seconds=thresholds_raw.get("failed_resume_seconds", 300),
        )

        power_cycle = PowerCycle(
            off_duration_seconds=raw["power_cycle"]["off_duration_seconds"],
        )

        ha_raw = raw["home_assistant"]
        home_assistant = HomeAssistantConfig(
            base_url=ha_raw["base_url"],
            entity_id=ha_raw["entity_id"],
            request_timeout_seconds=ha_raw["request_timeout_seconds"],
            retries=ha_raw["retries"],
            retry_interval_seconds=ha_raw["retry_interval_seconds"],
            token=os.environ.get("HA_TOKEN", ""),
        )

        notify_raw = raw["notify"]
        notify = NotifyConfig(
            discord_enabled=notify_raw.get("discord_enabled", False),
            misskey_enabled=notify_raw.get("misskey_enabled", False),
            discord_webhook_url=os.environ.get("DISCORD_WEBHOOK_URL", ""),
            misskey_api_token=os.environ.get("MISSKEY_API_TOKEN", ""),
            misskey_instance_url=os.environ.get("MISSKEY_INSTANCE_URL", ""),
        )

        logging_raw = raw["logging"]
        events_p = Path(logging_raw["events_path"])
        if not events_p.is_absolute():
            events_p = base_dir / events_p
        reboots_p = Path(logging_raw["reboots_path"])
        if not reboots_p.is_absolute():
            reboots_p = base_dir / reboots_p

        logging_cfg = LoggingConfig(
            events_path=str(events_p),
            reboots_path=str(reboots_p),
        )

        arp_raw = raw.get("arp", {})
        arp_cfg = ArpConfig(
            enabled=arp_raw.get("enabled", False),
            interface=arp_raw.get("interface", "eth0"),
        )

    except KeyError as exc:
        raise ValueError(f"必須の設定キーが不足しています: {exc}") from exc

    config = Config(
        ping_targets=ping_targets,
        thresholds=thresholds,
        interval_seconds=raw["interval_seconds"],
        power_cycle=power_cycle,
        home_assistant=home_assistant,
        notify=notify,
        logging=logging_cfg,
        arp=arp_cfg,
    )

    _validate(config)
    return config


def _validate(config: Config) -> None:
    problems = []

    # HA_TOKEN は必須（これがないと電源制御が一切できない）
    if not config.home_assistant.token:
        problems.append("HA_TOKEN が .env に設定されていません")

    # 数値・必須項目のバリデーション
    if config.interval_seconds <= 0:
        problems.append("interval_seconds は 1 以上の整数である必要があります")
    if config.thresholds.fail_threshold <= 0:
        problems.append("fail_threshold は 1 以上の整数である必要があります")
    if config.thresholds.verify_success_required <= 0:
        problems.append("verify_success_required は 1 以上の整数である必要があります")
    if config.thresholds.cooldown_seconds < 0:
        problems.append("cooldown_seconds は 0 以上の整数である必要があります")
    if config.thresholds.reboot_lock_seconds < 0:
        problems.append("reboot_lock_seconds は 0 以上の整数である必要があります")
    if config.thresholds.failed_resume_seconds < 0:
        problems.append("failed_resume_seconds は 0 以上の整数である必要があります")
    if config.thresholds.max_retry <= 0:
        problems.append("max_retry は 1 以上の整数である必要があります")
    if config.power_cycle.off_duration_seconds <= 0:
        problems.append("off_duration_seconds は 1 以上の整数である必要があります")
    if not config.ping_targets.router_lan_ip:
        problems.append("ping_targets.router_lan_ip が設定されていません")
    if not config.ping_targets.external:
        problems.append("ping_targets.external には少なくとも1つの外部ホストが必要です")

    # Discord / Misskey の資格情報未設定はエラーではなく警告扱い。
    # 実際の無効化は Notifier.from_config() が行う（資格情報がなければチャンネルを追加しない）。

    if problems:
        raise ValueError("設定のバリデーションに失敗しました:\n  - " + "\n  - ".join(problems))