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
    base_url = base_url.rstrip("/")
    return base_url if base_url.endswith("/v1") else f"{base_url}/v1"


def _discover_embedding_model(api_base, api_key, timeout):
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
        item.get("id")
        for item in response.json().get("data", [])
        if item.get("id")
    ]
    if not models:
        raise RuntimeError("GET /v1/models returned no embedding model IDs")
    return models[0]


class LlamaIndex:
    def __init__(self, embedding_config, path=None, *, clock):
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
                "embedding provider {} is not supported".format(embedding_config["provider"])
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
        return node_id in self._index.docstore.docs

    def find_node(self, node_id):
        return self._index.docstore.docs[node_id]

    def get_nodes(self, filter=None):
        def _check(node):
            if not filter:
                return True
            return filter(node)

        return [n for n in self._index.docstore.docs.values() if _check(n)]

    def remove_nodes(self, node_ids, delete_from_docstore=True):
        self._index.delete_nodes(node_ids, delete_from_docstore=delete_from_docstore)

    def cleanup(self):
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
        kwargs = {
            "similarity_top_k": similarity_top_k,
            "text_qa_template": text_qa_template,
            "refine_template": refine_template,
            "filters": filters,
        }
        for attempt in range(self._retry_attempts):
            try:
                if query_creator:
                    query_engine = query_creator(retriever=self._index.as_retriever(**kwargs))
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
        path = path or self._path
        if path is None:
            raise ValueError("an explicit storage path is required")
        os.makedirs(path, exist_ok=True)
        self._index.storage_context.persist(path)
        utils.save_dict(self._config, os.path.join(path, "index_config.json"))

    @property
    def nodes_num(self):
        return len(self._index.docstore.docs)
