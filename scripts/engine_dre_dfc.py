# -*- coding: utf-8 -*-
"""Engine de calculo do DRE/DFC a partir de julho/2026 em diante, usando os
lançamentos ja coletados da Omie (mesma lista usada no resto do dashboard)
combinada com a classificacao de dre_dfc_dados.py."""

import re
import unicodedata
import difflib
from datetime import datetime
from dre_dfc_dados import DE_PARA, DRE_LINHAS, DFC_LINHAS, JUROS_EMPRESTIMOS, \
    MANUAL_FATURAMENTO_KIT, MANUAL_FOLHA, MANUAL_WEG

MESES_ABREV = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

_RE_PERCENTUAL = re.compile(r'\(([\d.,]+)\s*%\)\s*$')
_RE_INATIVA = re.compile(r'\s*\(inativ[ao]\)', re.IGNORECASE)


def _chave_comparacao(s: str) -> str:
    """Normaliza uma string (categoria ou departamento) pra comparação
    tolerante a maiúscula/minúscula, acentuação e espaços sobrando -- ex:
    "comercial", "Comercial ", "COMERCIAL" e "Comércial" viram a mesma
    chave. NÃO tolera erro de digitação de letra (troca/falta de letra):
    testamos e um limiar de similaridade "% parecido" pra isso é perigoso
    (ex: "Mão de Obra Direta - MOD" e "Mão de Obra Indireta - MOI" dão 92%
    de parecido entre si sendo departamentos DIFERENTES de verdade -- mais
    parecido até que "Comercial" vs "Comerical", um erro de digitação real).
    Erros de digitação de letra ficam de fora da correção automática e são
    sinalizados como sugestão no alerta de não-classificados, pra correção
    manual (no Omie) em vez de correção automática arriscada."""
    s = unicodedata.normalize("NFKD", (s or "").strip().casefold())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.split())

# Algumas categorias do Omie (ex: "FGTS", "Salários", "Plano de Saúde") chegam
# sem indicar o departamento -- o valor é um único lançamento que precisa ser
# dividido entre os departamentos conforme o rateio (mesmo dado já usado na
# aba Lançamentos). O DRE/DFC, por sua vez, tem uma linha separada por
# departamento (ex: "Salários - MOD", "Salários - ADM"...), usando as siglas
# MOD/MOI/ADM ou "Comercial" como sufixo. Esse de-para traduz o nome do
# departamento tal como cadastrado no Omie (confirmado em 08/2026 via
# ListarDepartamentos nas 4 empresas -- Matriz, Lagos e Cabo Frio usam os
# mesmos nomes; PS Energia não tem departamento cadastrado, então lançamentos
# de lá com categoria sem sufixo não entram em nenhuma linha por
# departamento) para o sufixo usado nas linhas do DRE/DFC.
DEPARTAMENTO_OMIE_PARA_SUFIXO = {
    "Comercial": "Comercial",
    "Comercial Externo": "Comercial",
    "Comercial Interno": "Comercial",
    "Administrativo": "ADM",
    "Mão de Obra Direta - MOD": "MOD",
    "Mão de Obra Indireta - MOI": "MOI",
    "Corporativo": "Corporativo",
}
SUFIXOS_DEPARTAMENTO = {"MOD", "MOI", "ADM", "Comercial", "Corporativo"}


def normalizar_categoria(categoria_bruta: str) -> str:
    return DE_PARA.get((categoria_bruta or "").strip(), (categoria_bruta or "").strip())


