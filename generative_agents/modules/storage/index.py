"""generative_agents.storage.index"""

import os
import time
import requests
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever
from llama_index.core.schema import TextNode
from llama_index import core as index_core
from llama_index.embeddings.ollama import OllamaEmbedding
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.core.node_parser import SentenceSplitter
from generative_agents.modules import utils
from generative_agents.modules.utils.retry import interruptible_wait


def _openai_api_base(base_url):
    """执行`openai``api``base`的内部处理，供当前模块或类复用。

    参数:
        base_url: `base`的访问或连接地址。

    返回:
        返回函数计算得到的结果。
    """
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith("/v1") else f"{base_url}/v1"


def _discover_embedding_model(api_base, api_key, timeout):
    """执行`discover``embedding`模型的内部处理，供当前模块或类复用。

    参数:
        api_base: 传入当前算法的`api``base`；其结构与有效范围由类型注解和调用协议共同限定。
        api_key: 调用模型服务使用的 API 密钥；为空时由密钥解析器按配置加载。
        timeout: 等待操作的最长秒数；超时后按调用协议返回或抛出异常。

    返回:
        返回函数计算得到的结果。

    异常:
        RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
    """
    headers = {"Authorization": f"Bearer {api_key or 'unused'}"}
    response = requests.get(
        f"{api_base}/models",
        headers=headers,
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"HTTP {response.status_code} from {response.url}: {response.text}"
        )
    models = [
        item.get("id") for item in response.json().get("data", []) if item.get("id")
    ]
    if not models:
        raise RuntimeError("GET /v1/models returned no embedding model IDs")
    return models[0]


