"""Versioned JSON edits; Blender and its geometry never enter this module."""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from engine.params import normalize_params
from tools.semantic import EditError


def dump(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic_write(path, raw):
    descriptor, temporary = tempfile.mkstemp(prefix=".m4-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def changes(old, new):
    return [{"parameter": key, "before": old.get(key), "after": new.get(key)}
            for key in new if key not in ("revision", "schema_version") and old.get(key) != new.get(key)]


class DesignStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.designs = self.root / "designs"
        self.current = self.designs / "current.json"
        self.history = self.designs / "history"
        self.changelog = self.designs / "CHANGELOG.md"
        self.operations = self.designs / "operations.jsonl"

    @contextmanager
    def locked(self, *, readonly=False):
        path = self.designs / ".edit.lock"
        if readonly and not path.exists():
            yield
            return
        with path.open("rb" if readonly else "a+b") as handle:
            try:
                fcntl.flock(handle, (fcntl.LOCK_SH if readonly else fcntl.LOCK_EX) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EditError("已有修改或生成正在运行，请等它结束后再执行本轮操作。")
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def read_current(self):
        raw = self.current.read_bytes()
        values = json.loads(raw)
        effective, warnings = normalize_params(values)
        return raw, effective, warnings

    def revisions(self):
        _, current, _ = self.read_current()
        values = {current["revision"]}
        for path in self.history.glob("v*.json"):
            if path.stem[1:].isdigit():
                values.add(int(path.stem[1:]))
        return sorted(values)

    def read_revision(self, revision):
        _, current, _ = self.read_current()
        if revision == current["revision"]:
            return current
        path = self.history / f"v{revision:03d}.json"
        if not path.is_file():
            raise EditError(f"找不到 v{revision:03d}，现有版本：" + ", ".join(f"v{value:03d}" for value in self.revisions()))
        values = json.loads(path.read_bytes())
        if values.get("revision") != revision:
            raise EditError("历史文件中的版本号与文件名不一致：" + path.name)
        return normalize_params(values)[0]

    def read_operations(self):
        if not self.operations.exists():
            return []
        return [json.loads(line) for line in self.operations.read_text(encoding="utf-8").splitlines() if line.strip()]

    def undo_target(self, revision):
        operations = self.read_operations()
        record = next((item for item in reversed(operations) if item["revision"] == revision), None)
        if record is not None:
            return record["undo_target_revision"]
        earlier = [value for value in self.revisions() if value < revision]
        return max(earlier) if earlier else None

    def save(self, candidate, *, action, reason, restored_from=None):
        """Call under the exclusive lock; preserve exact old bytes first.

        A failed build retains its edited version, which remains undoable.
        Metadata-writing failures restore the original active bytes.
        """
        raw, before, _ = self.read_current()
        candidate, clamps = normalize_params(candidate)
        delta = changes(before, candidate)
        if not delta:
            return before, None, [], clamps
        records = self.read_operations()
        current_record = next((item for item in reversed(records) if item["revision"] == before["revision"]), None)
        if current_record and current_record["design_sha256"] != digest(raw):
            raise EditError("当前版本被手工改写，和版本记录不一致；请先保留该改动的新版本，避免覆盖历史。")
        backup = self.history / f"v{before['revision']:03d}.json"
        self.history.mkdir(exist_ok=True)
        if backup.exists():
            if backup.read_bytes() != raw:
                raise EditError("历史备份已存在且内容不同，拒绝覆盖：" + backup.name)
        else:
            with backup.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        candidate["revision"] = max(self.revisions() + [item["revision"] for item in records]) + 1
        parent = self.undo_target(restored_from) if action == "undo" else before["revision"]
        serialized = dump(candidate)
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        record = {"revision": candidate["revision"], "previous_revision": before["revision"],
                  "action": action, "restored_from": restored_from, "undo_target_revision": parent,
                  "reason": reason, "changes": delta, "design_sha256": digest(serialized),
                  "previous_sha256": digest(raw), "created_at": now.isoformat()}
        old_log = self.changelog.read_bytes() if self.changelog.exists() else b""
        old_records = self.operations.read_bytes() if self.operations.exists() else None
        header = "# 设计变更记录\n\n| 版本 | 日期 | 修改 | 原因 |\n| --- | --- | --- | --- |\n"
        description = "; ".join(f"{item['parameter']}: {item['before']} → {item['after']}" for item in delta)
        if restored_from is not None:
            description = f"{('撤销并恢复' if action == 'undo' else '回到')} v{restored_from:03d} 的参数；" + description
        clean = lambda value: str(value).replace("|", "／").replace("\n", " ").replace("\r", " ")
        line = f"| v{candidate['revision']:03d} | {now.date()} | {clean(description)} | {clean(reason)}；修改前逐字节备份 `history/{backup.name}` |\n"
        try:
            log_base = old_log or header.encode("utf-8")
            if not log_base.endswith(b"\n"):
                log_base += b"\n"
            records_base = old_records or b""
            if records_base and not records_base.endswith(b"\n"):
                records_base += b"\n"
            atomic_write(self.changelog, log_base + line.encode("utf-8"))
            atomic_write(self.operations, records_base + (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))
            atomic_write(self.current, serialized)
        except OSError:
            atomic_write(self.current, raw)
            atomic_write(self.changelog, old_log)
            if old_records is None:
                self.operations.unlink(missing_ok=True)
            else:
                atomic_write(self.operations, old_records)
            raise
        return candidate, record, delta, clamps
