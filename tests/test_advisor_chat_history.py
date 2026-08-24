from __future__ import annotations

from fastapi.testclient import TestClient

from tests.utils import register_user


def test_history_is_empty_for_a_new_account(client: TestClient) -> None:
    register_user(client, "chat-empty@example.com")

    response = client.get("/advisor/chat/history")

    assert response.status_code == 200
    assert response.json() == []


def test_a_chat_exchange_is_stored_and_replayed_in_order(client: TestClient) -> None:
    """The conversation belongs to the account, not to the device it was typed on.

    It used to live in the browser's localStorage, so the web app and the
    Android app showed two different histories for the same user.
    """
    register_user(client, "chat-stored@example.com")

    reply = client.post(
        "/advisor/chat",
        json={"messages": [{"role": "user", "text": "Salam, chhal bqa liya?"}]},
    )
    assert reply.status_code == 200

    history = client.get("/advisor/chat/history")
    assert history.status_code == 200
    stored = history.json()

    # The user turn, then the assistant's answer - oldest first, the order a
    # client needs to render the thread without sorting it again.
    assert [item["role"] for item in stored] == ["user", "assistant"]
    assert stored[0]["text"] == "Salam, chhal bqa liya?"
    assert stored[1]["text"]
    assert stored[0]["created_at"] <= stored[1]["created_at"]


def test_resending_the_transcript_does_not_duplicate_earlier_turns(client: TestClient) -> None:
    """Clients post their whole transcript; only the new turn must be stored."""
    register_user(client, "chat-nodupe@example.com")

    client.post(
        "/advisor/chat",
        json={"messages": [{"role": "user", "text": "premier message"}]},
    )
    client.post(
        "/advisor/chat",
        json={
            "messages": [
                {"role": "user", "text": "premier message"},
                {"role": "assistant", "text": "une réponse"},
                {"role": "user", "text": "deuxieme message"},
            ]
        },
    )

    stored = client.get("/advisor/chat/history").json()
    user_texts = [item["text"] for item in stored if item["role"] == "user"]
    assert user_texts == ["premier message", "deuxieme message"]


def test_clearing_the_history_empties_it_for_every_client(client: TestClient) -> None:
    register_user(client, "chat-clear@example.com")
    client.post("/advisor/chat", json={"messages": [{"role": "user", "text": "bonjour"}]})

    cleared = client.delete("/advisor/chat/history")

    assert cleared.status_code == 204
    assert client.get("/advisor/chat/history").json() == []


def test_history_is_private_to_its_owner(client: TestClient) -> None:
    register_user(client, "chat-owner@example.com")
    client.post("/advisor/chat", json={"messages": [{"role": "user", "text": "mon secret"}]})

    register_user(client, "chat-other@example.com")

    assert client.get("/advisor/chat/history").json() == []
