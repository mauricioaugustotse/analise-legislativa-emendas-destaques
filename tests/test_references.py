"""Leitura de referências a dispositivos, com cláusulas no formato real dos requerimentos e emendas."""

from __future__ import annotations

import pytest

from analise_legislativa.references import (
    clean_number, commands, expand, operation_for, proposed_devices, resolve, same_norm, text_role_for,
)


def keys(devices):
    out = []
    for d in devices:
        parts = [d.article]
        if d.caput:
            parts.append("caput")
        parts += [f"{name}={getattr(d, name)}" for name in ("paragraph", "inciso", "alinea", "item") if getattr(d, name)]
        if d.expression:
            parts.append(f"expr={d.expression}")
        out.append(" ".join(parts))
    return out


def from_commands(text):
    found = []
    for verb, clause, _start, _end in commands(text):
        found += resolve(clause, operation=operation_for(verb), text_role=text_role_for(clause))
    return found


@pytest.mark.parametrize("raw, expected", [
    ("2º-F", "2-F"), ("1o", "1"), ("XLVI- A", "XLVI-A"), ("1º-1", "1-1"), ("144-A", "144-A"), ("8º-\nA", "8-A"),
])
def test_clean_number(raw, expected):
    assert clean_number(raw, roman=raw.startswith("X")) == expected


def test_ranges_are_expanded():
    assert expand(["10", "13"], [" a "]) == ["10", "11", "12", "13"]
    assert expand(["2-C", "2-E"], [" a "]) == ["2-C", "2-D", "2-E"]
    assert expand(["1-1", "1-3"], [" a "]) == ["1-1", "1-2", "1-3"]
    assert expand(["I", "IV"], [" a "], roman=True) == ["I", "II", "III", "IV"]
    assert expand(["2", "11"], [" e "]) == ["2", "11"]


def test_dvs_paragraph_after_article():
    devices = resolve("supressão do art. 5°, §2°, do substitutivo apresentado à(ao) PL 100/2026")
    assert keys(devices) == ["5 paragraph=2"]
    assert text_role_for("do art. 5º do substitutivo") == "substitutivo"


def test_chain_from_alinea_to_article_with_container_and_norm():
    clause = "alínea 'a', do inciso II, do § 8º-A, do art. 144, da CF, com redação alterada pelo art. 1º do Substitutivo"
    [device] = resolve(clause)
    assert keys([device]) == ["144 paragraph=8-A inciso=II alinea=a"]
    assert device.container_article == "1"
    assert device.amended_norm == "Constituição Federal"


def test_expression_caput_and_consequential_paragraph():
    clause = ('expressão "antes da decisão" constante do art. 6º, caput, do substitutivo, e, por decorrência, '
              'a expressão "em até 30 dias", constante do § 1º deste mesmo artigo do substitutivo')
    assert keys(resolve(clause)) == ["6 caput expr=antes da decisão", "6 paragraph=1 expr=em até 30 dias"]


def test_inciso_with_suffix_split_by_pdf_line_break():
    clause = "Inciso XLVI-\nA, e respectivas alíneas, do art. 5º da Constituição Federal, com redação dada pelo art. 1º do substitutivo"
    [device] = resolve(clause)
    assert (device.article, device.inciso, device.container_article) == ("5", "XLVI-A", "1")


def test_numbers_inside_the_expression_are_not_devices():
    [device] = resolve('expressão "prazo do art. 10" do art. 4º do substitutivo')
    assert (device.article, device.expression) == ("4", "prazo do art. 10")


def test_item_and_sole_paragraph():
    assert keys(resolve("Destaque do item 1 da alínea a do inciso II do art. 5º")) == ["5 inciso=II alinea=a item=1"]
    [device] = resolve("supressão do parágrafo único do art. 3º da Lei nº 9.782, de 26 de janeiro de 1999")
    assert (device.paragraph, device.amended_norm) == ("unico", "Lei 9782/1999")


