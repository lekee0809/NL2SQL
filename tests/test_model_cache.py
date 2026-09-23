from types import SimpleNamespace

from app import llm
from app import main
from app.query_spec import QuerySpec


def test_repeated_valid_plan_reuses_one_model_response(monkeypatch):
    llm.clear_model_cache()
    calls = []
    content = ('{"metrics":["sales_amount"],"dimensions":[],"filters":[], '
               '"order_by":[],"limit":null,"comparison":null}')

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150),
            )

    monkeypatch.setattr(llm, "OpenAI", lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    ))
    monkeypatch.setattr(llm, "build_prompt_catalog", lambda question, use_retrieval=None: {"metrics": {}})
    metadata = []
    usage = []
    first = llm.generate_query_spec("销售额是多少", metadata_callback=metadata.append, usage_callback=usage.append)
    second = llm.generate_query_spec("销售额是多少", metadata_callback=metadata.append, usage_callback=usage.append)

    assert first == second
    assert len(calls) == 1
    assert metadata[0]["cache_hit"] is False
    assert metadata[1]["cache_hit"] is True
    assert usage[0]["total_tokens"] == 150
    assert usage[1]["total_tokens"] == 0
    llm.clear_model_cache()


def test_cache_key_changes_with_prompt_version(monkeypatch):
    llm.clear_model_cache()
    messages = [{"role": "user", "content": "test"}]
    schema = {"type": "json_schema", "name": "query_spec"}
    first = llm._cache_key(messages, schema)
    second = llm._cache_key([{"role": "user", "content": "test updated"}], schema)
    assert first != second


def test_query_api_exposes_model_usage_without_question_in_telemetry(monkeypatch):
    spec = QuerySpec.model_validate({
        "metrics": ["sales_amount"], "dimensions": [], "filters": [],
        "order_by": [], "limit": None, "comparison": None,
    })

    def fake_generate(question, metadata_callback):
        metadata_callback({"model": "test-model", "cache_hit": True,
                           "latency_ms": 0.2,
                           "tokens": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}})
        return spec

    monkeypatch.setattr(main, "generate_query_spec", fake_generate)
    monkeypatch.setattr(main, "execute_query_spec", lambda spec, question: {"count": 1})
    result = main.query_v2(main.QueryRequest(question="销售额是多少"))
    assert result["model_call"]["cache_hit"] is True
    assert result["model_call"]["tokens"]["total_tokens"] == 0
    assert "销售额是多少" not in str(result["model_call"])
