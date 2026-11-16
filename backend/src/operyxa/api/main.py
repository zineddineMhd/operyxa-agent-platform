"""ASGI entry point: uvicorn operyxa.api.main:app"""

from operyxa.api.app import create_app

app = create_app()
