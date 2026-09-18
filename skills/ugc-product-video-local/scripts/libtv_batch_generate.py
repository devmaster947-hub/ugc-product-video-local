#!/usr/bin/env python3
"""Create/reuse a LibTV canvas and generate approved UGC video segments."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import video_cli  # noqa: E402


class LibTVBatchError(RuntimeError):
    pass


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse_json_output(raw: str) -> dict[str, Any]:
    for line in reversed(raw.splitlines()):
        try:
            value = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise LibTVBatchError("LibTV CLI 没有返回可解析的 JSON。")


class LibTVClient:
    def __init__(self, executable: Path, cwd: Path):
        self.executable = str(executable)
        self.cwd = cwd

    def call(self, *arguments: str, expect_json: bool = True) -> dict[str, Any] | str:
        completed = subprocess.run(
            [self.executable, *arguments], cwd=self.cwd, text=True, capture_output=True, check=False
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
            raise LibTVBatchError(f"LibTV 命令失败：{' '.join(arguments[:3])}：{detail[:1200]}")
        return parse_json_output(completed.stdout) if expect_json else completed.stdout.strip()

    def account_info(self) -> dict[str, Any]:
        return self.call("account", "info")  # type: ignore[return-value]

    def model_info(self, model: str) -> dict[str, Any]:
        return self.call("model", model)  # type: ignore[return-value]

    def project_info(self, project_uuid: str) -> dict[str, Any]:
        return self.call("project", project_uuid)  # type: ignore[return-value]

    def create_project(self, name: str, workspace_id: int | None) -> str:
        arguments = ["project", "create", name]
        if workspace_id is not None:
            arguments.extend(("--workspace", str(workspace_id)))
        result = self.call(*arguments)
        assert isinstance(result, dict)
        nested = result.get("project") if isinstance(result.get("project"), dict) else {}
        project_uuid = str(
            result.get("uuid") or result.get("projectUuid") or nested.get("uuid") or nested.get("projectUuid") or ""
        ).strip()
        if not project_uuid:
            raise LibTVBatchError("创建 LibTV 画布后未返回 projectUuid。")
        return project_uuid

    def upload(self, project_uuid: str, name: str, file: str, x: int, y: int) -> str:
        result = self.call(
            "upload", name, "--project", project_uuid, "--file", file, "--type", "image", "--x", str(x), "--y", str(y)
        )
        assert isinstance(result, dict)
        node_key = str(result.get("nodeKey") or result.get("newNodeKey") or "").strip()
        if not node_key:
            raise LibTVBatchError(f"上传 {name} 后未返回 nodeKey。")
        return node_key

    def create_video_node(
        self,
        project_uuid: str,
        name: str,
        prompt: str,
        node_keys: list[str],
        model: str,
        duration: int,
        ratio: str,
        resolution: str,
        y: int,
    ) -> str:
        arguments = video_node_arguments(
            project_uuid=project_uuid,
            name=name,
            prompt=prompt,
            ordered_node_keys=node_keys,
            model=model,
            duration=duration,
            ratio=ratio,
            resolution=resolution,
            x=760,
            y=y,
        )
        result = self.call(*arguments)
        assert isinstance(result, dict)
        node_key = str(result.get("nodeKey") or result.get("newNodeKey") or "").strip()
        if not node_key:
            raise LibTVBatchError(f"创建 {name} 后未返回 nodeKey。")
        return node_key

    def run_video_node(self, project_uuid: str, node_key: str) -> dict[str, Any]:
        result = self.call("node", node_key, "--project", project_uuid, "--run")
        assert isinstance(result, dict)
        return result

    def download(self, project_uuid: str, node_key: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = self.call(
            "download", "--project", project_uuid, "--node", node_key, "--out", str(output_dir),
            "--without-ai-watermark", expect_json=False,
        )
        assert isinstance(raw, str)
        candidates = [Path(line.strip()).expanduser() for line in raw.splitlines() if line.strip()]
        file = next((item.resolve() for item in reversed(candidates) if item.is_file()), None)
        if file is None:
            raise LibTVBatchError(f"下载节点 {node_key} 后未找到本地文件。")
        return file


def video_node_arguments(
    *, project_uuid: str, name: str, prompt: str, ordered_node_keys: list[str], model: str,
    duration: int, ratio: str, resolution: str, x: int, y: int,
) -> list[str]:
    arguments = [
        "node", "--x", str(x), "--y", str(y), "create", name,
        "--project", project_uuid, "--type", "video", "--prompt", prompt,
        "--set", f"model={model}", "--set", "modeType=mixed2video",
        "--set", f"ratio={ratio}", "--set", f"resolution={resolution}",
        "--set", f"duration={duration}", "--set", "enableSound=on",
        "--set", "searchEnabled=0", "--set", "autoCompliance=1",
    ]
    for node_key in ordered_node_keys:
        arguments.extend(("--left", node_key))
    return arguments


def bind_prompt(prompt: str, ordered_node_keys: list[str]) -> str:
    references = "、".join(f"{{{{Node {node_key}}}}}" for node_key in ordered_node_keys)
    return f"参考图按以下顺序绑定：{references}。\n\n{prompt.strip()}"


def extract_task(result: dict[str, Any]) -> dict[str, Any]:
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    task = data.get("taskInfo") if isinstance(data, dict) and isinstance(data.get("taskInfo"), dict) else {}
    urls = data.get("url", []) if isinstance(data, dict) else []
    remote_url = str(urls[0]).strip() if isinstance(urls, list) and urls else ""
    if task.get("status") != 2 or not remote_url:
        raise LibTVBatchError(f"LibTV 视频节点未成功完成：status={task.get('status')!r}")
    return {"taskId": str(task.get("taskId", "")), "remoteUrl": remote_url}


def node_index(project: dict[str, Any]) -> dict[str, str]:
    nodes = project.get("nodes") if isinstance(project, dict) else None
    if not isinstance(nodes, list):
        raise LibTVBatchError("LibTV 画布详情缺少 nodes 数组。")
    result: dict[str, str] = {}
    duplicates: set[str] = set()
    for item in nodes:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        node_key = str(item.get("id") or item.get("nodeKey") or "").strip()
        if not name or not node_key:
            continue
        if name in result:
            duplicates.add(name)
        result[name] = node_key
    if duplicates:
        raise LibTVBatchError(f"LibTV 画布存在重复节点名，无法安全恢复：{sorted(duplicates)}")
    return result


def run_nodes_parallel(
    client: LibTVClient, project_uuid: str, nodes: dict[int, str], *, max_workers: int = 3
) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    completed: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(nodes)))) as pool:
        futures = {pool.submit(client.run_video_node, project_uuid, node): segment for segment, node in nodes.items()}
        for future in as_completed(futures):
            segment_id = futures[future]
            try:
                completed[segment_id] = extract_task(future.result())
            except (OSError, ValueError, LibTVBatchError) as exc:
                errors[segment_id] = str(exc)
    return completed, errors


def load_job(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list) or not value["segments"]:
        raise LibTVBatchError("任务 JSON 必须包含非空 segments 数组。")
    for segment in value["segments"]:
        if not isinstance(segment, dict):
            raise LibTVBatchError("每个 Segment 必须是对象。")
        for key in ("segmentId", "duration", "prompt"):
            if key not in segment:
                raise LibTVBatchError(f"Segment 缺少字段：{key}")
        references = segment.get("referenceFiles", [])
        if not isinstance(references, list) or not references:
            raise LibTVBatchError("每个 Segment 至少需要一张参考图。")
    return value


def load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"schemaVersion": "1.0", "provider": "libtv_cli", "uploads": {}, "segments": {}}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LibTVBatchError("LibTV 状态文件必须是 JSON 对象。")
    if value.get("provider") not in (None, "libtv_cli"):
        raise LibTVBatchError(f"任务已锁定 provider={value.get('provider')}，禁止切换。")
    value.setdefault("uploads", {})
    value.setdefault("segments", {})
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    parser.add_argument("--workspace-id", type=int)
    parser.add_argument("--project-uuid")
    parser.add_argument("--generation-approved", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    try:
        job_path = Path(args.job).expanduser().resolve()
        root = job_path.parent
        data = load_job(job_path)
        model = video_cli.libtv_model_name(str(data.get("videoModel", "seedance-2-fast")))
        plans: list[dict[str, Any]] = []
        for segment in data["segments"]:
            files = []
            for value in segment["referenceFiles"]:
                path = Path(str(value)).expanduser().resolve()
                if not path.is_file() or path.stat().st_size <= 0:
                    raise LibTVBatchError(f"参考图不存在或为空：{path}")
                if str(path) not in files:
                    files.append(str(path))
            plans.append({"segment": segment, "files": files})
        if args.plan_only:
            print(json.dumps({"ok": True, "provider": "libtv_cli", "model": model, "plans": plans}, ensure_ascii=False))
            return 0
        if not args.generation_approved:
            raise LibTVBatchError("首次视频生成必须处于用户确认授权内；请传入 --generation-approved。")

        client = LibTVClient(video_cli.resolve_libtv_cli(), root)
        client.account_info()
        model_info = client.model_info(model)
        schema = model_info.get("schema", {}) if isinstance(model_info, dict) else {}
        duration_spec = schema.get("properties", {}).get("duration", {}) if isinstance(schema, dict) else {}
        minimum, maximum = int(duration_spec.get("min", 4)), int(duration_spec.get("max", 15))
        for plan in plans:
            duration = int(plan["segment"]["duration"])
            if not minimum <= duration <= maximum:
                raise LibTVBatchError(f"Segment 时长 {duration} 秒超出 LibTV 模型范围 {minimum}-{maximum} 秒。")

        state_path = root / "libtv-generation.json"
        state = load_state(state_path)
        project_uuid = str(args.project_uuid or state.get("projectUuid") or "").strip()
        if project_uuid:
            project = client.project_info(project_uuid)
        else:
            project_uuid = client.create_project(str(data.get("projectName") or root.name), args.workspace_id)
            project = client.project_info(project_uuid)
        known_nodes = node_index(project)
        state.update({"provider": "libtv_cli", "projectUuid": project_uuid, "model": model, "status": "preparing"})
        atomic_write_json(state_path, state)

        uploads = state["uploads"]
        ordered_files: list[str] = []
        for plan in plans:
            for file in plan["files"]:
                if file not in ordered_files:
                    ordered_files.append(file)
        for index, file in enumerate(ordered_files, 1):
            record = uploads.get(file) if isinstance(uploads.get(file), dict) else {}
            node_key = str(record.get("nodeKey", "")).strip()
            name = str(record.get("name") or f"参考图-{index:02d}")
            if not node_key:
                node_key = known_nodes.get(name, "")
            if not node_key:
                node_key = client.upload(project_uuid, name, file, -760 + ((index - 1) % 3) * 380, ((index - 1) // 3) * 720)
                known_nodes[name] = node_key
            uploads[file] = {"nodeKey": node_key, "name": name}
            atomic_write_json(state_path, state)

        state_segments = state["segments"]
        runnable: dict[int, str] = {}
        for index, plan in enumerate(plans):
            segment = plan["segment"]
            segment_id = int(segment["segmentId"])
            key = str(segment_id)
            record = state_segments.get(key) if isinstance(state_segments.get(key), dict) else {}
            if record.get("status") == "succeeded":
                continue
            if record.get("status") == "run_started_uncertain":
                raise LibTVBatchError(f"Segment {segment_id} 已有状态不确定的 LibTV 任务；禁止自动重提。")
            node_keys = [str(uploads[file]["nodeKey"]) for file in plan["files"]]
            node_key = str(record.get("videoNodeKey", "")).strip()
            node_name = f"S{segment_id:02d}-生成视频"
            if not node_key:
                node_key = known_nodes.get(node_name, "")
            if not node_key:
                node_key = client.create_video_node(
                    project_uuid,
                    node_name,
                    bind_prompt(str(segment["prompt"]), node_keys),
                    node_keys,
                    model,
                    int(segment["duration"]),
                    str(data.get("aspectRatio", "9:16")),
                    str(data.get("resolution", "720p")),
                    index * 720,
                )
                known_nodes[node_name] = node_key
            record.update({"videoNodeKey": node_key, "orderedFiles": plan["files"], "status": "run_started_uncertain"})
            state_segments[key] = record
            runnable[segment_id] = node_key
            atomic_write_json(state_path, state)

        completed, errors = run_nodes_parallel(client, project_uuid, runnable)
        for segment_id, task in completed.items():
            state_segments[str(segment_id)].update(task)
            state_segments[str(segment_id)]["status"] = "succeeded"
            atomic_write_json(state_path, state)
        for segment_id, error in errors.items():
            state_segments[str(segment_id)]["error"] = error
        atomic_write_json(state_path, state)
        if errors:
            raise LibTVBatchError("；".join(f"Segment {key} 状态不确定：{value}" for key, value in sorted(errors.items())) + "。禁止自动重提。")

        output_dir = root / "segments"
        for key in sorted(state_segments, key=int):
            record = state_segments[key]
            local_file = Path(str(record.get("localFile", ""))).expanduser()
            if not local_file.is_file():
                local_file = client.download(project_uuid, str(record["videoNodeKey"]), output_dir)
                record["localFile"] = str(local_file)
                atomic_write_json(state_path, state)
        state["status"] = "segments_completed"
        atomic_write_json(state_path, state)
        print(json.dumps({
            "ok": True,
            "provider": "libtv_cli",
            "projectUuid": project_uuid,
            "canvasUrl": f"https://www.liblib.tv/canvas?projectId={project_uuid}",
            "segments": [state_segments[key] for key in sorted(state_segments, key=int)],
            "state": str(state_path),
        }, ensure_ascii=False))
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, LibTVBatchError, video_cli.VideoCliError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
