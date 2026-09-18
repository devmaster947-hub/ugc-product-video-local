from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import libtv_batch_generate  # noqa: E402


class ParallelClient:
    def __init__(self):
        self.active = 0
        self.maximum = 0
        self.lock = threading.Lock()

    def run_video_node(self, project_uuid: str, node_key: str):
        with self.lock:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        time.sleep(0.03)
        with self.lock:
            self.active -= 1
        return {"data": {"url": [f"https://example.invalid/{node_key}.mp4"], "taskInfo": {"taskId": node_key, "status": 2}}}


class LibTVBatchTests(unittest.TestCase):
    def test_prompt_binds_exact_node_keys_in_order(self):
        prompt = libtv_batch_generate.bind_prompt("镜头提示", ["product-key", "creator-key"])
        self.assertLess(prompt.index("{{Node product-key}}"), prompt.index("{{Node creator-key}}"))

    def test_node_arguments_connect_keys_and_do_not_run(self):
        arguments = libtv_batch_generate.video_node_arguments(
            project_uuid="project", name="S01", prompt="prompt",
            ordered_node_keys=["product-key", "creator-key"], model="Seedance 2.0 Fast VIP",
            duration=10, ratio="9:16", resolution="720p", x=760, y=0,
        )
        left = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--left"]
        self.assertEqual(left, ["product-key", "creator-key"])
        self.assertNotIn("--run", arguments)

    def test_parallel_runner_is_bounded_to_three(self):
        client = ParallelClient()
        completed, errors = libtv_batch_generate.run_nodes_parallel(
            client, "project", {index: f"node-{index}" for index in range(1, 6)}
        )
        self.assertFalse(errors)
        self.assertEqual(set(completed), {1, 2, 3, 4, 5})
        self.assertLessEqual(client.maximum, 3)

    def test_node_index_rejects_duplicate_names(self):
        with self.assertRaises(libtv_batch_generate.LibTVBatchError):
            libtv_batch_generate.node_index({"nodes": [
                {"name": "参考图-01", "id": "node-a"},
                {"name": "参考图-01", "id": "node-b"},
            ]})


if __name__ == "__main__":
    unittest.main()
