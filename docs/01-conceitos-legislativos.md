# 1. Conceitos do processo legislativo para a equipe de TI

As regras do parser só fazem sentido com alguns conceitos do processo legislativo. Este documento os apresenta na medida do necessário para ler o código, com a indicação de onde cada um aparece.

## 1.1 O cenário de uso

Uma proposição (projeto de lei, PEC, medida provisória etc.) chega ao Plenário com um **texto-base**. Em geral é o **substitutivo** do relator, e não o texto original. Antes e durante a votação, as bancadas apresentam **destaques** e **emendas**. O Presidente anuncia cada destaque, e a Mesa precisa saber, em segundos:

- o que exatamente está sendo destacado ou emendado (qual dispositivo, qual expressão);
- o que muda no texto se o destaque prevalecer;
- se o destaque é admissível pelo Regimento (RICD, art. 162, IV e V).

O fluxo descrito neste repositório entrega, para cada destaque e cada emenda, um **parágrafo curto** com o efeito material e um **veredito de substancialidade**. A parte determinística (identificar o objeto e recortar o texto literal) está em `src/`. A parte de redação, feita por um modelo de linguagem, tem contrato e regras descritos no [documento 4](04-analise-semantica.md).

## 1.2 Documentos e siglas

A API de Dados Abertos da Câmara identifica cada documento por `siglaTipo`. A tabela abaixo segue a lista oficial `GET /referencias/proposicoes/siglaTipo` e mostra o papel que cada sigla recebe no pipeline (`collect.role_for`).

| Sigla | Nome oficial na API | Papel no pipeline |
| --- | --- | --- |
| PL, PLP, PEC, MPV, PDL… | Projeto de Lei, Proposta de Emenda à Constituição, Medida Provisória… | `texto_principal` (o inteiro teor da proposição) |
| SBT, SBTA | Substitutivo | `substitutivo` |
| SSP | Subemenda Substitutiva de Plenário | `substitutivo` |
| PLV | Projeto de Lei de Conversão (texto aprovado na comissão mista da MPV) | `substitutivo` |
| PRL | Parecer do Relator | `parecer` |
| PAR | Parecer de Comissão | `parecer` |
| PRLP | Parecer Preliminar de Plenário | `parecer` |
| PPR | Parecer Reformulado de Plenário | `parecer` |
| PRLE | Parecer Preliminar às Emendas de Plenário | `parecer` |
| EMP | Emenda de Plenário | `emenda` |
| EMC | Emenda na Comissão; também Emenda à PEC | `emenda` |
| EMS | Emenda/Substitutivo do Senado | `emenda` |
| ESB | Emenda ao Substitutivo | `emenda` |
| SBE, SBR | Subemenda; Subemenda de Relator | `emenda` |
| EMR, EMRP | **Emenda de Relator**; Emenda de Relator Parcial | `emenda` |
| ERD | **Emenda de Redação** | `emenda` (modo `emenda_redacao`) |
| EMA | Emenda Aglutinativa | `emenda` |
| EMPV | Emenda à Medida Provisória (CN) | `emenda` |
| DTQ | Destaque (tipos: para Votação em Separado, de Emenda, de Bancada, de Preferência, para Proposição Autônoma) | `destaque` |
| REQ | Requerimento; sistemas antigos registravam destaques assim | `destaque`, se a ementa falar em destaque |

> **EMR não é emenda de redação.** Pela tabela oficial, EMR é "Emenda de Relator" e pode alterar o mérito; emenda de redação é ERD. Na PEC 18/2025, as EMR 1 e 2 da CCJC são supressões de mérito ("Suprima-se o termo 'exclusiva' do § 2º-B…"). O parser só trata como emenda de redação a sigla ERD ou o texto que se declare "emenda de redação".