def dividir_categoria_composta(categoria_bruta: str):
    """A Omie às vezes rateia um único lançamento entre 2+ categorias, ex:
    'Despesas com KITs (13,434829%); Insumos para Obras (86,565171%)'. Sem
    tratar isso, o lançamento inteiro não batia com NENHUMA linha do DRE/DFC
    (a string toda virava uma "categoria" que não existe em lugar nenhum) --
    mesmo problema que já tinha sido resolvido só pro Insights, replicado
    aqui pra engine do DRE/DFC.

    Retorna uma lista de (categoria_normalizada, peso), peso somando ~1.0.
    Quando a Omie informa o percentual de cada parte, usamos ele; quando não
    informa (categorias só separadas por ';', sem percentual), rateamos
    igualmente entre as partes -- mesma aproximação já usada no Insights."""
    bruta = (categoria_bruta or "").strip()
    if not bruta:
        return [("-", 1.0)]

    partes_brutas = [p.strip() for p in bruta.split(";") if p.strip()]
    if not partes_brutas:
        partes_brutas = [bruta]

    partes_com_pct = []  # (categoria_limpa, percentual_ou_None)
    for parte in partes_brutas:
        parte_sem_inativa = _RE_INATIVA.sub("", parte).strip()
        m = _RE_PERCENTUAL.search(parte_sem_inativa)
        if m:
            pct = float(m.group(1).replace(".", "").replace(",", ".")) if "," in m.group(1) else float(m.group(1))
            categoria_limpa = _RE_PERCENTUAL.sub("", parte_sem_inativa).strip()
            partes_com_pct.append((categoria_limpa, pct))
        else:
            partes_com_pct.append((parte_sem_inativa, None))

    tem_algum_pct = any(pct is not None for _, pct in partes_com_pct)
    if tem_algum_pct:
        soma_pct = sum(pct for _, pct in partes_com_pct if pct is not None)
        n_sem_pct = sum(1 for _, pct in partes_com_pct if pct is None)
        pct_restante_por_parte = (max(0.0, 100.0 - soma_pct) / n_sem_pct) if n_sem_pct else 0.0
        pesos = [(cat, (pct if pct is not None else pct_restante_por_parte) / 100.0) for cat, pct in partes_com_pct]
    else:
        peso_igual = 1.0 / len(partes_com_pct)
        pesos = [(cat, peso_igual) for cat, _ in partes_com_pct]

    return [(normalizar_categoria(cat), peso) for cat, peso in pesos]


def mes_efetivo(data_br: str, competencia: str):
    """data_br no formato dd/mm/yyyy. Retorna (ano, mes) já deslocado se a
    competência for 'Mês Anterior'."""
    if not data_br:
        return None
    try:
        dt = datetime.strptime(data_br, "%d/%m/%Y")
    except ValueError:
        return None
    ano, mes = dt.year, dt.month
    if competencia == "Mês Anterior":
        mes -= 1
        if mes == 0:
            mes = 12
            ano -= 1
    return (ano, mes)


def _capturado_para_linha(l, categoria_linha, base_sem_departamento, sufixo_alvo):
    """Quanto do valor do lançamento l é capturado pela linha categoria_linha
    -- considerando que a categoria do lançamento pode vir composta (ver
    dividir_categoria_composta) e que, se a linha pede um sufixo de
    departamento, a parte "base" sem sufixo é rateada pelo departamento.
    A comparação (categoria x categoria, departamento x departamento) é
    tolerante a maiúscula/minúscula, acento e espaço sobrando -- ver
    _chave_comparacao."""
    valor_total = l.get("valor", 0) or 0
    chave_categoria_linha = _chave_comparacao(categoria_linha)
    chave_base = _chave_comparacao(base_sem_departamento) if base_sem_departamento else None
    capturado = 0.0
    for cat_norm, peso in dividir_categoria_composta(l.get("categoria", "")):
        valor_parte = valor_total * peso
        chave_cat = _chave_comparacao(cat_norm)
        if chave_cat == chave_categoria_linha:
            capturado += valor_parte
        elif sufixo_alvo and chave_cat == chave_base:
            rateio = l.get("departamentosRateio") or [{"nome": l.get("departamento", "-"), "percentual": 100}]
            for d in rateio:
                if _sufixo_departamento(d.get("nome")) == sufixo_alvo:
                    capturado += valor_parte * (d.get("percentual", 0) or 0) / 100
    return capturado


def _sufixo_departamento(nome_departamento):
    """Acha o sufixo (MOD/MOI/ADM/Comercial) correspondente a um nome de
    departamento da Omie, tolerante a maiúscula/minúscula/acento/espaço."""
    chave = _chave_comparacao(nome_departamento)
    for nome_omie, sufixo in DEPARTAMENTO_OMIE_PARA_SUFIXO.items():
        if _chave_comparacao(nome_omie) == chave:
            return sufixo
    return None


