import os
from pathlib import Path

import uvicorn

from bistos.server import create_app
from bistos.settings import Settings


def main() -> None:
    settings = Settings.load()
    port = int(os.environ.get("BISTOS_PORT", "8765"))
    lan = os.environ.get("BISTOS_LAN", "0") == "1"
    if lan:
        cert = Path(os.environ["BISTOS_TLS_CERT"]).resolve()
        key = Path(os.environ["BISTOS_TLS_KEY"]).resolve()
        hosts = tuple(os.environ["BISTOS_ALLOWED_HOSTS"].split(","))
        secured = Settings(
            root=settings.root,
            data_dir=settings.data_dir,
            corpus_dir=settings.corpus_dir,
            ollama_url=settings.ollama_url,
            secure_cookie=True,
            allowed_hosts=hosts,
        )
        uvicorn.run(
            create_app(secured),
            host=os.environ["BISTOS_BIND_ADDRESS"],
            port=port,
            proxy_headers=False,
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            access_log=False,
        )
        return
    uvicorn.run(
        create_app(settings), host="127.0.0.1", port=port, proxy_headers=False, access_log=False
    )


if __name__ == "__main__":
    main()
