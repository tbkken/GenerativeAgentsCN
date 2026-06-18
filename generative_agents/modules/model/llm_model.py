"""generative_agents.model.llm_model"""

import time
import re
import json
import requests
from magentic import prompt


class LLMModel:
    def __init__(self, config):
        self._api_key = config["api_key"]
        self._base_url = config["base_url"]
        self._model = config["model"]
        self._summary = {"total": [0, 0, 0]}

        self._handle = self.setup(config)
        self._enabled = True

    def setup(self, config):
        raise NotImplementedError(
            "setup is not support for " + str(self.__class__)
        )

    def completion(
        self,
        prompt,
        retry=10,
        callback=None,
        failsafe=None,
        return_type=None,
        caller="llm_normal",
        **kwargs
    ):
        response = None
        self._summary.setdefault(caller, [0, 0, 0])
        for _ in range(retry):
            try:
                output = self._completion(prompt, return_type, **kwargs)
                self._summary["total"][0] += 1
                self._summary[caller][0] += 1
                if callback:
                    response = callback(output)
                else:
                    response = output
            except Exception as e:
                print(f"LLMModel.completion() caused an error: {e}")
                time.sleep(5)
                response = None
                continue
            if response is not None:
                break
        pos = 2 if response is None else 1
        self._summary["total"][pos] += 1
        self._summary[caller][pos] += 1
        return response or failsafe

    def _completion(self, prompt, return_type, **kwargs):
        raise NotImplementedError(
            "_completion is not support for " + str(self.__class__)
        )

    def is_available(self):
        return self._enabled  # and self._summary["total"][2] <= 10

    def get_summary(self):
        des = {}
        for k, v in self._summary.items():
            des[k] = "S:{},F:{}/R:{}".format(v[1], v[2], v[0])
        return {"model": self._model, "summary": des}

    def disable(self):
        self._enabled = False


class OpenAILLMModel(LLMModel):
    def setup(self, config):
        from magentic import OpenaiChatModel

        return OpenaiChatModel(self._model, api_key=self._api_key, base_url=self._base_url)

    def _completion(self, _prompt, return_type, temperature=0.5):
        @prompt(
            "{_prompt}",
            model=self._handle
        )
        def response(_prompt: str) -> return_type: ...
        output = response(_prompt).res
        return output


class OllamaLLMModel(LLMModel):
    def setup(self, config):
        return None

    def ollama_chat(self, messages, temperature, response_format=None):
        headers = {
            "Content-Type": "application/json"
        }
        params = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if response_format:
            params["response_format"] = response_format

        response = requests.post(
            url=f"{self._base_url}/chat/completions",
            headers=headers,
            json=params,
            stream=False,
            timeout=300
        )
        return response.json()

    def _completion(self, prompt, return_type, temperature=0.5):
        import json
        
        # Generate JSON schema from the Pydantic model for structured output
        response_format = None
        if return_type is not None:
            try:
                schema = return_type.model_json_schema()
                response_format = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": return_type.__name__,
                        "strict": True,
                        "schema": schema
                    }
                }
            except Exception:
                pass
        
        messages = [{"role": "user", "content": prompt}]
        response = self.ollama_chat(messages=messages, temperature=temperature, response_format=response_format)
        
        if response and len(response.get("choices", [])) > 0:
            ret = response["choices"][0]["message"]["content"]
            # 从输出结果中过滤掉<think>标签内的文字，以免影响后续逻辑
            ret = re.sub(r"<think>.*</think>", "", ret, flags=re.DOTALL)
            
            # Parse and validate the response using the Pydantic model
            if return_type is not None:
                try:
                    # Try to parse as JSON and validate with Pydantic
                    parsed = json.loads(ret)
                    validated = return_type.model_validate(parsed)
                    return validated.res
                except json.JSONDecodeError:
                    # If JSON parsing fails, try to extract JSON from the text
                    json_match = re.search(r'\{.*\}', ret, re.DOTALL)
                    if json_match:
                        try:
                            parsed = json.loads(json_match.group())
                            validated = return_type.model_validate(parsed)
                            return validated.res
                        except (json.JSONDecodeError, Exception):
                            pass
                    # If all parsing fails, return the raw text
                    return ret
                except Exception as e:
                    print(f"OllamaLLMModel: Failed to validate response: {e}")
                    return ret
            return ret
        return ""


class MiniMaxLLMModel(LLMModel):
    """适配 MiniMax 的 OpenAI 兼容接口（如 MiniMax-M3）。

    与 Ollama 不同，MiniMax 不支持 `response_format` 的 `json_schema` 模式
    （实测会被忽略），因此这里改用 `json_object` 模式，并把所需的 JSON Schema
    直接拼接到提示语中，强制模型输出结构化 JSON。MiniMax-M 系列为推理模型，
    其思考过程会以 <think>...</think> 形式出现在 content 中，需要过滤。
    """

    def setup(self, config):
        # 输出最大 token 数，需足够容纳“思考过程 + 结构化结果”
        self._max_tokens = config.get("max_tokens", 8192)
        return None

    def minimax_chat(self, messages, temperature, response_format=None):
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        params = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }
        if response_format:
            params["response_format"] = response_format

        response = requests.post(
            url=f"{self._base_url}/chat/completions",
            headers=headers,
            json=params,
            stream=False,
            timeout=300,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"MiniMax API returned {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    def _completion(self, prompt, return_type, temperature=0.5):
        response_format = None
        if return_type is not None:
            # MiniMax 忽略 json_schema，这里把 schema 写进提示语并启用 json_object
            try:
                schema = return_type.model_json_schema()
                prompt = (
                    f"{prompt}\n\n"
                    "请只输出一个符合下面 JSON Schema 的 JSON 对象，"
                    "不要输出任何解释、思考过程或 markdown 代码块标记，直接输出 JSON：\n"
                    f"JSON Schema: {json.dumps(schema, ensure_ascii=False)}\n"
                    "其中结果放在字段 \"res\" 中。"
                )
                response_format = {"type": "json_object"}
            except Exception:
                pass

        messages = [{"role": "user", "content": prompt}]
        response = self.minimax_chat(
            messages=messages,
            temperature=temperature,
            response_format=response_format,
        )

        if response and len(response.get("choices", [])) > 0:
            ret = response["choices"][0]["message"].get("content") or ""
            # 过滤掉 <think> 标签内的思考过程，以免影响后续解析
            ret = re.sub(r"<think>.*?</think>", "", ret, flags=re.DOTALL).strip()
            return parse_structured_output(ret, return_type)
        return ""


def parse_structured_output(ret, return_type):
    """将文本解析为 return_type（Pydantic 模型）的 res 字段，失败时返回原始文本。"""
    if return_type is None:
        return ret
    try:
        parsed = json.loads(ret)
        return return_type.model_validate(parsed).res
    except json.JSONDecodeError:
        json_match = re.search(r"\{.*\}", ret, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group())
                return return_type.model_validate(parsed).res
            except Exception:
                pass
        return ret
    except Exception as e:
        print(f"parse_structured_output: Failed to validate response: {e}")
        return ret


def create_llm_model(llm_config):
    """Create llm model"""

    if llm_config["provider"] == "ollama":
        return OllamaLLMModel(llm_config)

    elif llm_config["provider"] == "minimax":
        return MiniMaxLLMModel(llm_config)

    elif llm_config["provider"] == "openai":
        return OpenAILLMModel(llm_config)
    else:
        raise NotImplementedError(
            "llm provider {} is not supported".format(llm_config["provider"])
        )
    return None
