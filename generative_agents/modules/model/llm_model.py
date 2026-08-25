"""generative_agents.model.llm_model"""

import json
import time
import re
import requests
from datetime import datetime, timezone
from uuid import uuid4

from generative_agents.runtime.model_trace import (
    ModelTraceEvent,
    ModelTraceEventType,
    ModelTraceStatus,
)
from generative_agents.modules.utils.retry import interruptible_wait


class LLMModel:
    def __init__(self, config, *, recorder=None, control=None, logger=None, sleep=None):
        """初始化当前对象，保存依赖并建立后续操作所需的初始状态。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。
            recorder: 接收模型调用、步骤副作用或诊断事件的记录器。 默认值：`None`。
            control: 运行控制器，用于在安全边界检测暂停、取消或终止请求。 默认值：`None`。
            logger: 记录运行诊断信息的日志器。 默认值：`None`。
            sleep: 是否允许重试过程实际等待；测试可关闭等待。 默认值：`None`。

        返回:
            无返回值。
        """
        self._api_key = config.get("api_key", "")
        self._base_url = config.get("base_url", "")
        self._model = config["model"]
        self._summary = {"total": [0, 0, 0]}
        self._retry_attempts = config.get("retry_attempts", 10)
        self._retry_backoff = config.get("retry_backoff_seconds", 5)
        self._recorder = recorder
        self._control = control
        self._logger = logger
        self._sleep = sleep or time.sleep
        self._last_usage = {}

        self._handle = self.setup(config)
        self._enabled = True

    def setup(self, config):
        """执行 `LLMModel` 的`setup`操作。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。

        返回:
            无返回值。

        异常:
            NotImplementedError: 当当前实现不支持所请求的操作时抛出。
        """
        raise NotImplementedError("setup is not support for " + str(self.__class__))

    def completion(
        self,
        prompt,
        retry=None,
        callback=None,
        failsafe=None,
        return_type=None,
        caller="llm_normal",
        **kwargs,
    ):
        """执行 `LLMModel` 的`completion`操作。

        参数:
            prompt: 发送给模型的最终提示词文本或消息结构。
            retry: 传入当前算法的`retry`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。
            callback: 传入当前算法的`callback`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。
            failsafe: 传入当前算法的`failsafe`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。
            return_type: 调用方期望的返回数据类型，用于选择解析和校验方式。 默认值：`None`。
            caller: 传入当前算法的`caller`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`'llm_normal'`。
            **kwargs: 传给底层调用的额外关键字参数，键名和含义与被调用接口保持一致。

        返回:
            返回函数计算得到的结果。
        """
        retry = retry or self._retry_attempts
        response = None
        call_id = uuid4()
        agent_key = kwargs.pop("agent_key", None)
        prompt_key = kwargs.pop("prompt_key", None)
        step_no = kwargs.pop("step_no", None)
        logical_started = datetime.now(timezone.utc)
        last_error = None
        self._summary.setdefault(caller, [0, 0, 0])
        for attempt_no in range(1, retry + 1):
            started_at = datetime.now(timezone.utc)
            self._last_usage = {}
            attempt_error = None
            try:
                output = self._completion(prompt, return_type, **kwargs)
                self._summary["total"][0] += 1
                self._summary[caller][0] += 1
                if callback:
                    response = callback(output)
                else:
                    response = output
            except Exception as e:
                last_error = e
                attempt_error = e
                response = None
            ended_at = datetime.now(timezone.utc)
            self._record(
                ModelTraceEvent(
                    event_type=ModelTraceEventType.PHYSICAL_ATTEMPT,
                    run_id=self._recorder.run_id if self._recorder else uuid4(),
                    attempt_id=self._recorder.attempt_id if self._recorder else uuid4(),
                    call_id=call_id,
                    step_no=step_no,
                    agent_key=agent_key,
                    purpose=caller,
                    prompt_key=prompt_key,
                    provider=self.provider,
                    resolved_model=self._model,
                    started_at=started_at,
                    ended_at=ended_at,
                    latency_ms=max(
                        0, int((ended_at - started_at).total_seconds() * 1000)
                    ),
                    attempt_no=attempt_no,
                    status=(
                        ModelTraceStatus.SUCCEEDED
                        if response is not None
                        else ModelTraceStatus.FAILED
                    ),
                    prompt_tokens=self._last_usage.get("prompt_tokens"),
                    completion_tokens=self._last_usage.get("completion_tokens"),
                    total_tokens=self._last_usage.get("total_tokens"),
                    error_code=type(attempt_error).__name__ if attempt_error else None,
                    error_summary=str(attempt_error) if attempt_error else None,
                )
            )
            if response is not None:
                break
            if attempt_no < retry and self._retry_backoff:
                if not interruptible_wait(
                    self._retry_backoff,
                    control=self._control,
                    sleep=self._sleep,
                ):
                    break
        pos = 2 if response is None else 1
        self._summary["total"][pos] += 1
        self._summary[caller][pos] += 1
        result = response if response is not None else failsafe
        logical_ended = datetime.now(timezone.utc)
        logical_error = last_error if response is None else None
        self._record(
            ModelTraceEvent(
                event_type=ModelTraceEventType.LOGICAL_END,
                run_id=self._recorder.run_id if self._recorder else uuid4(),
                attempt_id=self._recorder.attempt_id if self._recorder else uuid4(),
                call_id=call_id,
                step_no=step_no,
                agent_key=agent_key,
                purpose=caller,
                prompt_key=prompt_key,
                provider=self.provider,
                resolved_model=self._model,
                started_at=logical_started,
                ended_at=logical_ended,
                latency_ms=max(
                    0, int((logical_ended - logical_started).total_seconds() * 1000)
                ),
                attempt_no=None,
                status=(
                    ModelTraceStatus.SUCCEEDED
                    if response is not None
                    else ModelTraceStatus.FALLBACK
                    if failsafe is not None
                    else ModelTraceStatus.FAILED
                ),
                error_code=type(logical_error).__name__ if logical_error else None,
                error_summary=str(logical_error) if logical_error else None,
            )
        )
        return result

    @property
    def provider(self):
        """执行 `LLMModel` 的`provider`操作。

        返回:
            返回函数计算得到的结果。
        """
        return self.__class__.__name__.removesuffix("LLMModel").casefold()

    def _record(self, event):
        """执行`record`的内部处理，供当前模块或类复用。

        参数:
            event: 当前感知、处理或写入结果账本的领域事件。

        返回:
            无返回值。
        """
        if self._recorder is not None:
            self._recorder.append(event)

    def _completion(self, prompt, return_type, **kwargs):
        """执行`completion`的内部处理，供当前模块或类复用。

        参数:
            prompt: 发送给模型的最终提示词文本或消息结构。
            return_type: 调用方期望的返回数据类型，用于选择解析和校验方式。
            **kwargs: 传给底层调用的额外关键字参数，键名和含义与被调用接口保持一致。

        返回:
            无返回值。

        异常:
            NotImplementedError: 当当前实现不支持所请求的操作时抛出。
        """
        raise NotImplementedError(
            "_completion is not support for " + str(self.__class__)
        )

    def is_available(self):
        """判断是否`available`。

        返回:
            返回函数计算得到的结果。
        """
        return self._enabled  # and self._summary["total"][2] <= 10

    def get_summary(self):
        """获取摘要。

        返回:
            返回函数计算得到的结果。
        """
        des = {}
        for k, v in self._summary.items():
            des[k] = "S:{},F:{}/R:{}".format(v[1], v[2], v[0])
        return {"model": self._model, "summary": des}

    def disable(self):
        """执行 `LLMModel` 的`disable`操作。

        返回:
            无返回值。
        """
        self._enabled = False


