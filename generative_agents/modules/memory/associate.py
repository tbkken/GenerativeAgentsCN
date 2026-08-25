"""generative_agents.memory.associate"""

import datetime
from llama_index.core.retrievers import BaseRetriever
from llama_index.core.vector_stores import MetadataFilters, ExactMatchFilter
from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever

from generative_agents.modules.storage.index import LlamaIndex
from generative_agents.modules import utils
from .event import Event


def enforce_memory_limit(memory, max_memory):
    """执行 的`enforce`记忆`limit`操作。

    参数:
        memory: 当前读取、更新或转换的记忆记录。
        max_memory: 记忆允许的最大值。

    返回:
        返回函数计算得到的结果。

    异常:
        ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
    """
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
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            describe: 事件、行为或记忆的人类可读描述文本。
            node_id: `node`的唯一标识。
            node_type: 节点类型判别值，用于选择对应的校验与转换规则。
            subject: 事件三元组中的主体，通常是智能体或世界对象标识。
            predicate: 事件三元组中描述主体与宾语关系的谓词。
            object: 事件三元组中的宾语或当前交互对象。
            address: 由层级名称组成的空间地址，用于定位地图中的区域、场所或对象。
            poignancy: 记忆重要性评分，通常取 1 到 10。
            create: 记忆、事件或记录的创建时间；为空时使用当前仿真时间。 默认值：`None`。
            expire: 记忆的过期时间；为空时按记忆策略计算或表示不过期。 默认值：`None`。
            access: 传入当前算法的`access`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。
            evidence_memory_ids: 需要批量处理的`evidence`记忆唯一标识集合。 默认值：`None`。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。 默认值：`None`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        self.node_id = node_id
        self.node_type = node_type
        self.event = Event(
            subject, predicate, object, describe=describe, address=address.split(":")
        )
        self.poignancy = poignancy
        legacy_timezone = (
            clock.get_date().tzinfo if clock is not None else datetime.timezone.utc
        )
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
        """执行 `Concept` 的`abstract`操作。

        返回:
            返回函数计算得到的结果。
        """
        return {
            "{}(P.{})".format(self.node_type, self.poignancy): str(self.event),
            "duration": "{} ~ {} (access: {})".format(
                self.create.strftime("%Y%m%d-%H:%M"),
                self.expire.strftime("%Y%m%d-%H:%M"),
                self.access.strftime("%Y%m%d-%H:%M"),
            ),
        }

    def __str__(self):
        """执行`str`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        return utils.dump_dict(self.abstract())

    @property
    def describe(self):
        """执行 `Concept` 的`describe`操作。

        返回:
            返回函数计算得到的结果。
        """
        return self.event.get_describe()

    @classmethod
    def from_node(cls, node, *, clock=None):
        """执行 `Concept` 的`from``node`操作。

        参数:
            node: 当前遍历、校验或转换的树节点。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        return cls(node.text, node.id_, clock=clock, **node.metadata)

    @classmethod
    def from_event(cls, node_id, node_type, event, poignancy, *, clock):
        """执行 `Concept` 的`from`事件操作。

        参数:
            node_id: `node`的唯一标识。
            node_type: 节点类型判别值，用于选择对应的校验与转换规则。
            event: 当前感知、处理或写入结果账本的领域事件。
            poignancy: 记忆重要性评分，通常取 1 到 10。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。

        返回:
            返回函数计算得到的结果。
        """
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
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。
            *args: 传给底层调用的额外位置参数，顺序和含义与被调用接口保持一致。
            **kwargs: 传给底层调用的额外关键字参数，键名和含义与被调用接口保持一致。

        返回:
            无返回值。
        """
        self._config = config
        self._clock = clock
        self._vector_retriever = VectorIndexRetriever(*args, **kwargs)
        super().__init__()

    def _retrieve(self, query_bundle):
        """执行`retrieve`的内部处理，供当前模块或类复用。

        参数:
            query_bundle: 传入当前算法的`query``bundle`；其结构与有效范围由类型注解和调用协议共同限定。

        返回:
            返回函数计算得到的结果。
        """

        nodes = self._vector_retriever.retrieve(query_bundle)
        if not nodes:
            return []
        timezone = self._clock.get_date().tzinfo
        nodes = sorted(
            nodes,
            key=lambda n: utils.to_date(n.metadata["access"], naive_timezone=timezone),
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
        """执行`normalize`的内部处理，供当前模块或类复用。

        参数:
            data: 待编码、解码、校验或持久化的原始数据。
            factor: 传入当前算法的`factor`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`1`。
            t_min: 传入当前算法的`t``min`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`0`。
            t_max: 传入当前算法的`t``max`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`1`。

        返回:
            返回函数计算得到的结果。
        """
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
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。
            embedding: 传入当前算法的`embedding`；其结构与有效范围由类型注解和调用协议共同限定。
            retention: 需要保留的最新检查点、日志或记录数量。 默认值：`8`。
            max_memory: 记忆允许的最大值。 默认值：`-1`。
            max_importance: `importance`允许的最大值。 默认值：`10`。
            recency_decay: 传入当前算法的`recency``decay`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`0.995`。
            recency_weight: 传入当前算法的`recency``weight`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`0.5`。
            relevance_weight: 传入当前算法的`relevance``weight`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`3`。
            importance_weight: 传入当前算法的`importance``weight`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`2`。
            memory: 当前读取、更新或转换的记忆记录。 默认值：`None`。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。 默认值：`None`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
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
        """执行 `Associate` 的`abstract`操作。

        返回:
            返回函数计算得到的结果。
        """
        des = {"nodes": self._index.nodes_num}
        for t in ["event", "chat", "thought"]:
            des[t] = [self.find_concept(c).describe for c in self.memory[t]]
        return des

    def __str__(self):
        """执行`str`的内部处理，供当前模块或类复用。

        返回:
            返回函数计算得到的结果。
        """
        return utils.dump_dict(self.abstract())

    def cleanup_index(self, *, record=True):
        """执行 `Associate` 的`cleanup``index`操作。

        参数:
            record: 当前读取、校验、投影或序列化的持久化记录。 默认值：`True`。

        返回:
            无返回值。
        """
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
        """执行 `Associate` 的`add``node`操作。

        参数:
            node_type: 节点类型判别值，用于选择对应的校验与转换规则。
            event: 当前感知、处理或写入结果账本的领域事件。
            poignancy: 记忆重要性评分，通常取 1 到 10。
            create: 记忆、事件或记录的创建时间；为空时使用当前仿真时间。 默认值：`None`。
            expire: 记忆的过期时间；为空时按记忆策略计算或表示不过期。 默认值：`None`。
            filling: 写入记忆节点的补充结构化内容。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
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
        """执行 `Associate` 的`last``evicted`操作。

        返回:
            返回按接口约定组织的结果集合。
        """
        return self._last_evicted

    def to_concept(self, node):
        """执行 `Associate` 的`to``concept`操作。

        参数:
            node: 当前遍历、校验或转换的树节点。

        返回:
            返回函数计算得到的结果。
        """
        return Concept.from_node(node, clock=self._clock)

    def find_concept(self, node_id):
        """执行 `Associate` 的`find``concept`操作。

        参数:
            node_id: `node`的唯一标识。

        返回:
            返回函数计算得到的结果。
        """
        return self.to_concept(self._index.find_node(node_id))

    def _retrieve_nodes(self, node_type, text=None):
        """执行`retrieve``nodes`的内部处理，供当前模块或类复用。

        参数:
            node_type: 节点类型判别值，用于选择对应的校验与转换规则。
            text: 待规范化、解析、脱敏或写入的文本。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
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
        """执行 `Associate` 的`retrieve``events`操作。

        参数:
            text: 待规范化、解析、脱敏或写入的文本。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        return self._retrieve_nodes("event", text)

    def retrieve_thoughts(self, text=None):
        """执行 `Associate` 的`retrieve``thoughts`操作。

        参数:
            text: 待规范化、解析、脱敏或写入的文本。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        return self._retrieve_nodes("thought", text)

    def retrieve_chats(self, name=None):
        """执行 `Associate` 的`retrieve``chats`操作。

        参数:
            name: 目标对象的人类可读名称。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        text = ("对话 " + name) if name else None
        return self._retrieve_nodes("chat", text)

    def retrieve_focus(self, focus, retrieve_max=30, reduce_all=True):
        """执行 `Associate` 的`retrieve``focus`操作。

        参数:
            focus: 当前反思、检索或对话需要重点关注的主题。
            retrieve_max: 传入当前算法的`retrieve``max`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`30`。
            reduce_all: 传入当前算法的`reduce``all`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`True`。

        返回:
            返回函数计算得到的结果。
        """

        def _create_retriever(*args, **kwargs):
            """创建`retriever`。

            参数:
                *args: 传给底层调用的额外位置参数，顺序和含义与被调用接口保持一致。
                **kwargs: 传给底层调用的额外关键字参数，键名和含义与被调用接口保持一致。

            返回:
                返回函数计算得到的结果。
            """
            self._retrieve_config["retrieve_max"] = retrieve_max
            return AssociateRetriever(
                self._retrieve_config, self._clock, *args, **kwargs
            )

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
            for text, nodes in retrieved.items()
        }

    def _mark_accessed(self, nodes, node_type):
        """执行`mark``accessed`的内部处理，供当前模块或类复用。

        参数:
            nodes: 需要批量遍历、校验或转换的节点集合。
            node_type: 节点类型判别值，用于选择对应的校验与转换规则。

        返回:
            无返回值。
        """
        for node in nodes:
            self._pending_accessed[node.id_] = node_type

    def drain_lifecycle_events(self):
        """执行 `Associate` 的`drain``lifecycle``events`操作。

        返回:
            返回函数计算得到的结果。
        """

        accessed = tuple(sorted(self._pending_accessed.items()))
        expired = tuple(self._pending_expired)
        self._pending_accessed.clear()
        self._pending_expired.clear()
        return {"accessed": accessed, "expired": expired}

    def get_relation(self, node):
        """获取`relation`。

        参数:
            node: 当前遍历、校验或转换的树节点。

        返回:
            返回函数计算得到的结果。
        """
        return {
            "node": node,
            "events": self.retrieve_events(node.describe),
            "thoughts": self.retrieve_thoughts(node.describe),
        }

    def to_dict(self):
        """执行 `Associate` 的`to``dict`操作。

        返回:
            返回函数计算得到的结果。
        """
        return {"memory": {key: list(values) for key, values in self.memory.items()}}

    def export_storage(self, path):
        """执行 `Associate` 的`export`存储操作。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。

        返回:
            无返回值。
        """
        self._index.save(path)

    @property
    def index(self):
        """执行 `Associate` 的`index`操作。

        返回:
            返回函数计算得到的结果。
        """
        return self._index
