from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_TEXT = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
CLI_TEXT = (SKILL_ROOT / "scripts" / "lzstudio_cli.py").read_text(encoding="utf-8")
VIDEO_CLI_TEXT = (SKILL_ROOT / "scripts" / "video_cli.py").read_text(encoding="utf-8")
LIBTV_TEXT = (SKILL_ROOT / "scripts" / "libtv_batch_generate.py").read_text(encoding="utf-8")
OPENAI_YAML = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
PROMPT_LIBRARY_TEXT = (SKILL_ROOT / "scripts" / "prompt_library.py").read_text(encoding="utf-8")
PROMPT_LIBRARY_INTERFACE = (SKILL_ROOT / "references" / "prompt-library-interface.md").read_text(encoding="utf-8")


class SkillContractTests(unittest.TestCase):
    def test_no_lingzhi_runtime(self):
        for forbidden in (
            "LINGZHI_API_KEY",
            "resolve_api_key",
            "save_api_key",
            "studio.lingzhiai.com.cn",
            "--api-key",
            "submit_video(",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, SKILL_TEXT + CLI_TEXT)

    def test_lingzhi_api_key_is_not_requested(self):
        self.assertIn("不请求或使用灵智工坊 API Key", SKILL_TEXT)
        self.assertNotIn("请提供灵智工坊 API Key", SKILL_TEXT)

    def test_official_oauth_flow_is_declared(self):
        for text in ("dreamina login", "OAuth", "dreamina user_credit", "image2image", "multimodal2video"):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)

    def test_local_creator_image_is_primary_multiview_and_product_grounded(self):
        self.assertIn("优先调用当前智能体可用的本地生图能力", SKILL_TEXT)
        self.assertIn("单张三视图参考板", SKILL_TEXT)
        self.assertIn("正面、三分之四侧面、侧面或背面", SKILL_TEXT)
        self.assertIn("该前置步骤不可跳过", SKILL_TEXT)
        self.assertIn("把产品图作为 `reference_files` 传入", SKILL_TEXT)
        self.assertIn("官方 `dreamina image2image`", SKILL_TEXT)
        self.assertIn("默认模型 `5.0`、9:16、2K", SKILL_TEXT)
        self.assertIn("取得确认", SKILL_TEXT)

    def test_auto_creator_visibly_uses_product_reference(self):
        for text in (
            "必须把 `productBrief.productImages` 中所有有效产品图作为实际生图参考输入",
            "穿戴类产品默认由达人正确穿戴",
            "成套服装必须完整穿着",
            "产品必须融入全部视角",
            "不得把产品替换成普通占位服装",
        ):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)

    def test_default_market_does_not_depend_on_conversation_language(self):
        self.assertIn("目标市场默认美国", SKILL_TEXT)
        self.assertIn("不得根据用户的对话语言", SKILL_TEXT)
        self.assertIn('"targetCountry": "美国"', SKILL_TEXT)

    def test_generated_creator_images_are_displayed(self):
        self.assertIn("必须在进入 Seedance 视频生成前立即向用户显示", SKILL_TEXT)
        self.assertIn("不得只发送文件路径", SKILL_TEXT)

    def test_auto_creator_is_shared_by_default_and_used_as_reference(self):
        self.assertIn("批量任务中默认只生成一张并跨视频复用", SKILL_TEXT)
        self.assertIn("只有用户明确要求每条视频使用不同达人时", SKILL_TEXT)
        self.assertIn("默认向每条视频传入同一张共享图", SKILL_TEXT)

    def test_auto_creator_uses_user_context_and_stays_natural(self):
        for text in (
            "目标市场、目标受众、产品名称、可见卖点、产品风格和自定义创作要求",
            "用户明确提供的达人要求优先级最高",
            "不得仅凭国家或地区套用种族、肤色、五官或文化刻板印象",
            "真实自然的普通 UGC 创作者质感",
            "避免过度磨皮、夸张身材、僵硬姿势、塑料皮肤",
        ):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)

    def test_confirmation_uses_video_prompt_choice(self):
        self.assertIn("视频提示词：", SKILL_TEXT)
        self.assertIn("1. AI 生成（默认）", SKILL_TEXT)
        self.assertIn("2. 用户提供", SKILL_TEXT)
        self.assertNotIn("创作需求：1 无（默认）｜2 自定义", SKILL_TEXT)

    def test_confirmation_includes_supported_models_and_count(self):
        for text in (
            "1. Seedance 2 Fast（默认）",
            "2. Seedance 2 Mini",
            "3. Seedance 2",
            "4. Seedance 2.5",
            "数量：1–10 条（默认 1 条）",
            "[1] 按默认配置继续",
            "[2] 修改配置",
        ):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)

    def test_removed_models_are_not_offered(self):
        menu = re.search(r"模型：\n\n(?:.*\n){4}", SKILL_TEXT)
        self.assertIsNotNone(menu)
        for removed in ("Minimax", "Google Omni", "Grok Imagine"):
            self.assertNotIn(removed, menu.group(0))

    def test_video_count_is_not_added_to_user_config(self):
        match = re.search(
            r'"userConfig":\s*(\{.*?\})', SKILL_TEXT, flags=re.DOTALL
        )
        self.assertIsNotNone(match)
        config = json.loads(match.group(1))
        self.assertEqual(set(config), {"videoModel", "duration", "videoStyle"})
        self.assertIn("不得将数量写入 `userConfig`", SKILL_TEXT)

    def test_batch_and_output_rules_are_preserved(self):
        for text in (
            "1～10 条独立",
            "最多同时运行 3 条",
            "video_01/",
            "final_video.mp4",
            "不得跨视频拼接",
            "不自动换模型",
        ):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)

    def test_all_models_use_bounded_concurrency(self):
        for text in (
            "所有受支持模型的批量任务均允许并发生成",
            "`seedance-2-mini`、`seedance-2-fast`、`seedance-2` 和 `seedance-2-5`",
            "最多同时运行 3 条完整视频工作流",
            "不在单条视频内部并发 Segment",
            "非终态任务都计入并发数",
            "`ExceedConcurrencyLimit`",
            "所有模型最多同时运行 3 条完整视频工作流",
        ):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)

    def test_logical_models_use_exact_vip_provider_ids(self):
        mappings = (
            "Seedance 2 Fast → `seedance-2-fast` → `seedance2.0fast_vip`",
            "Seedance 2 Mini → `seedance-2-mini` → `seedance2.0mini_vip`",
            "Seedance 2 → `seedance-2` → `seedance2.0_vip`",
        )
        for text in mappings:
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)
        self.assertIn("不得静默改用", SKILL_TEXT)
        self.assertIn("必须在提交前通过该命令的 `--help` 明确声明对应 provider ID", SKILL_TEXT)

    def test_video_provider_priority_and_lock_are_declared(self):
        for text in (
            "libtv_cli → xiaoyunque_cli → dreamina_cli",
            "预检必须短路",
            "整条视频锁定该 provider",
            "不得自动切到下一渠道重新提交",
            "scripts/libtv_batch_generate.py",
            "scripts/local_video_generate.py",
        ):
            self.assertIn(text, SKILL_TEXT)
        self.assertIn('PROVIDER_ORDER = ("libtv_cli", "xiaoyunque_cli", "dreamina_cli")', VIDEO_CLI_TEXT)
        self.assertIn("run_started_uncertain", LIBTV_TEXT)

    def test_local_prompt_contract_is_preserved(self):
        for text in ("scripts/local_prompt.py", "videoPrompts", "creatorPrompts", "validate_result()"):
            self.assertIn(text, SKILL_TEXT)

    def test_prompt_quality_rules_cover_story_density_voice_and_independence(self):
        for text in (
            "触发事件→冲突升级→产品介入→可见操作→可见 Proof→反应与 CTA",
            "禁止用走路、空镜、重复惊讶",
            "单个时间块不超过 4 秒",
            "人物固定声音（角色名）",
            "这一整句必须逐字相同",
            "禁止“上一段”",
            "校验失败不得进入计费生成",
        ):
            with self.subTest(text=text):
                self.assertIn(text, SKILL_TEXT)

    def test_prompt_library_is_optional_and_does_not_change_request_contract(self):
        for text in (
            "当前版本没有飞书或其他数据源适配器",
            "未提供 `PromptLibraryContext` 时",
            "不得把它加入原有四个顶层输入",
            "确认页不展示提示词库配置项",
        ):
            self.assertIn(text, SKILL_TEXT)

    def test_prompt_library_context_has_stable_bounded_contract(self):
        for text in ("schemaVersion", "disabled", "ready", "cached", "unavailable", "MAX_MATCHES = 3"):
            self.assertIn(text, PROMPT_LIBRARY_TEXT + PROMPT_LIBRARY_INTERFACE)

    def test_prompt_library_reserves_no_network_or_credentials(self):
        combined = PROMPT_LIBRARY_TEXT + PROMPT_LIBRARY_INTERFACE
        for forbidden in ("urlopen(", "requests.", "FEISHU_APP_ID", "FEISHU_APP_SECRET"):
            self.assertNotIn(forbidden, combined)

    def test_ui_metadata_mentions_batch_generation(self):
        self.assertTrue("多条" in OPENAI_YAML or "批量" in OPENAI_YAML)


if __name__ == "__main__":
    unittest.main()
