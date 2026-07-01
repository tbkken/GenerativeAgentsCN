"""generative_agents.memory.associate"""

import datetime
import json
import os
import re
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter
from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever

from modules.storage.index import LlamaIndex
from modules import utils
from .event import Event


DEFAULT_MEMORY_TYPES = [
    "event",
    "chat",
    "thought",
    "relationship",
    "goal",
    "summary",
    "skill",
    "conflict",
]

ADVANCED_MEMORY_TYPES = {"relationship", "goal", "summary", "skill", "conflict"}


class Concept:
    def __init__(
        self,
        describe,
        node_id,
        node_type,
        subject,
        predicate,
        object,
        address,
        poignancy,
        create=None,
        expire=None,
        access=None,
        **metadata,
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.event = Event(
            subject, predicate, object, describe=describe, address=address.split(":")
        )
        self.poignancy = poignancy
        self.create = utils.to_date(create) if create else utils.get_timer().get_date()
        if expire:
            self.expire = utils.to_date(expire)
        else:
            self.expire = self.create + datetime.timedelta(days=30)
        self.access = utils.to_date(access) if access else self.create
        self.metadata = metadata or {}
        self.source_nodes = self.metadata.get("source_nodes", [])
        self.source_type = self.metadata.get("source_type")
        self.confidence = self.metadata.get("confidence")
        self.generated_by = self.metadata.get("generated_by")
        self.downstream_use = self.metadata.get("downstream_use")
        self.conflict_with = self.metadata.get("conflict_with", [])

    def abstract(self):
        des = {
            "{}(P.{})".format(self.node_type, self.poignancy): str(self.event),
            "duration": "{} ~ {} (access: {})".format(
                self.create.strftime("%Y%m%d-%H:%M"),
                self.expire.strftime("%Y%m%d-%H:%M"),
                self.access.strftime("%Y%m%d-%H:%M"),
            ),
        }
        governance = {}
        for key in [
            "source_nodes",
            "source_type",
            "confidence",
            "generated_by",
            "downstream_use",
            "conflict_with",
        ]:
            if self.metadata.get(key) not in (None, "", []):
                governance[key] = self.metadata[key]
        if governance:
            des["governance"] = governance
        return des

    def __str__(self):
        return utils.dump_dict(self.abstract())

    @property
    def describe(self):
        return self.event.get_describe()

    @classmethod
    def from_node(cls, node):
        return cls(node.text, node.id_, **node.metadata)

    @classmethod
    def from_event(cls, node_id, node_type, event, poignancy):
        return cls(
            event.get_describe(),
            node_id,
            node_type,
            event.subject,
            event.predicate,
            event.object,
            ":".join(event.address),
            poignancy,
        )


class AssociateRetriever(BaseRetriever):
    def __init__(self, config, *args, **kwargs) -> None:
        self._config = config
        self._vector_retriever = VectorIndexRetriever(*args, **kwargs)
        super().__init__()

    def _retrieve(self, query_bundle):
        """Retrieve nodes given query."""

        nodes = self._vector_retriever.retrieve(query_bundle)
        if not nodes:
            return []
        nodes = sorted(
            nodes, key=lambda n: utils.to_date(n.metadata["access"]), reverse=True
        )
        # get scores
        fac = self._config["recency_decay"]
        recency_scores = self._normalize(
            [fac**i for i in range(1, len(nodes) + 1)], self._config["recency_weight"]
        )
        relevance_scores = self._normalize(
            [n.score for n in nodes], self._config["relevance_weight"]
        )
        importance_scores = self._normalize(
            [n.metadata["poignancy"] for n in nodes], self._config["importance_weight"]
        )
        final_scores = {
            n.id_: r1 + r2 + i
            for n, r1, r2, i in zip(
                nodes, recency_scores, relevance_scores, importance_scores
            )
        }
        # re-rank nodes
        nodes = sorted(nodes, key=lambda n: final_scores[n.id_], reverse=True)
        nodes = nodes[: self._config["retrieve_max"]]
        for n in nodes:
            n.metadata["access"] = utils.get_timer().get_date("%Y%m%d-%H:%M:%S")
        return nodes

    def _normalize(self, data, factor=1, t_min=0, t_max=1):
        min_val, max_val = min(data), max(data)
        diff = max_val - min_val
        if diff == 0:
            return [(t_max - t_min) * factor / 2 for _ in data]
        return [(d - min_val) * (t_max - t_min) * factor / diff + t_min for d in data]


class Associate:
    def __init__(
        self,
        path,
        embedding,
        retention=8,
        max_memory=-1,
        max_importance=10,
        recency_decay=0.995,
        recency_weight=0.5,
        relevance_weight=3,
        importance_weight=2,
        memory=None,
        long_term_memory=None,
        memory_governance=None,
    ):
        self._index = LlamaIndex(embedding, path)
        self.memory = memory or {t: [] for t in DEFAULT_MEMORY_TYPES}
        for t in DEFAULT_MEMORY_TYPES:
            self.memory.setdefault(t, [])
        self.cleanup_index()
        self.retention = retention
        self.max_memory = max_memory
        self.max_importance = max_importance
        self.memory_governance = {
            "relationship_update": True,
            "summary_merge": True,
            "summary_poignancy_max": 3,
            "summary_threshold": 3,
            "summary_window": 12,
            "conflict_detection": True,
            "conflict_window": 30,
            **(memory_governance or {}),
        }
        self._retrieve_config = {
            "recency_decay": recency_decay,
            "recency_weight": recency_weight,
            "relevance_weight": relevance_weight,
            "importance_weight": importance_weight,
        }
        if long_term_memory:
            self.import_long_term_memory(long_term_memory)

    def abstract(self):
        des = {"nodes": self._index.nodes_num}
        for t in DEFAULT_MEMORY_TYPES:
            des[t] = [self.find_concept(c).describe for c in self.memory.get(t, [])]
        return des

    def __str__(self):
        return utils.dump_dict(self.abstract())

    def cleanup_index(self):
        node_ids = self._index.cleanup()
        self.memory = {
            n_type: [n for n in nodes if n not in node_ids]
            for n_type, nodes in self.memory.items()
        }

    def add_node(
        self,
        node_type,
        event,
        poignancy,
        create=None,
        expire=None,
        filling=None,
    ):
        create = create or utils.get_timer().get_date()
        expire = expire or (create + datetime.timedelta(days=30))
        self.memory.setdefault(node_type, [])
        metadata = {
            "node_type": node_type,
            "subject": event.subject,
            "predicate": event.predicate,
            "object": event.object,
            "address": ":".join(event.address),
            "poignancy": poignancy,
            "create": create.strftime("%Y%m%d-%H:%M:%S"),
            "expire": expire.strftime("%Y%m%d-%H:%M:%S"),
            "access": create.strftime("%Y%m%d-%H:%M:%S"),
        }
        metadata.update(self._normalize_filling(filling))
        node = self._index.add_node(event.get_describe(), metadata)
        memory = self.memory[node_type]
        memory.insert(0, node.id_)
        if len(memory) >= self.max_memory > 0:
            self._index.remove_nodes(memory[self.max_memory:])
            self.memory[node_type] = memory[: self.max_memory - 1]
        return self.to_concept(node)

    def to_concept(self, node):
        return Concept.from_node(node)

    def find_concept(self, node_id):
        return self.to_concept(self._index.find_node(node_id))

    def _normalize_filling(self, filling):
        if not filling:
            return {}
        if isinstance(filling, dict):
            metadata = {}
            for key in [
                "source_nodes",
                "source_type",
                "confidence",
                "generated_by",
                "downstream_use",
                "last_verified_at",
                "conflict_with",
                "target",
                "affinity",
                "trust",
                "familiarity",
                "last_interaction",
                "summary",
                "claim_a",
                "claim_b",
                "conflict_type",
                "resolution_status",
                "loaded_from",
                "source_experiment",
                "source_node_id",
                "drop_after",
                "reason",
            ]:
                if key in filling and filling[key] is not None:
                    metadata[key] = filling[key]
            if "source_nodes" in metadata and not isinstance(metadata["source_nodes"], list):
                metadata["source_nodes"] = [metadata["source_nodes"]]
            if "conflict_with" in metadata and not isinstance(metadata["conflict_with"], list):
                metadata["conflict_with"] = [metadata["conflict_with"]]
            return metadata
        if isinstance(filling, (list, tuple, set)):
            return {
                "source_nodes": list(filling),
                "source_type": "reflection",
                "generated_by": "reflect_insights",
            }
        return {
            "source_nodes": [str(filling)],
            "source_type": "manual",
            "generated_by": "manual",
        }

    def _retrieve_nodes(self, node_type, text=None):
        self.memory.setdefault(node_type, [])
        if text:
            filters = MetadataFilters(
                filters=[ExactMatchFilter(key="node_type", value=node_type)]
            )
            nodes = self._index.retrieve(
                text, filters=filters, node_ids=self.memory[node_type]
            )
        else:
            nodes = [self._index.find_node(n) for n in self.memory[node_type]]
        return [self.to_concept(n) for n in nodes[: self.retention]]

    def retrieve_events(self, text=None):
        return self._retrieve_nodes("event", text)

    def retrieve_thoughts(self, text=None):
        return self._retrieve_nodes("thought", text)

    def retrieve_chats(self, name=None):
        text = ("对话 " + name) if name else None
        return self._retrieve_nodes("chat", text)

    def retrieve_relationships(self, target_name=None):
        nodes = []
        for node_id in self.memory.get("relationship", []):
            try:
                node = self.find_concept(node_id)
            except KeyError:
                continue
            if target_name and node.metadata.get("target") != target_name:
                continue
            nodes.append(node)
        return nodes[: self.retention]

    def latest_relationship(self, target_name):
        nodes = self.retrieve_relationships(target_name)
        return nodes[0] if nodes else None

    def relationship_state(self, target_name):
        node = self.latest_relationship(target_name)
        if not node:
            return {
                "target": target_name,
                "affinity": 0,
                "trust": 0,
                "familiarity": 0,
                "summary": "",
                "evidence": [],
                "confidence": 0.0,
            }
        return {
            "target": target_name,
            "affinity": int(node.metadata.get("affinity", 0)),
            "trust": int(node.metadata.get("trust", 0)),
            "familiarity": int(node.metadata.get("familiarity", 0)),
            "summary": node.metadata.get("summary", node.describe),
            "evidence": node.metadata.get("source_nodes", []),
            "confidence": float(node.metadata.get("confidence", 0.0) or 0.0),
        }

    def retrieve_focus(
        self,
        focus,
        retrieve_max=30,
        reduce_all=True,
        node_types=None,
    ):
        def _create_retriever(*args, **kwargs):
            self._retrieve_config["retrieve_max"] = retrieve_max
            return AssociateRetriever(self._retrieve_config, *args, **kwargs)

        retrieved = {}
        node_types = node_types or ("event", "thought")
        node_ids = []
        for node_type in node_types:
            node_ids.extend(self.memory.get(node_type, []))
        if not node_ids:
            return [] if reduce_all else {text: [] for text in focus}
        for text in focus:
            nodes = self._index.retrieve(
                text,
                similarity_top_k=len(node_ids),
                node_ids=node_ids,
                retriever_creator=_create_retriever,
            )
            if reduce_all:
                retrieved.update({n.id_: n for n in nodes})
            else:
                retrieved[text] = nodes
        if reduce_all:
            return [self.to_concept(v) for v in retrieved.values()]
        return {
            text: [self.to_concept(n) for n in nodes]
            for text, nodes, in retrieved.items()
        }

    def retrieve_for_dialogue(self, target_name, context=None):
        focus = [target_name]
        if context:
            focus.append(context)
        return self.retrieve_focus(
            focus,
            retrieve_max=20,
            node_types=("relationship", "chat", "event"),
        )

    def retrieve_for_planning(self, goal):
        return self.retrieve_focus(
            [goal],
            retrieve_max=20,
            node_types=("goal", "summary", "event", "skill"),
        )

    def retrieve_for_reflection(self, focus):
        return self.retrieve_focus(
            focus if isinstance(focus, list) else [focus],
            retrieve_max=30,
            node_types=("event", "thought", "conflict"),
        )

    def retrieve_for_reaction(self, observation):
        return self.retrieve_focus(
            [observation],
            retrieve_max=15,
            node_types=("event", "relationship", "chat"),
        )

    def retrieve_for_recovery(self, outcome):
        return self.retrieve_focus(
            [outcome],
            retrieve_max=15,
            node_types=("skill", "summary", "event"),
        )

    def get_relation(self, node):
        return {
            "node": node,
            "events": self.retrieve_for_reaction(node.describe),
            "thoughts": self.retrieve_thoughts(node.describe),
            "relationships": self.retrieve_relationships(node.event.subject),
        }

    def maybe_create_summary(self, concept, threshold=3, window=12):
        if concept.node_type != "event":
            return None
        threshold = int(self.memory_governance.get("summary_threshold", threshold))
        window = int(self.memory_governance.get("summary_window", window))
        if concept.poignancy > self.memory_governance.get("summary_poignancy_max", 3):
            return None
        candidates = []
        for node_id in self.memory.get("event", [])[1 : window + 1]:
            try:
                node = self.find_concept(node_id)
            except KeyError:
                continue
            same_place = node.event.address == concept.event.address
            same_subject = node.event.subject == concept.event.subject
            low_value = node.poignancy <= self.memory_governance.get("summary_poignancy_max", 3)
            if low_value and (same_place or same_subject):
                candidates.append(node)
        if len(candidates) + 1 < threshold:
            return None
        source_nodes = [concept.node_id] + [n.node_id for n in candidates[: threshold - 1]]
        if self._has_summary_for_sources(source_nodes):
            return None
        summary = "{} 在 {} 附近出现了多条相似的日常事件，可合并为低价值重复记忆。".format(
            concept.event.subject,
            utils.get_timer().get_date("%Y%m%d-%H:%M"),
        )
        event = Event(
            concept.event.subject,
            "摘要",
            "低价值重复事件",
            describe=summary,
            address=concept.event.address,
        )
        return self.add_node(
            "summary",
            event,
            max(1, concept.poignancy),
            filling={
                "source_nodes": source_nodes,
                "source_type": "event",
                "confidence": 0.7,
                "generated_by": "memory_merge",
                "downstream_use": "planning,reflection",
                "reason": "same_subject_or_place_low_poignancy",
            },
        )

    def maybe_detect_conflict(self, concept, window=30):
        if concept.node_type in ADVANCED_MEMORY_TYPES:
            return None
        new_times = self._extract_time_claims(concept.describe)
        if not new_times:
            return None
        for node_type in ("event", "chat", "thought"):
            for node_id in self.memory.get(node_type, [])[:window]:
                if node_id == concept.node_id:
                    continue
                try:
                    other = self.find_concept(node_id)
                except KeyError:
                    continue
                old_times = self._extract_time_claims(other.describe)
                if not old_times or old_times == new_times:
                    continue
                if not self._same_conflict_topic(concept.describe, other.describe):
                    continue
                summary = "检测到时间冲突：{}；{}".format(other.describe, concept.describe)
                event = Event(
                    concept.event.subject,
                    "冲突",
                    "时间",
                    describe=summary,
                    address=concept.event.address,
                )
                return self.add_node(
                    "conflict",
                    event,
                    max(concept.poignancy, other.poignancy, 5),
                    filling={
                        "source_nodes": [other.node_id, concept.node_id],
                        "source_type": "memory_conflict_check",
                        "confidence": 0.65,
                        "generated_by": "memory_conflict_check",
                        "downstream_use": "planning,dialogue,reflection",
                        "conflict_type": "time_conflict",
                        "claim_a": other.describe,
                        "claim_b": concept.describe,
                        "resolution_status": "unresolved",
                    },
                )
        return None

    def _has_summary_for_sources(self, source_nodes):
        source_set = set(source_nodes)
        for node_id in self.memory.get("summary", []):
            try:
                node = self.find_concept(node_id)
            except KeyError:
                continue
            if source_set.issubset(set(node.metadata.get("source_nodes", []))):
                return True
        return False

    def _extract_time_claims(self, text):
        claims = set(re.findall(r"\b\d{1,2}[:：]\d{2}\b", text))
        cn_map = {
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
            "十": 10,
            "十一": 11,
            "十二": 12,
        }
        for prefix, base in [("上午", 0), ("中午", 12), ("下午", 12), ("晚上", 12)]:
            for cn, hour in cn_map.items():
                if prefix + cn + "点" in text:
                    value = hour if prefix == "上午" else (hour if hour == 12 else hour + base)
                    claims.add(f"{value:02d}:00")
        return claims

    def _same_conflict_topic(self, a, b):
        topics = ("派对", "会议", "见面", "约", "晚餐", "午餐", "早餐", "活动", "讨论")
        return any(topic in a and topic in b for topic in topics)

    def import_long_term_memory(self, config):
        if not config.get("enabled", True):
            return 0
        agent_name = config.get("agent_name")
        source_root = config.get("source_root") or config.get("root")
        if not source_root:
            return 0
        candidate_paths = []
        if agent_name:
            candidate_paths.append(os.path.join(source_root, agent_name, "associate", "docstore.json"))
        candidate_paths.append(os.path.join(source_root, "associate", "docstore.json"))
        candidate_paths.append(os.path.join(source_root, "docstore.json"))
        docstore_path = next((p for p in candidate_paths if os.path.exists(p)), None)
        if not docstore_path:
            return 0
        with open(docstore_path, "r", encoding="utf-8") as f:
            docstore = json.load(f)
        docs = docstore.get("docstore/data", {})
        max_nodes = int(config.get("max_nodes", 50))
        allowed_types = set(config.get("types", DEFAULT_MEMORY_TYPES))
        min_confidence = float(config.get("min_confidence", 0.0))
        extend_days = int(config.get("extend_days", 180))
        loaded = 0
        def _sort_key(item):
            metadata = item[1].get("__data__", {}).get("metadata", {})
            return metadata.get("create", "")

        for old_id, payload in sorted(docs.items(), key=_sort_key, reverse=True):
            if loaded >= max_nodes:
                break
            data = payload.get("__data__", {})
            metadata = dict(data.get("metadata", {}))
            node_type = metadata.get("node_type", "event")
            if node_type not in allowed_types:
                continue
            confidence = metadata.get("confidence")
            if confidence is not None and float(confidence) < min_confidence:
                continue
            metadata["loaded_from"] = config.get("label") or source_root
            metadata["source_experiment"] = config.get("source_experiment")
            metadata["source_node_id"] = old_id
            metadata["generated_by"] = metadata.get("generated_by") or "long_term_memory_import"
            metadata["downstream_use"] = metadata.get("downstream_use") or "dialogue,planning,reflection"
            metadata["access"] = utils.get_timer().get_date("%Y%m%d-%H:%M:%S")
            expire_text = metadata.get("expire")
            expire = utils.to_date(expire_text) if expire_text else utils.get_timer().get_date()
            now = utils.get_timer().get_date()
            if expire < now:
                metadata["expire"] = (now + datetime.timedelta(days=extend_days)).strftime(
                    "%Y%m%d-%H:%M:%S"
                )
            node = self._index.add_node(data.get("text", ""), metadata)
            self.memory.setdefault(node_type, [])
            self.memory[node_type].insert(0, node.id_)
            loaded += 1
        return loaded

    def to_dict(self):
        self._index.save()
        return {"memory": self.memory}

    @property
    def index(self):
        return self._index