def _base_e_sufixo(categoria_linha):
    if " - " in categoria_linha:
        possivel_base, possivel_sufixo = categoria_linha.rsplit(" - ", 1)
        if possivel_sufixo in SUFIXOS_DEPARTAMENTO:
            return possivel_base, possivel_sufixo
    return None, None


def _sugestao_mais_parecida(valor_bruto, candidatos):
    """Entre os candidatos conhecidos (nomes de categoria ou de
    departamento), acha o mais parecido com valor_bruto e devolve
    (candidato, % parecido) só se for uma pista razoável (>=70%) -- não é
    correção automática, é só a dica que aparece no alerta pra facilitar a
    correção manual no Omie."""
    if not valor_bruto:
        return None
    melhor, melhor_score = None, 0.0
    chave_bruta = _chave_comparacao(valor_bruto)
    for candidato in candidatos:
        score = difflib.SequenceMatcher(None, chave_bruta, _chave_comparacao(candidato)).ratio()
        if score > melhor_score:
            melhor, melhor_score = candidato, score
    if melhor and melhor_score >= 0.70:
        return {"sugestao": melhor, "parecidoPct": round(melhor_score * 100, 1)}
    return None


def _capturado_categoria_simples(l, categoria_alvo):
    """Quanto do valor do lançamento l pertence à categoria_alvo (sem rateio
    por departamento -- usado pelas linhas híbridas de receita/custo de
    Kits, que são um total único da empresa, não divididas por
    departamento). Lida com categoria composta (dividir_categoria_composta)
    e comparação tolerante a maiúscula/acento/espaço."""
    valor_total = l.get("valor", 0) or 0
    chave_alvo = _chave_comparacao(categoria_alvo)
    return sum(
        valor_total * peso
        for cat_norm, peso in dividir_categoria_composta(l.get("categoria", ""))
        if _chave_comparacao(cat_norm) == chave_alvo
    )


