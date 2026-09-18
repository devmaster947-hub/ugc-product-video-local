#!/usr/bin/env python3
"""Local prompt builder/validator for ugc-product-video-local.

This module mirrors the former n8n "带货视频提示词v2" workflow prompt logic, but
contains no webhook, callback, task submission, or remote prompt-generation call.
The current agent/model is expected to generate the JSON result locally from the
rendered instruction.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

from prompt_library import load_context as load_prompt_library_context
from prompt_library import render_context as render_prompt_library_context


class LocalPromptError(RuntimeError):
    pass


COMMON_RULES = r'''你是顶级 TikTok UGC 带货视频导演和 AI 视频提示词专家。

# 任务

根据输入信息生成可直接用于视频模型的视频生成提示词。

# 1.任务

根据输入信息生成可直接用于视频模型的视频Prompt。

输出必须符合真实商业短视频生产要求。


# 2. 输出结构

最终只能输出以下 JSON：

{
  "videoPrompts": {
    "summary": "",
    "segments": [
      {
        "segmentId": 1,
        "title": "",
        "duration": 10,
        "prompt": ""
      }
    ]
  },
  "creatorPrompts": {
    "summary": "",
    "creators": [
      {
        "creatorId": 1,
        "role": "",
        "appearsInSegments": [1],
        "consistencyReason": "",
        "prompt": ""
      }
    ]
  }
}

禁止输出：
- 解释
- 分析
- Markdown
- 额外字段


# 3. videoPrompts规则

用于生成最终视频。


## summary

一句话描述：

- 视频模型
- 总时长
- Segment数量


## segments

每个 Segment 是独立视频生成任务。


字段：
segmentId：
Segment编号。


title：

格式：

第X段｜时长｜视频功能


duration：

Segment实际生成时长。

要求：

- 必须整数
- 必须符合当前视频模型的单段时长限制
- 具体限制以【Segment拆分规则】中的模型规则为准
- 拼接后的总时长必须尽量接近用户目标时长
- 当无法精确匹配时，优先选择总时长略高于目标时长，而不是明显低于目标时长


prompt：

完整视频生成 Prompt。Segment 是模型的生成任务容器，不是内容阶段；一个 Segment 内可以包含多个镜头、硬切、景别变化和多个营销节拍。

每个 Segment 必须包含：

1. 本次任务的参考图及其职责：产品图只锁定产品外观，达人图只锁定人物身份和穿戴。
2. 完整的人物、产品、场景、光线、机位和本段初始状态。
3. 从 0 秒开始的连续时间轴，覆盖画面、动作、运镜、环境声、音效、音乐和口播。
4. 产品操作或穿戴过程、肉眼可见的 Proof、人物反应和自然 CTA。
5. 声音规则与必要限制。

每个 Segment 必须遵守：

- 时间轴使用 `[0s-3s]` 格式，从 0 秒开始，无空档、重叠或倒序，最后结束时间等于 Segment duration。
- 每个时间块不超过 4 秒，只安排一个核心镜头或连续动作链。
- 禁止字幕、水印，禁止擅自新增或修改产品元素，不得编造参数、功效或承诺。
- 每段使用绝对、自包含描述；禁止出现“上一段”“下一段”“承接”“继续前段”“同一个达人”“同一人物”“相同人物”“同上一人物”“声音同上段”“保持相同声音”等跨段引用。
- 后续 Segment 可以在最终成片中推进前面的剧情，但必须绝对描述自己的初始画面和人物状态，不重演前段内容来补背景。

# 4. Segment拆分规则


根据当前视频模型限制进行 Segment 拆分。


## 通用规则：

- 必须先根据目标总时长和模型单段限制确定最少 Segment 数，再在这些 Segment 内安排内容。
- Segment 不得按 Hook、Problem、Solution、Proof、CTA 或场景阶段拆分。
- 当目标总时长不超过模型单段上限时，必须只生成 1 个 Segment。
- 每个Segment必须可以独立生成。
- 连续动作、连续口播、连续产品展示尽量保持在同一个Segment。
- 不允许为了满足目标总时长生成模型不支持的Segment时长。
- 如果模型限制导致无法精确匹配目标时长，需要通过多个合法Segment组合，使最终总时长尽量接近目标时长。
- 不要为了减少Segment数量而明显缩短最终视频时长。


## 模型时长限制：
* seedance-2 / seedance-2-fast / seedance-2-mini：单段 4～15 秒。
* seedance-2-5：单段 4～30 秒。


# 5. creatorPrompts规则

用于生成达人产品参考图。用户未提供达人图时，必须结合用户上传的产品图，生成一张同一人物的多视角达人产品参考图。

达人设计必须综合使用输入中的：

- productBrief.targetCountry
- productBrief.targetAudience
- productBrief.productName
- productBrief.sellingPoints
- 产品图呈现的品类、风格与使用场景
- creativeRequirement中的达人或创意要求

生成达人产品参考图时，必须把productBrief.productImages中的有效产品图作为实际生图参考输入，不得只在文字Prompt中描述产品。

产品必须融入全部视角：

- 穿戴类产品默认由达人正确穿戴，成套服装必须完整穿着。
- 手持或使用型产品由达人自然持有或使用。
- 不适合穿戴或手持的大型产品与达人同场展示并保持可信比例。
- 严格保留产品图中可见的颜色、版型、结构、图案、材质观感与部件。
- 不得把产品替换成普通占位服装，不得凭空补造看不见的细节。

用户明确提供的达人要求优先级最高。达人年龄感、整体气质、妆发、体型和穿搭应与目标市场的内容语境、产品定位和目标受众自然匹配。不得脱离输入生成泛化达人。

目标市场只用于本地化内容语境，不得仅凭国家或地区套用种族、肤色、五官或文化刻板印象，不得虚构职业、身份或生活方式。

creator按照人物生成，不按照 Segment 生成。

同一人物多个 Segment出现：

只生成一个 Creator。


字段：


summary：

一句话介绍达人信息。


creators：

role：

人物角色，例如：

- 主达人
- 演示达人
- 旁观者


appearsInSegments：

填写出现的 Segment。


consistencyReason：

说明保持人物一致原因。


prompt：

完整多视角达人参考图 Prompt。


必须包含：

- 9:16竖图
- 真人摄影风格
- 单张三视图参考板
- 同一达人并列呈现正面、三分之四侧面、侧面或背面三个全身视角
- 三个视角的脸部特征、发型、体型、服装、鞋履和固定配饰完全一致
- 三个视角中的产品外观与穿戴、持有或使用方式一致
- 自然站姿
- 浅色干净背景
- 均匀光线
- 五官
- 肤色
- 发型
- 体型
- 完整服装
- 鞋履
- 固定配饰
- 自然皮肤纹理
- 真实UGC达人感
- 自然表情与真实人体比例
- 生活化妆发和合身日常服装


禁止：

- 忽略产品参考图或使用与产品无关的占位服装
- 修改产品颜色、版型、结构、图案、材质观感或部件
- 文字
- Logo
- 边框
- 额外人物
- 过度磨皮
- 夸张身材
- 僵硬姿势
- 塑料皮肤
- 奢华影棚模特感
- 明显AI面孔


不要生成：

- 一次性路人
- 背景人物



# 7. 产品规则

产品外观由产品参考图锁定。

无需在每个 Segment 重复描述完整产品。


禁止：

- 修改产品结构
- 修改颜色
- 添加不存在功能
- 编造参数


# 8. 声音与口播规则


口播：

必须：

- 使用目标国家自然口语
- 不逐字翻译
- 符合人物身份、年龄和场景
- 保持真实UGC表达


禁止：

- 生成字幕


人物固定声音：

- 只要本段有人物口播，必须在时间轴之前独立写一句：`人物固定声音（角色名）：语言=...；口音=...；成年性别=...；年龄感=...；核心音色=...；音高=...；基础语速=...；表达风格=...。`
- 同一角色跨 Segment 说话时，每个相关 Segment 都必须逐字复用这一整句，包括字段顺序、用词和标点；不得缩写成“同上段”或“保持同一声音”。
- 固定声音只锁定语言、口音、成年性别、年龄感、核心音色、音高、基础语速和表达风格。惊讶、紧张、兴奋、放松、音量变化和关键词强调写入对应时间块，不改动固定声纹。
- 多个主要说话人物必须分别写固定声音，不得共用。
- 无人物口播的 Segment 必须明确写：`本段无人物口播。`


# 9. 输出语言规则


所有生成内容必须使用中文。


包括：

- videoPrompts.summary
- segments[].title
- segments[].prompt
- creatorPrompts.summary
- creators[].prompt
- consistencyReason


禁止输出英文视频提示词。


口播内容除外：

口播必须根据目标国家生成对应语言，例如：

- 美国：美式英语
- 巴西：葡萄牙语
- 泰国：泰语


但视频描述、镜头描述、人物描述、声音描述等 Prompt 必须使用中文。


# 10. 模型限制

所有人物必须为成年人。禁止危险动作、性化服装、恐吓式表达。'''


UGC_RULES = r'''【输入信息】
产品信息
-产品名称：{product_name}
-产品卖点：{selling_points}
-目标国家：{target_country}
-目标受众：{target_audience}

达人信息：{creator_json}

视频信息
-视频模型：{video_model}
-视频时长：{duration}

额外创作要求：
{creative_requirement}

目标：生成真实 TikTok UGC 产品演示视频。

【UGC模式要求】

视频逻辑：
根据产品选择：
- 痛点展示
- 产品使用
- 体验变化
- 信任建立
- 行动引导

不要强制固定顺序。


要求：

1. 前1秒必须抓住用户。


优先：

- 痛点
- 强反差
- 用户真实反应
- 产品关键动作


禁止：

- 空镜
- 自我介绍
- 静态产品展示开场


2. 每条视频：

只突出：

- 一个核心卖点
- 一个使用场景
- 一个主要动作


3. 表现：

像真实用户分享：

- 自然动作
- 真实表情
- 简短口播


不要：

- 广告腔
- 影视大片感


【Prompt要求】

videoPrompts中的prompt必须是完整中文视频生成提示词。

必须包含：

- 场景
- 人物
- 产品动作
- 镜头
- 光线
- 口播
- 环境声音
- CTA'''


STORY_RULES = r'''【输入信息】

产品：{product_json}
达人：{creator_json}
创作要求：{creative_requirement}
视频模型：{video_model}
时长：{duration}秒


目标：生成 TikTok 轻剧情带货视频。


【剧情模式要求】


不要：

- 品牌广告片
- 影视短剧


在生成 JSON 前，先在内部完成不输出的节拍表：

触发事件 → 冲突升级 → 产品介入 → 真实操作或穿戴 → 肉眼可见的 Proof → 情绪回报 → 自然 CTA。

不是每条视频都要机械套用相同镜头，但必须保证“冲突如何被产品解决”在画面上可见，不得只靠台词宣布结果。


要求：

1. 前1秒必须发生事情。


例如：

- 遇到问题
- 尴尬瞬间
- 失败
- 强烈反应


禁止：

- 空镜
- 走路
- 自我介绍
- 产品摆拍开场


2. 剧情限制：

只允许：

- 一个冲突
- 一个解决过程
- 一个结果


默认：

- 一个主角
- 最多一个配角
- 一个主要场景


3. 剧情密度：

- 每个时间块必须新增一项有用信息：冲突、升级、选择、产品动作、Proof、情绪变化或 CTA。
- 禁止为凑时长加入走路、空镜、重复惊讶、重复台词、无信息的产品慢镜头或与卖点无关的支线。
- 用动作、道具反应、前后对比、上身效果或使用结果提供可见 Proof，不得用未经支持的功效承诺代替 Proof。
- 时长大于单段上限时，先完成整条故事的统一节拍表，再按时间切成最少 Segment；不得让每个 Segment 各自重演一遍 Hook→Solution→CTA。


4. 情绪变化：

开始：

困扰/怀疑


中间：

发现产品解决问题


结尾：

满意/惊喜/认可


【Prompt要求】

每个 Segment 必须是可独立执行的完整视频生成 Prompt，但不得把它写成一个重复的完整小广告。用绝对描述写清本段初始状态，使其无需知道其他 Segment 也能生成；同时让它在最终拼接中只承担整体剧情中分配给它的新节拍。'''


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def build_prompt(
    product_brief: Mapping[str, Any],
    user_config: Mapping[str, Any],
    creator_brief: Mapping[str, Any] | None = None,
    creative_requirement: str | None = None,
    prompt_library_context: Mapping[str, Any] | None = None,
) -> str:
    """Render the exact local instruction corresponding to the old workflow branch."""
    if not isinstance(product_brief, Mapping):
        raise LocalPromptError("productBrief 必须是对象。")
    if not isinstance(user_config, Mapping):
        raise LocalPromptError("userConfig 必须是对象。")
    normalized_product_brief = dict(product_brief)
    if not _text(normalized_product_brief.get("targetCountry")).strip():
        normalized_product_brief["targetCountry"] = "美国"
    creator_brief = creator_brief or {"creatorMode": "auto", "creatorImages": []}
    requirement = creative_requirement.strip() if isinstance(creative_requirement, str) else ""
    requirement = requirement or "无"
    style = _text(user_config.get("videoStyle"))

    if style == "storyDrivenAd":
        branch = STORY_RULES.format(
            product_json=json.dumps(normalized_product_brief, ensure_ascii=False, separators=(",", ":")),
            creator_json=json.dumps(creator_brief, ensure_ascii=False, separators=(",", ":")),
            creative_requirement=requirement,
            video_model=_text(user_config.get("videoModel")),
            duration=_text(user_config.get("duration")),
        )
    else:
        branch = UGC_RULES.format(
            product_name=_text(normalized_product_brief.get("productName")),
            selling_points=_text(normalized_product_brief.get("sellingPoints")),
            target_country=_text(normalized_product_brief.get("targetCountry")),
            target_audience=_text(normalized_product_brief.get("targetAudience")),
            creator_json=json.dumps(creator_brief, ensure_ascii=False, separators=(",", ":")),
            video_model=_text(user_config.get("videoModel")),
            duration=_text(user_config.get("duration")),
            creative_requirement=requirement,
        )
    library_reference = render_prompt_library_context(prompt_library_context)
    sections = [COMMON_RULES]
    if library_reference:
        sections.append(library_reference)
    sections.append(branch)
    return "\n\n".join(sections)


def _duration_allowed(model: str, duration: int) -> bool:
    model = model.strip().lower()
    if model in {"seedance-2", "seedance-2-fast", "seedance-2-mini"}:
        return 4 <= duration <= 15
    if model == "seedance-2-5":
        return 4 <= duration <= 30
    return False


TIMELINE_PATTERN = re.compile(r"\[(\d+(?:\.\d+)?)s-(\d+(?:\.\d+)?)s\]")
VOICE_PATTERN = re.compile(r"人物固定声音（([^\uff09]+)）：([^\u3002\n]+。)")
VOICE_FIELDS = (
    "语言=",
    "口音=",
    "成年性别=",
    "年龄感=",
    "核心音色=",
    "音高=",
    "基础语速=",
    "表达风格=",
)
CROSS_SEGMENT_REFERENCES = (
    "上一段",
    "下一段",
    "承接上段",
    "承接前段",
    "继续前段",
    "同一个达人",
    "同一位达人",
    "同一人物",
    "相同人物",
    "同上一人物",
    "声音同上段",
    "保持相同声音",
)


def _validate_timeline(prompt: str, duration: int, segment_index: int) -> None:
    ranges = [(float(start), float(end)) for start, end in TIMELINE_PATTERN.findall(prompt)]
    if not ranges:
        raise LocalPromptError(f"第 {segment_index} 个 Segment 缺少 [0s-3s] 格式的连续时间轴。")
    if abs(ranges[0][0]) > 1e-6:
        raise LocalPromptError(f"第 {segment_index} 个 Segment 时间轴必须从 0 秒开始。")
    previous_end = 0.0
    for block_index, (start, end) in enumerate(ranges, 1):
        if abs(start - previous_end) > 1e-6:
            raise LocalPromptError(
                f"第 {segment_index} 个 Segment 的第 {block_index} 个时间块与前一块不连续。"
            )
        if end <= start:
            raise LocalPromptError(f"第 {segment_index} 个 Segment 存在无效或倒序时间块。")
        if end - start > 4.000001:
            raise LocalPromptError(f"第 {segment_index} 个 Segment 的单个时间块不得超过 4 秒。")
        previous_end = end
    if abs(previous_end - duration) > 1e-6:
        raise LocalPromptError(
            f"第 {segment_index} 个 Segment 时间轴结束于 {previous_end:g} 秒，必须等于 duration {duration} 秒。"
        )


def _validate_prompt_independence(prompt: str, segment_index: int) -> None:
    for forbidden in CROSS_SEGMENT_REFERENCES:
        if forbidden in prompt:
            raise LocalPromptError(
                f"第 {segment_index} 个 Segment 含跨段引用“{forbidden}”，必须改为本段的绝对描述。"
            )


def _voice_fingerprints(prompt: str, segment_index: int) -> dict[str, str]:
    if "本段无人物口播。" in prompt:
        if VOICE_PATTERN.search(prompt):
            raise LocalPromptError(f"第 {segment_index} 个 Segment 同时声明无口播和人物固定声音。")
        return {}
    matches = VOICE_PATTERN.findall(prompt)
    if not matches:
        raise LocalPromptError(
            f"第 {segment_index} 个 Segment 未写人物固定声音，无口播时必须明确写“本段无人物口播。”"
        )
    voices: dict[str, str] = {}
    for role, description in matches:
        missing = [field for field in VOICE_FIELDS if field not in description]
        if missing:
            raise LocalPromptError(
                f"第 {segment_index} 个 Segment 的角色“{role}”声音特征缺少：{' '.join(missing)}"
            )
        fingerprint = f"人物固定声音（{role}）：{description}"
        if role in voices and voices[role] != fingerprint:
            raise LocalPromptError(f"第 {segment_index} 个 Segment 重复且冲突地定义角色“{role}”的声音。")
        voices[role] = fingerprint
    return voices


def validate_result(
    result: Mapping[str, Any],
    *,
    video_model: str | None = None,
    target_duration: int | None = None,
) -> dict[str, Any]:
    """Validate the former workflow's public JSON contract and return normalized data."""
    if not isinstance(result, Mapping):
        raise LocalPromptError("本地提示词结果必须是 JSON 对象。")
    if set(result) != {"videoPrompts", "creatorPrompts"}:
        raise LocalPromptError("本地提示词结果顶层只能包含 videoPrompts 和 creatorPrompts。")

    video_prompts = result.get("videoPrompts")
    creator_prompts = result.get("creatorPrompts")
    if not isinstance(video_prompts, Mapping):
        raise LocalPromptError("videoPrompts 必须是对象。")
    if not isinstance(creator_prompts, Mapping):
        raise LocalPromptError("creatorPrompts 必须是对象。")

    segments = video_prompts.get("segments")
    creators = creator_prompts.get("creators")
    if not isinstance(segments, list) or not segments:
        raise LocalPromptError("videoPrompts.segments 必须是非空数组。")
    if not isinstance(creators, list):
        raise LocalPromptError("creatorPrompts.creators 必须是数组。")

    voice_fingerprints: dict[str, str] = {}
    total_duration = 0
    for index, segment in enumerate(segments, 1):
        if not isinstance(segment, Mapping):
            raise LocalPromptError(f"第 {index} 个 Segment 必须是对象。")
        for key in ("segmentId", "title", "duration", "prompt"):
            if key not in segment:
                raise LocalPromptError(f"第 {index} 个 Segment 缺少 {key}。")
        duration = segment.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise LocalPromptError(f"第 {index} 个 Segment duration 必须是整数。")
        if video_model and not _duration_allowed(video_model, duration):
            raise LocalPromptError(
                f"第 {index} 个 Segment 时长 {duration} 不符合模型 {video_model} 的限制。"
            )
        total_duration += duration
        if not isinstance(segment.get("prompt"), str) or not segment["prompt"].strip():
            raise LocalPromptError(f"第 {index} 个 Segment prompt 不能为空。")
        prompt = segment["prompt"].strip()
        _validate_timeline(prompt, duration, index)
        _validate_prompt_independence(prompt, index)
        segment_voices = _voice_fingerprints(prompt, index)
        for role, fingerprint in segment_voices.items():
            if role in voice_fingerprints and voice_fingerprints[role] != fingerprint:
                raise LocalPromptError(
                    f"角色“{role}”跨 Segment 的人物固定声音必须逐字一致。"
                )
            voice_fingerprints[role] = fingerprint

    if target_duration is not None and total_duration != target_duration:
        raise LocalPromptError(
            f"Segment 总时长 {total_duration} 秒与目标时长 {target_duration} 秒不一致。"
        )
    if target_duration is not None and video_model:
        max_duration = 30 if video_model.strip().lower() == "seedance-2-5" else 15
        minimum_segments = math.ceil(target_duration / max_duration)
        if len(segments) != minimum_segments:
            raise LocalPromptError(
                f"模型 {video_model} 生成 {target_duration} 秒视频应使用最少 "
                f"{minimum_segments} 个 Segment，实际为 {len(segments)} 个。"
            )

    for index, creator in enumerate(creators, 1):
        if not isinstance(creator, Mapping):
            raise LocalPromptError(f"第 {index} 个 Creator 必须是对象。")
        for key in ("creatorId", "role", "appearsInSegments", "consistencyReason", "prompt"):
            if key not in creator:
                raise LocalPromptError(f"第 {index} 个 Creator 缺少 {key}。")

    return {
        "videoPrompts": dict(video_prompts),
        "creatorPrompts": dict(creator_prompts),
        "segments": list(segments),
        "summary": video_prompts.get("summary", ""),
        "success": True,
        "errorMessage": "",
    }


def _load_request(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LocalPromptError("输入 JSON 必须是对象。")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--input", required=True, type=Path)
    build.add_argument("--prompt-library-context", type=Path)

    validate = sub.add_parser("validate")
    validate.add_argument("--input", required=True, type=Path)
    validate.add_argument("--model", default="")
    validate.add_argument("--target-duration", type=int)

    args = parser.parse_args()
    try:
        if args.command == "build":
            request = _load_request(args.input)
            library_context = (
                load_prompt_library_context(args.prompt_library_context)
                if args.prompt_library_context
                else None
            )
            print(
                build_prompt(
                    request.get("productBrief", {}),
                    request.get("userConfig", {}),
                    request.get("creatorBrief", {}),
                    request.get("creativeRequirement", ""),
                    prompt_library_context=library_context,
                )
            )
            return
        result = _load_request(args.input)
        normalized = validate_result(
            result,
            video_model=args.model or None,
            target_duration=args.target_duration,
        )
        print(json.dumps(normalized, ensure_ascii=False))
    except (OSError, json.JSONDecodeError, LocalPromptError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
