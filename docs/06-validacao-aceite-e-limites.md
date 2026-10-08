# 6. Validação, aceite e limites

## 6.1 Testes automatizados

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

A suíte roda sem rede e sem modelo de linguagem. Os textos de teste são sintéticos, mas reproduzem o formato dos documentos reais da Câmara.

| Arquivo | O que garante |
| --- | --- |
| `tests/test_references.py` | leitura de dispositivos: sufixos (`2º-F`, `XLVI-A`, `1º-1`), intervalos (`§§ 2º-C a 2º-E`, `incisos I a VII`), cadeias, contenedor, norma alterada, expressão; os comandos de emenda reais da tabela do documento 3 |
| `tests/test_parser.py` | objeto do requerimento; modo pelo art. 161 e pelo tipo oficial; destaque de emenda sem a emenda não vira DVS; vínculo ambíguo; `§ 2º` ≠ `§ 2º-B`; `144-A` ≠ `144`; prosa ≠ cabeçalho; inciso do caput; MPV com dois "Art. 1º"; redação parcial; artigo novo; emenda dentro de parecer; EMR ≠ emenda de redação; versão mais recente indisponível; redação vigente; o lote inteiro de `examples/` |
| `tests/test_collect.py` | paginação, 429 e falhas estruturadas; redirecionamento externo; HTTP do Senado promovido a HTTPS; complemento de MPV só com o que falta; sidecar do fluxo original; formas de referência à proposição |
| `tests/test_extract.py` | OCR quando o texto é raso; OCR em UTF-8 e `TESSERACT_CMD`; ordem de tabela em DOCX; remoção do carimbo do Infoleg |
| `tests/test_analyze.py` | endpoint interno obrigatório; checkpoint e nova tentativa; números sem lastro; citação por afirmação; `change_kind`; veredito obrigatório e restrito a destaques; normas notórias; instruções por modo |
| `tests/test_reference_analyzer.py` | circuito completo: pipeline, analisador de referência via HTTP, modelo simulado e validação |

## 6.2 Validação com proposições reais

O parser foi conferido com três proposições públicas, uma de cada tipo, coletadas da API em outubro de 2026. Os números refletem o estado dos dados naquela data e podem mudar se a Câmara publicar novos documentos.

```bash
analise-legislativa collect --proposicao "PEC 18/2025" --workspace work/pec18
analise-legislativa prepare --workspace work/pec18
```

| Proposição | Documentos | Casos | Modos | Casos com `review_required` |
| --- | --- | --- | --- | --- |
| PL 5229/2025 | 15 | 9 | 3 destaques de emenda, 3 DVS, 3 emendas | 0 |
| PEC 18/2025 | 72 | 55 | 27 destaques de emenda, 5 DVS, 23 emendas | 1 (o DTQ 15 cita o art. 161, I, para destacar artigo de emenda) |
| MPV 1357/2026 | 126 | 123 | 10 destaques de emenda, 1 DVS, 112 emendas | 0 |

Conferências feitas caso a caso, entre outras:

- **MPV 1357/2026, DTQ 11** ("§ 2º-F do art. 1º do Decreto-Lei nº 1.804, de 1980, alterado pelo PLV nº 13, de 2026"). O recorte saiu do PLV 13: "§ 2º-F O Poder Executivo poderá estabelecer limites quantitativos ou de frequência para a aplicação da redução de alíquota prevista no inciso II do § 2º-B…". É o caso que exigiu tratar sufixo de parágrafo e o bloco da norma alterada.
- **PL 5229/2025, DTQ 5 e 6** (DVS de expressões). As expressões "A obtenção da certificação de" e "após o registro sanitário na Anvisa, obtido" foram localizadas literalmente no substitutivo do PRLP 2, o mais recente. O SBT 1, da comissão, não as contém.
- **PEC 18/2025, DVS da segunda rodada**. "alínea 'a', do inciso II, do § 8º-A, do art. 144, da CF" resultou em "a) demonstrem capacidade financeira…"; "expressão 'ou grave ameaça à pessoa' … do inciso XLVI-A do art. 5º" resultou no inciso XLVI-A e na expressão.
- **PEC 18/2025, EMR 1 e 2** (CCJC). São supressões de mérito, e não emendas de redação: "Suprima-se o termo 'exclusiva' do § 2º-B…".

