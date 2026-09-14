import argparse
import json
from pathlib import Path

from app.catalog_builder import (
    apply_catalog_overrides,
    build_rag_documents,
    introspect_postgres,
    write_catalog_artifacts,
)
from app.catalog_models import CatalogOverrides
from app.config import settings


ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description="把 PostgreSQL 元数据转换为统一目录和 RAG 文档")
    parser.add_argument("--source-id", default="analytics_local")
    parser.add_argument("--source-name", default=None)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "catalog" / "generated")
    parser.add_argument("--include-value-samples", action="store_true")
    parser.add_argument("--max-sample-values", type=int, default=20)
    parser.add_argument("--overrides", type=Path, default=ROOT / "catalog" / "overrides.json")
    args = parser.parse_args()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL 未配置")
    dictionary = json.loads((ROOT / "business_dictionary.json").read_text(encoding="utf-8"))
    catalog = introspect_postgres(
        settings.database_url,
        source_id=args.source_id,
        source_name=args.source_name,
        include_value_samples=args.include_value_samples,
        max_sample_values=args.max_sample_values,
    )
    if args.overrides.exists():
        overrides = CatalogOverrides.model_validate_json(args.overrides.read_text(encoding="utf-8"))
        catalog = apply_catalog_overrides(catalog, overrides)
    documents = build_rag_documents(catalog, dictionary)
    paths = write_catalog_artifacts(catalog, documents, args.output_dir)
    print(f"tables={len(catalog.tables)} documents={len(documents)}")
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
