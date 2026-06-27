#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Normalize chapter 18-38 prose terms to the "中文 English" style.

The script edits prose, tables, captions, and Mermaid labels. It skips ordinary
code fences, inline code spans, and local-source reference rows so paths,
commands, field names, and method names remain literal.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MANUSCRIPT = ROOT / "docs/book/manuscript"


def target_files() -> list[Path]:
    files: list[Path] = []
    for part in ("part_03", "part_04", "part_05"):
        for path in (MANUSCRIPT / part).glob("chapter_*.md"):
            match = re.search(r"chapter_(\d+)\.md$", path.name)
            if match and int(match.group(1)) >= 18:
                files.append(path)
    return sorted(files)


PHRASES = [
    ("Generative Agent-Based Modeling", "生成式智能体建模 Generative Agent-Based Modeling"),
    ("Generative Agents", "生成式智能体 Generative Agents"),
    ("LLM-driven generative agents", "大语言模型驱动的生成式智能体 LLM-driven generative agents"),
    ("LLM as agents", "大语言模型作为智能体 LLM as agents"),
    ("LLM provider", "大语言模型提供方 LLM provider"),
    ("autonomous agents", "自主智能体 autonomous agents"),
    ("role-playing communicative agents", "角色扮演式沟通智能体 role-playing communicative agents"),
    ("multi agent conversation framework", "多智能体对话框架 multi-agent conversation framework"),
    ("multi-agent conversation framework", "多智能体对话框架 multi-agent conversation framework"),
    ("multi-agent collaboration", "多智能体协作 multi-agent collaboration"),
    ("multi-agent interaction", "多智能体互动 multi-agent interaction"),
    ("agent profile", "智能体画像 agent profile"),
    ("agent demo", "智能体 demo"),
    ("agent memory", "智能体记忆 agent memory"),
    ("agent benchmark", "智能体基准 agent benchmark"),
    ("agent-based models", "基于智能体的模型 agent-based models"),
    ("grounded agent-based models", "落地的智能体模型 grounded agent-based models"),
    ("memory stream", "记忆流 memory stream"),
    ("Memory Stream", "记忆流 Memory Stream"),
    ("associate memory", "关联记忆 associate memory"),
    ("relation memory", "关系记忆 relation memory"),
    ("relationship memory", "关系记忆 relationship memory"),
    ("chat memory", "聊天记忆 chat memory"),
    ("chat summary", "对话摘要 chat summary"),
    ("event memory", "事件记忆 event memory"),
    ("memory type", "记忆类型 memory type"),
    ("memory system", "记忆系统 memory system"),
    ("memory node", "记忆节点 memory node"),
    ("memory nodes", "记忆节点 memory nodes"),
    ("episodic memory", "情景记忆 episodic memory"),
    ("semantic memory", "语义记忆 semantic memory"),
    ("procedural memory", "程序性记忆 procedural memory"),
    ("working memory", "工作记忆 working memory"),
    ("long-term memory", "长期记忆 long-term memory"),
    ("short-term memory", "短期记忆 short-term memory"),
    ("memory governance", "记忆治理 memory governance"),
    ("embedding provider", "向量嵌入提供方 embedding provider"),
    ("embedding model", "向量嵌入模型 embedding model"),
    ("storage index", "存储索引 storage index"),
    ("evidence graph", "证据图 evidence graph"),
    ("event board", "公共事件板 event board"),
    ("task status", "任务状态 task status"),
    ("daily goal", "每日目标 daily goal"),
    ("hourly plan", "小时计划 hourly plan"),
    ("minute action", "分钟行动 minute action"),
    ("active goals", "主动目标 active goals"),
    ("goal decomposition", "目标分解 goal decomposition"),
    ("goal progress evaluation", "目标进度评估 goal progress evaluation"),
    ("goal progress", "目标进度 goal progress"),
    ("goal contribution", "目标贡献 goal contribution"),
    ("action generation", "行动生成 action generation"),
    ("action outcome", "行动结果 action outcome"),
    ("dialogue act", "对话行为 dialogue act"),
    ("chat model", "聊天模型 chat model"),
    ("reflection model", "反思模型 reflection model"),
    ("planning model", "规划模型 planning model"),
    ("reflexion-style learning", "反思式学习 reflexion-style learning"),
    ("verbal reinforcement", "语言强化 verbal reinforcement"),
    ("controlled evaluation", "受控评价 controlled evaluation"),
    ("human evaluation", "人工评价 human evaluation"),
    ("spatial grounding", "空间落地 spatial grounding"),
    ("spatial tree", "空间树 spatial tree"),
    ("sandbox grounding", "沙盒落地 sandbox grounding"),
    ("reaction/dialogue", "反应 reaction / 对话 dialogue"),
    ("replay/evaluation", "回放 replay / 评价 evaluation"),
    ("information diffusion", "信息扩散 information diffusion"),
    ("schedule revision", "日程修订 schedule revision"),
    ("schedule decompose", "日程拆解 schedule decompose"),
    ("schedule revise", "日程修订 schedule revise"),
    ("repeat check", "重复检查 repeat check"),
    ("tile map", "瓦片地图 tile map"),
    ("line-of-sight", "视线 line-of-sight"),
    ("source of truth", "事实来源 source of truth"),
    ("API key", "接口密钥 API key"),
    ("API token", "接口令牌 API token"),
]


