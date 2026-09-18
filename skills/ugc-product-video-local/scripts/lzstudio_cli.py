#!/usr/bin/env python3
"""Official Dreamina CLI adapter for ugc-product-video-local.

The historical filename is retained for compatibility. This module does not use
LZStudio, Lingzhi services, or API keys.
"""

from __future__ import annotations

import json
import math
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import Request, urlopen


PENDING_STATES = {
    "created",
    "pending",
    "processing",
    "running",
    "queued",
    "querying",
    "submitted",
}
SUCCESS_STATES = {"succeeded", "success", "completed", "complete"}
FAILED_STATES = {"failed", "failure", "error", "cancelled", "canceled"}
VIDEO_MODEL_IDS = {
    "seedance 2 mini": "seedance-2-mini",
    "seedance 2 fast": "seedance-2-fast",
    "seedance 2": "seedance-2",
    "seedance 2 5": "seedance-2-5",
}
OFFICIAL_VIDEO_MODEL_IDS = {
    "seedance-2-mini": "seedance2.0mini_vip",
    "seedance-2-fast": "seedance2.0fast_vip",
    "seedance-2": "seedance2.0_vip",
    "seedance-2-5": "seedance2.5",
}
MEDIA_DOWNLOAD_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


class DreaminaError(RuntimeError):
    """Raised for official Dreamina CLI and media failures."""


# Backwards-compatible exception name for existing local callers.
LzStudioError = DreaminaError


def resolve_official_cli(
    *, cli_path: str | os.PathLike[str] | None = None, system: str | None = None
) -> Path:
    system_name = system or platform.system()
    if cli_path is not None:
        candidate = Path(cli_path).expanduser().resolve()
    else:
        names = ("dreamina.exe", "dreamina") if system_name == "Windows" else ("dreamina",)
        discovered = next((shutil.which(name) for name in names if shutil.which(name)), None)
        if discovered:
            candidate = Path(discovered).resolve()
        else:
            local_bin = Path.home() / ".local" / "bin"
            candidates = [local_bin / name for name in names]
            candidate = next((p.resolve() for p in candidates if p.is_file()), candidates[0])
    if not candidate.is_file() or candidate.stat().st_size <= 0:
        raise DreaminaError("未发现可执行的官方 Dreamina CLI；请先安装 dreamina。")
    if system_name != "Windows" and not os.access(candidate, os.X_OK):
        raise DreaminaError("官方 Dreamina CLI 不可执行。")
    return candidate


def _parse_json(raw: str) -> Any:
    text = raw.strip()
    if not text:
        raise DreaminaError("官方 Dreamina CLI 返回了空响应。")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for line in reversed(text.splitlines()):
            try:
                return json.loads(line.strip())
            except json.JSONDecodeError:
                continue
    raise DreaminaError("官方 Dreamina CLI 返回的内容不是有效 JSON。")


