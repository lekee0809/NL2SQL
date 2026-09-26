from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .database import check_database, execute_readonly
from .llm import generate_query_spec, generate_query_spec_patch, generate_sql
from .query_spec import QuerySpec, compile_query
from .sql_guard import UnsafeSQL, validate_readonly_sql
from .value_resolver import NeedsClarification, resolve_query_spec
from .entity_aliases import lookup_alias, lookup_name
from .catalog_retrieval import CatalogRetriever
from .config import settings
from .source_registry import SourceRegistry
from .conversation import ConversationStore, SessionConflictError, apply_query_spec_patch, parse_local_patch
from .session_storage import SQLiteConversationStore
from .query_normalization import normalize_query_spec

app = FastAPI(title="中文智能问数", version="0.9.0")
ROOT = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class ResolveRequest(BaseModel):
    query_spec: QuerySpec
    filter_index: int = Field(ge=0)
    value: str = Field(min_length=1, max_length=200)
    entity_id: int | None = Field(default=None, gt=0)


class SessionResolveRequest(BaseModel):
    value: str = Field(min_length=1, max_length=200)
    entity_id: int | None = Field(default=None, gt=0)


class CatalogSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    document_types: list[str] | None = None
    limit: int = Field(default=8, ge=1, le=30)


class SourceScanRequest(BaseModel):
    include_value_samples: bool = False
    max_sample_values: int = Field(default=20, ge=1, le=50)


_catalog_retriever: CatalogRetriever | None = None
if settings.session_storage == "sqlite":
    conversation_store = SQLiteConversationStore(
        settings.session_store_path, ttl_seconds=settings.session_ttl_seconds,
        max_sessions=settings.session_max_saved,
    )
elif settings.session_storage == "memory":
    conversation_store = ConversationStore(
        ttl_seconds=settings.session_ttl_seconds, max_sessions=settings.session_max_saved
    )
else:
    raise ValueError("SESSION_STORAGE 只支持 sqlite 或 memory")


def get_catalog_retriever() -> CatalogRetriever:
    global _catalog_retriever
    if _catalog_retriever is None:
        _catalog_retriever = CatalogRetriever.from_jsonl()
    return _catalog_retriever


@app.get("/", include_in_schema=False)
def web_app():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_app():
    return FileResponse(ROOT / "static" / "admin.html")


@app.get("/health")
def health():
    database = check_database()
    return {
        "api": "ok",
        "database": database,
        "catalog_retrieval": {
            "enabled": settings.catalog_retrieval_enabled,
            "top_k": settings.catalog_retrieval_top_k,
        },
        "sessions": {
            "storage": settings.session_storage,
            "ttl_seconds": settings.session_ttl_seconds,
            "max_saved": settings.session_max_saved,
        },
    }


@app.post("/catalog/search")
def search_catalog(request: CatalogSearchRequest):
    allowed_types = {"table", "column", "relationship", "metric", "dimension", "value_dictionary"}
    selected_types = set(request.document_types) if request.document_types else None
    if selected_types and not selected_types <= allowed_types:
        raise HTTPException(status_code=400, detail="包含未知的文档类型")
    results = get_catalog_retriever().search(
        request.query.strip(), limit=request.limit, document_types=selected_types
    )
    return {"query": request.query, "count": len(results), "results": [item.as_dict() for item in results]}


@app.get("/sources")
def list_sources():
    registry = SourceRegistry.load()
    return {"sources": [registry.public_status(source) for source in registry.definition.sources]}


@app.post("/sources/{source_id}/test")
def test_source(source_id: str):
    try:
        registry = SourceRegistry.load()
        return registry.test_connection(registry.get(source_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"连接测试失败：{exc}") from exc


@app.post("/sources/{source_id}/scan")
def scan_source(source_id: str, request: SourceScanRequest):
    try:
        registry = SourceRegistry.load()
        result = registry.scan(
            registry.get(source_id),
            include_value_samples=request.include_value_samples,
            max_sample_values=request.max_sample_values,
        )
        global _catalog_retriever
        _catalog_retriever = None
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"扫描失败：{exc}") from exc


