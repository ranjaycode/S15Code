from __future__ import annotations


def test_s15code_advertises_an_a2a_agent_card(app_client):
    response = app_client.get("/.well-known/agent-card.json")
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "S15 live-agent runtime"
    assert card["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"
    assert card["capabilities"] == {"streaming": True, "pushNotifications": True}