def run_official_cli(
    arguments: Sequence[str],
    *,
    cli_path: str | os.PathLike[str] | None = None,
    timeout: float = 600.0,
) -> Any:
    executable = resolve_official_cli(cli_path=cli_path)
    try:
        completed = subprocess.run(
            [str(executable), *map(str, arguments)],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise DreaminaError(f"官方 Dreamina CLI 调用超时（{timeout:g} 秒）。") from None
    except OSError as exc:
        raise DreaminaError(f"无法启动官方 Dreamina CLI：{exc}") from None
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        if "operation not permitted" in detail.lower() or "permission denied" in detail.lower():
            raise DreaminaError("当前环境无法访问 Dreamina 本地登录状态；请允许在沙箱外运行官方 CLI。")
        if "login" in detail.lower() or "unauthor" in detail.lower():
            raise DreaminaError("官方 Dreamina CLI 尚未登录；请运行 dreamina login 完成 OAuth 登录。")
        raise DreaminaError(f"官方 Dreamina CLI 退出码 {completed.returncode}：{detail[:2000]}")
    return _parse_json(completed.stdout)


def _video_model_id(model: str) -> str:
    normalized = " ".join(
        str(model).lower().replace("_", " ").replace("-", " ").replace(".", " ").split()
    )
    return VIDEO_MODEL_IDS.get(normalized, str(model).strip())


def _task_id(value: Any) -> str:
    source = value.get("data", value) if isinstance(value, dict) else value
    identifier = (
        source.get("submit_id", source.get("taskId", source.get("id")))
        if isinstance(source, dict)
        else None
    )
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        raise DreaminaError("官方提交响应缺少 submit_id。")
    identifier = str(identifier).strip()
    if not identifier:
        raise DreaminaError("官方提交响应缺少 submit_id。")
    return identifier


def _state(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    source = value.get("data", value)
    if not isinstance(source, dict):
        return ""
    state = source.get("gen_status") or source.get("status") or source.get("state")
    return state.strip().lower() if isinstance(state, str) else ""


def _failure(value: Any) -> str:
    if isinstance(value, dict):
        source = value.get("data", value)
        if isinstance(source, dict):
            for key in ("fail_reason", "errorMessage", "message", "error", "detail"):
                message = source.get(key)
                if isinstance(message, str) and message.strip():
                    return message.strip()[:2000]
    return "任务失败。"


def get_credit_balance(**kwargs: Any) -> int | float:
    value = run_official_cli(["user_credit"], timeout=300, **kwargs)
    source = value.get("data", value) if isinstance(value, dict) else value
    balance = source.get("total_credit") if isinstance(source, dict) else None
    if isinstance(balance, bool) or not isinstance(balance, (int, float)) or not math.isfinite(balance):
        raise DreaminaError("官方积分响应缺少有效的 total_credit。")
    return balance


def fetch_task(task_id: str, **kwargs: Any) -> Any:
    if not str(task_id).strip():
        raise DreaminaError("任务 ID 不能为空。")
    return run_official_cli(
        ["query_result", "--submit_id", str(task_id).strip()], timeout=300, **kwargs
    )


def poll_task(
    task_id: str,
    *,
    interval: float = 20.0,
    timeout: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        value = fetch_task(task_id, **kwargs)
        state = _state(value)
        if state in SUCCESS_STATES:
            return value
        if state in FAILED_STATES:
            raise DreaminaError(_failure(value))
        if state not in PENDING_STATES:
            raise DreaminaError(f"query_result 返回未知任务状态：{state or '缺失'}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DreaminaError(f"任务 {task_id} 轮询超时。")
        sleep(min(interval, remaining))


def _local_files(values: Iterable[str | os.PathLike[str]] | None) -> list[Path]:
    result: list[Path] = []
    for value in values or []:
        path = Path(value).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise DreaminaError(f"参考文件不存在或为空：{path}")
        if path not in result:
            result.append(path)
    if len(result) > 30:
        raise DreaminaError("参考图数量超过官方 Dreamina CLI 上限。")
    return result


def _official_duration(model_id: str, duration: int | float | str) -> int:
    try:
        value = int(float(str(duration)))
    except (TypeError, ValueError):
        raise DreaminaError("视频时长必须是整数。") from None
    maximum = 30 if model_id == "seedance-2-5" else 15
    if value < 4 or value > maximum:
        raise DreaminaError(f"模型 {model_id} 的单段时长必须是 4-{maximum} 秒。")
    return value


def submit_image(
    prompt: str,
    *,
    model: str = "5.0",
    aspect_ratio: str = "9:16",
    resolution: str = "2k",
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    **kwargs: Any,
) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise DreaminaError("图片 Prompt 不能为空。")
    files = _local_files(reference_files)
    command = "image2image" if files else "text2image"
    arguments = [
        command,
        "--prompt", prompt.strip(),
        "--ratio", aspect_ratio,
        "--resolution_type", resolution,
        "--model_version", model,
        "--generate_num", "1",
    ]
    for path in files:
        arguments.extend(["--images", str(path)])
    value = run_official_cli(
        arguments,
        timeout=600,
        **kwargs,
    )
    return _task_id(value)


def poll_image(task_id: str, **kwargs: Any) -> dict[str, Any]:
    interval = kwargs.pop("interval", 20.0)
    poll_timeout = kwargs.pop("poll_timeout", 1800.0)
    value = poll_task(task_id, interval=interval, timeout=poll_timeout, **kwargs)
    return {"taskId": task_id, "mimeType": "image/png", "response": value}


def submit_official_video(
    model: str,
    prompt: str,
    duration: int | float | str,
    *,
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    **kwargs: Any,
) -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise DreaminaError("视频 Prompt 不能为空。")
    model_id = _video_model_id(model)
    model_version = OFFICIAL_VIDEO_MODEL_IDS.get(model_id)
    if model_version is None:
        raise DreaminaError(f"不支持的视频模型：{model_id}")
    duration_value = _official_duration(model_id, duration)
    files = _local_files(reference_files)
    command = "multimodal2video" if files else "text2video"
    arguments = [
        command,
        "--prompt", prompt.strip(),
        "--duration", str(duration_value),
        "--ratio", aspect_ratio,
        "--video_resolution", resolution,
        "--model_version", model_version,
    ]
    for path in files:
        arguments.extend(["--image", str(path)])
    return _task_id(run_official_cli(arguments, timeout=600, **kwargs))


def poll_official_video(task_id: str, **kwargs: Any) -> dict[str, Any]:
    interval = kwargs.pop("interval", 20.0)
    poll_timeout = kwargs.pop("poll_timeout", 3600.0)
    value = poll_task(task_id, interval=interval, timeout=poll_timeout, **kwargs)
    return {"taskId": task_id, "mimeType": "video/mp4", "response": value}


def generate_video(
    model: str,
    prompt: str,
    duration: int | float | str,
    *,
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    aspect_ratio: str = "9:16",
    resolution: str = "720p",
    official_cli_path: str | os.PathLike[str] | None = None,
    poll_interval: float = 20.0,
    poll_timeout: float = 3600.0,
    sleep: Callable[[float], None] = time.sleep,
    on_submitted: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if official_cli_path is not None:
        options["cli_path"] = official_cli_path
    task_id = submit_official_video(
        model,
        prompt,
        duration,
        reference_files=reference_files,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        **options,
    )
    if on_submitted is not None:
        on_submitted("official_cli", task_id)
    video = poll_official_video(
        task_id,
        interval=poll_interval,
        poll_timeout=poll_timeout,
        sleep=sleep,
        **options,
    )
    return {"provider": "official_cli", "taskId": task_id, "video": video}


def _http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def download_media(
    media: Mapping[str, Any] | str,
    output_path: str | os.PathLike[str],
    *,
    timeout: float = 600.0,
    cli_path: str | os.PathLike[str] | None = None,
) -> Path:
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 0:
        return destination

    url = media if isinstance(media, str) else media.get("url")
    if _http_url(url):
        temporary = destination.with_name(destination.name + ".part")
        request = Request(str(url), headers={"User-Agent": MEDIA_DOWNLOAD_USER_AGENT, "Accept": "*/*"})
        try:
            with urlopen(request, timeout=timeout) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            if temporary.stat().st_size <= 0:
                raise DreaminaError("下载的媒体文件为空。")
            temporary.replace(destination)
            return destination
        except Exception as exc:
            temporary.unlink(missing_ok=True)
            if isinstance(exc, DreaminaError):
                raise
            raise DreaminaError(f"无法下载媒体结果：{exc}") from None

    task_id = media.get("taskId") if isinstance(media, Mapping) else None
    if not isinstance(task_id, (str, int)) or not str(task_id).strip():
        raise DreaminaError("媒体结果缺少 URL 或 taskId。")
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        download_dir = Path(directory)
        run_official_cli(
            ["query_result", "--submit_id", str(task_id).strip(), "--download_dir", str(download_dir)],
            cli_path=cli_path,
            timeout=timeout,
        )
        suffix = destination.suffix.lower()
        candidates = [p for p in download_dir.rglob("*") if p.is_file() and (not suffix or p.suffix.lower() == suffix)]
        if not candidates:
            candidates = [p for p in download_dir.rglob("*") if p.is_file()]
        if not candidates:
            raise DreaminaError("官方 Dreamina CLI 未下载到媒体文件。")
        shutil.move(str(candidates[0]), destination)
    if destination.stat().st_size <= 0:
        destination.unlink(missing_ok=True)
        raise DreaminaError("下载的媒体文件为空。")
    return destination


def generate_creator_image(
    prompt: str,
    output_path: str | os.PathLike[str],
    *,
    model: str = "5.0",
    aspect_ratio: str = "9:16",
    resolution: str = "2k",
    reference_files: Iterable[str | os.PathLike[str]] | None = None,
    cli_path: str | os.PathLike[str] | None = None,
    poll_interval: float = 20.0,
    poll_timeout: float = 1800.0,
    sleep: Callable[[float], None] = time.sleep,
    on_submitted: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if cli_path is not None:
        options["cli_path"] = cli_path
    task_id = submit_image(
        prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        reference_files=reference_files,
        **options,
    )
    if on_submitted is not None:
        on_submitted("official_cli", task_id)
    image = poll_image(
        task_id,
        interval=poll_interval,
        poll_timeout=poll_timeout,
        sleep=sleep,
        **options,
    )
    path = download_media(image, output_path, cli_path=cli_path)
    return {"provider": "official_cli", "taskId": task_id, "image": image, "path": str(path)}
