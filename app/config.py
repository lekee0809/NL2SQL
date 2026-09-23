import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "")
    # 兼容早期配置文件中的 OPENAI_* 命名；请求仍发送到 DashScope。
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    llm_base_url: str = os.getenv(
        "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    llm_model: str = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "qwen-plus")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    statement_timeout_ms: int = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "5000"))
    max_result_rows: int = int(os.getenv("MAX_RESULT_ROWS", "200"))
    catalog_retrieval_enabled: bool = env_bool("CATALOG_RETRIEVAL_ENABLED", False)
    catalog_retrieval_top_k: int = int(os.getenv("CATALOG_RETRIEVAL_TOP_K", "10"))
    session_ttl_seconds: int = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
    default_source_id: str = os.getenv("DEFAULT_SOURCE_ID", "analytics_local")


settings = Settings()