def calcular_nao_classificados(linhas_config, todos_lancamentos, meses_dinamicos):
    """'Prova dos 9': para cada lançamento (fora transferência interna, e com
    data real dentro dos meses dinâmicos -- Jan-Jun/2026 é estático, copiado
    da planilha, fora do escopo dessa conferência automática), verifica se o
    valor foi 100% capturado por alguma linha omie_categoria de linhas_config
    -- batendo direto pela categoria, pela categoria "base" ratada por
    departamento (ver DEPARTAMENTO_OMIE_PARA_SUFIXO), pelas linhas híbridas
    de receita/custo de Kits (só a partir do mês em que cada uma vira
    automática), e também lidando com categorias compostas (ver
    dividir_categoria_composta). O que sobrar (positivo = dinheiro que não
    caiu em nenhuma linha; negativo = caiu em mais de uma linha por engano)
    é devolvido pra virar o alerta na tela, junto com uma sugestão de
    categoria/departamento parecido (só um palpite pra facilitar a correção
    manual -- ver _sugestao_mais_parecida). Não depende de competência/mês
    de cada linha omie_categoria -- é uma checagem de COBERTURA de
    categoria, não de valor por mês (que seria distorcido pelas linhas com
    competência "Mês Anterior")."""
    meses_validos = set(meses_dinamicos)
    linhas_parsed = [
        (item["classif"]["categoria"], *_base_e_sufixo(item["classif"]["categoria"]))
        for item in linhas_config if item["classif"]["tipo"] == "omie_categoria"
    ]

    # linhas híbridas: capturam a categoria inteira (sem rateio por
    # departamento) só a partir do mês em que passam a ser automáticas.
    regras_hibridas = []
    for item in linhas_config:
        if item["classif"]["tipo"] == "receita_kit_hibrida":
            corte = item["classif"]["automatico_a_partir_de"]
            regras_hibridas.extend((cat, corte) for cat in item["classif"]["categorias"])
        elif item["classif"]["tipo"] == "especial_kits_cogs" and item["classif"].get("custo_real_a_partir_de"):
            regras_hibridas.append(("Despesas com Kits", item["classif"]["custo_real_a_partir_de"]))

    categorias_conhecidas = sorted({categoria_linha for categoria_linha, _, _ in linhas_parsed}
                                    | {base for _, base, _ in linhas_parsed if base}
                                    | {cat for cat, _ in regras_hibridas})
    departamentos_conhecidos = list(DEPARTAMENTO_OMIE_PARA_SUFIXO.keys())

    resultado = []
    for l in todos_lancamentos:
        if l.get("transferenciaInterna"):
            continue
        eff_real = mes_efetivo(l.get("data", ""), "Mesmo Mês")
        if eff_real not in meses_validos:
            continue
        valor = l.get("valor", 0) or 0
        capturado = sum(
            _capturado_para_linha(l, categoria_linha, base, sufixo)
            for categoria_linha, base, sufixo in linhas_parsed
        )
        for categoria_alvo, corte in regras_hibridas:
            if eff_real >= corte:
                capturado += _capturado_categoria_simples(l, categoria_alvo)
        residual = valor - capturado
        if abs(residual) > 0.01:
            cat_bruta = l.get("categoria", "") or ""
            chave_cat_bruta = _chave_comparacao(cat_bruta)
            categoria_bate = any(_chave_comparacao(c) == chave_cat_bruta for c in categorias_conhecidas)
            if categoria_bate:
                # a categoria em si já é reconhecida (ex: "FGTS", que precisa
                # de departamento) -- então o que falta é o departamento
                # bater com um dos conhecidos (Comercial/ADM/MOD/MOI).
                sugestao = _sugestao_mais_parecida(l.get("departamento", ""), departamentos_conhecidos)
            else:
                sugestao = _sugestao_mais_parecida(cat_bruta, categorias_conhecidas)
            item = {
                "data": l.get("data", ""),
                "empresa": l.get("empresa", "-"),
                "categoria": l.get("categoria", "-") or "-",
                "departamento": l.get("departamento", "-"),
                "cliente": l.get("cliente", "-"),
                "valor_residual": residual,
            }
            if sugestao:
                item["sugestao"] = sugestao["sugestao"]
                item["sugestao_parecido_pct"] = sugestao["parecidoPct"]
            resultado.append(item)
    resultado.sort(key=lambda x: abs(x["valor_residual"]), reverse=True)
    return resultado


def somar_omie_categoria(todos_lancamentos, categoria_linha, competencia, ano_alvo, mes_alvo):
    # Se a linha pede uma categoria com sufixo de departamento (ex: "FGTS -
    # MOI"), lançamentos que chegam da Omie já com esse sufixo na categoria
    # continuam batendo direto. Além disso, lançamentos da categoria "base"
    # sem sufixo (ex: "FGTS") entram aqui também, rateados pelo percentual
    # de cada departamento -- ver DEPARTAMENTO_OMIE_PARA_SUFIXO. E, se a
    # categoria do lançamento vier composta (rateada entre 2+ categorias),
    # cada parte é tratada separadamente -- ver dividir_categoria_composta.
    base_sem_departamento, sufixo_alvo = _base_e_sufixo(categoria_linha)

    total = 0.0
    for l in todos_lancamentos:
        if l.get("transferenciaInterna"):
            continue
        eff = mes_efetivo(l.get("data", ""), competencia)
        if eff != (ano_alvo, mes_alvo):
            continue
        total += _capturado_para_linha(l, categoria_linha, base_sem_departamento, sufixo_alvo)
    return total


