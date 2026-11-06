import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

_TUNNEL_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.(?:trycloudflare\.com|ngrok-free\.dev)$"
)


def local_data_directory(root: Path) -> Path:
    explicit = os.environ.get("BISTOS_DATA_DIR")
    if explicit:
        return Path(explicit).resolve()
    location = root / "data" / "storage-location.json"
    if not location.exists():
        return (root / "data").resolve()
    value: object = json.loads(location.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError("Invalid local storage configuration")
    configuration = cast("dict[str, object]", value)
    if set(configuration) != {"data_dir"}:
        raise ValueError("Invalid local storage configuration")
    path_value = configuration.get("data_dir")
    if not isinstance(path_value, str) or path_value.startswith(("\\\\", "//")):
        raise ValueError("Storage must be an absolute local directory")
    target = Path(path_value)
    if not target.is_absolute() or not (target / "bistos.sqlite3").is_file():
        raise ValueError("Configured data is missing; refusing to create an empty account store")
    return target.resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    root: Path
    data_dir: Path
    corpus_dir: Path
    ollama_url: str = "http://127.0.0.1:11434"
    secure_cookie: bool = False
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost", "[::1]")
    tunnel_host: str | None = None

    def __post_init__(self) -> None:
        if self.tunnel_host is not None and not _TUNNEL_HOST_RE.fullmatch(self.tunnel_host):
            raise ValueError(
                "tunnel_host must be one lowercase *.trycloudflare.com or *.ngrok-free.dev hostname"
            )

    @classmethod
    def load(cls) -> "Settings":
        root = Path(__file__).resolve().parent.parent
        tunnel_host = os.environ.get("BISTOS_TUNNEL_HOST") or None
        settings = cls(
            root=root,
            data_dir=local_data_directory(root),
            corpus_dir=Path(
                os.environ.get(
                    "BISTOS_CORPUS_DIR",
                    "C:/Users/User/.codex/knowledge/bistos-manuals",
                ),
            ).resolve(),
            tunnel_host=tunnel_host,
        )
        if tunnel_host is not None:
            settings = cls(
                root=settings.root,
                data_dir=settings.data_dir,
                corpus_dir=settings.corpus_dir,
                ollama_url=settings.ollama_url,
                secure_cookie=settings.secure_cookie,
                allowed_hosts=(*settings.allowed_hosts, tunnel_host),
                tunnel_host=tunnel_host,
            )
        return settings
