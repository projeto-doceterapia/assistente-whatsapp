from __future__ import annotations

import argparse
import asyncio
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.evaluation.loader import ManifestError, carregar_manifesto
from app.evaluation.providers import provider_de_env
from app.evaluation.report import Relatorio, render_tabela
from app.evaluation.runner import executar

_DEFAULT_MANIFEST = Path("evaluation/set.json")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description="Run the AI extraction evaluation set and report per-item accuracy.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_DEFAULT_MANIFEST,
        help="Path to the manifest JSON (default: evaluation/set.json).",
    )
    parser.add_argument(
        "--images",
        type=Path,
        default=None,
        help="Base folder for image cases (default: the manifest folder's images/).",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Provider name for real runs (default: LLM_PROVIDER or 'gemini').",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="Replay the manifest's recorded responses offline (no real provider).",
    )
    parser.add_argument(
        "--threshold",
        default=None,
        help="Override the per-item accuracy bar (0-1). Default: the manifest's.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the JSON report to this path in addition to printing the table.",
    )
    return parser.parse_args(argv)


def _threshold(valor: str | None) -> Decimal | None:
    if valor is None:
        return None
    try:
        limite = Decimal(valor)
    except InvalidOperation as exc:
        raise SystemExit(f"invalid --threshold: {valor}") from exc
    if not (Decimal(0) < limite <= Decimal(1)):
        raise SystemExit("--threshold must be in (0, 1]")
    return limite


async def _run(args: argparse.Namespace) -> Relatorio:
    try:
        manifesto = carregar_manifesto(args.manifest)
    except ManifestError as exc:
        raise SystemExit(str(exc)) from exc
    base_imagens = args.images or (args.manifest.parent / "images")
    provider_nome = args.provider or provider_de_env()
    return await executar(
        manifesto,
        base_imagens,
        usar_fake=args.fake,
        provider_nome=provider_nome,
        threshold=_threshold(args.threshold),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    relatorio = asyncio.run(_run(args))
    print(render_tabela(relatorio))
    if args.report is not None:
        args.report.write_text(relatorio.to_json(), encoding="utf-8")
        print(f"\nrelatório JSON salvo em {args.report}")
    return 0 if relatorio.aprovado else 1


if __name__ == "__main__":
    raise SystemExit(main())