TERMS = [
    ("Agent", "智能体 Agent"),
    ("agent", "智能体 agent"),
    ("LLM", "大语言模型 LLM"),
    ("API", "接口 API"),
    ("prompt", "提示词 prompt"),
    ("Prompt", "提示词 Prompt"),
    ("memory", "记忆 memory"),
    ("Memory", "记忆 Memory"),
    ("retrieval", "检索 retrieval"),
    ("Retrieval", "检索 Retrieval"),
    ("reflection", "反思 reflection"),
    ("Reflection", "反思 Reflection"),
    ("planning", "规划 planning"),
    ("Planning", "规划 Planning"),
    ("schedule", "日程 schedule"),
    ("Schedule", "日程 Schedule"),
    ("event", "事件 event"),
    ("Event", "事件 Event"),
    ("chat", "聊天 chat"),
    ("Chat", "聊天 Chat"),
    ("conversation", "对话记录 conversation"),
    ("Conversation", "对话记录 Conversation"),
    ("dialogue", "对话 dialogue"),
    ("Dialogue", "对话 Dialogue"),
    ("checkpoint", "断点 checkpoint"),
    ("Checkpoint", "断点 Checkpoint"),
    ("provider", "模型提供方 provider"),
    ("Provider", "模型提供方 Provider"),
    ("embedding", "向量嵌入 embedding"),
    ("Embedding", "向量嵌入 Embedding"),
    ("concept", "概念 concept"),
    ("Concept", "概念节点 Concept"),
    ("spatial", "空间记忆 spatial"),
    ("Spatial", "空间记忆 Spatial"),
    ("scratch", "草稿状态 scratch"),
    ("Scratch", "草稿状态 Scratch"),
    ("tile", "地图格子 tile"),
    ("Tile", "地图格子 Tile"),
    ("maze", "世界地图 maze"),
    ("Maze", "世界地图 Maze"),
    ("arena", "场所 arena"),
    ("Arena", "场所 Arena"),
    ("step", "仿真步 step"),
    ("Step", "仿真步 Step"),
    ("stride", "步长 stride"),
    ("simulation", "时间线 simulation"),
    ("movement", "移动回放 movement"),
    ("summary", "摘要 summary"),
    ("action", "行动 action"),
    ("thought", "想法 thought"),
    ("Thought", "想法 Thought"),
    ("insight", "洞察 insight"),
    ("Insight", "洞察 Insight"),
    ("evidence", "证据 evidence"),
    ("metadata", "元数据 metadata"),
    ("source", "来源 source"),
    ("focus", "焦点 focus"),
    ("query", "查询 query"),
    ("node", "节点 node"),
    ("nodes", "节点 nodes"),
    ("storage", "存储 storage"),
    ("index", "索引 index"),
    ("path", "路径 path"),
    ("profile", "画像 profile"),
    ("goal", "目标 goal"),
    ("Goal", "目标 Goal"),
    ("skill", "技能 skill"),
    ("Skill", "技能 Skill"),
    ("metrics", "指标 metrics"),
    ("Metrics", "指标 Metrics"),
    ("report", "报告 report"),
    ("Report", "报告 Report"),
    ("evaluation", "评价 evaluation"),
    ("Evaluation", "评价 Evaluation"),
    ("workflow", "工作流 workflow"),
    ("Workflow", "工作流 Workflow"),
    ("tool", "工具 tool"),
    ("Tool", "工具 Tool"),
    ("task", "任务 task"),
    ("Task", "任务 Task"),
    ("context", "上下文 context"),
    ("Context", "上下文 Context"),
    ("reward", "奖励 reward"),
    ("Reward", "奖励 Reward"),
    ("verbal", "语言 verbal"),
    ("Reflexion", "反思式学习 Reflexion"),
    ("planner", "规划器 planner"),
    ("Planner", "规划器 Planner"),
    ("executor", "执行器 executor"),
    ("Executor", "执行器 Executor"),
    ("observer", "观察器 observer"),
    ("Observer", "观察器 Observer"),
    ("environment", "环境 environment"),
    ("Environment", "环境 Environment"),
]