class OpenAILLMModel(LLMModel):
    def setup(self, config):
        """执行 `OpenAILLMModel` 的`setup`操作。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。

        返回:
            返回函数计算得到的结果。
        """
        from magentic import OpenaiChatModel

        return OpenaiChatModel(
            self._model, api_key=self._api_key, base_url=self._base_url
        )

    def _completion(self, _prompt, return_type, temperature=0.5):
        """执行`completion`的内部处理，供当前模块或类复用。

        参数:
            _prompt: 传入当前算法的提示词；其结构与有效范围由类型注解和调用协议共同限定。
            return_type: 调用方期望的返回数据类型，用于选择解析和校验方式。
            temperature: 模型采样温度；数值越高，生成结果随机性越强。 默认值：`0.5`。

        返回:
            返回函数计算得到的结果。
        """
        from magentic import prompt

        @prompt("{_prompt}", model=self._handle)
        def response(_prompt: str) -> return_type:
            """执行 `OpenAILLMModel` 的`response`操作。

            参数:
                _prompt: 传入当前算法的提示词；其结构与有效范围由类型注解和调用协议共同限定。 类型：`str`。

            返回:
                返回 `return_type` 类型的处理结果。
            """
            ...

        output = response(_prompt).res
        return output


class OllamaLLMModel(LLMModel):
    def setup(self, config):
        """执行 `OllamaLLMModel` 的`setup`操作。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。

        返回:
            返回函数计算得到的结果。
        """
        return None

    def ollama_chat(self, messages, temperature, response_format=None):
        """执行 `OllamaLLMModel` 的`ollama``chat`操作。

        参数:
            messages: 按会话顺序排列的消息集合。
            temperature: 模型采样温度；数值越高，生成结果随机性越强。
            response_format: 传入当前算法的`response``format`；其结构与有效范围由类型注解和调用协议共同限定。 默认值：`None`。

        返回:
            返回函数计算得到的结果。
        """
        headers = {"Content-Type": "application/json"}
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
            timeout=300,
        )
        result = response.json()
        self._last_usage = result.get("usage") or {}
        return result

    def _completion(self, prompt, return_type, temperature=0.5):
        # Generate JSON schema from the Pydantic model for structured output
        """执行`completion`的内部处理，供当前模块或类复用。

        参数:
            prompt: 发送给模型的最终提示词文本或消息结构。
            return_type: 调用方期望的返回数据类型，用于选择解析和校验方式。
            temperature: 模型采样温度；数值越高，生成结果随机性越强。 默认值：`0.5`。

        返回:
            返回函数计算得到的结果。
        """
        response_format = None
        if return_type is not None:
            try:
                schema = return_type.model_json_schema()
                response_format = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": return_type.__name__,
                        "strict": True,
                        "schema": schema,
                    },
                }
            except Exception:
                pass

        messages = [{"role": "user", "content": prompt}]
        response = self.ollama_chat(
            messages=messages, temperature=temperature, response_format=response_format
        )

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
                    json_match = re.search(r"\{.*\}", ret, re.DOTALL)
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
                    if self._logger is not None:
                        self._logger.warning("Ollama response validation failed: %s", e)
                    return ret
            return ret
        return ""


