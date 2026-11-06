import json
import threading
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bistos.errors import AppError
from bistos.models import Model, ModelStatus

CONTEXT_TOKENS: Final = 16384
OUTPUT_TOKENS: Final = 1536
INPUT_BYTES: Final = CONTEXT_TOKENS - OUTPUT_TOKENS - 1024


class WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class InstalledModel(WireModel):
    name: str
    digest: str


class Tags(WireModel):
    models: list[InstalledModel]


class ModelConfig(Model):
    name: str = ""
    digest: str = ""
    enabled: bool = False


class SettingsStore(Protocol):
    def settings_json(self, key: str) -> str | None: ...
    def set_settings_json(self, key: str, value: str) -> None: ...


class Thinking(WireModel):
    values: list[bool | str] = Field(default_factory=list)


class Show(WireModel):
    capabilities: list[str] = Field(default_factory=list)
    remote_model: str = ""
    remote_host: str = ""
    thinking: Thinking = Field(default_factory=Thinking)


class WireMessage(WireModel):
    role: str
    content: str
    tool_calls: list[dict[str, str]] = Field(default_factory=list)


class ChatResponse(WireModel):
    message: WireMessage
    done: bool
    done_reason: str = ""
    prompt_eval_count: int = 0
    eval_count: int = 0


class Ollama:
    """Own pinned model state; all requests remain on one fixed loopback endpoint."""

    def __init__(
        self, url: str, store: SettingsStore, transport: httpx.BaseTransport | None = None
    ) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or parsed.port != 11434
            or parsed.path not in {"", "/"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise AppError("AI 주소는 http://127.0.0.1:11434만 허용됩니다.")
        self.url = "http://127.0.0.1:11434"
        self.store = store
        self.transport = transport
        self.lock = threading.RLock()
        self.generation_lock = threading.Lock()
        self.quarantined = False
        self.disable_thinking = False

    def _config(self) -> ModelConfig:
        value = self.store.settings_json("model_config")
        if value is None:
            return ModelConfig()
        try:
            return ModelConfig.model_validate_json(value)
        except (OSError, ValidationError) as error:
            raise AppError("저장된 로컬 모델 설정을 읽을 수 없습니다.", 503) from error

    def _client(self, read_timeout: float = 15) -> httpx.Client:
        return httpx.Client(
            base_url=self.url,
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            timeout=httpx.Timeout(connect=2, read=read_timeout, write=10, pool=2),
        )

    def _installed(self, name: str) -> InstalledModel:
        if not name or "cloud" in name.casefold() or "/" in name or len(name) > 120:
            raise AppError("설치된 로컬 모델 이름만 사용할 수 있습니다.")
        with self._client() as client:
            tags_response = client.get("/api/tags")
            tags_response.raise_for_status()
            models = Tags.model_validate_json(tags_response.content)
            candidates = [item for item in models.models if item.name == name]
            if not candidates:
                raise AppError("해당 모델이 이 PC에 설치되어 있지 않습니다.", 409)
            show = client.post("/api/show", json={"model": name})
            show.raise_for_status()
            data = Show.model_validate_json(show.content)
            if data.remote_model or data.remote_host:
                raise AppError("클라우드 모델은 사용할 수 없습니다.", 403)
            if "completion" not in data.capabilities:
                raise AppError("텍스트 답변을 지원하는 로컬 모델이 아닙니다.")
            self.disable_thinking = False in data.thinking.values
            return candidates[0]

    def status(self) -> ModelStatus:
        with self.lock:
            config = self._config()
            if not config.name:
                return ModelStatus(ready=False, detail="로컬 모델을 아직 선택하지 않았습니다.")
            try:
                installed = self._installed(config.name)
                matched = installed.digest == config.digest
                return ModelStatus(
                    ready=matched and config.enabled and not self.quarantined,
                    name=config.name,
                    digest=config.digest,
                    enabled=config.enabled,
                    detail=(
                        "생성 시간 초과로 AI 호출을 중지했습니다. Ollama와 프로그램을 재시작하세요."
                        if self.quarantined
                        else "모델 파일이 변경되었습니다. 관리자가 다시 확인해야 합니다."
                        if not matched
                        else "로컬 모델 준비 완료"
                        if config.enabled
                        else "로컬 AI 생성이 꺼져 있습니다."
                    ),
                )
            except (httpx.HTTPError, ValidationError, KeyError, TypeError, ValueError, AppError):
                return ModelStatus(
                    ready=False,
                    name=config.name,
                    digest=config.digest,
                    enabled=config.enabled,
                    detail="로컬 모델 서버에 연결할 수 없거나 모델 검증에 실패했습니다.",
                )

    def configure(self, name: str, enabled: bool) -> ModelStatus:
        with self.lock:
            try:
                installed = self._installed(name)
            except (httpx.HTTPError, ValidationError, KeyError, TypeError, ValueError) as error:
                raise AppError(
                    "로컬 모델 서버 또는 설치 모델을 확인할 수 없습니다.", 503
                ) from error
            config = ModelConfig(name=installed.name, digest=installed.digest, enabled=enabled)
            self.store.set_settings_json("model_config", config.model_dump_json())
        return self.status()

    def generate(
        self, system: str, user_json: str, schema_path: Path, *, output_tokens: int = 256
    ) -> str:
        if not 1 <= output_tokens <= 1024:
            raise AppError("AI 출력 예산은 1~1,024 토큰이어야 합니다.", 400)
        with self.generation_lock:
            status = self.status()
            if not status.ready:
                raise AppError(status.detail, 503)
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            input_bytes = (
                len(system.encode("utf-8"))
                + len(user_json.encode("utf-8"))
                + len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
            )
            if input_bytes > INPUT_BYTES:
                raise AppError("근거가 문맥 한도를 초과했습니다. 질문을 좁혀 주세요.", 413)
            request = {
                "model": status.name,
                "stream": False,
                "format": schema,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_json},
                ],
                "options": {
                    "temperature": 0,
                    "presence_penalty": 0,
                    "num_ctx": CONTEXT_TOKENS,
                    "num_predict": output_tokens,
                },
            }
            if self.disable_thinking:
                request["think"] = False
            try:
                with self._client(180) as client:
                    response = client.post("/api/chat", json=request)
                    response.raise_for_status()
                    output = ChatResponse.model_validate_json(response.content)
            except httpx.TimeoutException as error:
                self.quarantined = True
                raise AppError(
                    "AI 생성 제한 시간을 넘겼습니다. 검색 결과를 확인하세요.", 504
                ) from error
            except (httpx.HTTPError, ValidationError, ValueError, KeyError, TypeError) as error:
                raise AppError("로컬 AI가 정상 답변을 반환하지 않았습니다.", 503) from error
            if not output.done or output.done_reason != "stop" or output.message.tool_calls:
                raise AppError("AI 답변이 중단되었거나 허용되지 않은 출력을 포함합니다.", 422)
            if output.prompt_eval_count > CONTEXT_TOKENS - OUTPUT_TOKENS:
                raise AppError("AI 문맥 예산 검사를 통과하지 못했습니다.", 422)
            return output.message.content