QUESTION_REPAIRS = [
    ("?????????????? LLM-driven generative agents", "大语言模型驱动的生成式智能体 LLM-driven generative agents"),
    ("?????????????? LLM-driven generative", "大语言模型驱动的生成式智能体 LLM-driven generative"),
    ("?????????? role-playing communicative agents", "角色扮演式沟通智能体 role-playing communicative agents"),
    ("?????????? role-playing communicative", "角色扮演式沟通智能体 role-playing communicative"),
    ("?????????? LLM as agents", "大语言模型作为智能体 LLM as agents"),
    ("?????????? LLM as", "大语言模型作为智能体 LLM as"),
    ("???????? Generative Agent-Based Modeling", "生成式智能体建模 Generative Agent-Based Modeling"),
    ("???????? Generative Agent-Based", "生成式智能体建模 Generative Agent-Based"),
    ("???????? multi-agent conversation framework", "多智能体对话框架 multi-agent conversation framework"),
    ("???????? multi-agent conversation", "多智能体对话框架 multi-agent conversation"),
    ("???????? agent-based models", "基于智能体的模型 agent-based models"),
    ("??????? embedding provider", "向量嵌入提供方 embedding provider"),
    ("?????? Generative Agents", "生成式智能体 Generative Agents"),
    ("?????? multi-agent collaboration", "多智能体协作 multi-agent collaboration"),
    ("?????? multi-agent interaction", "多智能体互动 multi-agent interaction"),
    ("?????? goal progress evaluation", "目标进度评估 goal progress evaluation"),
    ("?????? goal progress", "目标进度 goal progress"),
    ("????? procedural memory", "程序性记忆 procedural memory"),
    ("????? reflexion-style learning", "反思式学习 reflexion-style learning"),
    ("????? event board", "公共事件板 event board"),
    ("????? provider", "模型提供方 provider"),
    ("????? Reflexion", "反思式学习 Reflexion"),
    ("????? LLM model", "大语言模型 LLM model"),
    ("????? LLM", "大语言模型 LLM"),
    ("???? active goals", "主动目标 active goals"),
    ("???? associate memory", "关联记忆 associate memory"),
    ("???? chat memory", "聊天记忆 chat memory"),
    ("???? chat summary", "对话摘要 chat summary"),
    ("???? conversation", "对话记录 conversation"),
    ("???? embedding model", "向量嵌入模型 embedding model"),
    ("???? embedding", "向量嵌入 embedding"),
    ("???? episodic memory", "情景记忆 episodic memory"),
    ("???? goal decomposition", "目标分解 goal decomposition"),
    ("???? information diffusion", "信息扩散 information diffusion"),
    ("???? memory governance", "记忆治理 memory governance"),
    ("???? memory node", "记忆节点 memory node"),
    ("???? movement", "移动回放 movement"),
    ("???? relation memory", "关系记忆 relation memory"),
    ("???? repeat check", "重复检查 repeat check"),
    ("???? sandbox grounding", "沙盒落地 sandbox grounding"),
    ("???? schedule revision", "日程修订 schedule revision"),
    ("???? semantic memory", "语义记忆 semantic memory"),
    ("???? spatial grounding", "空间落地 spatial grounding"),
    ("???? spatial tree", "空间树 spatial tree"),
    ("???? storage index", "存储索引 storage index"),
    ("???? task status", "任务状态 task status"),
    ("???? tile map", "瓦片地图 tile map"),
    ("???? verbal reinforcement", "语言强化 verbal reinforcement"),
    ("???? Concept", "概念节点 Concept"),
    ("???? Maze", "世界地图 Maze"),
    ("???? Tile", "地图格子 Tile"),
    ("???? chat", "聊天 chat"),
    ("???? maze", "世界地图 maze"),
    ("???? scratch", "草稿状态 scratch"),
    ("???? spatial", "空间记忆 spatial"),
    ("???? tile", "地图格子 tile"),
    ("??? Memory Stream", "记忆流 Memory Stream"),
    ("??? evidence graph", "证据图 evidence graph"),
    ("??? memory stream", "记忆流 memory stream"),
    ("??? metadata", "元数据 metadata"),
    ("??? prompt", "提示词 prompt"),
    ("??? simulation", "时间线 simulation"),
    ("??? Agent", "智能体 Agent"),
    ("??? agent", "智能体 agent"),
    ("??? step", "仿真步 step"),
    ("?? API key", "接口密钥 API key"),
    ("?? API token", "接口令牌 API token"),
    ("?? action generation", "行动生成 action generation"),
    ("?? action outcome", "行动结果 action outcome"),
    ("?? chat model", "聊天模型 chat model"),
    ("?? dialogue act", "对话行为 dialogue act"),
    ("?? goal contribution", "目标贡献 goal contribution"),
    ("?? goal progress", "目标进度 goal progress"),
    ("?? memory node_type", "记忆类型 memory node_type"),
    ("?? memory system", "记忆系统 memory system"),
    ("?? memory type", "记忆类型 memory type"),
    ("?? planning model", "规划模型 planning model"),
    ("?? reflection model", "反思模型 reflection model"),
    ("?? schedule decompose", "日程拆解 schedule decompose"),
    ("?? schedule revise", "日程修订 schedule revise"),
    ("?? API", "接口 API"),
    ("?? Dialogue", "对话 Dialogue"),
    ("?? Evaluation", "评价 Evaluation"),
    ("?? Event", "事件 Event"),
    ("?? Goal", "目标 Goal"),
    ("?? Planning", "规划 Planning"),
    ("?? Reflection", "反思 Reflection"),
    ("?? Retrieval", "检索 Retrieval"),
    ("?? Schedule", "日程 Schedule"),
    ("?? action", "行动 action"),
    ("?? arena", "场所 arena"),
    ("?? chat", "聊天 chat"),
    ("?? checkpoint", "断点 checkpoint"),
    ("?? concept", "概念 concept"),
    ("?? dialogue", "对话 dialogue"),
    ("?? evaluation", "评价 evaluation"),
    ("?? event", "事件 event"),
    ("?? evidence", "证据 evidence"),
    ("?? focus", "焦点 focus"),
    ("?? goal", "目标 goal"),
    ("?? index", "索引 index"),
    ("?? insight", "洞察 insight"),
    ("?? memory", "记忆 memory"),
    ("?? metrics", "指标 metrics"),
    ("?? node", "节点 node"),
    ("?? nodes", "节点 nodes"),
    ("?? path", "路径 path"),
    ("?? planning", "规划 planning"),
    ("?? profile", "画像 profile"),
    ("?? query", "查询 query"),
    ("?? reflection", "反思 reflection"),
    ("?? 检索 retrieval", "检索 retrieval"),
    ("?? retrieval", "检索 retrieval"),
    ("?? report", "报告 report"),
    ("?? schedule", "日程 schedule"),
    ("?? skill", "技能 skill"),
    ("?? storage", "存储 storage"),
    ("?? stride", "步长 stride"),
    ("?? summary", "摘要 summary"),
    ("?? thought", "想法 thought"),
    ("?? verbal", "语言 verbal"),
    ("???? 草稿状态 Scratch", "草稿状态 Scratch"),
    ("???? 每日目标 daily goal", "每日目标 daily goal"),
    ("???? 小时计划 hourly plan", "小时计划 hourly plan"),
    ("???? 分钟行动 minute action", "分钟行动 minute action"),
]