O **parecer** de plenário costuma trazer, no fim, o texto do substitutivo que será votado. Por isso substitutivo e parecer são buscados juntos, do mais recente para o mais antigo (ver [documento 3](03-extracao-e-parser.md#35-localizar-o-dispositivo-no-texto-base)).

## 1.3 Destaques: o que diz o Regimento Interno

Texto vigente do RICD (Resolução nº 17/1989, com a redação da Resolução nº 21/2021):

> **Art. 161.** Admitem-se destaques para:
> **I** - votação em separado de parte de proposição;
> **II** - votação de emenda, subemenda, parte de emenda ou de subemenda;
> **III** - tornar emenda ou parte de uma proposição projeto autônomo;
> **IV** - votação de projeto ou substitutivo, ou de parte deles, quando a preferência recair sobre o outro ou sobre proposição apensada;
>
> **Art. 162.** (…) **IV** - não será permitido destaque de expressão cuja retirada inverta o sentido da proposição ou a modifique substancialmente;
> **V** - o destaque será possível quando o texto destacado possa ajustar-se à proposição em que deva ser integrado e forme sentido completo;
> **VI** - o destaque para votação em separado será apreciado submetendo-se a votos, primeiramente, a matéria principal e, em seguida, a destacada, que somente integrará o texto se for aprovada;

O inciso VI do art. 162 explica as operações que o parser atribui:

| Requerimento | Modo no pipeline | Operação pretendida | Por quê |
| --- | --- | --- | --- |
| art. 161, I (parte da proposição) | `dvs` | `supressao_pretendida` | a parte destacada só fica no texto se for aprovada; quem destaca quer retirá-la |
| art. 161, II (emenda ou parte dela) | `destaque_emenda` | `inclusao_emenda` | a emenda só entra no texto se for aprovada; quem destaca quer aprová-la |
| art. 161, III ou IV | `indefinido` | `indeterminada` | projeto autônomo e preferência estão fora do escopo; o caso vai para revisão |

Os incisos IV e V do art. 162 orientam o **veredito** (ver [documento 4](04-analise-semantica.md#43-o-veredito-de-substancialidade)). O § 2º do art. 161 faz do destaque uma prerogativa de bancada, de 1 a 4 por partido conforme o tamanho; daí o título "Destaque de Bancada" nos documentos.

Texto completo: [RICD no portal da Câmara](https://www2.camara.leg.br/legin/fed/rescad/1989/resolucaodacamaradosdeputados-17-21-setembro-1989-320110-normaatualizada-pl.html).

## 1.4 Como um dispositivo é endereçado

A Lei Complementar nº 95/1998 organiza o texto normativo em níveis. O parser representa cada nível num campo do alvo (`target` em `cases.json`):

```text
Art. 144.                            article = "144"
  caput (o texto do artigo)          caput = true
    I - ...                          inciso = "I"          (inciso sem parágrafo pertence ao caput)
  § 8º-A ...                         paragraph = "8-A"     ("parágrafo único" vira "unico")
    II - ...                         inciso = "II"
      a) ...                         alinea = "a"
        1. ...                       item = "1"
```

Exemplo: "alínea 'a', do inciso II, do § 8º-A, do art. 144, da CF" vira `{"article": "144", "paragraph": "8-A", "inciso": "II", "alinea": "a", "amended_norm": "Constituição Federal"}`.

**Sufixos** identificam dispositivos acrescidos depois, sem renumerar os vizinhos: `§ 2º-F`, `art. 144-A`, `inciso XLVI-A`, `art. 1º-1` (forma usada em emendas a MPV). O sufixo é a identidade do dispositivo. `§ 2º` e `§ 2º-B` são dispositivos diferentes, e confundi-los é o erro mais caro do domínio.

**Proposição que altera outra norma.** Uma PEC ou uma MPV costuma ter a forma:

```text
Art. 1º O Decreto-Lei nº 1.804, de 3 de setembro de 1980, passa a vigorar com as seguintes alterações:
“Art. 1º ..............................................
§ 2º-F O Poder Executivo poderá estabelecer limites quantitativos ...” (NR)
Art. 2º Esta Medida Provisória entra em vigor na data de sua publicação.
```

O mesmo documento tem dois "Art. 1º": o da própria MPV (sem aspas) e o do decreto-lei alterado (entre aspas, como manda a LC 95/1998, art. 12, III, "d"). Dois campos registram isso:

- `amended_norm`: a norma a que o dispositivo pertence ("Decreto-Lei 1804/1980", "Constituição Federal");
- `container_article`: o artigo da proposição que contém a alteração ("…no art. 1º da PEC", "…com redação dada pelo art. 1º do substitutivo").

## 1.5 Anatomia de um requerimento de destaque

O sistema da Câmara gera o requerimento num formato fixo. O texto livre do autor fica entre "destaque para…" e "apresentado à(ao)":

```text
CÂMARA DOS DEPUTADOS
DESTAQUE PARA VOTAÇÃO EM SEPARADO - PL 100/2026          <- tipo oficial (título)
DESTAQUE DE BANCADA
Requeiro a V. Exa., nos termos do art. 161, I, do Regimento Interno   <- inciso do RICD
da Câmara dos Deputados, destaque para votação em separado do(a)
expressão "antes da decisão" constante do art. 6º, caput, do substitutivo,   <- objeto
e, por decorrência, a expressão "em até 30 dias", constante do § 1º
deste mesmo artigo do substitutivo apresentado à(ao) PL 100/2026
Sala das Sessões, ____/_____/_____.                       <- fim do objeto
LÍDER - PARTIDO/UF
*CD26...* DTQ n.3 / Apresentação: ... / DTQ 3 => PL 100/2026   <- carimbo, removido na extração
```

O destaque de emenda tem a forma "nos termos do art. 161, II, … destaque para EMP 1 apresentada à(ao) PL …". O teor material está na emenda, e não no requerimento. Sem a emenda no lote, a análise ficaria limitada ao requerimento, e o caso vai para revisão.

## 1.6 Anatomia de uma emenda

```text
EMENDA DE PLENÁRIO
Dê-se ao art. 6º do Projeto de Lei nº 100, de 2026, a seguinte redação:   <- comando
“Art. 6º Os documentos somente serão aceitos após a conferência.          <- redação proposta
...............................................                           <- mantém o que não foi reescrito
§ 3º Sem conferência, o órgão não decidirá.
..............................................”
JUSTIFICAÇÃO                                                              <- fim do comando
```

O **comando** diz a operação e o dispositivo:

| Forma | Exemplo | Operação |
| --- | --- | --- |
| Dê-se / Altere-se / Modifique-se / "passa a vigorar com a seguinte redação" | "Dê-se ao art. 4º a seguinte redação" | `nova_redacao` |
| Acrescente-se / Inclua-se / Insira-se / "passa a vigorar acrescido de" | "Acrescente-se o inciso VIII ao art. 144 da CF" | `acrescimo` |
| Suprima-se / Exclua-se / Revogue-se | "Suprima-se o art. 2º" | `supressao` |
| Substitua-se | "Substitua-se a expressão X por Y" | `substituicao` |

As **linhas pontilhadas** na redação proposta indicam dispositivos mantidos. No exemplo acima, a emenda altera o caput e o § 3º do art. 6º, e não o artigo inteiro; o parser usa isso para estreitar o alvo (`parser._refine_with_proposed`).

Emendas a MPV publicadas pelo Congresso costumam acrescentar artigo novo: "Acrescente-se, onde couber, na Medida Provisória o seguinte artigo: 'Art. …'". Não existe dispositivo atual correspondente. O caso sai como `acrescimo`, sem recorte do texto-base e com uma nota, e não com um aviso.

## 1.7 Armadilhas reais tratadas pelo parser

Cada linha abaixo é um defeito encontrado em documentos reais e coberto por teste automatizado.

| Armadilha | Consequência sem tratamento | Onde é tratada |
| --- | --- | --- |
| `§ 2º` versus `§ 2º-B` | recorte do parágrafo errado | `parser.paragraph_marker` |
| "Art. 144-A." lido como "Art. 144" | incisos do 144-A atribuídos ao 144 | `parser.ARTICLE_LINE` |
| linha de prosa que começa com "art. 144 da Constituição…" | justificação tratada como dispositivo | `parser.ARTICLE_LINE` |
| carimbo do Infoleg entre o caput e o § 1º | evidência com texto que não é norma | `extract.STAMP_LINE` |
| duas rodadas de destaques com a mesma numeração (PEC 18/2025 tem dois "DTQ 1") | um objeto sobrescreve o outro | identidade por `source_id` |
| substitutivo em votação dentro do parecer de plenário mais recente | comparação com texto superado | `parser.SEARCH_ORDER` e `_date_key` |
| destaque de emenda sem a emenda no lote | tratado como supressão (DVS) | `parser.classify_request` |
| emenda de MPV publicada no Senado com URL `http://` | complemento do Congresso nunca baixado | `collect.official_url` |
| EMR tratada como emenda de redação | veredito de mérito suprimido | `parser._case` (ERD) |
| comando sem "-se" ("Acrescente o § 11…") e falso comando ("Concede-se") | dispositivo perdido ou inventado | `references.COMMAND_RE` |
| inciso citado sem parágrafo | recorte invade o § seguinte | `parser._narrow` (inciso do caput) |
| texto da norma alterada entre aspas e artigo próprio sem aspas | "art. 1º" da MPV confundido com o do decreto-lei | `parser.find_device` (`amended_norm` e aspas) |
