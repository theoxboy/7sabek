from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app


class FakeSession:
    async def execute(self, *_args, **_kwargs) -> None:
        return None


async def override_get_db():
    yield FakeSession()


def test_healthcheck():
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}
