"""generative_agents.storage.index"""

import json
import os
import time
from pathlib import Path

import requests
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.bridge.pydantic import Field
from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever
from llama_index.core.schema import TextNode
from llama_index import core as index_core
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core import Settings

from modules import utils


class OllamaHttpEmbedding(BaseEmbedding):
    model_name: str = Field(description="The Ollama embedding model to use.")
    base_url: str = Field(default="http://localhost:11434")

    def _embed(self, text):
        response = requests.post(
            url=f"{self.base_url.rstrip('/')}/api/embed",
            json={
                "model": self.model_name,
                "input": text,
            },
            timeout=300,
        )
        response.raise_for_status()
        return response.json()["embeddings"]

    def _get_text_embedding(self, text):
        return self._embed(text)[0]

    def _get_text_embeddings(self, texts):
        return self._embed(texts)

    def _get_query_embedding(self, query):
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query):
        return self._get_query_embedding(query)


class MiniMaxHttpEmbedding(BaseEmbedding):
    model_name: str = Field(description="The MiniMax embedding model to use.")
    base_url: str = Field(default="https://api.minimax.chat/v1")
    api_key: str = Field(default="")
    group_id: str = Field(default="")

    def _embedding_url(self):
        url = f"{self.base_url.rstrip('/')}/embeddings"
        if self.group_id:
            url = f"{url}?GroupId={self.group_id}"
        return url

    def _embed(self, texts, embedding_type):
        response = requests.post(
            url=self._embedding_url(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            json={
                "model": self.model_name,
                "type": embedding_type,
                "texts": texts,
            },
            timeout=300,
        )
        response.raise_for_status()
        data = response.json()
        base_resp = data.get("base_resp") or {}
        status_code = base_resp.get("status_code", 0)
        if status_code not in (0, "0"):
            raise RuntimeError(f"MiniMax embedding failed: {base_resp}")
        vectors = data.get("vectors")
        if vectors is None and "data" in data:
            vectors = [item["embedding"] for item in data["data"]]
        if not isinstance(vectors, list):
            raise RuntimeError(f"MiniMax embedding response missing vectors: {data}")
        return vectors

    def _get_text_embedding(self, text):
        return self._embed([text], "db")[0]

    def _get_text_embeddings(self, texts):
        return self._embed(texts, "db")

    def _get_query_embedding(self, query):
        return self._embed([query], "query")[0]

    async def _aget_query_embedding(self, query):
        return self._get_query_embedding(query)


class LlamaIndex:
    def __init__(self, embedding_config, path=None):
        self._config = {"max_nodes": 0}
        if embedding_config["provider"] == "hugging_face":
            from llama_index.embeddings.huggingface import HuggingFaceEmbedding

            embed_model = HuggingFaceEmbedding(model_name=embedding_config["model"])
        elif embedding_config["provider"] == "ollama":
            embed_model = OllamaHttpEmbedding(
                model_name=embedding_config["model"],
                base_url=embedding_config["base_url"],
            )
        elif embedding_config["provider"] == "minimax":
            api_key = embedding_config.get("api_key") or os.getenv("MINIMAX_API_KEY", "")
            if not api_key:
                raise ValueError(
                    "MiniMax embedding API key is required. Set MINIMAX_API_KEY or embedding.api_key in config.json."
                )
            embed_model = MiniMaxHttpEmbedding(
                model_name=embedding_config["model"],
                base_url=embedding_config["base_url"],
                api_key=api_key,
                group_id=embedding_config.get("group_id") or os.getenv("MINIMAX_GROUP_ID", ""),
            )
        elif embedding_config["provider"] == "openai":
            embed_model = OpenAIEmbedding(
                model_name=embedding_config["model"],
                api_base=embedding_config["base_url"],
                api_key=embedding_config["api_key"],
            )
        else:
            raise NotImplementedError(
                "embedding provider {} is not supported".format(embedding_config["provider"])
            )

        Settings.embed_model = embed_model
        Settings.node_parser = SentenceSplitter(chunk_size=512, chunk_overlap=64)
        Settings.num_output = 1024
        Settings.context_window = 4096
        if path and os.path.exists(path):
            self._index = index_core.load_index_from_storage(
                index_core.StorageContext.from_defaults(persist_dir=path),
                show_progress=True,
            )
            self._config = utils.load_dict(os.path.join(path, "index_config.json"))
        else:
            self._index = index_core.VectorStoreIndex([], show_progress=True)
        self._path = path

    def add_node(
        self,
        text,
        metadata=None,
        exclude_llm_keys=None,
        exclude_embedding_keys=None,
        id=None,
    ):
        for _ in range(10):
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
                print(f"LlamaIndex.add_node() caused an error: {e}")
                time.sleep(5)
        raise RuntimeError(f"LlamaIndex.add_node() failed after 10 retries")

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
        now, remove_ids = utils.get_timer().get_date(), []
        for node_id, node in self._index.docstore.docs.items():
            create = utils.to_date(node.metadata["create"])
            expire = utils.to_date(node.metadata["expire"])
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
            # print(f"LlamaIndex.retrieve() caused an error: {e}")
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
        for _ in range(10):
            try:
                if query_creator:
                    query_engine = query_creator(retriever=self._index.as_retriever(**kwargs))
                else:
                    query_engine = self._index.as_query_engine(**kwargs)
                return query_engine.query(text)
            except Exception as e:
                print(f"LlamaIndex.query() caused an error: {e}")
                time.sleep(5)
        raise RuntimeError(f"LlamaIndex.query() failed after 10 retries")

    def save(self, path=None):
        path = path or self._path
        path = os.path.abspath(os.fspath(path))
        os.makedirs(path, exist_ok=True)
        try:
            self._index.storage_context.persist(path)
        except OSError as exc:
            if exc.errno != 22:
                raise
            self._persist_storage_context_locally(path)
        utils.save_dict(self._config, os.path.join(path, "index_config.json"))

    def _persist_storage_context_locally(self, path):
        # Work around Windows/fsspec path failures on local non-ASCII storage dirs.
        persist_dir = Path(path)
        persist_dir.mkdir(parents=True, exist_ok=True)
        storage_context = self._index.storage_context

        def _write_json(file_name, data):
            with open(persist_dir / file_name, "w", encoding="utf-8") as f:
                json.dump(data, f)

        _write_json("docstore.json", storage_context.docstore.to_dict())
        _write_json("index_store.json", storage_context.index_store.to_dict())
        _write_json("graph_store.json", storage_context.graph_store.to_dict())
        if storage_context.property_graph_store:
            _write_json("property_graph_store.json", storage_context.property_graph_store.to_dict())
        for vector_store_name, vector_store in storage_context.vector_stores.items():
            _write_json(f"{vector_store_name}__vector_store.json", vector_store.to_dict())

    @property
    def nodes_num(self):
        return len(self._index.docstore.docs)