def test_external_norm_alteration_keeps_the_norm():
    [device] = resolve("§ 2º-F do art. 1º do Decreto-Lei nº 1.804, de 3 de setembro de 1980, alterado pelo "
                       "Projeto de Lei de Conversão nº 13, de 2026", text_role="substitutivo")
    assert (device.article, device.paragraph, device.amended_norm) == ("1", "2-F", "Decreto-Lei 1804/1980")
    assert text_role_for("alterado pelo Projeto de Lei de Conversão nº 13") == "substitutivo"
    assert same_norm("Decreto-Lei 1804/1980", "Decreto-Lei 1804") and not same_norm("Lei 1804/1980", "Decreto-Lei 1804/1980")


def test_part_of_amendment_is_its_own_article():
    [device] = resolve(",artigo 144 da emenda 04 apresentada à(ao) PEC 18/2025")
    assert (device.article, device.amended_norm, device.container_article) == ("144", "", "")


@pytest.mark.parametrize("text, expected", [
    ("Dê-se ao Art. 5º a seguinte redação, suprime-se seus parágrafos:\n“Art. 5º Texto proposto.", ["5"]),
    ("Art. 1º Acrescente-se o inciso VIII ao art. 144 da Constituição Federal,\nno art. 1º da Proposta de Emenda "
     "à Constituição nº 18, de 2025, com a seguinte redação:\n\"Art. 144.", ["144 inciso=VIII"]),
    ("Art. 1º Altere-se o inciso XXII do art. 22, o inciso II e os §§ 2º, 2º-A e 11\ndo art. 144 da Constituição "
     "Federal, ambos no art. 1º da Proposta, com a seguinte redação:\n\"Art. 22",
     ["22 inciso=XXII", "144 inciso=II", "144 paragraph=2", "144 paragraph=2-A", "144 paragraph=11"]),
    ("Acrescente-se ao artigo 144 o inciso VIII e os § 5º-B e § 9º-A, com a seguinte\nredação:\n\"Art. 144",
     ["144 inciso=VIII", "144 paragraph=5-B", "144 paragraph=9-A"]),
    ("Altere-se o inciso VII do caput, o §8º e o §14 do art. 144 da Constituição Federal:\n“Art.144",
     ["144 caput inciso=VII", "144 paragraph=8", "144 paragraph=14"]),
    ("Acrescentem-se §§ 2º-C a 2º-E ao art. 1º, todos do Decreto-Lei nº 1.804, de 3 de setembro de 1980:\n“Art. 1º",
     ["1 paragraph=2-C", "1 paragraph=2-D", "1 paragraph=2-E"]),
    ("Suprima-se o art. 2º; e acrescentem-se arts. 3º e 4º à Medida Provisória, nos termos a seguir:\n“Art. 3º",
     ["2", "3", "4"]),
    ("Art. 1º O art. 22 da Constituição Federal passa a vigorar acrescido do inciso\nXXXII, com a seguinte redação:\n“Art. 22",
     ["22 inciso=XXXII"]),
    ("Acrescentem-se alíneas “a” e “b” ao inciso II do § 2º-B do art. 1º do Decreto-Lei nº 1.804, de 1980:",
     ["1 paragraph=2-B inciso=II alinea=a", "1 paragraph=2-B inciso=II alinea=b"]),
])
def test_amendment_commands(text, expected):
    assert keys(from_commands(text)) == expected


def test_command_operations_and_word_boundary():
    assert [operation_for(v) for v, *_ in commands("Suprima-se o art. 2º; e acrescentem-se arts. 3º e 4º:")] == ["supressao", "acrescimo"]
    assert operation_for("Dê-se") == "nova_redacao"
    assert not list(commands("Concede-se crédito presumido aos contribuintes."))


def test_header_summary_of_the_proposition_is_not_a_command():
    text = "PROPOSTA DE EMENDA À CONSTITUIÇÃO Nº 18, DE 2025\nAltera os art. 21, art. 22 e art. 144 da Constituição.\nEMENDA Nº\n"
    assert not list(commands(text))


def test_partial_redaction_lists_only_written_devices():
    proposed = ("“Art. 6º Os suplementos somente poderão ser comercializados após a regularização.\n"
                "...............................................\n"
                "§ 3º Sem comprovação científica, a autoridade não concederá a regularização.\n"
                "§ 4º Ficam proibidos de intermediar a venda de produto irregular.\n"
                "..............................................”")
    assert keys(proposed_devices(proposed)) == ["6 caput", "6 paragraph=3", "6 paragraph=4"]