def calcular_coluna(linhas_config, todos_lancamentos, ano, mes, manual_faturamento, manual_folha, manual_weg):
    """Calcula o valor de cada linha (por numero de linha) para um mes-alvo."""
    chave_mes = f"{ano:04d}-{mes:02d}"
    valor_por_linha = {}

    # 1a passada: linhas "folha" (nao dependem de outras linhas)
    for item in linhas_config:
        tipo = item["classif"]["tipo"]
        linha = item["linha"]
        if tipo == "omie_categoria":
            valor_por_linha[linha] = somar_omie_categoria(
                todos_lancamentos, item["classif"]["categoria"],
                item["classif"]["competencia"], ano, mes,
            )
        elif tipo == "valor_fixo":
            valor_por_linha[linha] = item["classif"]["valor"]
        elif tipo == "juros_emprestimos":
            valor_por_linha[linha] = -JUROS_EMPRESTIMOS.get(chave_mes, 0)
        elif tipo == "manual_faturamento_kit":
            valor_por_linha[linha] = manual_faturamento.get(chave_mes, {}).get(linha, None)
        elif tipo == "receita_kit_hibrida":
            # Até o mes de corte (inclusive), o valor continua vindo da
            # planilha/fonte manual (MANUAL_FATURAMENTO_KIT) -- dali em
            # diante, soma automaticamente as categorias da Omie listadas.
            corte_ano, corte_mes = item["classif"]["automatico_a_partir_de"]
            if (ano, mes) < (corte_ano, corte_mes):
                valor_por_linha[linha] = manual_faturamento.get(chave_mes, {}).get(linha, None)
            else:
                valor_por_linha[linha] = sum(
                    somar_omie_categoria(todos_lancamentos, categoria, item["classif"]["competencia"], ano, mes)
                    for categoria in item["classif"]["categorias"]
                )
        elif tipo == "manual_folha":
            valor_por_linha[linha] = manual_folha.get(chave_mes, {}).get(linha, None)
        elif tipo == "manual_weg":
            valor_por_linha[linha] = manual_weg.get(chave_mes, {}).get(linha, None)
        elif tipo in ("vazio", "saldo_inicial_jan"):
            valor_por_linha[linha] = 0

    # linha especial: custo de Kits. Até o mes de corte, mantem a formula
    # antiga (-50% * (receita kit + descontos + descontos cartao)); a partir
    # dali, usa o custo REAL de "Despesas com Kits" somado direto da Omie.
    for item in linhas_config:
        if item["classif"]["tipo"] == "especial_kits_cogs":
            corte_ano, corte_mes = item["classif"].get("custo_real_a_partir_de", (9999, 1))
            if (ano, mes) >= (corte_ano, corte_mes):
                valor_por_linha[item["linha"]] = somar_omie_categoria(
                    todos_lancamentos, "Despesas com Kits", "Não se Aplica", ano, mes,
                )
            else:
                r7 = valor_por_linha.get(7) or 0
                r21 = valor_por_linha.get(21) or 0
                r22 = valor_por_linha.get(22) or 0
                valor_por_linha[item["linha"]] = -0.5 * (r7 + r21 + r22)

    # passadas seguintes: resolve "combinacao" iterativamente (podem depender
    # de outras combinacoes, entao repete ate estabilizar). Termos ausentes
    # (linha vazia na planilha original, ou dado manual ainda nao informado)
    # contam como 0 na soma -- igual o Excel trata celula em branco dentro
    # de um SUM(). A linha individual em si continua aparecendo como None
    # na tabela final, para sinalizar visualmente o que falta preencher.
    pendentes = [item for item in linhas_config if item["classif"]["tipo"] == "combinacao"]
    for _ in range(12):
        ainda_pendente = []
        for item in pendentes:
            termos = item["classif"]["termos"]
            refs_de_combinacao = {x["linha"] for x in linhas_config if x["classif"]["tipo"] == "combinacao"}
            if any(t["linha"] in refs_de_combinacao and t["linha"] not in valor_por_linha for t in termos):
                ainda_pendente.append(item)
                continue
            valor_por_linha[item["linha"]] = sum(
                t["sinal"] * (valor_por_linha.get(t["linha"]) or 0) for t in termos
            )
        if not ainda_pendente:
            break
        pendentes = ainda_pendente

    return valor_por_linha


def calcular_meses_dinamicos(todos_lancamentos, ano_final, mes_final):
    """Retorna lista de (ano, mes) de Julho/2026 até ano_final/mes_final."""
    meses = []
    ano, mes = 2026, 7
    while (ano, mes) <= (ano_final, mes_final):
        meses.append((ano, mes))
        mes += 1
        if mes == 13:
            mes = 1
            ano += 1
    return meses