class LlamaIndex:
    """为单个智能体封装向量索引，并把嵌入模型限制在实例范围内。"""

    def __init__(self, embedding_config, path=None, *, clock):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            embedding_config: 传入当前算法的`embedding`配置；其结构与有效范围由类型注解和调用协议共同限定。
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 默认值：`None`。
            clock: 提供当前时间的可替换时钟，便于测试并避免直接依赖系统时间。

        返回:
            无返回值。

        异常:
            NotImplementedError: 当当前实现不支持所请求的操作时抛出。
        """
        self._clock = clock
        self._control = embedding_config.get("_control")
        self._logger = embedding_config.get("_logger")
        self._sleep = embedding_config.get("_sleep", time.sleep)
        self._retry_attempts = embedding_config.get(
            "index_operation_retry_attempts", embedding_config.get("max_retries", 10)
        )
        self._retry_backoff = embedding_config.get("retry_backoff_seconds", 5)
        self._config = {"max_nodes": 0}
        resolved_model = embedding_config["model"]
        if embedding_config["provider"] == "hugging_face":
            embed_model = HuggingFaceEmbedding(model_name=resolved_model)
        elif embedding_config["provider"] == "ollama":
            embed_model = OllamaEmbedding(
                model_name=resolved_model,
                base_url=embedding_config["base_url"],
                ollama_additional_kwargs={"mirostat": 0},
            )
        elif embedding_config["provider"] in ("openai", "openai_compatible"):
            api_base = _openai_api_base(embedding_config["base_url"])
            api_key = embedding_config.get("api_key") or "unused"
            timeout = embedding_config.get("timeout", 120)
            if not resolved_model or resolved_model == "auto":
                resolved_model = _discover_embedding_model(
                    api_base,
                    api_key,
                    timeout,
                )
            embed_model = OpenAIEmbedding(
                model_name=resolved_model,
                api_base=api_base,
                api_key=api_key,
                timeout=timeout,
                max_retries=embedding_config.get("max_retries", 3),
            )
        else:
            raise NotImplementedError(
                "embedding provider {} is not supported".format(
                    embedding_config["provider"]
                )
            )

        transformations = [SentenceSplitter(chunk_size=512, chunk_overlap=64)]
        self._embed_model = embed_model
        self._transformations = transformations
        if path and os.path.exists(path):
            self._index = index_core.load_index_from_storage(
                index_core.StorageContext.from_defaults(persist_dir=path),
                embed_model=embed_model,
                transformations=transformations,
                show_progress=True,
            )
            self._config = utils.load_dict(os.path.join(path, "index_config.json"))
        else:
            self._index = index_core.VectorStoreIndex(
                [],
                embed_model=embed_model,
                transformations=transformations,
                show_progress=True,
            )
        self._config["embedding"] = {
            "provider": embedding_config["provider"],
            "model": resolved_model,
        }
        self._path = path

    def add_node(
        self,
        text,
        metadata=None,
        exclude_llm_keys=None,
        exclude_embedding_keys=None,
        id=None,
    ):
        """执行 `LlamaIndex` 的`add``node`操作。

        参数:
            text: 待规范化、解析、脱敏或写入的文本。
            metadata: 传入当前算法的`metadata`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。
            exclude_llm_keys: 需要批量处理的`exclude``llm`稳定键集合。 默认值：`None`。
            exclude_embedding_keys: 需要批量处理的`exclude``embedding`稳定键集合。 默认值：`None`。
            id: 传入当前算法的`id`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。

        返回:
            返回函数计算得到的结果。

        异常:
            InterruptedError: 当底层操作报告该异常条件时抛出。
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        for attempt in range(self._retry_attempts):
            try:
                metadata = metadata or {}
                exclude_llm_keys = exclude_llm_keys or list(metadata.keys())
                exclude_embedding_keys = exclude_embedding_keys or list(metadata.keys())
                id = id or "node_" + str(self._config["max_nodes"])
                self._config["max_nodes"] += 1
                node = TextNode(
                    text=text,
                    id_=id,
                    metadata=metadata,
                    excluded_llm_metadata_keys=exclude_llm_keys,
                    excluded_embed_metadata_keys=exclude_embedding_keys,
                )
                self._index.insert_nodes([node])
                return node
            except Exception as e:
                if self._logger is not None:
                    self._logger.warning(
                        "embedding index add failed (attempt %s/%s): %s",
                        attempt + 1,
                        self._retry_attempts,
                        e,
                    )
                if attempt + 1 < self._retry_attempts and self._retry_backoff:
                    if not interruptible_wait(
                        self._retry_backoff,
                        control=self._control,
                        sleep=self._sleep,
                    ):
                        raise InterruptedError(
                            "embedding index retry interrupted by Run control"
                        ) from e
        raise RuntimeError(
            f"LlamaIndex.add_node() failed after {self._retry_attempts} retries"
        )

    def has_node(self, node_id):
        """判断是否存在`node`。

        参数:
            node_id: `node`的唯一标识。

        返回:
            返回函数计算得到的结果。
        """
        return node_id in self._index.docstore.docs

    def find_node(self, node_id):
        """执行 `LlamaIndex` 的`find``node`操作。

        参数:
            node_id: `node`的唯一标识。

        返回:
            返回函数计算得到的结果。
        """
        return self._index.docstore.docs[node_id]

    def get_nodes(self, filter=None):
        """获取`nodes`。

        参数:
            filter: 传入当前算法的`filter`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """

        def _check(node):
            """执行`check`的内部处理，供当前模块或类复用。

            参数:
                node: 当前遍历、校验或转换的树节点。

            返回:
                返回函数计算得到的结果。
            """
            if not filter:
                return True
            return filter(node)

        return [n for n in self._index.docstore.docs.values() if _check(n)]

    def remove_nodes(self, node_ids, delete_from_docstore=True):
        """移除`nodes`。

        参数:
            node_ids: 需要批量处理的`node`唯一标识集合。
            delete_from_docstore: 传入当前算法的`delete``from``docstore`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`True`。

        返回:
            无返回值。
        """
        self._index.delete_nodes(node_ids, delete_from_docstore=delete_from_docstore)

    def cleanup(self):
        """执行 `LlamaIndex` 的`cleanup`操作。

        返回:
            返回函数计算得到的结果。
        """
        clock_now = self._clock.get_date()
        now, remove_ids = utils.as_utc(clock_now), []
        for node_id, node in self._index.docstore.docs.items():
            create = utils.to_date(
                node.metadata["create"], naive_timezone=clock_now.tzinfo
            )
            expire = utils.to_date(
                node.metadata["expire"], naive_timezone=clock_now.tzinfo
            )
            if create > now or expire < now:
                remove_ids.append(node_id)
        self.remove_nodes(remove_ids)
        return remove_ids

    def retrieve(
        self,
        text,
        similarity_top_k=5,
        filters=None,
        node_ids=None,
        retriever_creator=None,
    ):
        """执行 `LlamaIndex` 的`retrieve`操作。

        参数:
            text: 待规范化、解析、脱敏或写入的文本。
            similarity_top_k: 向量检索阶段按相似度保留的最大候选数量。 默认值：`5`。
            filters: 需要同时应用到查询或检索过程的筛选条件集合。 默认值：`None`。
            node_ids: 需要批量处理的`node`唯一标识集合。 默认值：`None`。
            retriever_creator: 传入当前算法的`retriever``creator`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        try:
            retriever_creator = retriever_creator or VectorIndexRetriever
            return retriever_creator(
                self._index,
                similarity_top_k=similarity_top_k,
                filters=filters,
                node_ids=node_ids,
            ).retrieve(text)
        except Exception as e:
            if self._logger is not None:
                self._logger.warning("embedding retrieval failed: %s", e)
            return []

    def query(
        self,
        text,
        similarity_top_k=5,
        text_qa_template=None,
        refine_template=None,
        filters=None,
        query_creator=None,
    ):
        """执行 `LlamaIndex` 的`query`操作。

        参数:
            text: 待规范化、解析、脱敏或写入的文本。
            similarity_top_k: 向量检索阶段按相似度保留的最大候选数量。 默认值：`5`。
            text_qa_template: 传入当前算法的`text``qa``template`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。
            refine_template: 传入当前算法的`refine``template`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。
            filters: 需要同时应用到查询或检索过程的筛选条件集合。 默认值：`None`。
            query_creator: 传入当前算法的`query``creator`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。

        返回:
            返回函数计算得到的结果。

        异常:
            InterruptedError: 当底层操作报告该异常条件时抛出。
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        kwargs = {
            "similarity_top_k": similarity_top_k,
            "text_qa_template": text_qa_template,
            "refine_template": refine_template,
            "filters": filters,
        }
        for attempt in range(self._retry_attempts):
            try:
                if query_creator:
                    query_engine = query_creator(
                        retriever=self._index.as_retriever(**kwargs)
                    )
                else:
                    query_engine = self._index.as_query_engine(**kwargs)
                return query_engine.query(text)
            except Exception as e:
                if self._logger is not None:
                    self._logger.warning(
                        "embedding query failed (attempt %s/%s): %s",
                        attempt + 1,
                        self._retry_attempts,
                        e,
                    )
                if attempt + 1 < self._retry_attempts and self._retry_backoff:
                    if not interruptible_wait(
                        self._retry_backoff,
                        control=self._control,
                        sleep=self._sleep,
                    ):
                        raise InterruptedError(
                            "embedding query retry interrupted by Run control"
                        ) from e
        raise RuntimeError(
            f"LlamaIndex.query() failed after {self._retry_attempts} retries"
        )

    def save(self, path=None):
        """执行 `LlamaIndex` 的`save`操作。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。 默认值：`None`。

        返回:
            无返回值。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        path = path or self._path
        if path is None:
            raise ValueError("an explicit storage path is required")
        os.makedirs(path, exist_ok=True)
        self._index.storage_context.persist(path)
        utils.save_dict(self._config, os.path.join(path, "index_config.json"))

    @property
    def nodes_num(self):
        """执行 `LlamaIndex` 的`nodes``num`操作。

        返回:
            返回函数计算得到的结果。
        """
        return len(self._index.docstore.docs)
