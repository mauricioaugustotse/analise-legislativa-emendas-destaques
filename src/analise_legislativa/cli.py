"""Interface de linha de comando para reproduzir as etapas do pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from .analyze import analyze, validate
from .collect import collect_local, collect_official
from .parser import prepare


STAGES = {
    "collect": "Obtém os documentos nas fontes oficiais (ou numa pasta) e grava manifest.json",
    "prepare": "Extrai o texto, identifica objeto, operação e dispositivo e grava cases.json",
    "analyze": "Envia cada caso ao serviço de análise interno (ANALYZER_URL) e grava results.json",
    "validate": "Confere citações, números e coerência das análises e grava validation.json",
    "run": "Executa as quatro etapas em sequência",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="analise-legislativa", description="Núcleo rastreável de emendas e destaques")
    parser.add_argument("--verbose", action="store_true", help="Logs técnicos sem texto integral")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, summary in STAGES.items():
        sub = commands.add_parser(name, help=summary, description=summary)
        sub.add_argument("--workspace", type=Path, required=True, help="Pasta de artefatos da execução")
        if name in {"collect", "run"}:
            source = sub.add_mutually_exclusive_group(required=True)
            source.add_argument("--proposicao", help="Sigla número/ano, ID ou URL oficial")
            source.add_argument("--input-dir", type=Path, help="Pasta de PDFs ou DOCXs locais e metadados opcionais")
            sub.add_argument("--all-congress-amendments", action="store_true",
                             help="Para MPV, inclui todas as emendas dos dados do Congresso")
        if name in {"analyze", "run"}:
            sub.add_argument("--analyzer-url", default="", help="Endpoint HTTP do analisador interno")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "run" and not (args.analyzer_url or os.getenv("ANALYZER_URL")):
            raise ValueError("Configure ANALYZER_URL para um serviço interno antes de executar run")
        if args.command in {"collect", "run"}:
            if args.input_dir:
                collected = collect_local(args.input_dir, args.workspace)
            else:
                collected = collect_official(args.proposicao, args.workspace,
                                             include_all_congress_amendments=args.all_congress_amendments)
            print(json.dumps({"stage": "collect", "run_id": collected["run_id"],
                              "documents": len(collected["documents"]), "warnings": len(collected["warnings"])}, ensure_ascii=False))
        if args.command in {"prepare", "run"}:
            package = prepare(args.workspace)
            print(json.dumps({"stage": "prepare", "run_id": package["run_id"],
                              "cases": len(package["cases"]),
                              "review_required": sum(x["review_required"] for x in package["cases"])}, ensure_ascii=False))
        if args.command in {"analyze", "run"}:
            results = analyze(args.workspace, url=args.analyzer_url or os.getenv("ANALYZER_URL", ""))
            print(json.dumps({"stage": "analyze", "run_id": results["run_id"],
                              "results": len(results["results"])}, ensure_ascii=False))
        if args.command in {"validate", "run"}:
            report = validate(args.workspace)
            print(json.dumps({"stage": "validate", "run_id": report["run_id"],
                              "counts": report["counts"]}, ensure_ascii=False))
            return 1 if report["counts"]["failed"] else 0
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        logging.error("pipeline_failed stage=%s type=%s detail=%s", args.command, type(exc).__name__, exc)
        return 2