@app.post("/query/v1")
def query_v1(request: QueryRequest):
    """V1 基线：模型直接生成 SQL。"""
    try:
        raw_sql = generate_sql(request.question.strip())
        sql = validate_readonly_sql(raw_sql)
        rows = execute_readonly(sql)
        return {"question": request.question, "sql": sql, "rows": rows, "count": len(rows)}
    except UnsafeSQL as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"查询失败：{exc}") from exc


@app.post("/query")
@app.post("/query/v2")
def query_v2(request: QueryRequest):
    """V2 默认链路：结构化计划 -> 后端编译参数化 SQL。"""
    try:
        model_call: dict = {}
        spec = generate_query_spec(request.question.strip(), metadata_callback=model_call.update)
        return {**execute_query_spec(spec, request.question), "model_call": model_call}
    except NeedsClarification as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except UnsafeSQL as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"查询失败：{exc}") from exc


@app.post("/query/resolve")
def resolve_query(request: ResolveRequest):
    """使用用户选择的候选值继续执行，不再次调用模型。"""
    try:
        data = request.query_spec.model_dump()
        if request.filter_index >= len(data["filters"]):
            raise ValueError("待确认的过滤条件不存在")
        item = data["filters"][request.filter_index]
        if request.entity_id is not None:
            field = item["field"]
            if field not in {"product", "customer"}:
                raise ValueError("该字段不支持实体 ID 确认")
            matches = lookup_alias(field, item["value"]) + lookup_name(field, request.value)
            if not any(match.entity_id == request.entity_id and match.value == request.value
                       for match in matches):
                raise ValueError("实体 ID 不属于当前候选值")
        data["filters"][request.filter_index]["value"] = request.value
        data["filters"][request.filter_index]["entity_id"] = request.entity_id
        spec = QuerySpec.model_validate(data)
        return execute_query_spec(spec, "候选值确认")
    except NeedsClarification as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"查询失败：{exc}") from exc


def _session_result(result: dict, state, changes: list[str], patch_source: str) -> dict:
    return {
        **result,
        "session_id": state.session_id,
        "turn_count": state.turn_count,
        "changes": changes,
        "patch_source": patch_source,
    }


@app.post("/sessions")
def create_session(request: QueryRequest):
    """Start a multi-turn query with a complete QuerySpec."""
    question = request.question.strip()
    try:
        model_call: dict = {}
        spec = generate_query_spec(question, metadata_callback=model_call.update)
        try:
            result = execute_query_spec(spec, question)
        except NeedsClarification as exc:
            detail = exc.detail()
            state = conversation_store.create(spec, question, detail)
            detail["session_id"] = state.session_id
            detail["turn_count"] = state.turn_count
            raise HTTPException(status_code=409, detail=detail) from exc
        resolved = QuerySpec.model_validate(result["query_spec"])
        state = conversation_store.create(resolved, question)
        return {**_session_result(result, state, ["已创建多轮查询"], "initial_model"),
                "model_call": model_call}
    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"查询失败：{exc}") from exc


