"""Gera um lote fictício, no formato dos documentos reais da Câmara, para exercitar coleta, parser e evidências.

O conteúdo normativo é inventado (PL 100/2026); a forma segue os PDFs do sistema da Câmara:
requerimento "nos termos do art. 161, I/II, do Regimento Interno", comando de emenda, substitutivo
dentro do parecer e carimbo de autenticação no rodapé.

Uso: python examples/build_synthetic_inputs.py work/demo_input
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document

PROPOSITION = {"id": None, "sigla": "PL", "numero": 100, "ano": 2026}
STAMP = ["*CD260000000001*", "Apresentação: 10/03/2026 15:00:00.000 - PLEN",
         "Assinado eletronicamente pelo(a) Dep. Fulano de Tal"]

ORIGINAL = [
    "PROJETO DE LEI Nº 100, DE 2026",
    "Dispõe sobre a conferência de documentos em processos administrativos.",
    "Art. 1º Esta Lei disciplina a conferência de documentos.",
    "Art. 3º O órgão manterá:",
    "I - cadastro dos interessados;",
    "II - registro da data de recebimento;",
    "Art. 4º O órgão exigirá laudo técnico para todo documento recebido.",
    "Art. 5º O órgão verificará a integridade do documento.",
    "§ 1º A verificação será feita por servidor efetivo.",
    "§ 2º O resultado será publicado no sítio eletrônico do órgão.",
    "Art. 6º A decisão será comunicada ao interessado antes da decisão final.",
    "§ 1º A comunicação ocorrerá em até 30 dias.",
]


def substitutivo(version: int) -> list[str]:
    published = "em até 5 dias úteis" if version == 2 else "no sítio eletrônico do órgão"
    return [
        "SUBSTITUTIVO AO PROJETO DE LEI Nº 100, DE 2026",
        "Art. 1º Esta Lei disciplina a conferência de documentos.",
        "Art. 3º O órgão manterá:",
        "I - cadastro dos interessados;",
        "II - registro da datta de recebimento;",
        "Art. 4º O órgão exigirá laudo técnico para todo documento recebido.",
        "Art. 5º O órgão verificará a integridade do documento antes de registrar a decisão.",
        "§ 1º A verificação será feita por servidor efetivo.",
        f"§ 2º O resultado da verificação será publicado {published}.",
        *STAMP,
        "Art. 6º O órgão comunicará a decisão ao interessado antes da decisão.",
        "§ 1º A comunicação ocorrerá em até 30 dias, por meio eletrônico.",
    ]


def request(number: int, basis: str, title: str, obj: str) -> list[str]:
    return [
        "CÂMARA DOS DEPUTADOS", f"{title} - PL 100/2026", "DESTAQUE DE BANCADA", "Senhor(a) Presidente,",
        f"Requeiro a V. Exa., nos termos do art. 161, {basis}, do Regimento Interno da Câmara dos Deputados, {obj} apresentado à(ao) PL 100/2026",
        "Sala das Sessões, ____/_____/_____.", "LÍDER FICTÍCIO", "PARTIDO/UF",
        f"DTQ {number} => PL 100/2026", *STAMP,
    ]


FILES = {
    "PL-100-2026.docx": (ORIGINAL, {"source_id": "local:pl-100", "role": "texto_principal", "kind": "PL", "number": 100,
                                    "date": "2026-02-02", "description": "Dispõe sobre a conferência de documentos."}),
    "PRLP-1.docx": (["PARECER PROFERIDO EM PLENÁRIO", "I - RELATÓRIO", "Voto pela aprovação, na forma do substitutivo.",
                     *substitutivo(1)],
                    {"source_id": "local:prlp-1", "role": "parecer", "kind": "PRLP", "number": 1, "date": "2026-03-03"}),
    "PRLP-2.docx": (["PARECER PROFERIDO EM PLENÁRIO (REFORMULAÇÃO)", "Voto pela aprovação, na forma do novo substitutivo.",
                     *substitutivo(2)],
                    {"source_id": "local:prlp-2", "role": "parecer", "kind": "PRLP", "number": 2, "date": "2026-03-09"}),
    "DTQ-1.docx": (request(1, "II", "DESTAQUE DE EMENDA", "destaque para EMP 1"),
                   {"source_id": "local:dtq-1", "role": "destaque", "kind": "DTQ", "number": 1, "date": "2026-03-10"}),
    "EMP-1.docx": (["CÂMARA DOS DEPUTADOS", "EMENDA DE PLENÁRIO", "PROJETO DE LEI Nº 100, DE 2026",
                    "Dê-se ao art. 4º do Projeto de Lei nº 100, de 2026, a seguinte redação:",
                    "“Art. 4º O órgão exigirá laudo técnico apenas para documentos de valor superior a R$ 50.000,00.”",
                    "JUSTIFICAÇÃO", "Exemplo fictício: restringe a exigência de laudo.", "EMP 1 => PL 100/2026", *STAMP],
                   {"source_id": "local:emp-1", "role": "emenda", "kind": "EMP", "number": 1, "date": "2026-03-08"}),
    "DTQ-2.docx": (request(2, "I", "DESTAQUE PARA VOTAÇÃO EM SEPARADO",
                           "destaque para votação em separado do(a) supressão do art. 5º, § 2º, do substitutivo"),
                   {"source_id": "local:dtq-2", "role": "destaque", "kind": "DTQ", "number": 2, "date": "2026-03-10"}),
    "DTQ-3.docx": (request(3, "I", "DESTAQUE PARA VOTAÇÃO EM SEPARADO",
                           "destaque para votação em separado do(a) expressão \"antes da decisão\" constante do art. 6º, "
                           "caput, do substitutivo, e, por decorrência, a expressão \"em até 30 dias\", constante do § 1º "
                           "deste mesmo artigo do substitutivo"),
                   {"source_id": "local:dtq-3", "role": "destaque", "kind": "DTQ", "number": 3, "date": "2026-03-10"}),
    "ERD-1.docx": (["EMENDA DE REDAÇÃO", "Dê-se ao inciso II do art. 3º do substitutivo a seguinte redação:",
                    "“II - registro da data de recebimento;”", "JUSTIFICAÇÃO", "Corrige erro de grafia.", *STAMP],
                   {"source_id": "local:erd-1", "role": "emenda", "kind": "ERD", "number": 1, "date": "2026-03-10"}),
}


def create(path: Path, lines: list[str], meta: dict) -> None:
    doc = Document()
    for line in lines:
        doc.add_paragraph(line)
    doc.save(path)
    path.with_name(path.stem + ".meta.json").write_text(
        json.dumps({**meta, "proposition": PROPOSITION}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "work/demo_input")
    output.mkdir(parents=True, exist_ok=True)
    for name, (lines, meta) in FILES.items():
        create(output / name, lines, meta)
    print(output)


if __name__ == "__main__":
    main()