class VLLMLLMModel(LLMModel):
    """Call a local vLLM server through its OpenAI-compatible HTTP API."""

    def setup(self, config):
        """执行 `VLLMLLMModel` 的`setup`操作。

        参数:
            config: 当前组件使用的结构化配置；字段约束由对应配置模型定义。

        返回:
            返回函数计算得到的结果。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        self._timeout = config.get("timeout", 300)
        self._max_tokens = config.get("max_tokens", 2048)
        self._enable_thinking = config.get("enable_thinking", False)
        self._base_url = self._base_url.rstrip("/")

        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        if self._api_key:
            session.headers.update({"Authorization": f"Bearer {self._api_key}"})

        if not self._model or self._model == "auto":
            response = session.get(self._api_url("models"), timeout=self._timeout)
            self._raise_for_status(response)
            models = [
                item.get("id")
                for item in response.json().get("data", [])
                if item.get("id")
            ]
            if not models:
                raise RuntimeError("GET /v1/models returned no model IDs")
            self._model = models[0]

        return session

    def _api_url(self, path):
        """执行`api``url`的内部处理，供当前模块或类复用。

        参数:
            path: 目标文件或目录路径；使用前会按调用场景进行存在性或归属校验。

        返回:
            返回函数计算得到的结果。
        """
        prefix = (
            self._base_url if self._base_url.endswith("/v1") else f"{self._base_url}/v1"
        )
        return f"{prefix}/{path.lstrip('/')}"

    @staticmethod
    def _raise_for_status(response):
        """执行`raise``for``status`的内部处理，供当前模块或类复用。

        参数:
            response: 模型、HTTP 接口或下游组件返回的原始响应，尚待校验或转换。

        返回:
            无返回值。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        if response.ok:
            return
        raise RuntimeError(
            f"HTTP {response.status_code} from {response.url}: {response.text}"
        )

    @staticmethod
    def _parse_structured_output(content, return_type):
        """解析`structured``output`。

        参数:
            content: 待解析、写入、哈希或发送给下游组件的正文内容。
            return_type: 调用方期望的返回数据类型，用于选择解析和校验方式。

        返回:
            返回函数计算得到的结果。

        异常:
            ValueError: 当参数值、配置内容或状态转换不符合约束时抛出。
        """
        candidates = [content]
        json_match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if json_match and json_match.group() != content:
            candidates.append(json_match.group())

        last_error = None
        for candidate in candidates:
            try:
                return return_type.model_validate_json(candidate).res
            except Exception as exc:  # Try a JSON object embedded in prose next.
                last_error = exc
        raise ValueError(
            f"vLLM returned invalid {return_type.__name__} JSON: {last_error}; "
            f"content={content!r}"
        )

    def _completion(self, prompt, return_type, temperature=0.5, max_tokens=None):
        """执行`completion`的内部处理，供当前模块或类复用。

        参数:
            prompt: 发送给模型的最终提示词文本或消息结构。
            return_type: 调用方期望的返回数据类型，用于选择解析和校验方式。
            temperature: 模型采样温度；数值越高，生成结果随机性越强。 默认值：`0.5`。
            max_tokens: `tokens`允许的最大值。 默认值：`None`。

        返回:
            返回函数计算得到的结果。

        异常:
            RuntimeError: 当运行状态不允许继续执行或底层操作失败时抛出。
        """
        params = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens or self._max_tokens,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": self._enable_thinking},
        }
        if return_type is not None:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": return_type.__name__,
                    "strict": True,
                    "schema": return_type.model_json_schema(),
                },
            }

        response = self._handle.post(
            self._api_url("chat/completions"),
            json=params,
            timeout=self._timeout,
        )
        self._raise_for_status(response)
        result = response.json()
        self._last_usage = result.get("usage") or {}
        choices = result.get("choices") or []
        if not choices:
            raise RuntimeError(f"vLLM response has no choices: {result}")

        message = choices[0].get("message") or {}
        content = message.get("content") or ""
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
        if not content:
            reasoning = message.get("reasoning") or message.get("reasoning_content")
            raise RuntimeError(
                "vLLM response has no visible content"
                + (f"; reasoning={reasoning!r}" if reasoning else "")
            )

        if return_type is not None:
            return self._parse_structured_output(content, return_type)
        return content


