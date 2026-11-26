"""Secrets checked at startup and rate limit on the auth routes."""

import httpx
import pytest

from operyxa.api.app import create_app
from operyxa.api.routes.auth import SlidingWindow
from operyxa.config import Settings


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize(
    "secret",
    ["change-me", "replace_me_with_a_long_random_value", "", "short-but-not-default"],
)
@pytest.mark.parametrize("environment", ["staging", "production"])
def test_weak_jwt_secret_refused_outside_local(environment, secret):
    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        create_app(make(environment=environment, jwt_secret_key=secret))


def test_placeholder_secrets_key_refused_in_production():
    settings = make(
        environment="production", jwt_secret_key="j" * 48, secrets_key="replace_me_please"
    )
    with pytest.raises(RuntimeError, match="SECRETS_KEY"):
        settings.check_secrets()


def test_strong_secrets_accepted_and_local_only_warns(caplog):
    make(environment="production", jwt_secret_key="j" * 48, secrets_key="s" * 48).check_secrets()
    make(environment="local", jwt_secret_key="change-me").check_secrets()
    assert "accepted in local only" in caplog.text


def test_sliding_window_forgets_after_a_minute():
    window = SlidingWindow()
    assert all(window.allow("k", 3, now=t) for t in (0, 1, 2))
    assert not window.allow("k", 3, now=30)
    assert window.allow("other", 3, now=30)
    assert window.allow("k", 3, now=61)
    assert window.allow("k", 0, now=61)


async def test_login_attempts_are_limited_per_email(settings, db):
    settings = settings.model_copy(update={"auth_rate_limit_per_minute": 3})
    app = create_app(settings, db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        body = {"email": "a@example.com", "password": "a-long-password"}
        assert (await http.post("/api/v1/auth/register", json=body)).status_code == 201
        wrong = {**body, "password": "wrong-password"}
        codes = [(await http.post("/api/v1/auth/login", json=wrong)).status_code for _ in range(3)]
        assert codes == [401, 401, 401]
        blocked = await http.post("/api/v1/auth/login", json=body)
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"
        other = {"email": "b@example.com", "password": "a-long-password"}
        assert (await http.post("/api/v1/auth/login", json=other)).status_code == 401
