"""generative_agents.memory.associate"""

import datetime
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter
from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever

from generative_agents.modules.storage.index import LlamaIndex
from generative_agents.modules import utils
from .event import Event


def enforce_memory_limit(memory, max_memory):
    """Return exact kept/evicted partitions for one memory type."""
    if max_memory == -1:
        return list(memory), []
    if max_memory <= 0:
        raise ValueError("max_memory must be -1 or positive")
    return list(memory[:max_memory]), list(memory[max_memory:])


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
        evidence_memory_ids=None,
        clock=None,
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.event = Event(
            subject, predicate, object, describe=describe, address=address.split(":")
        )
        self.poignancy = poignancy
        legacy_timezone = clock.get_date().tzinfo if clock is not None else datetime.timezone.utc
        if create:
            self.create = utils.to_date(create, naive_timezone=legacy_timezone)
        elif clock is not None:
            self.create = clock.get_date()
        else:
            raise ValueError("Concept requires create or an injected clock")
        if expire:
            self.expire = utils.to_date(expire, naive_timezone=legacy_timezone)
        else:
            self.expire = self.create + datetime.timedelta(days=30)
        self.access = (
            utils.to_date(access, naive_timezone=legacy_timezone)
            if access
            else self.create
        )
        self.evidence_memory_ids = tuple(evidence_memory_ids or ())

    def abstract(self):
        return {
            "{}(P.{})".format(self.node_type, self.poignancy): str(self.event),
            "duration": "{} ~ {} (access: {})".format(
                self.create.strftime("%Y%m%d-%H:%M"),
                self.expire.strftime("%Y%m%d-%H:%M"),
                self.access.strftime("%Y%m%d-%H:%M"),
            ),
        }

    def __str__(self):
        return utils.dump_dict(self.abstract())

    @property
    def describe(self):
        return self.event.get_describe()

    @classmethod
    def from_node(cls, node, *, clock=None):
        return cls(node.text, node.id_, clock=clock, **node.metadata)

    @classmethod
    def from_event(cls, node_id, node_type, event, poignancy, *, clock):
        return cls(
            event.get_describe(),
            node_id,
            node_type,
            event.subject,
            event.predicate,
            event.object,
            ":".join(event.address),
            poignancy,
            clock=clock,
        )


class AssociateRetriever(BaseRetriever):
    def __init__(self, config, clock, *args, **kwargs) -> None:
        self._config = config
        self._clock = clock
        self._vector_retriever = VectorIndexRetriever(*args, **kwargs)
        super().__init__()

    def _retrieve(self, query_bundle):
        """Retrieve nodes given query."""

        nodes = self._vector_retriever.retrieve(query_bundle)
        if not nodes:
            return []
        timezone = self._clock.get_date().tzinfo
        nodes = sorted(
            nodes,
            key=lambda n: utils.to_date(
                n.metadata["access"], naive_timezone=timezone
            ),
            reverse=True,
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
            n.metadata["access"] = self._clock.get_date("%Y%m%d-%H:%M:%S")
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
        clock=None,
    ):
        if clock is None:
            raise ValueError("Associate requires an injected clock")
        self._clock = clock
        self._index = LlamaIndex(embedding, path, clock=clock)
        self.memory = memory or {"event": [], "thought": [], "chat": []}
        self._pending_accessed: dict[str, str] = {}
        self._pending_expired: list[tuple[str, str]] = []
        self.cleanup_index(record=False)
        self.retention = retention
        self.max_memory = max_memory
        self.max_importance = max_importance
        self._last_evicted: tuple[str, ...] = ()
        self._retrieve_config = {
            "recency_decay": recency_decay,
            "recency_weight": recency_weight,
            "relevance_weight": relevance_weight,
            "importance_weight": importance_weight,
        }

    def abstract(self):
        des = {"nodes": self._index.nodes_num}
        for t in ["event", "chat", "thought"]:
            des[t] = [self.find_concept(c).describe for c in self.memory[t]]
        return des

    def __str__(self):
        return utils.dump_dict(self.abstract())

    def cleanup_index(self, *, record=True):
        node_ids = self._index.cleanup()
        if record:
            for node_type, nodes in self.memory.items():
                self._pending_expired.extend(
                    (node_id, node_type) for node_id in nodes if node_id in node_ids
                )
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
        create = create or self._clock.get_date()
        expire = expire or (create + datetime.timedelta(days=30))
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
            "evidence_memory_ids": list(filling or ()),
        }
        node = self._index.add_node(event.get_describe(), metadata)
        memory = self.memory[node_type]
        memory.insert(0, node.id_)
        self._last_evicted = ()
        if len(memory) > self.max_memory > 0:
            kept, evicted = enforce_memory_limit(memory, self.max_memory)
            self._index.remove_nodes(evicted)
            self.memory[node_type] = kept
            self._last_evicted = tuple(evicted)
        return self.to_concept(node)

    @property
    def last_evicted(self) -> tuple[str, ...]:
        """IDs evicted by the most recent add, for the result ledger."""
        return self._last_evicted

    def to_concept(self, node):
        return Concept.from_node(node, clock=self._clock)

    def find_concept(self, node_id):
        return self.to_concept(self._index.find_node(node_id))

    def _retrieve_nodes(self, node_type, text=None):
        if text:
            filters = MetadataFilters(
                filters=[ExactMatchFilter(key="node_type", value=node_type)]
            )
            nodes = self._index.retrieve(
                text, filters=filters, node_ids=self.memory[node_type]
            )
        else:
            nodes = [self._index.find_node(n) for n in self.memory[node_type]]
        selected = nodes[: self.retention]
        self._mark_accessed(selected, node_type)
        return [self.to_concept(n) for n in selected]

    def retrieve_events(self, text=None):
        return self._retrieve_nodes("event", text)

    def retrieve_thoughts(self, text=None):
        return self._retrieve_nodes("thought", text)

    def retrieve_chats(self, name=None):
        text = ("对话 " + name) if name else None
        return self._retrieve_nodes("chat", text)

    def retrieve_focus(self, focus, retrieve_max=30, reduce_all=True):
        def _create_retriever(*args, **kwargs):
            self._retrieve_config["retrieve_max"] = retrieve_max
            return AssociateRetriever(self._retrieve_config, self._clock, *args, **kwargs)

        retrieved = {}
        node_ids = self.memory["event"] + self.memory["thought"]
        for text in focus:
            nodes = self._index.retrieve(
                text,
                similarity_top_k=len(node_ids),
                node_ids=node_ids,
                retriever_creator=_create_retriever,
            )
            for node in nodes:
                self._pending_accessed[node.id_] = str(
                    node.metadata.get("node_type") or "event"
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

    def _mark_accessed(self, nodes, node_type):
        for node in nodes:
            self._pending_accessed[node.id_] = node_type

    def drain_lifecycle_events(self):
        """Return retrieval/expiry facts once for the current simulation step."""

        accessed = tuple(sorted(self._pending_accessed.items()))
        expired = tuple(self._pending_expired)
        self._pending_accessed.clear()
        self._pending_expired.clear()
        return {"accessed": accessed, "expired": expired}

    def get_relation(self, node):
        return {
            "node": node,
            "events": self.retrieve_events(node.describe),
            "thoughts": self.retrieve_thoughts(node.describe),
        }

    def to_dict(self):
        return {"memory": {key: list(values) for key, values in self.memory.items()}}

    def export_storage(self, path):
        """Explicit checkpoint hook; serialization itself performs no IO."""
        self._index.save(path)

    @property
    def index(self):
        return self._index
