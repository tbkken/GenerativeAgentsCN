"""Public simulation interface contracts shared by authoring and execution.

Each call returns new descriptors so an actor-specific runtime can narrow its
capabilities without mutating the authoring catalog.
"""
from typing import Any


def simulation_interface_catalog() -> list[dict[str, Any]]:
    tools = [
        {
            "name": "world-perceive",
            "description": (
                "Observe compact, unique four-layer spatial semantics, events, "
                "nearby Agents, and nearby Game Objects without changing the "
                "simulation world. radius_tiles is optional and is always "
                "clamped to this Agent's configured vision radius."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "radius_tiles": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                    },
                    "message_id": {"type": "string", "description": "In a 1D world, read one accessible message by its real ID."},
                    "conversation_id": {"type": "string", "description": "In a 1D world, read messages in this conversation or forum topic."},
                    "after_message_id": {"type": "string", "description": "Read accessible messages after this message; omitted selects the latest messages."},
                    "before_message_id": {"type": "string", "description": "Read earlier accessible messages for pagination."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "participant_offset": {"type": "integer", "minimum": 0, "description": "Page through the 1D space's participant names within attention bandwidth."},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "world-navigate",
            "description": (
                "Read-only navigation from your current position. Query a coordinate "
                "within your vision radius, or an exact perceived or remembered arena/object address. "
                "Returns reachability, distance and next_coord (only the FIRST path tile), "
                "never undiscovered map semantics. Does not move you. To continue toward "
                "the queried destination, submit MOVE with the SAME target_coord or "
                "target_address; do not replace it with next_coord unless you intend "
                "to stop at that adjacent tile. The kernel applies the Step movement budget."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_coord": {"type": "array", "items": {"type": "integer"},
                                     "minItems": 2, "maxItems": 2},
                    "target_address": {"type": "array", "items": {"type": "string"},
                                       "minItems": 3, "maxItems": 4},
                },
                "additionalProperties": False,
                "oneOf": [{"required": ["target_coord"]}, {"required": ["target_address"]}],
            },
        },
        {
            "name": "world-act",
            "description": (
                "Select the single replayable world-changing action for this "
                "Agent iteration. Call at most once. SET_OBJECT_STATE uses object_key and "
                "state_patch; use state_patch.state for an observed visual state name, "
                "or an empty string to restore the default appearance."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action_type": {
                        "type": "string",
                        "enum": [
                            "MOVE",
                            "ACT",
                            "WAIT",
                            "SPEAK",
                            "INTERACT",
                            "SET_OBJECT_STATE",
                        ],
                    },
                    "description": {"type": "string"},
                    "predicate": {"type": "string", "description": (
                        "For MOVE, an optional short natural-language phrase for the activity "
                        "performed while moving (at most 240 characters), without locations, "
                        "coordinates, plans or claims of arrival. The committed movement_activity "
                        "preserves this phrase. Actual displacement and arrival at this MOVE's "
                        "target are determined by the kernel. For ACT, the Event predicate."
                    )},
                    "object": {"type": "string"},
                    "emoji": {"type": "string"},
                    "target_address": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "For MOVE, the destination. For ACT, when supplied, "
                            "it must equal the Agent's current four-layer address."
                        ),
                    },
                    "target_coord": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 2,
                        "description": (
                            "For MOVE, the destination. For ACT, when supplied, "
                            "it must equal the Agent's current coordinate."
                        ),
                    },
                    "target_node_id": {
                        "type": "string",
                        "description": (
                            "Optional stable spatial node identity for MOVE. "
                            "When supplied it must exist at the destination."
                        ),
                    },
                    "participant_agent_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "message": {"type": "string"},
                    "visibility": {"type": "string", "enum": ["public", "private"]},
                    "title": {"type": "string", "description": "Required when creating a public forum topic."},
                    "reply_to_message_id": {"type": "string", "description": "Real parent message ID in the same conversation; forum replies may be nested."},
                    "quoted_message_id": {"type": "string", "description": "Quote an accessible public message, or a private message in the same private conversation. The runtime resolves its original content."},
                    "conversation_id": {
                        "type": "string",
                        "description": (
                            "Continue an open conversation UUID belonging to you "
                            "and the selected participant. Omit for a new conversation "
                            "or when changing participants. Do not invent an ID."
                        ),
                    },
                    "start_new_conversation": {
                        "type": "boolean",
                        "default": False,
                    },
                    "end_conversation": {
                        "type": "boolean",
                        "default": False,
                    },
                    "selection_key": {"type": "string"},
                    "request": {"type": "string"},
                    "object_key": {"type": "string"},
                    "state_patch": {"type": "object"},
                    "wait_reason": {
                        "type": "string",
                        "description": (
                            "For WAIT, explain the external condition or schedule "
                            "boundary being awaited, or the reason for remaining silent in a 1D world."
                        ),
                    },
                    "expected_until_step": {
                        "type": "integer",
                        "minimum": 1,
                        "description": (
                            "For an intentional bounded WAIT, the last simulation "
                            "step through which waiting is expected."
                        ),
                    },
                    "expected_until_time": {
                        "type": "string",
                        "description": (
                            "For an intentional bounded WAIT, the simulation-time "
                            "boundary in ISO-8601 form."
                        ),
                    },
                },
                "required": ["action_type"],
                "additionalProperties": False,
            },
        },
    ]
    tools.extend(
        [
            {
                "name": "memory-stream-search",
                "description": (
                    "Semantically search this Agent's persistent memory "
                    "stream. The Agent identity is injected by the system."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100,
                        },
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "memory-stream-append",
                "description": (
                    "Save natural-language memory for this Agent, optionally "
                    "with Event(subject, predicate, object) semantics."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "kind": {"type": "string"},
                        "poignancy": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                        },
                        "subject": {"type": "string"},
                        "predicate": {"type": "string"},
                        "object": {"type": "string"},
                        "source_event_id": {"type": "string", "description": "Stable source event ID returned by world-perceive; the runtime checks access before saving it."},
                        "address": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "evidence_memory_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["content"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "memory-stream-supersede",
                "description": (
                    "Replace one active memory with a corrected version. "
                    "The old version remains replayable as SUPERSEDED."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "content": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["memory_id", "content"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "memory-stream-invalidate",
                "description": (
                    "Mark one active memory INVALIDATED so it no longer "
                    "participates in retrieval while history is retained."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["memory_id", "reason"],
                    "additionalProperties": False,
                },
            },
        ]
    )
    return tools


def object_interface_catalog() -> list[dict[str, Any]]:
    """Return the same contracts narrowed to a stationary Game Object actor."""
    available = [item for item in simulation_interface_catalog() if item["name"] != "world-navigate"]
    action = next(tool for tool in available if tool["name"] == "world-act")
    action["description"] = (
        "Commit your one Game Object action this Step. ACT records an activity; "
        "SET_OBJECT_STATE changes only your own state; WAIT waits. You are stationary. "
        "Attach responses to answer pending request IDs in the same action. "
        "Use target_agent_key and evidence_ids for an observed subject, and "
        "idempotency_key to prevent repeating an activity across Steps/resumes. "
        "Only the state label is publicly visible; other state fields are private."
    )
    names = {"action_type", "description", "predicate", "object", "emoji", "object_key",
             "state_patch", "wait_reason", "expected_until_step", "expected_until_time"}
    props = action["parameters"]["properties"]
    action["parameters"]["properties"] = props = {key: value for key, value in props.items() if key in names}
    props["action_type"] = {"type": "string", "enum": ["ACT", "WAIT", "SET_OBJECT_STATE"]}
    props.update({
        "target_agent_key": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 100},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 240},
        "responses": {"type": "array", "maxItems": 100, "items": {
            "type": "object", "properties": {
                "request_id": {"type": "string"}, "message": {"type": "string", "minLength": 1}},
            "required": ["request_id", "message"], "additionalProperties": False}},
    })
    for tool in available:
        tool["description"] = tool["description"].replace("this Agent", "this Game Object").replace("The Agent identity", "The Game Object identity")
    return available


