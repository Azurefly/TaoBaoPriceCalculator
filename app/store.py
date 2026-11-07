"""Local JSON persistence with full-document CAS and rotating, validated backups."""
from __future__ import annotations
import hashlib
import json
import os
import re
import threading
import shutil
import time
from decimal import Decimal
from pathlib import Path
from .models import Project


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / 'project.json'
        self.backups = directory / 'backups'
        self.lock = threading.RLock()

    @staticmethod
    def revision(project: Project) -> str:
        raw = json.dumps(project.model_dump(mode='json'), sort_keys=True,
                         ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(raw.encode()).hexdigest()

    def load(self) -> Project:
        with self.lock:
            if not self.path.exists():
                return Project()
            try:
                return Project.model_validate_json(self.path.read_text(encoding='utf-8'))
            except (ValueError, OSError) as exc:
                raise ValueError('项目文件损坏或不可读；未覆盖原文件。请在“数据与说明”恢复备份。') from exc

    def snapshot(self) -> tuple[Project, str]:
        with self.lock:
            project = self.load()
            return project, self.revision(project)

    def assert_revision(self, expected: str | None):
        if expected is not None and self.revision(self.load()) != expected:
            raise ConflictError('项目或报价已被另一操作修改；未覆盖新数据。请刷新后重试。')

    @staticmethod
    def _atomic_write(path: Path, content: str):
        temp = path.with_name(path.name + '.tmp')
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _backup_files(self) -> list[Path]:
        # Numeric ordering also handles old names and clocks moving backwards.
        entries = []
        for path in self.backups.glob('project-*.json'):
            match = re.fullmatch(r'project-(\d+)\.json', path.name)
            if match and path.is_file():
                entries.append((int(match[1]), path))
        return [path for _, path in sorted(entries, key=lambda entry: entry[0])]

    def _next_backup_path(self) -> Path:
        # time_ns has coarse resolution on some systems. Never reuse a filename,
        # including after an application restart or wall-clock rollback.
        existing = self._backup_files()
        latest = int(existing[-1].stem.split('-')[1]) if existing else -1
        serial = max(0, time.time_ns(), latest + 1)
        return self.backups / f'project-{serial}.json'

    def save(self, project: Project, expected_revision: str | None = None):
        with self.lock:
            self.assert_revision(expected_revision)
            project = Project.model_validate(project.model_dump())
            if self.path.exists():
                # Preserve the previous valid state before replacing; corrupt files are not silently saved over.
                previous = self.load()
                self.backups.mkdir(exist_ok=True, mode=0o700)
                self._atomic_write(self._next_backup_path(),
                                   previous.model_dump_json(indent=2))
                for old in self._backup_files()[:-20]:
                    old.unlink()
            self._atomic_write(self.path, project.model_dump_json(indent=2))
            return self.revision(project)

    def list_backups(self) -> list[dict]:
        with self.lock:
            return [{'name': p.name, 'size': p.stat().st_size, 'modified_ns': p.stat().st_mtime_ns}
                    for p in reversed(self._backup_files())]

    def storage_hash(self) -> str:
        with self.lock:
            raw = self.path.read_bytes() if self.path.exists() else b''
            return hashlib.sha256(raw).hexdigest()

    def restore(self, name: str, expected_revision: str | None = None,
                expected_storage_hash: str | None = None) -> str:
        if not re.fullmatch(r'project-\d+\.json', name):
            raise ValueError('备份文件名无效')
        with self.lock:
            path = self.backups / name
            if not path.is_file():
                raise ValueError('备份不存在')
            if expected_storage_hash is not None and self.storage_hash() != expected_storage_hash:
                raise ConflictError('备份列表读取后项目文件已变化；请重新读取备份列表。')
            restored = Project.model_validate_json(path.read_text(encoding='utf-8'))
            try:
                self.load()
            except ValueError:
                if expected_revision is not None:
                    raise ConflictError('当前项目损坏，不能按旧版本恢复；请读取备份列表后使用文件校验恢复。')
                archive = self.directory / f'corrupt-project-{time.time_ns()}.json'
                shutil.copyfile(self.path, archive)
                try:
                    os.chmod(archive, 0o600)
                except OSError:
                    pass
                self._atomic_write(self.path, restored.model_dump_json(indent=2))
                return self.revision(restored)
            return self.save(restored, expected_revision)


def parse_coupon_text(text: str) -> list[dict]:
    """Extract literal, untrusted drafts. Never infer ownership, scope, or stacking."""
    out = []
    common = {'confirmed': False,
              'warning': '仅提取文案；真实实例、门槛口径、适用范围、有效期、渠道、互斥均须核实'}
    num = r'(\d+(?:\.\d{1,2})?)(?![\d.])'
    for m in re.finditer(r'(每)?满\s*' + num + r'\s*(?:元)?\s*(?:减|[-－])\s*' + num, text):
        out.append({**common, 'kind': 'repeat_threshold' if m[1] else 'fixed',
                    'threshold_cents': int(Decimal(m[2])*100),
                    'amount_cents': int(Decimal(m[3])*100), 'text': m[0]})
    for m in re.finditer(r'(?<![\d.])(\d(?:\.\d{1,2})?)\s*折(?:[^\n，,；;]{0,12}?(?:最高减|封顶)\s*' + num + r'\s*元?)?', text):
        discount = Decimal(m[1])
        if not 0 < discount < 10:
            continue
        out.append({**common, 'kind': 'percent', 'discount_bps': int((10-discount)*1000),
                    'cap_cents': int(Decimal(m[2])*100) if m[2] else None, 'text': m[0]})
    return out or [{'confirmed': False, 'warning': '未找到明确满减/折扣结构；不把红包汇总额当余额。'}]
