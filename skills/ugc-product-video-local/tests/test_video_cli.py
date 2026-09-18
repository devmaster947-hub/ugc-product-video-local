from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import video_cli  # noqa: E402


class VideoCliTests(unittest.TestCase):
    def _fake_cli(self, root: Path, name: str, model_id: str) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / name
        path.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"multimodal2video\" ] && [ \"$2\" = \"--help\" ]; then\n"
            f"  echo '--image --prompt --duration --ratio --video_resolution --model_version {model_id}'\n"
            "  exit 0\n"
            "fi\n"
            "if [ \"$1\" = \"query_result\" ] && [ \"$2\" = \"--help\" ]; then\n"
            "  echo '--submit_id'\n"
            "  exit 0\n"
            "fi\n"
            "echo '{\"id\":\"task-1\"}'\n",
            encoding="utf-8",
        )
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def test_provider_order(self):
        self.assertEqual(video_cli.PROVIDER_ORDER, ("libtv_cli", "xiaoyunque_cli", "dreamina_cli"))

    def test_preflight_short_circuits_after_libtv(self):
        with patch.object(video_cli, "libtv_cli_available", return_value=True), patch.object(
            video_cli, "resolve_libtv_cli", return_value=Path("/tmp/libtv")
        ), patch.object(video_cli, "local_cli_supports_model") as lower:
            result = video_cli.preflight("Seedance 2 Fast")
        self.assertEqual(result["selected"], "libtv_cli")
        self.assertIsNone(result["availability"]["xiaoyunque_cli"])
        lower.assert_not_called()

    def test_preflight_prefers_xiaoyunque_over_dreamina(self):
        with patch.object(video_cli, "libtv_cli_available", return_value=False), patch.object(
            video_cli, "local_cli_supports_model", side_effect=lambda provider, model: provider == "xiaoyunque_cli"
        ), patch.object(video_cli, "_local_executable", return_value=Path("/tmp/xiaoyunque")):
            result = video_cli.preflight("seedance-2-fast")
        self.assertEqual(result["selected"], "xiaoyunque_cli")
        self.assertIsNone(result["availability"]["dreamina_cli"])

    def test_libtv_default_install_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = self._fake_cli(root / ".libtv", "libtv", "unused")
            with patch.object(video_cli.Path, "home", return_value=root), patch.object(
                video_cli.shutil, "which", return_value=None
            ), patch.dict(os.environ, {"LIBTV_CLI": ""}):
                self.assertEqual(video_cli.resolve_libtv_cli(), executable.resolve())

    def test_exact_model_id_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            cli = self._fake_cli(Path(directory), "dreamina", "seedance2.0fast_vip")
            with patch.dict(os.environ, {"DREAMINA_CLI": str(cli)}):
                self.assertTrue(video_cli.local_cli_supports_model("dreamina_cli", "Seedance 2 Fast"))
                self.assertFalse(video_cli.local_cli_supports_model("dreamina_cli", "Seedance 2 Mini"))

    def test_submit_repeats_all_reference_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            product, creator = root / "product.png", root / "creator.png"
            product.write_bytes(b"product")
            creator.write_bytes(b"creator")
            with patch.object(video_cli, "_local_executable", return_value=Path("/tmp/dreamina")), patch.object(
                video_cli, "_run_json", return_value={"submit_id": "task-1"}
            ) as run:
                task = video_cli.submit_local_video(
                    "dreamina_cli", "seedance-2-fast", "prompt", 10,
                    reference_files=[product, creator],
                )
        self.assertEqual(task, "task-1")
        arguments = run.call_args.args[1]
        images = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--image"]
        self.assertEqual(images, [str(product.resolve()), str(creator.resolve())])


if __name__ == "__main__":
    unittest.main()
