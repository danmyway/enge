import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


SCHEMA_VERSION = 1


class ManifestWriter:
    def __init__(
        self,
        run_id: str,
        command: str,
        argv: List[str],
        tags: Optional[List[str]] = None,
        parent_run_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ):
        self.run_id = run_id
        self.command = command
        self.argv = list(argv)
        self.tags = list(tags) if tags else []
        self.parent_run_id = parent_run_id
        self.context = dict(context) if context else {}
        self.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._requests: List[Dict[str, Any]] = []

    def add_request(
        self,
        task_id: str,
        *,
        set_name: Optional[str] = None,
        tier: Optional[str] = None,
        arch: Optional[str] = None,
        plan: Optional[str] = None,
        source_compose: Optional[str] = None,
        target_compose: Optional[str] = None,
        artifacts_url: Optional[str] = None,
        dispatched_at: Optional[str] = None,
    ) -> None:
        self._requests.append(
            {
                "task_id": task_id,
                "set": set_name,
                "tier": tier,
                "arch": arch,
                "plan": plan,
                "source_compose": source_compose,
                "target_compose": target_compose,
                "artifacts_url": artifacts_url,
                "dispatched_at": dispatched_at
                or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "command": self.command,
            "argv": self.argv,
            "tags": self.tags,
            "parent_run_id": self.parent_run_id,
            "origin": "native",
            "context": self.context,
            "requests": list(self._requests),
        }

    def flush(self, runs_dir: Path, latest_path: Path) -> Path:
        runs_dir.mkdir(parents=True, exist_ok=True)

        manifest_path = runs_dir / f"{self.run_id}.json"
        tmp_path = runs_dir / f"{self.run_id}.json.tmp"
        tmp_path.write_text(json.dumps(self.to_dict(), indent=2))
        os.rename(tmp_path, manifest_path)

        latest_path.parent.mkdir(parents=True, exist_ok=True)
        latest_tmp = latest_path.parent / f"{latest_path.name}.tmp"
        latest_tmp.write_text(str(manifest_path))
        os.rename(latest_tmp, latest_path)

        return manifest_path


class ManifestReader:
    @staticmethod
    def load(path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text())

    @staticmethod
    def load_latest(latest_path: Path) -> Optional[Dict[str, Any]]:
        if not latest_path.exists():
            return None
        manifest_path = Path(latest_path.read_text().strip())
        if not manifest_path.exists():
            return None
        return ManifestReader.load(manifest_path)

    @staticmethod
    def list_runs(runs_dir: Path) -> List[Dict[str, Any]]:
        if not runs_dir.exists():
            return []
        summaries = []
        for p in runs_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            summaries.append(
                {
                    "run_id": data.get("run_id"),
                    "created_at": data.get("created_at"),
                    "command": data.get("command"),
                    "tags": data.get("tags", []),
                    "origin": data.get("origin", "native"),
                    "context": data.get("context", {}),
                    "request_count": len(data.get("requests", [])),
                    "parent_run_id": data.get("parent_run_id"),
                    "path": str(p),
                }
            )
        summaries.sort(key=lambda s: s.get("run_id", ""), reverse=True)
        return summaries

    @staticmethod
    def find_runs(
        runs_dir: Path,
        *,
        set_name: Optional[str] = None,
        tier: Optional[str] = None,
        arch: Optional[str] = None,
        tag: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        all_runs = ManifestReader.list_runs(runs_dir)
        results = []
        for summary in all_runs:
            ctx_set_matches = (
                not set_name or summary.get("context", {}).get("set") == set_name
            )
            if tag and tag not in summary.get("tags", []):
                continue
            created = summary.get("created_at", "")
            if created:
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                except ValueError:
                    dt = None
                if dt:
                    if since and dt < since:
                        continue
                    if until and dt > until:
                        continue
            if not ctx_set_matches or tier or arch:
                full = ManifestReader.load(Path(summary["path"]))
                requests = full.get("requests", [])
                if not ctx_set_matches and not any(
                    r.get("set") == set_name for r in requests
                ):
                    continue
                if tier and not any(r.get("tier") == tier for r in requests):
                    continue
                if arch and not any(r.get("arch") == arch for r in requests):
                    continue
            results.append(summary)
        return results

    @staticmethod
    def get_run(runs_dir: Path, run_id: str) -> Dict[str, Any]:
        from enge.utils.errors import ValidationError

        path = runs_dir / f"{run_id}.json"
        if path.exists():
            return ManifestReader.load(path)
        if not runs_dir.exists():
            raise ValidationError(f"No run matching '{run_id}'")
        matches = [p for p in runs_dir.glob("*.json") if p.stem.startswith(run_id)]
        if len(matches) == 1:
            return ManifestReader.load(matches[0])
        if len(matches) > 1:
            ids = ", ".join(sorted(p.stem for p in matches))
            raise ValidationError(
                f"'{run_id}' matches {len(matches)} runs: {ids}; use a longer prefix"
            )
        raise ValidationError(f"No run matching '{run_id}'")

    @staticmethod
    def get_task_ids(manifest: Dict[str, Any]) -> List[str]:
        return [r["task_id"] for r in manifest.get("requests", []) if r.get("task_id")]
