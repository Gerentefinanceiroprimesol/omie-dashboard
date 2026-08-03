# -*- coding: utf-8 -*-
"""Engine de calculo do DRE/DFC a partir de julho/2026 em diante, usando os
lançamentos ja coletados da Omie (mesma lista usada no resto do dashboard)
combinada com a classificacao de dre_dfc_dados.py."""

from datetime import datetime
from dre_dfc_dados import DE_PARA, DRE_LINHAS, DFC_LINHAS, JUROS_EMPRESTIMOS, \
    MANUAL_FATURAMENTO_KIT, MANUAL_FOLHA, MANUAL_WEG

MESES_ABREV = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']


def normalizar_categoria(categoria_bruta: str) -> str:
    return DE_PARA.get((categoria_bruta or "").strip(), (categoria_bruta or "").strip())


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


def somar_omie_categoria(todos_lancamentos, categoria_linha, competencia, ano_alvo, mes_alvo):
    total = 0.0
    for l in todos_lancamentos:
        if l.get("transferenciaInterna"):
            continue
        cat_norm = normalizar_categoria(l.get("categoria", ""))
        if cat_norm != categoria_linha:
            continue
        eff = mes_efetivo(l.get("data", ""), competencia)
        if eff == (ano_alvo, mes_alvo):
            total += l.get("valor", 0) or 0
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
        elif tipo == "manual_folha":
            valor_por_linha[linha] = manual_folha.get(chave_mes, {}).get(linha, None)
        elif tipo == "manual_weg":
            valor_por_linha[linha] = manual_weg.get(chave_mes, {}).get(linha, None)
        elif tipo in ("vazio", "saldo_inicial_jan"):
            valor_por_linha[linha] = 0

    # linha especial: custo de Kits = -50% * (receita kit + descontos + descontos cartao)
    for item in linhas_config:
        if item["classif"]["tipo"] == "especial_kits_cogs":
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

    # "Posição de Caixa Acumulada" (linha 190, só existe no DFC) depende do
    # saldo acumulado do MÊS ANTERIOR + resultado do mês atual -- não dá pra
    # calcular mês a mês isoladamente como as outras linhas. Corrige aqui em
    # cascata, carregando o acumulado de um mês pro próximo.
    labels_por_linha = {item["linha"]: item["label"] for item in linhas_config}
    if 190 in labels_por_linha and 188 in labels_por_linha:
        item_190 = next(x for x in linhas_config if x["linha"] == 190)
        acumulado = item_190["valores"].get("Jun", 0) or 0
        for (ano, mes) in meses_dinamicos:
            resultado_do_mes = colunas_dinamicas[(ano, mes)].get(188) or 0
            acumulado += resultado_do_mes
            colunas_dinamicas[(ano, mes)][190] = acumulado

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
