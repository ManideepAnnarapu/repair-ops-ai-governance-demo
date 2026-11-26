from repair_ops.fixtures import ORDERS, PASSAGES
from repair_ops.models import BedrockProvider, PROMPT


def test_bedrock_converse_adapter_with_fake_client(monkeypatch):
    import boto3

    def converse(**kwargs):
        assert kwargs["modelId"] == "test-model-id"
        assert kwargs["system"] == [{"text": PROMPT}]
        assert "CED-CAMERA@2" in kwargs["messages"][0]["content"][0]["text"]
        return {
            "output": {"message": {"content": [{"text": "{}"}]}},
            "usage": {"inputTokens": 12, "outputTokens": 2, "totalTokens": 14},
        }

    class FakeClient:
        pass

    client = FakeClient()
    client.converse = converse
    monkeypatch.setattr(boto3, "client", lambda service, **kwargs: client)
    assert BedrockProvider("test-model-id").review(ORDERS["RO-101"], PASSAGES[1]) == (
        "{}", {"inputTokens": 12, "outputTokens": 2},
    )
