# 提示词库扩展接口

当前版本只定义本地接口，不连接飞书或任何外部服务。未传入上下文时，提示词生成行为保持不变。

## 接入边界

未来的数据源适配器负责读取、过滤、匹配和缓存提示词库，并输出 `PromptLibraryContext`。主流程只验证和渲染已经选中的最多 3 条参考，不自行访问数据源。

适配器不得修改 `productBrief`、`userConfig`、`creativeRequirement` 或 `creatorBrief`，不得提交图片或视频任务。调用适配器必须发生在任何计费任务之前。

## PromptLibraryContext v1.0

```json
{
  "schemaVersion": "1.0",
  "status": "ready",
  "source": "feishu-bitable",
  "version": "2026-09-10T16:00:00+08:00",
  "matches": [
    {
      "templateId": "PK-001",
      "title": "便携榨汁杯：上班迟到反差开场",
      "score": 0.92,
      "hook": "通勤痛点后立刻展示产品",
      "structure": "痛点→产品出现→实测→结果→CTA",
      "shotRhythm": "0–3秒钩子；3–12秒实测；结尾CTA",
      "conversionPattern": "节省时间→操作简单→行动引导",
      "sellingPoints": "便携；操作直观；清洗方便",
      "avoid": "不使用未经证实的健康或续航承诺",
      "prompt": "用户维护的原始提示词"
    }
  ],
  "warning": ""
}
```

### 状态

- `disabled`：未配置提供者，`matches` 必须为空；默认状态。
- `ready`：使用数据源的最新匹配结果。
- `cached`：数据源不可用，使用最近一次有效缓存。
- `unavailable`：已配置但没有可用数据或缓存，`matches` 必须为空。

`ready` 和 `cached` 必须包含 1～3 条唯一 `templateId` 的匹配记录。`score` 可选，取值为 0～1。其余匹配字段均为可选字符串。

## 主流程调用

Python 调用：

```python
build_prompt(
    product_brief,
    user_config,
    creator_brief,
    creative_requirement,
    prompt_library_context=context,
)
```

命令行调用保持主请求只有原有四个字段，并通过独立文件传入上下文：

```bash
python3 scripts/local_prompt.py build \
  --input request.json \
  --prompt-library-context prompt_library_context.json
```

可在接入数据源前独立验证适配器输出：

```bash
python3 scripts/prompt_library.py validate --input prompt_library_context.json
python3 scripts/prompt_library.py render --input prompt_library_context.json
```

## 后续飞书适配器

飞书适配器只需完成以下职责：读取启用记录、规范化字段、按产品与视频配置匹配最多 3 条、实现缓存与失败状态，然后输出上述 JSON。飞书凭证必须由环境变量或安全配置提供，不得写入 Skill、上下文、缓存或日志。