def authoring_interface_catalog() -> list[dict[str, Any]]:
    """Chinese author guidance with the exact executable names and schemas."""
    labels = {
        "world-perceive": ("感知周围世界", "只读获取当前位置的四层空间语义、附近人物、对象和事件。可选 radius_tiles 只能缩小已配置的视野；返回内容受注意力带宽约束。"),
        "world-navigate": ("查询导航路线", "从当前位置检查真实坐标或已知地址的可达性，返回距离和路径首格 next_coord；本接口不会移动。继续前往目的地时，MOVE 使用原始目标，不能把路径首格误当最终目的地。固定对象不能调用。"),
        "world-act": ("提交本轮世界动作", "仅根大脑或对象根技能可调用，子技能只能返回行动建议。每名参与者每步最多成功接受一次动作；成功后结束本轮。人物可使用 MOVE、ACT、WAIT、SPEAK、INTERACT、SET_OBJECT_STATE，最终事实由内核统一提交；对象仅可 ACT、WAIT 或修改自身状态。"),
        "memory-stream-search": ("检索私有记忆", "检索当前参与者自己的有效持久记忆。可选 query 和 limit；身份由系统绑定，不能读取其他人物或对象的私有记忆。"),
        "memory-stream-append": ("写入私有记忆", "写入当前参与者的自然语言记忆，必填 content；可附类型、重要性、主谓宾、地址和证据记忆 ID。只记录有依据的观察或明确标注的计划，写入后即可检索。"),
        "memory-stream-supersede": ("修正私有记忆", "以新内容替代当前参与者的一条有效记忆，必填 memory_id 和 content，可附 reason。旧记录保留历史并标为已替代。"),
        "memory-stream-invalidate": ("作废私有记忆", "将当前参与者的一条错误或过期记忆标记失效，必填 memory_id 和 reason。历史仍保留，默认检索不再返回该有效事实。"),
    }
    objects = {item["name"]: item for item in object_interface_catalog()}
    result = []
    for item in simulation_interface_catalog():
        name = item["name"]
        display_name, description_zh = labels[name]
        entry = {"name": name, "display_name": display_name, "description": item["description"],
                 "description_zh": description_zh, "input_schema": item["parameters"],
                 "object_available": name in objects, "root_only": name == "world-act"}
        if name in objects:
            obj = objects[name]
            entry.update(object_description=obj["description"], object_input_schema=obj["parameters"],
                         object_description_zh=("固定对象每步在人物动作后执行一次，仅允许 ACT、WAIT、SET_OBJECT_STATE；只可修改自身状态。可在同次动作的 responses 中回复真实待处理 request_id。state 外观标签公开，其余状态字段及私有记忆不自动公开。"
                                                if name == "world-act" else description_zh))
        result.append(entry)
    return result
