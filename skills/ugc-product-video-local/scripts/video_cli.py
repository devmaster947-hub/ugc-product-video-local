#!/usr/bin/env python3
"""Select and operate local video CLIs in a fixed, safe provider order."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


PROVIDER_ORDER = ("libtv_cli", "xiaoyunque_cli", "dreamina_cli")
LOCAL_PROVIDERS = {"xiaoyunque_cli", "dreamina_cli"}
LOGICAL_MODELS = {
    "seedance 2 fast": "seedance-2-fast",
    "seedance 2 mini": "seedance-2-mini",
    "seedance 2": "seedance-2",
    "seedance 2 5": "seedance-2-5",
}
PROVIDER_MODEL_IDS = {
    "seedance-2-fast": "seedance2.0fast_vip",
    "seedance-2-mini": "seedance2.0mini_vip",
    "seedance-2": "seedance2.0_vip",
    "seedance-2-5": "seedance2.5",
}
LIBTV_MODEL_NAMES = {
    "seedance-2-fast": "Seedance 2.0 Fast VIP",
    "seedance-2-mini": "Seedance 2.0 Mini",
    "seedance-2": "Seedance 2.0 VIP",
    "seedance-2-5": "Seedance 2.5",
}
PENDING_STATES = {
    "created", "pending", "processing", "running", "queued", "querying", "submitted", "generating",
}
SUCCESS_STATES = {"succeeded", "success", "completed", "complete"}
FAILED_STATES = {"failed", "failure", "fail", "error", "cancelled", "canceled"}


class VideoCliError(RuntimeError):
    pass


def normalize_model(model: str) -> str:
    normalized = " ".join(
        str(model).lower().replace("_", " ").replace("-", " ").replace(".", " ").split()
    )
    value = LOGICAL_MODELS.get(normalized, str(model).strip().lower())
    if value not in PROVIDER_MODEL_IDS:
        raise VideoCliError(f"不支持的视频模型：{model}")
    return value


def provider_model_id(model: str) -> str:
    return PROVIDER_MODEL_IDS[normalize_model(model)]


def libtv_model_name(model: str) -> str:
    return LIBTV_MODEL_NAMES[normalize_model(model)]


def validate_duration(model: str, duration: int | float | str) -> int:
    model_id = normalize_model(model)
    try:
        value = int(float(str(duration)))
    except (TypeError, ValueError):
        raise VideoCliError("视频时长必须是整数。") from None
    maximum = 30 if model_id == "seedance-2-5" else 15
    if value < 4 or value > maximum:
        raise VideoCliError(f"模型 {model_id} 的单段时长必须是 4-{maximum} 秒。")
    return value


def _resolve_executable(
    names: Sequence[str], *, configured: str = "", cli_path: str | os.PathLike[str] | None = None
) -> Path:
    if cli_path is not None:
        candidate = Path(cli_path).expanduser().resolve()
    else:
        discovered = configured or next((path for name in names if (path := shutil.which(name))), "")
        candidate = Path(discovered).expanduser().resolve() if discovered else Path.home() / ".local" / "bin" / names[0]
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise VideoCliError(f"未发现可执行 CLI：{names[0]}")
    if platform.system() != "Windows" and not os.access(candidate, os.X_OK):
        raise VideoCliError(f"CLI 不可执行：{candidate}")
    return candidate.resolve()


def resolve_libtv_cli(*, cli_path: str | os.PathLike[str] | None = None) -> Path:
    names = ("libtv.exe", "libtv") if platform.system() == "Windows" else ("libtv",)
    configured = os.environ.get("LIBTV_CLI", "").strip()
    discovered = configured or next((path for name in names if (path := shutil.which(name))), "")
    if cli_path is None and not discovered:
        official = Path.home() / ".libtv" / ("libtv.exe" if platform.system() == "Windows" else "libtv")
        if official.is_file():
            cli_path = official
    return _resolve_executable(names, configured=configured, cli_path=cli_path)


def resolve_xiaoyunque_cli(*, cli_path: str | os.PathLike[str] | None = None) -> Path:
    names = (
        ("xiaoyunque.exe", "xiao-yunque.exe", "xiaoyunque-cli.exe")
        if platform.system() == "Windows"
        else ("xiaoyunque", "xiao-yunque", "xiaoyunque-cli")
    )
    return _resolve_executable(
        names, configured=os.environ.get("XIAOYUNQUE_CLI", "").strip(), cli_path=cli_path
    )


def resolve_dreamina_cli(*, cli_path: str | os.PathLike[str] | None = None) -> Path:
    names = ("dreamina.exe", "dreamina") if platform.system() == "Windows" else ("dreamina",)
    return _resolve_executable(
        names, configured=os.environ.get("DREAMINA_CLI", "").strip(), cli_path=cli_path
    )


def _run_process(executable: Path, arguments: Sequence[str], *, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [str(executable), *map(str, arguments)],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise VideoCliError(f"CLI 调用超时（{timeout:g} 秒）。") from None
    except OSError as exc:
        raise VideoCliError(f"无法启动 CLI：{exc}") from None


def _parse_json(raw: str, provider: str) -> Any:
    text = raw.strip()
    if not text:
        raise VideoCliError(f"{provider} CLI 返回空响应。")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line.strip())
            except json.JSONDecodeError:
                continue
    raise VideoCliError(f"{provider} CLI 返回内容不是有效 JSON。")


def _run_json(executable: Path, arguments: Sequence[str], *, provider: str, timeout: float = 600.0) -> Any:
    completed = _run_process(executable, arguments, timeout=timeout)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        if "login" in detail.lower() or "unauthor" in detail.lower():
            raise VideoCliError(f"{provider} CLI 尚未登录：{detail[:1000]}")
        raise VideoCliError(f"{provider} CLI 退出码 {completed.returncode}：{detail[:1000]}")
    return _parse_json(completed.stdout, provider)


def libtv_cli_available(*, cli_path: str | os.PathLike[str] | None = None) -> bool:
    try:
        executable = resolve_libtv_cli(cli_path=cli_path)
        return _run_process(executable, ["--help"], timeout=30).returncode == 0
    except (VideoCliError, OSError, subprocess.SubprocessError):
        return False


def _local_executable(provider: str) -> Path:
    if provider == "xiaoyunque_cli":
        return resolve_xiaoyunque_cli()
    if provider == "dreamina_cli":
        return resolve_dreamina_cli()
    raise VideoCliError(f"未知本地视频渠道：{provider}")


def local_cli_supports_model(provider: str, model: str) -> bool:
    try:
        executable = _local_executable(provider)
        submit_help = _run_process(executable, ["multimodal2video", "--help"], timeout=30)
        query_help = _run_process(executable, ["query_result", "--help"], timeout=30)
    except (VideoCliError, OSError, subprocess.SubprocessError):
        return False
    submit_text = f"{submit_help.stdout}\n{submit_help.stderr}"
    query_text = f"{query_help.stdout}\n{query_help.stderr}"
    required = ("--image", "--prompt", "--duration", "--ratio", "--video_resolution", "--model_version")
    return (
        submit_help.returncode == 0
        and query_help.returncode == 0
        and provider_model_id(model) in submit_text
        and all(token in submit_text for token in required)
        and "--submit_id" in query_text
    )


def preflight(model: str) -> dict[str, Any]:
    """Short-circuit on the first usable provider without touching lower-priority CLIs."""
    normalized = normalize_model(model)
    if libtv_cli_available():
        return {
            "priority": list(PROVIDER_ORDER),
            "selected": "libtv_cli",
            "availability": {"libtv_cli": True, "xiaoyunque_cli": None, "dreamina_cli": None},
            "executable": str(resolve_libtv_cli()),
            "model": normalized,
            "lowerPriorityChecksSkipped": True,
        }
    availability: dict[str, bool | None] = {"libtv_cli": False}
    for provider in PROVIDER_ORDER[1:]:
        available = local_cli_supports_model(provider, normalized)
        availability[provider] = available
        if available:
            for lower in PROVIDER_ORDER[PROVIDER_ORDER.index(provider) + 1:]:
                availability[lower] = None
            return {
                "priority": list(PROVIDER_ORDER),
                "selected": provider,
                "availability": availability,
                "executable": str(_local_executable(provider)),
                "model": normalized,
                "lowerPriorityChecksSkipped": True,
            }
    return {
        "priority": list(PROVIDER_ORDER),
        "selected": None,
        "availability": availability,
        "executable": "",
        "model": normalized,
        "lowerPriorityChecksSkipped": False,
    }


def _reference_files(values: Iterable[str | os.PathLike[str]] | None, maximum: int) -> list[Path]:
    result: list[Path] = []
    for value in values or []:
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise VideoCliError(f"参考文件不存在或为空：{path}")
        if path not in result:
            result.append(path)
    if len(result) > maximum:
        raise VideoCliError(f"参考图数量超过模型上限 {maximum}。")
    return result


def _task_id(value: Any) -> str:
    source = value.get("data", value) if isinstance(value, dict) else value
    while isinstance(source, dict) and isinstance(source.get("result"), dict):
        source = source["result"]
    identifier = source.get("submit_id", source.get("taskId", source.get("id"))) if isinstance(source, dict) else None
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier).strip():
        raise VideoCliError("提交响应缺少任务 ID。")
    return str(identifier).strip()


def submit_local_video(
    provider: str,
    model: str,
    prompt: str,
    duration: int | float | str,
    *,
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
) -> str:
    if provider not in LOCAL_PROVIDERS:
        raise VideoCliError(f"本函数不支持渠道：{provider}")
    if not isinstance(prompt, str) or not prompt.strip():
        raise VideoCliError("视频 Prompt 不能为空。")
    model_id = normalize_model(model)
    duration_value = validate_duration(model_id, duration)
    maximum = 30 if model_id == "seedance-2-5" else 9
    files = _reference_files(reference_files, maximum)
    command = "multimodal2video" if files else "text2video"
    arguments = [
        command,
        "--prompt", prompt.strip(),
        "--duration", str(duration_value),
        "--ratio", aspect_ratio,
        "--video_resolution", resolution,
        "--model_version", provider_model_id(model_id),
    ]
    for path in files:
        arguments.extend(("--image", str(path)))
    value = _run_json(_local_executable(provider), arguments, provider=provider)
    return _task_id(value)


def fetch_local_video(provider: str, task_id: str) -> Any:
    if provider not in LOCAL_PROVIDERS:
        raise VideoCliError(f"未知本地视频渠道：{provider}")
    if not str(task_id).strip():
        raise VideoCliError("任务 ID 不能为空。")
    return _run_json(
        _local_executable(provider),
        ["query_result", "--submit_id", str(task_id).strip()],
        provider=provider,
        timeout=300,
    )


def _walk_status(value: Any) -> str:
    queue = [value]
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            state = current.get("gen_status") or current.get("status") or current.get("state") or current.get("task_status")
            if isinstance(state, str) and state.strip():
                return state.strip().lower()
            queue.extend(item for item in current.values() if isinstance(item, (dict, list)))
        elif isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))
    return ""


def poll_local_video(
    provider: str,
    task_id: str,
    *,
    interval: float = 5.0,
    timeout: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = fetch_local_video(provider, task_id)
        state = _walk_status(value)
        if state in SUCCESS_STATES:
            return value
        if state in FAILED_STATES:
            raise VideoCliError(f"任务 {task_id} 失败：{state}")
        if state not in PENDING_STATES:
            raise VideoCliError(f"任务 {task_id} 返回未知状态：{state or '缺失'}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise VideoCliError(f"任务 {task_id} 轮询超时。")
        sleep(min(interval, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("preflight")
    command.add_argument("--model", required=True)
    args = parser.parse_args()
    if args.command == "preflight":
        print(json.dumps(preflight(args.model), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
