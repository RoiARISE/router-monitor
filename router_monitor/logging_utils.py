"""
JSON Lines 形式のイベントログ。

2つの追記専用ファイルを使用する：
  - events_path  : ping 結果サマリ・状態遷移・電源サイクル試行・
                   再起動ロックスキップなど、注目すべき全イベントを記録する。
  - reboots_path : 実際の電源サイクル試行 1回につき 1行。
                   今日・今月・累計の再起動統計を算出するための
                   唯一のデータソース。

どちらも標準的な JSON Lines 形式なので、tail / grep /
任意のツールで解析できる。各行が自己記述型のため、
スキーマ移行の心配も不要。
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, List, Optional


class JsonlLogger:
    def __init__(self, events_path: str, reboots_path: str):
        self.events_path = Path(events_path)
        self.reboots_path = Path(reboots_path)
        # ログファイルの親ディレクトリが存在しない場合は自動作成する
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self.reboots_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _now_iso(self) -> str:
        """現在時刻をローカルタイムゾーン付きの ISO 8601 文字列で返す。"""
        return datetime.now(timezone.utc).astimezone().isoformat()

    def _append(self, path: Path, entry: dict) -> None:
        """JSON Lines ファイルに 1行追記する。I/O例外でプロセスが死なないよう保護する。"""
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            import sys
            print(f"[logging_error] Failed to append to {path}: {exc}", file=sys.stderr)

    def log_event(self, event: str, **fields: Any) -> None:
        """汎用イベントを events.jsonl に記録する。"""
        entry = {"timestamp": self._now_iso(), "event": event, **fields}
        self._append(self.events_path, entry)

    def log_reboot(self, **fields: Any) -> None:
        """電源サイクル成功 1回につき必ず 1回呼び出す。reboots.jsonl に記録する。"""
        entry = {"timestamp": self._now_iso(), "event": "reboot", **fields}
        self._append(self.reboots_path, entry)

    def as_logger_fn(self):
        """ステートマシンが期待する `logger: Callable` シグネチャ
        (logger(event, **fields)) に適合するアダプタを返す。"""
        return self.log_event


def read_jsonl_reverse_iter(path: str, buf_size: int = 8192) -> Iterator[dict]:
    """
    JSON Lines ファイルを末尾から逆順に（新しいものから）読み込んで dict を yield します。
    ファイル全体をメモリにロードせず、ブロック単位で逆読みするため非常に高速かつ省メモリです。
    """
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return

    with open(p, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        remainder = b""

        while pos > 0:
            sz = min(buf_size, pos)
            pos -= sz
            f.seek(pos)
            chunk = f.read(sz) + remainder

            # 行単位で分割。最初の要素は次のチャンクで処理する remainder になる可能性がある
            lines = chunk.split(b"\n")
            remainder = lines[0]

            # lines[1:] を逆順に処理
            for line_bytes in reversed(lines[1:]):
                line = line_bytes.strip()
                if line:
                    try:
                        yield json.loads(line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        pass

        # 最後の remainder を処理
        remainder = remainder.strip()
        if remainder:
            try:
                yield json.loads(remainder.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                pass


def load_jsonl_reverse(path: str, limit: Optional[int] = None) -> List[dict]:
    """
    JSON Lines ファイルを末尾から読み込み、最新の `limit` 件をリストとして返します。
    limit が指定されていない場合は全件を新しい順で返します。
    """
    results = []
    for entry in read_jsonl_reverse_iter(path):
        results.append(entry)
        if limit is not None and len(results) >= limit:
            break
    return results