def create_llm_model(
    llm_config,
    *,
    recorder=None,
    control=None,
    logger=None,
    sleep=None,
):
    """创建`llm`模型。

    参数:
        llm_config: 传入当前算法的`llm`配置；其结构与有效范围由类型注解和调用协议共同限定。
        recorder: 接收模型调用、步骤副作用或诊断事件的记录器。 默认值：`None`。
        control: 运行控制器，用于在安全边界检测暂停、取消或终止请求。 默认值：`None`。
        logger: 记录运行诊断信息的日志器。 默认值：`None`。
        sleep: 是否允许重试过程实际等待；测试可关闭等待。 默认值：`None`。

    返回:
        返回函数计算得到的结果。

    异常:
        NotImplementedError: 当当前实现不支持所请求的操作时抛出。
    """

    if llm_config["provider"] == "vllm":
        return VLLMLLMModel(
            llm_config,
            recorder=recorder,
            control=control,
            logger=logger,
            sleep=sleep,
        )
    elif llm_config["provider"] == "ollama":
        return OllamaLLMModel(
            llm_config,
            recorder=recorder,
            control=control,
            logger=logger,
            sleep=sleep,
        )

    elif llm_config["provider"] == "openai":
        return OpenAILLMModel(
            llm_config,
            recorder=recorder,
            control=control,
            logger=logger,
            sleep=sleep,
        )
    else:
        raise NotImplementedError(
            "llm provider {} is not supported".format(llm_config["provider"])
        )
    return None