@app.post("/sessions/{session_id}/query")
def continue_session(session_id: str, request: QueryRequest):
    """Apply a model-generated patch while preserving unmentioned state."""
    message = request.question.strip()
    try:
        current = conversation_store.get(session_id)
        if current.pending_clarification is not None:
            raise HTTPException(
                status_code=409,
                detail={
                    **current.pending_clarification,
                    "session_id": session_id,
                    "turn_count": current.turn_count,
                    "message": "请先完成当前候选值确认",
                },
            )
        model_call: dict = {}
        patch = parse_local_patch(message, current.query_spec)
        patch_source = "local" if patch is not None else "model"
        if patch is None:
            patch = generate_query_spec_patch(message, current.query_spec,
                                              metadata_callback=model_call.update)
        merged, changes = apply_query_spec_patch(current.query_spec, patch)
        try:
            result = execute_query_spec(merged, message)
        except NeedsClarification as exc:
            detail = exc.detail()
            state = conversation_store.update(
                session_id, merged, message, detail, expected_turn_count=current.turn_count
            )
            detail["session_id"] = state.session_id
            detail["turn_count"] = state.turn_count
            detail["changes"] = changes
            raise HTTPException(status_code=409, detail=detail) from exc
        resolved = QuerySpec.model_validate(result["query_spec"])
        state = conversation_store.update(
            session_id, resolved, message, expected_turn_count=current.turn_count
        )
        return {**_session_result(result, state, changes, patch_source),
                "model_call": model_call if patch_source == "model" else None}
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"续问失败：{exc}") from exc


@app.post("/sessions/{session_id}/resolve")
def resolve_session(session_id: str, request: SessionResolveRequest):
    """Resolve the pending entity candidate without another model call."""
    try:
        current = conversation_store.get(session_id)
        pending = current.pending_clarification
        if pending is None:
            raise ValueError("当前会话没有待确认的候选值")
        filter_index = int(pending["filter_index"])
        data = current.query_spec.model_dump()
        if filter_index >= len(data["filters"]):
            raise ValueError("待确认的过滤条件不存在")
        if not any(candidate.get("value") == request.value and
                   candidate.get("entity_id") == request.entity_id
                   for candidate in pending.get("candidates", [])):
            raise ValueError("请选择当前会话提供的候选值")
        data["filters"][filter_index]["value"] = request.value
        data["filters"][filter_index]["entity_id"] = request.entity_id
        selected = QuerySpec.model_validate(data)
        try:
            result = execute_query_spec(selected, request.value)
        except NeedsClarification as exc:
            detail = exc.detail()
            state = conversation_store.update(
                session_id, selected, request.value, detail,
                expected_turn_count=current.turn_count,
            )
            detail["session_id"] = state.session_id
            detail["turn_count"] = state.turn_count
            raise HTTPException(status_code=409, detail=detail) from exc
        resolved = QuerySpec.model_validate(result["query_spec"])
        state = conversation_store.update(
            session_id, resolved, request.value,
            expected_turn_count=current.turn_count,
        )
        return _session_result(result, state, ["已确认候选值"], "clarification")
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"候选值确认失败：{exc}") from exc


@app.get("/sessions")
def list_sessions(limit: int = 20):
    if not 1 <= limit <= 50:
        raise HTTPException(status_code=422, detail="limit 必须在 1 到 50 之间")
    sessions = conversation_store.list_recent(limit)
    return {"sessions": [
        {
            "session_id": state.session_id,
            "title": state.first_message or state.last_message,
            "turn_count": state.turn_count,
            "pending_clarification": state.pending_clarification is not None,
            "updated_at": state.updated_at.isoformat(),
        }
        for state in sessions
    ]}


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    try:
        return conversation_store.get(session_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    if not conversation_store.delete(session_id):
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return {"deleted": True, "session_id": session_id}


def execute_query_spec(spec: QuerySpec, question: str):
    normalized_spec, normalizations = normalize_query_spec(spec, question)
    resolved_spec, resolutions = resolve_query_spec(normalized_spec)
    compiled = compile_query(resolved_spec)
    sql = validate_readonly_sql(compiled.sql)
    rows = execute_readonly(sql, compiled.params)
    return {
        "question": question,
        "query_spec": resolved_spec.model_dump(),
        "resolutions": [item.__dict__ for item in resolutions],
        "normalizations": normalizations,
        "sql": sql,
        "parameters": [str(value) for value in compiled.params],
        "rows": rows,
        "count": len(rows),
        "pipeline": "query-spec-v2.2",
    }
