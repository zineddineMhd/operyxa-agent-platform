"""Run the API: python -m operyxa.api [--host HOST] [--port PORT]

Same as `uvicorn operyxa.api.main:app`, but always on a selector event loop: on Windows,
uvicorn picks the Proactor loop, which psycopg's async driver does not support.
"""

import argparse
import asyncio

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m operyxa.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = uvicorn.Config("operyxa.api.main:app", host=args.host, port=args.port, loop="none")
    asyncio.run(uvicorn.Server(config).serve(), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    main()
