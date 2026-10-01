"""
Entry point for the router monitor. Wires together:
  - config (config.yaml + .env)
  - checkers (ping / arp)
  - RouterStateMachine + UpstreamStateMachine
  - RelayController (Home Assistant)
  - Notifier (Discord / Misskey)
  - JsonlLogger + stats

This file intentionally contains no business logic of its own beyond
"call things in the right order". All decision logic lives in the state
machines so it stays unit-testable without a real network or HA.
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

from router_monitor.config import load_config, Config
from router_monitor.checkers import ping, arp_check
from router_monitor.state_machines.router_sm import RouterStateMachine, RouterState
from router_monitor.state_machines.upstream_sm import UpstreamStateMachine
from router_monitor.relay.home_assistant import RelayController
from router_monitor.notify import Notifier
from router_monitor.logging_utils import JsonlLogger
from router_monitor.stats import (
    get_reboot_stats,
    format_stats_message,
    get_daily_ping_stats,
    format_daily_stability_message,
)

# Local fallback log, separate from the JSON Lines event log, so that
# even if JsonlLogger itself fails (e.g. disk full) operators can still
# see what happened via `journalctl -u router-monitor`.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
syslog = logging.getLogger("router_monitor")


def run_cycle(
    config: Config,
    router_sm: RouterStateMachine,
    upstream_sm: UpstreamStateMachine,
    jlog: JsonlLogger,
) -> None:
    router_ok = ping(config.ping_targets.router_lan_ip)
    external_ok = any(ping(host) for host in config.ping_targets.external)

    arp_result = None
    if config.arp.enabled and not router_ok:
        # Diagnostic only. Per design: ARP failures (including the
        # arping binary being unavailable) NEVER affect the reboot
        # decision. We only ever log the result here.
        arp_result = arp_check(config.ping_targets.router_lan_ip, config.arp.interface)
        jlog.log_event("arp_diagnostic", result=arp_result)

    jlog.log_event(
        "ping_cycle",
        router_ok=router_ok,
        external_ok=external_ok,
        router_state=router_sm.state.value,
        upstream_state=upstream_sm.state.value,
    )

    upstream_sm.tick(router_ok, external_ok)
    router_sm.tick(router_ok, external_ok)

    if router_sm.awaiting_power_cycle:
        success = router_sm.perform_power_cycle()
        if success:
            jlog.log_reboot(
                attempt=router_sm.retry_count,
                off_duration_seconds=config.power_cycle.off_duration_seconds,
            )
            stats = get_reboot_stats(config.logging.reboots_path)
            router_sm.notify(f"\U0001F501 \u30eb\u30fc\u30bf\u30fc\u3092\u518d\u8d77\u52d5\u3057\u307e\u3057\u305f\n{format_stats_message(stats)}")


def main() -> int:
    project_root = Path(__file__).resolve().parent
    default_config = str(project_root / "config.yaml")
    default_env = str(project_root / ".env")

    parser = argparse.ArgumentParser(description="TL-WR902AC auto-recovery monitor")
    parser.add_argument("--config", default=default_config)
    parser.add_argument("--env", default=default_env)
    args = parser.parse_args()

    try:
        config = load_config(args.config, args.env)
    except (ValueError, FileNotFoundError) as exc:
        syslog.error("config error: %s", exc)
        return 1

    jlog = JsonlLogger(config.logging.events_path, config.logging.reboots_path)

    relay = RelayController(
        base_url=config.home_assistant.base_url,
        token=config.home_assistant.token,
        entity_id=config.home_assistant.entity_id,
        timeout_seconds=config.home_assistant.request_timeout_seconds,
        retries=config.home_assistant.retries,
        retry_interval_seconds=config.home_assistant.retry_interval_seconds,
        logger=jlog.as_logger_fn(),
    )

    try:
        relay.ensure_on()
    except Exception as exc:
        syslog.warning("relay.ensure_on during startup failed: %s", exc)

    notifier = Notifier.from_config(config.notify, logger=jlog.as_logger_fn())

    router_sm = RouterStateMachine(
        fail_threshold=config.thresholds.fail_threshold,
        cooldown_seconds=config.thresholds.cooldown_seconds,
        reboot_lock_seconds=config.thresholds.reboot_lock_seconds,
        max_retry=config.thresholds.max_retry,
        verify_success_required=config.thresholds.verify_success_required,
        failed_resume_seconds=config.thresholds.failed_resume_seconds,
        off_duration_seconds=config.power_cycle.off_duration_seconds,
        relay=relay,
        logger=jlog.as_logger_fn(),
        notifier=notifier.as_notifier_fn(),
    )

    upstream_sm = UpstreamStateMachine(
        fail_threshold=config.thresholds.fail_threshold,
        logger=jlog.as_logger_fn(),
        notifier=notifier.as_notifier_fn(),
    )

    syslog.info("router-monitor starting; interval=%ss", config.interval_seconds)
    jlog.log_event("monitor_started")

    # 日次安定レポート用: 現在の日付を記録し、日付が変わったら前日分を出力する
    _report_date = datetime.now().astimezone().date()

    try:
        while True:
            # 日付変わり目チェック
            today = datetime.now().astimezone().date()
            if today != _report_date:
                # 前日の ping 安定レポートを生成・出力
                yesterday = datetime(  # type: ignore[call-arg]
                    _report_date.year, _report_date.month, _report_date.day
                ).astimezone()
                ping_stats = get_daily_ping_stats(config.logging.events_path, yesterday)
                reboot_stats = get_reboot_stats(config.logging.reboots_path, yesterday)
                msg = format_daily_stability_message(
                    ping_stats, yesterday, reboot_count=reboot_stats["today"]
                )
                jlog.log_event(
                    "daily_stability_report",
                    date=_report_date.isoformat(),
                    reboot_count=reboot_stats["today"],
                    **ping_stats,
                )
                notifier.send(msg)
                syslog.info("daily report: %s", msg)
                _report_date = today

            try:
                run_cycle(config, router_sm, upstream_sm, jlog)
            except Exception:
                # A single bad cycle (e.g. transient ping subprocess
                # error) must not kill the whole service; systemd
                # Restart=always is the last line of defense, but we
                # should not rely on a crash-loop for routine hiccups.
                syslog.exception("unhandled error during monitoring cycle")
                jlog.log_event("cycle_error")

            time.sleep(config.interval_seconds)
    except KeyboardInterrupt:
        syslog.info("router-monitor stopping (KeyboardInterrupt)")
        return 0


if __name__ == "__main__":
    sys.exit(main())