PROTECTS = sorted({replacement for _, replacement in PHRASES + TERMS}, key=len, reverse=True)
WORD_BOUNDARY_LEFT = r"(?<![A-Za-z0-9_/.-])"
WORD_BOUNDARY_RIGHT = r"(?![A-Za-z0-9_/.-])"


def repair_question_marks(text: str) -> str:
    for bad, good in sorted(QUESTION_REPAIRS, key=lambda pair: len(pair[0]), reverse=True):
        text = text.replace(bad, good)
    return collapse_duplicates(text)


def collapse_duplicates(text: str) -> str:
    replacements = [
        ("生成式智能体 生成式智能体 Generative Agents", "生成式智能体 Generative Agents"),
        ("智能体 智能体 Agent", "智能体 Agent"),
        ("智能体 智能体 agent", "智能体 agent"),
        ("大语言模型 大语言模型 LLM", "大语言模型 LLM"),
        ("接口 接口 API", "接口 API"),
        ("接口 接口密钥 API key", "接口密钥 API key"),
        ("仿真步 仿真步 step", "仿真步 step"),
        ("提示词 提示词 prompt", "提示词 prompt"),
        ("提示词文本 提示词 prompt", "提示词文本 prompt"),
        ("提示词组装器 草稿状态 Scratch", "提示词组装器 Scratch"),
        ("提示词组装器 ???? 草稿状态 Scratch", "提示词组装器 Scratch"),
        ("智能体业务逻辑 智能体 Agent", "智能体业务逻辑 Agent"),
        ("记忆 记忆 memory", "记忆 memory"),
        ("记忆流 记忆流 memory stream", "记忆流 memory stream"),
        ("记忆流 记忆流 Memory Stream", "记忆流 Memory Stream"),
        ("元数据 元数据 metadata", "元数据 metadata"),
        ("向量嵌入 向量嵌入 embedding", "向量嵌入 embedding"),
        ("向量嵌入配置 向量嵌入 embedding", "向量嵌入配置 embedding"),
        ("向量嵌入提供方 向量嵌入提供方 embedding provider", "向量嵌入提供方 embedding provider"),
        ("模型提供方 模型提供方 provider", "模型提供方 provider"),
        ("事件 事件 Event", "事件 Event"),
        ("事件 事件 event", "事件 event"),
        ("对话 聊天 chat", "对话 chat"),
        ("对话 对话 dialogue", "对话 dialogue"),
        ("对话 对话记录 conversation", "对话记录 conversation"),
        ("聊天 聊天 chat", "聊天 chat"),
        ("聊天记忆 聊天记忆 chat memory", "聊天记忆 chat memory"),
        ("想法 想法 thought", "想法 thought"),
        ("概念节点 概念节点 Concept", "概念节点 Concept"),
        ("反思 反思 reflection", "反思 reflection"),
        ("反思 反思 Reflection", "反思 Reflection"),
        ("反思学习 反思式学习 reflexion-style learning", "反思式学习 reflexion-style learning"),
        ("检索 检索 retrieval", "检索 retrieval"),
        ("检索 ?? 检索 retrieval", "检索 retrieval"),
        ("规划 规划 planning", "规划 planning"),
        ("日程 日程 schedule", "日程 schedule"),
        ("日程 日程 Schedule", "日程 Schedule"),
        ("日程 日程拆解 schedule decompose", "日程拆解 schedule decompose"),
        ("日程 日程修订 schedule revise", "日程修订 schedule revise"),
        ("断点 断点 checkpoint", "断点 checkpoint"),
        ("时间线 时间线 simulation", "时间线 simulation"),
        ("对话记录 对话记录 conversation", "对话记录 conversation"),
        ("移动 移动回放 movement", "移动回放 movement"),
        ("移动回放 移动回放 movement", "移动回放 movement"),
        ("行动 行动 action", "行动 action"),
        ("目标 目标 goal", "目标 goal"),
        ("目标规划 目标 goal 规划 planning", "目标规划 goal planning"),
        ("技能 技能 skill", "技能 skill"),
        ("指标 指标 metrics", "指标 metrics"),
        ("报告 报告 report", "报告 report"),
        ("评价 评价 evaluation", "评价 evaluation"),
        ("评价体系 评价 evaluation", "评价体系 evaluation"),
        ("大语言模型摘要 大语言模型 LLM 摘要 summary", "大语言模型摘要 LLM summary"),
        ("大语言模型 LLM 摘要 summary", "大语言模型 LLM summary"),
        ("大语言模型 LLM 模型提供方 provider", "大语言模型提供方 LLM provider"),
        ("向量嵌入提供方 embedding provider 与大语言模型提供方 大语言模型提供方 LLM provider", "向量嵌入提供方 embedding provider 与大语言模型提供方 LLM provider"),
        ("世界地图 世界地图 Maze", "世界地图 Maze"),
        ("世界地图 ???? Maze", "世界地图 Maze"),
        ("地图格子 地图格子 Tile", "地图格子 Tile"),
        ("空间落地 空间落地 spatial grounding", "空间落地 spatial grounding"),
        ("信息扩散 信息扩散 information diffusion", "信息扩散 information diffusion"),
        ("日程修订 日程修订 schedule revision", "日程修订 schedule revision"),
        ("关系记忆 关系记忆 relation memory", "关系记忆 relation memory"),
        ("多智能体协作 多智能体协作 multi-agent collaboration", "多智能体协作 multi-agent collaboration"),
        ("多智能体互动 多智能体互动 multi-agent interaction", "多智能体互动 multi-agent interaction"),
        ("沙盒落地 沙盒落地 sandbox grounding", "沙盒落地 sandbox grounding"),
        ("长期记忆 记忆治理 memory governance", "长期记忆治理 memory governance"),
        ("智能体 智能体基准 agent benchmark", "智能体基准 agent benchmark"),
        ("智能体画像 ??智能体 智能体画像 agent profile", "智能体画像 agent profile"),
        ("??智能体 智能体画像 agent profile", "智能体画像 agent profile"),
        ("主动目标主动目标 active goals", "主动目标 active goals"),
        ("目标分解目标分解 goal decomposition", "目标分解 goal decomposition"),
        ("目标进度评估目标进度评估目标进度 goal progress 评价 evaluation", "目标进度评估 goal progress evaluation"),
        ("candidate 行动生成 action generation", "候选行动生成 candidate action generation"),
        ("检索结果概念节点 Concept", "检索结果：概念节点 Concept"),
        ("可检索记忆节点概念节点 Concept", "可检索概念节点 Concept"),
        ("可检索记忆节点 `Concept`", "可检索概念节点 `Concept`"),
        ("向量索引元数据 metadata", "向量索引的元数据 metadata"),
        ("真正写进向量索引元数据 metadata", "真正写进向量索引的元数据 metadata"),
        ("三类记忆列表事件 event", "三类记忆列表：事件 event"),
        ("调用 大语言模型 LLM", "调用大语言模型 LLM"),
        ("使用 大语言模型 LLM", "使用大语言模型 LLM"),
        ("grounded 基于智能体的模型 agent-based models", "落地的基于智能体模型 grounded agent-based models"),
        ("落地的基于智能体模型 落地的基于智能体模型 grounded agent-based models", "落地的基于智能体模型 grounded agent-based models"),
        ("大语言模型驱动的生成式智能体大语言模型驱动的生成式智能体 LLM-driven generative agents", "大语言模型驱动的生成式智能体 LLM-driven generative agents"),
        ("候选行动生成 候选行动生成 candidate action generation", "候选行动生成 candidate action generation"),
        ("目标进度评估目标进度 goal progress 评价 evaluation", "目标进度评估 goal progress evaluation"),
        ("增加目标进度评估目标进度 goal progress 评价 evaluation", "增加目标进度评估 goal progress evaluation"),
        ("单个 智能体 agent", "单个智能体 agent"),
        ("一个 智能体 agent", "一个智能体 agent"),
        ("一个 智能体 demo", "一个智能体 demo"),
        ("每个 智能体 agent", "每个智能体 agent"),
        ("多个 智能体 agent", "多个智能体 agent"),
        ("很多 智能体 agent", "很多智能体 agent"),
        ("很多 智能体 demo", "很多智能体 demo"),
        ("当前 提示词 prompt", "当前提示词 prompt"),
        ("当前 仿真步 step", "当前仿真步 step"),
        ("社会仿真 social 时间线 simulation", "社会仿真 social simulation"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    labels = [
        "生成式智能体",
        "大语言模型驱动的生成式智能体",
        "大语言模型作为智能体",
        "大语言模型",
        "角色扮演式沟通智能体",
        "多智能体对话框架",
        "多智能体协作",
        "多智能体互动",
        "基于智能体的模型",
        "智能体画像",
        "智能体基准",
        "自主智能体",
        "智能体",
        "接口密钥",
        "接口令牌",
        "接口",
        "提示词",
        "记忆流",
        "长期记忆治理",
        "长期记忆",
        "短期记忆",
        "工作记忆",
        "情景记忆",
        "语义记忆",
        "程序性记忆",
        "记忆治理",
        "记忆节点",
        "记忆类型",
        "记忆系统",
        "记忆",
        "元数据",
        "向量嵌入提供方",
        "向量嵌入模型",
        "向量嵌入",
        "模型提供方",
        "草稿状态",
        "事件记忆",
        "事件",
        "聊天记忆",
        "聊天模型",
        "聊天",
        "对话摘要",
        "对话行为",
        "对话记录",
        "对话",
        "想法",
        "概念节点",
        "概念",
        "反应",
        "反思式学习",
        "反思模型",
        "反思",
        "洞察",
        "检索",
        "规划模型",
        "规划器",
        "规划",
        "日程拆解",
        "日程修订",
        "日程",
        "断点",
        "时间线",
        "移动回放",
        "行动生成",
        "行动结果",
        "行动",
        "目标进度评估",
        "目标分解",
        "目标进度",
        "目标贡献",
        "目标",
        "主动目标",
        "技能",
        "指标",
        "报告",
        "评价体系",
        "评价",
        "世界地图",
        "地图格子",
        "场所",
        "空间落地",
        "空间树",
        "空间记忆",
        "瓦片地图",
        "信息扩散",
        "关系记忆",
        "沙盒落地",
        "公共事件板",
        "任务状态",
        "每日目标",
        "小时计划",
        "分钟行动",
        "重复检查",
        "视线",
        "事实来源",
        "来源",
        "证据图",
        "证据",
        "焦点",
        "查询",
        "节点",
        "存储索引",
        "存储",
        "索引",
        "路径",
        "画像",
        "工具",
        "任务",
        "上下文",
        "奖励",
        "语言强化",
        "语言",
        "执行器",
        "观察器",
        "环境",
        "摘要",
        "仿真步",
        "步长",
    ]
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    text = re.sub(r"([\u4e00-\u9fff，。；：、（）“”]) (" + label_pattern + r")", r"\1\2", text)
    text = re.sub(r"(大语言模型提供方){2,} LLM provider", "大语言模型提供方 LLM provider", text)
    text = re.sub(r"^(# 第 \d+ 章)(?=\S)", r"\1 ", text, flags=re.MULTILINE)
    return text


def replace_phrase(text: str, english: str, replacement: str) -> str:
    chinese = replacement[: -len(english)].rstrip()
    pattern = re.compile(WORD_BOUNDARY_LEFT + re.escape(english) + WORD_BOUNDARY_RIGHT)

    def substitute(match: re.Match[str]) -> str:
        prefix = text[max(0, match.start() - len(chinese) - 1) : match.start()]
        if prefix.endswith(chinese + " "):
            return english
        return replacement

    return pattern.sub(substitute, text)


def protect(text: str) -> tuple[str, list[tuple[str, str]]]:
    saved: list[tuple[str, str]] = []
    for item in PROTECTS:
        if item in text:
            token = f"§§{len(saved)}§§"
            text = text.replace(item, token)
            saved.append((token, item))
    return text, saved


def restore(text: str, saved: list[tuple[str, str]]) -> str:
    for token, item in reversed(saved):
        text = text.replace(token, item)
    return text


def fix_plain(text: str) -> str:
    text = repair_question_marks(text)
    text, existing = protect(text)
    for english, replacement in PHRASES:
        text = replace_phrase(text, english, replacement)
    text = restore(text, existing)
    text, saved = protect(text)
    for english, replacement in TERMS:
        pattern = re.compile(WORD_BOUNDARY_LEFT + re.escape(english) + WORD_BOUNDARY_RIGHT)
        text = pattern.sub(replacement, text)
    return collapse_duplicates(restore(text, saved))


def fix_outside_inline(line: str) -> str:
    parts = line.split("`")
    for index in range(0, len(parts), 2):
        parts[index] = fix_plain(parts[index])
    return "`".join(parts)


def fix_mermaid_line(line: str) -> str:
    def quoted(match: re.Match[str]) -> str:
        return '"' + fix_plain(match.group(1)) + '"'

    line = re.sub(r'"([^"]*)"', quoted, line)

    def edge_label(match: re.Match[str]) -> str:
        return "|" + fix_plain(match.group(1)) + "|"

    return re.sub(r"\|([^|\n]+)\|", edge_label, line)


def transform(text: str) -> str:
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        if line.startswith("```"):
            lang = line.strip()[3:].strip().lower()
            fence = None if fence is not None else lang
            out.append(line)
            continue
        if fence == "mermaid":
            out.append(fix_mermaid_line(line))
        elif fence is not None:
            out.append(line)
        elif line.startswith("- Local "):
            out.append(line)
        else:
            out.append(fix_outside_inline(line))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    changed = 0
    for path in target_files():
        old = path.read_text(encoding="utf-8")
        new = transform(old)
        if new != old:
            path.write_text(new, encoding="utf-8")
            changed += 1
            print(f"normalized: {path.relative_to(ROOT).as_posix()}")
    print(f"changed_files={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
