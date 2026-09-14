from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .database import check_database, execute_readonly
from .llm import generate_query_spec, generate_sql
from .query_spec import QuerySpec, compile_query
from .sql_guard import UnsafeSQL, validate_readonly_sql
from .value_resolver import NeedsClarification, resolve_query_spec
from .catalog_retrieval import CatalogRetriever
from .config import settings
from .source_registry import SourceRegistry

app = FastAPI(title="中文智能问数", version="0.5.0")
ROOT = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class ResolveRequest(BaseModel):
    query_spec: QuerySpec
    filter_index: int = Field(ge=0)
    value: str = Field(min_length=1, max_length=200)


class CatalogSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    document_types: list[str] | None = None
    limit: int = Field(default=8, ge=1, le=30)


class SourceScanRequest(BaseModel):
    include_value_samples: bool = False
    max_sample_values: int = Field(default=20, ge=1, le=50)


_catalog_retriever: CatalogRetriever | None = None


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
        spec = generate_query_spec(request.question.strip())
        return execute_query_spec(spec, request.question)
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
        data["filters"][request.filter_index]["value"] = request.value
        spec = QuerySpec.model_validate(data)
        return execute_query_spec(spec, "候选值确认")
    except NeedsClarification as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"查询失败：{exc}") from exc


def execute_query_spec(spec: QuerySpec, question: str):
    resolved_spec, resolutions = resolve_query_spec(spec)
    compiled = compile_query(resolved_spec)
    sql = validate_readonly_sql(compiled.sql)
    rows = execute_readonly(sql, compiled.params)
    return {
        "question": question,
        "query_spec": resolved_spec.model_dump(),
        "resolutions": [item.__dict__ for item in resolutions],
        "sql": sql,
        "parameters": [str(value) for value in compiled.params],
        "rows": rows,
        "count": len(rows),
        "pipeline": "query-spec-v2.2",
    }