O substitutivo mais recente da PEC 18/2025 (SBT 1) é um PDF digitalizado. Sem Tesseract, 30 casos ficam com o aviso "versão mais recente indisponível". Com OCR, o arquivo é lido e esses avisos desaparecem. É o comportamento esperado: o caso não fica errado em silêncio.

## 6.3 Critérios de aceite de uma implantação

1. `collect` e `prepare` reproduzem os resultados da seção 6.2 (ou explicam as diferenças por documentos novos na fonte) e, para cada evidência, o trecho é localizável no original pela página.
2. Num lote com DVS, destaque de emenda, emenda e emenda de redação, a operação nunca é invertida: destaque de emenda não vira supressão e EMR não vira emenda de redação. Emendas homônimas (EMP 4 e EMC 4) nunca são unidas.
3. A versão do texto-base usada é a mais recente legível. Versões anteriores diferentes aparecem como nota, e versão mais recente ilegível aparece como aviso.
4. O serviço de análise recebe só caso e evidências, responde no schema, e `validate` aponta números sem lastro, citações ausentes e veredito incoerente com o objeto.
5. Interrupção e reinício refazem só o que falta; mudança na fonte invalida o checkpoint.
6. Logs e repositório não contêm credenciais nem teor de documentos.
7. A pessoa revisora vê, para cada caso, requerimento, emenda, dispositivo, avisos, notas e texto produzido antes de qualquer uso institucional.

## 6.4 Diferenças em relação ao fluxo original

| Tema | Fluxo original (uso pessoal) | Esta referência |
| --- | --- | --- |
| Modelo | chamada direta a um modelo comercial por API | serviço interno com contrato próprio; modelo escolhido pela DITEC |
| Contexto enviado | resumo do parecer (cerca de 260 tokens) e do texto principal, mais recortes literais | recortes literais por dispositivo, com ID e posição; o serviço pode acrescentar resumo |
| Escolha das versões | o operador marca, na interface, o documento mais atual de cada sigla | tudo é coletado; o parser usa o mais recente de cada grupo e anota as versões anteriores |
| Redação vigente de lei alterada | consulta ao Planalto durante a análise | cópia capturada com `law_ref`, com proveniência no manifesto |
| EMR | tratada como emenda de redação | Emenda de Relator, pela tabela oficial; emenda de redação é ERD |
| Destaques ausentes da API | último recurso: leitura da página `prop_destaques` | não implementado (seção 2.7) |
| Saída | documento Word e texto para copiar | JSON; a apresentação fica com a solução institucional |

## 6.5 Limites conhecidos

- O parser cobre os padrões de redação encontrados nos documentos reais (seções 1.7 e 3.4), mas redação legislativa fora do padrão pode escapar. Nesse caso, o resultado é um aviso, e não um alvo inventado.
- Emendas substitutivas globais e emendas que alteram dezenas de dispositivos são recortadas até 12 dispositivos; o restante vira nota, e o texto integral da emenda segue como evidência.
- O OCR herda os erros do Tesseract ("XLVIA" no lugar de "XLVI-A"); quando o marcador não casa, o parser usa a versão legível anterior e avisa.
- Destaques do art. 161, III (projeto autônomo) e IV (preferência) não são analisados.
- A referência não decide resultado de votação, não declara prejudicialidade e não substitui a conferência jurídica.
- Autenticação corporativa, armazenamento central, fila de tarefas e interface de revisão são da implantação institucional.