def calcular_abrangencias(linhas_config):
    """Para cada linha 'combinacao', calcula (inicio, fim): a faixa de linhas
    que ela abrange no total, incluindo sub-seções aninhadas (ex: '(-)
    Despesas Operacionais' referencia outras 5 sub-seções distantes -- a
    abrangência dela vai do menor até o fim da última sub-seção, cobrindo
    tudo). Isso permite recolher em cascata em qualquer nível, não só o
    nível imediatamente abaixo."""
    item_por_linha = {item["linha"]: item for item in linhas_config}
    cache = {}

    def abrangencia(linha):
        if linha in cache:
            return cache[linha]
        item = item_por_linha.get(linha)
        if not item or item["classif"]["tipo"] != "combinacao":
            cache[linha] = (linha, linha)
            return cache[linha]
        termos = item["classif"]["termos"]
        refs = [t["linha"] for t in termos]
        if not refs:
            cache[linha] = (linha, linha)
            return cache[linha]
        cache[linha] = (linha, linha)  # evita recursão infinita em caso de ciclo
        ini = min(refs)
        fim = max(abrangencia(r)[1] for r in refs)
        cache[linha] = (ini, fim)
        return cache[linha]

    return {item["linha"]: abrangencia(item["linha"]) for item in linhas_config
            if item["classif"]["tipo"] == "combinacao"}


def montar_linhas_tabela(linhas_config, todos_lancamentos, meses_dinamicos,
                          manual_faturamento, manual_folha, manual_weg):
    """Monta, para cada linha do DRE/DFC, a lista de valores Jan-Jun (estatico)
    seguida dos meses dinamicos calculados. Retorna lista de dicts prontos
    para renderizar: {linha, label, tipo_visual, valores: [..], secoes_pai,
    eh_secao}."""
    estatico_por_mes = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun']

    colunas_dinamicas = {}
    for (ano, mes) in meses_dinamicos:
        colunas_dinamicas[(ano, mes)] = calcular_coluna(
            linhas_config, todos_lancamentos, ano, mes,
            manual_faturamento, manual_folha, manual_weg,
        )

    # "Posição de Caixa Acumulada" (linha 195, só existe no DFC) depende do
    # saldo acumulado do MÊS ANTERIOR + resultado do mês atual -- não dá pra
    # calcular mês a mês isoladamente como as outras linhas. Corrige aqui em
    # cascata, carregando o acumulado de um mês pro próximo.
    labels_por_linha = {item["linha"]: item["label"] for item in linhas_config}
    if 195 in labels_por_linha and 193 in labels_por_linha:
        item_195 = next(x for x in linhas_config if x["linha"] == 195)
        acumulado = item_195["valores"].get("Jun", 0) or 0
        for (ano, mes) in meses_dinamicos:
            resultado_do_mes = colunas_dinamicas[(ano, mes)].get(193) or 0
            acumulado += resultado_do_mes
            colunas_dinamicas[(ano, mes)][195] = acumulado

    abrangencias = calcular_abrangencias(linhas_config)  # secao_linha -> (ini, fim)

    linhas_prontas = []
    for item in linhas_config:
        label = item["label"]
        tipo = item["classif"]["tipo"]
        eh_subtotal = label.strip().startswith(('(-)', '(+)', '(=)', '(+/-)'))
        valores = [item["valores"].get(m, 0) for m in estatico_por_mes]
        for (ano, mes) in meses_dinamicos:
            valores.append(colunas_dinamicas[(ano, mes)].get(item["linha"]))

        # todas as seções (de qualquer nível) cuja abrangência contém esta
        # linha -- ao recolher qualquer uma delas, esta linha deve sumir
        secoes_pai = [
            secao for secao, (ini, fim) in abrangencias.items()
            if secao != item["linha"] and ini <= item["linha"] <= fim
        ]
        linhas_prontas.append({
            "linha": item["linha"],
            "label": label,
            "subtotal": eh_subtotal,
            "vazio": tipo == "vazio",
            "pendente_manual": tipo in ("manual_faturamento_kit", "manual_folha", "manual_weg"),
            "valores": valores,
            "secoes_pai": secoes_pai,
            "eh_secao": item["linha"] in abrangencias,
        })
    return linhas_prontas
