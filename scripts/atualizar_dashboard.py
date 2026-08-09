#!/usr/bin/env python3
"""
Atualiza um dashboard HTML com dados Financeiros (extrato de contas
bancárias + cartões de crédito) puxados direto da API da Omie.

A página gerada tem filtros INTERATIVOS (em JavaScript, no navegador):
- Painel superior: quais contas/cartões exibir, período, conciliação,
  cliente/fornecedor, valor, situação
- Filtro estilo Excel em cada coluna das tabelas (clique na seta do
  cabeçalho para escolher valores específicos)

Para isso, o script busca TODOS os lançamentos brutos de 01/01/2026 até
a data de hoje (a cada execução, mês a mês para evitar o limite de 100
registros por chamada da Omie), e embute esses dados na própria página.
Os filtros só reorganizam o que já foi baixado — não fazem nova chamada
à Omie a cada clique.
"""

import os
import json as jsonlib
import html as htmllib
import time
from datetime import datetime, timezone, timedelta
import requests

from dre_dfc_dados import DRE_LINHAS, DFC_LINHAS, MANUAL_FATURAMENTO_KIT, MANUAL_FOLHA, MANUAL_WEG, DE_PARA, \
    MANUAL_FATURAMENTO_POR_EMPRESA
from engine_dre_dfc import calcular_meses_dinamicos, montar_linhas_tabela, calcular_nao_classificados
# =============================================================
# Bloco abaixo: lógica da aba Insights (Custo Fixo/Variável, resumo
# da DRE, e o HTML/JS da aba). Fica isolado aqui dentro só visualmente
# -- é código igual ao que estaria num arquivo insights_helpers.py
# separado, só que colado direto neste arquivo por preferência do
# usuário, para manter tudo num único script.
# =============================================================

# ---------------------------------------------------------------------------
# CONFIGURAÇÕES EDITÁVEIS
# Ajuste esses valores livremente conforme a necessidade do time financeiro,
# sem precisar mexer em mais nada do script.
# ---------------------------------------------------------------------------

# Saldo abaixo deste valor, em qualquer conta bancária, dispara o alerta de
# "Contas com saldo baixo" na aba Insights.
LIMITE_SALDO_BAIXO = 10000

# Quantos meses completos anteriores usar para calcular a média de queima de
# caixa (usada no cálculo do Runway).
MESES_RUNWAY = 3

# Janela (em dias, a partir de hoje) considerada "em aberto" para os cards de
# A Receber / A Pagar na Saúde de Caixa.
PROXIMOS_DIAS_A_RECEBER_PAGAR = 90


def carregar_classificacao_custos(caminho_base: str) -> dict:
    """Lê classificacao_custos.json (esperado na raiz do repositório, junto
    com atualizar_dashboard.py) e retorna um dicionário com as 4 listas:
    fixo, variavel, ignorar, nao_classificado.

    Se o arquivo não existir ou tiver algum erro, devolve listas vazias --
    nesse caso toda categoria de despesa cai em "não classificada" no
    dashboard, o que é seguro (só gera um alerta visual, não quebra nada)."""
    caminho = os.path.join(caminho_base, "classificacao_custos.json")
    padrao = {"fixo": [], "variavel": [], "ignorar": [], "nao_classificado": []}
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = jsonlib.load(f)
        for chave in padrao:
            padrao[chave] = dados.get(chave, [])
    except FileNotFoundError:
        print(
            "AVISO: classificacao_custos.json não encontrado -- Custo Fixo/Variável "
            "ficará vazio até o arquivo ser adicionado na raiz do repositório."
        )
    except Exception as erro:
        print(f"AVISO: erro ao ler classificacao_custos.json: {erro}")
    return padrao


def extrair_linha_dre(linhas: list, label_alvo: str) -> list:
    """Busca, dentro da lista de linhas já calculadas pela engine de DRE
    (montar_linhas_tabela), a linha cujo label bate exatamente com
    label_alvo, e devolve sua lista de valores mensais. Se não encontrar,
    devolve uma lista de None do mesmo tamanho -- os cards tratam None como
    "sem dado" em vez de quebrar."""
    for linha in linhas:
        if linha.get("label") == label_alvo:
            return linha.get("valores", [])
    tamanho = len(linhas[0]["valores"]) if linhas else 12
    return [None] * tamanho


def montar_nomes_meses(meses_dinamicos: list) -> list:
    """Monta a lista de rótulos de mês (Jan-Jun estáticos + os meses
    dinâmicos calculados), sempre só com a abreviação de 3 letras, sem ano
    -- padronizado em 06/08/2026 pra não ter mais "Jan", "Fev"... (sem ano,
    meses estáticos) misturado com "Jul/2026", "Ago/2026"... (com ano,
    meses dinâmicos) na mesma tabela. Usada tanto pela tabela DRE/DFC
    quanto pela Insights, pra não ter esse formato duplicado (e poder ficar
    dessincronizado de novo) em dois lugares do código.

    Atenção: sem o ano, "Jan" de 2026 e um eventual "Jan" de 2027 (se o
    dashboard continuar rodando até lá) ficariam com o mesmo rótulo na
    tabela. Combinado com o Fabrício que essa ambiguidade é aceitável por
    ora -- o Fabrício prefere a tabela mais limpa sem o ano."""
    nomes_base = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']
    return [nomes_base[i] for i in range(6)] + [
        nomes_base[m - 1] for (a, m) in meses_dinamicos
    ]


def montar_resumo_dre(linhas_dre: list, nomes_meses: list, meses_dinamicos: list, manual_faturamento: dict) -> dict:
    """Monta o resumo mensal de Receita Bruta, Lucro Bruto, EBITDA, Lucro
    Líquido e Comissões, extraído das linhas já calculadas da DRE -- os
    mesmos números que já aparecem na aba DRE, só isolados para os cards
    da Insights. Também monta o número de vendas do mês (quando informado
    manualmente), usado pro card de Ticket Médio.

    Os labels abaixo precisam bater exatamente com os usados em
    dre_dfc_dados.py. Se algum desses nomes mudar lá, ajuste a string
    correspondente aqui também."""
    numero_vendas = []
    for mes in range(1, 7):  # Jan..Jun, sempre 2026 (estático)
        chave = f"2026-{mes:02d}"
        numero_vendas.append(manual_faturamento.get(chave, {}).get("numero_vendas"))
    for (ano, mes) in meses_dinamicos:
        chave = f"{ano:04d}-{mes:02d}"
        numero_vendas.append(manual_faturamento.get(chave, {}).get("numero_vendas"))

    return {
        "nomesMeses": nomes_meses,
        "receitaBruta": extrair_linha_dre(linhas_dre, "Receita Operacional Bruta"),
        "lucroBruto": extrair_linha_dre(linhas_dre, "(=) Lucro Bruto"),
        "ebitda": extrair_linha_dre(linhas_dre, "(=) Resultado Operacional (EBITDA)"),
        "lucroLiquido": extrair_linha_dre(linhas_dre, "(=) Lucro / Prejuízo Líquido do Exercício"),
        "comissoesInternas": extrair_linha_dre(linhas_dre, "Comissões Internas"),
        "comissoesExternas": extrair_linha_dre(linhas_dre, "Comissões Externas"),
        "numeroVendas": numero_vendas,
    }


def montar_html_insights(dre_resumo_json: str, classificacao_custos_json: str, insights_config_json: str,
                          faturamento_por_empresa_json: str) -> str:
    """Monta o HTML+JS completo da aba Insights, injetando os blocos de
    dados (JSON) nos pontos marcados por placeholders. Usa .replace() (não
    f-string/.format()) de propósito, para não precisar escapar as chaves
    { } do JavaScript abaixo."""
    html = INSIGHTS_HTML_TEMPLATE
    html = html.replace("__DRE_RESUMO_JSON__", dre_resumo_json)
    html = html.replace("__CLASSIFICACAO_JSON__", classificacao_custos_json)
    html = html.replace("__INSIGHTS_CONFIG_JSON__", insights_config_json)
    html = html.replace("__FATURAMENTO_POR_EMPRESA_JSON__", faturamento_por_empresa_json)
    return html


INSIGHTS_HTML_TEMPLATE = r"""
<div id="abaInsights" style="display:none">
<style>
  #abaInsights .ins-wrap { padding: 20px 32px 40px; }
  #abaInsights h2 { margin-top: 32px; }
  #abaInsights h2:first-child { margin-top: 8px; }

  #abaInsights .ins-alerta-nc {
    background: rgba(250,168,33,0.12); border: 1px solid var(--laranja);
    border-radius: var(--radius-sm); padding: 10px 14px; font-size: 12.5px;
    color: var(--laranja); margin-bottom: 18px;
  }
  #abaInsights .ins-alerta-nc code { color: var(--cyan); }
  #abaInsights .ins-btn-alerta {
    background: rgba(250,168,33,0.18); border: 1px solid var(--laranja); color: var(--laranja);
    font-size: 10.5px; font-weight: 700; padding: 4px 10px; border-radius: 5px; cursor: pointer;
  }
  #abaInsights .ins-btn-alerta:hover { background: rgba(250,168,33,0.32); }

  #abaInsights .ins-hero {
    background: linear-gradient(135deg, rgba(250,168,33,.12), rgba(54,91,221,.12));
    border: 1px solid var(--laranja); border-radius: var(--radius);
    padding: 20px 24px; margin-bottom: 8px; cursor: pointer; transition: border-color 0.15s;
  }
  #abaInsights .ins-hero:hover { border-color: #ffbf4d; }
  #abaInsights .ins-hero-top {
    display: flex; justify-content: space-between; align-items: center;
    flex-wrap: wrap; gap: 16px;
  }
  #abaInsights .ins-hero-label { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; }
  #abaInsights .ins-hero-value { font-size: 32px; font-weight: 800; color: var(--laranja); line-height: 1.1; }
  #abaInsights .ins-hero-sub { font-size: 11.5px; color: var(--text-faint); margin-top: 6px; display: flex; align-items: center; gap: 6px; }
  #abaInsights .ins-hero-stats { display: flex; gap: 26px; flex-wrap: wrap; }
  #abaInsights .ins-hero-stat .l { font-size: 11px; color: var(--text-muted); }
  #abaInsights .ins-hero-stat .v { font-size: 16px; font-weight: 700; }

  #abaInsights .ins-grid { display: grid; gap: 14px; grid-template-columns: repeat(4, 1fr); }
  #abaInsights .ins-grid-3 { grid-template-columns: repeat(3, 1fr); }
  #abaInsights .ins-grid-2 { grid-template-columns: 2fr 1fr; }
  #abaInsights .ins-grid-5 { grid-template-columns: repeat(5, 1fr); }

  #abaInsights .ins-card {
    background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 12px 14px;
  }
  #abaInsights .ins-kpi-label { font-size: 10.5px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .03em; margin-bottom: 6px; }
  #abaInsights .ins-kpi-value { font-size: 21px; font-weight: 800; }
  #abaInsights .ins-delta { font-size: 11.5px; margin-top: 5px; font-weight: 600; }
  #abaInsights .ins-up { color: var(--green); }
  #abaInsights .ins-down { color: var(--red); }
  #abaInsights .ins-neutro { color: var(--text-faint); }

  #abaInsights .ins-card-expandivel { cursor: pointer; transition: border-color 0.15s; }
  #abaInsights .ins-card-expandivel:hover { border-color: var(--laranja); }
  #abaInsights .ins-expandir-seta { display: inline-block; font-size: 9px; transition: transform 0.2s; color: var(--text-faint); }
  #abaInsights .ins-card-expandivel.aberto .ins-expandir-seta { transform: rotate(180deg); }
  #abaInsights .ins-expandivel-corpo { display: none; margin-top: 10px; cursor: default; max-height: 220px; overflow-y: auto; }
  #abaInsights .ins-card-expandivel.aberto .ins-expandivel-corpo { display: block; }

  #abaInsights .ins-clickable { cursor: pointer; position: relative; transition: border-color .15s, background .15s; }
  #abaInsights .ins-clickable:hover { border-color: var(--laranja); background: #1c2841; }
  #abaInsights .ins-clickable::after {
    /* Sempre visível (antes só aparecia no hover) -- em touch/mobile não
       existe hover, então a dica de "dá pra clicar" nunca aparecia. */
    content: 'clique para detalhar ▾'; position: absolute; top: 12px; right: 14px;
    font-size: 9.5px; color: var(--text-faint); opacity: 1;
  }

  #abaInsights .ins-detail-panel { max-height: 0; overflow: hidden; transition: max-height .25s ease; }
  #abaInsights .ins-detail-panel.aberto { max-height: 420px; overflow-y: auto; margin-top: 12px; }
  #abaInsights .ins-detail-table { width: 100%; border-collapse: collapse; font-size: 12px; }
  #abaInsights .ins-detail-table th {
    text-align: left; color: var(--text-faint); font-weight: 600; padding: 5px 6px;
    border-bottom: 1px solid var(--border); font-size: 9.5px; text-transform: uppercase;
  }
  #abaInsights .ins-detail-table td { padding: 6px 6px; border-bottom: 1px solid var(--border); }
  #abaInsights .ins-detail-table tr:last-child td { border-bottom: none; }

  #abaInsights .ins-mini-table { width: 100%; border-collapse: collapse; font-size: 11.5px; margin-top: 10px; }
  #abaInsights .ins-mini-table th {
    text-align: right; color: var(--text-faint); font-weight: 600; padding: 4px 5px;
    border-bottom: 1px solid var(--border); font-size: 9.5px; text-transform: uppercase;
  }
  #abaInsights .ins-mini-table th:first-child, #abaInsights .ins-mini-table td:first-child { text-align: left; }
  #abaInsights .ins-mini-table td { text-align: right; padding: 5px; border-bottom: 1px solid var(--border); }
  #abaInsights .ins-mini-table tr:last-child td { border-bottom: none; font-weight: 700; }

  #abaInsights .ins-rentab-card { margin-top: 14px; }
  #abaInsights .ins-rentab-table { font-size: 12.5px; }
  #abaInsights .ins-rentab-table th { padding: 7px 10px; }
  #abaInsights .ins-rentab-table td { padding: 7px 10px; }
  #abaInsights .ins-rentab-table tbody tr:hover td { background: rgba(250,168,33,0.05); }

  #abaInsights .ins-card-largo { margin-top: 14px; }
  #abaInsights .ins-tabela-scroll { overflow-x: auto; }
  #abaInsights .ins-tabela-empresas { font-size: 12.5px; min-width: 640px; }
  #abaInsights .ins-tabela-empresas th { padding: 7px 10px; white-space: nowrap; }
  #abaInsights .ins-tabela-empresas td { padding: 7px 10px; }
  #abaInsights .ins-tabela-empresas tbody tr:hover td { background: rgba(250,168,33,0.05); }
  #abaInsights .ins-tabela-empresas td:last-child, #abaInsights .ins-tabela-empresas th:last-child { border-left: 1px solid var(--border); }
  #abaInsights .ins-nota-rodape { font-size: 10.5px; color: var(--text-faint); margin-top: 8px; }

  #abaInsights .ins-status-ok { color: var(--green); font-weight: 700; font-size: 10.5px; }
  #abaInsights .ins-status-bad { color: var(--red); font-weight: 700; font-size: 10.5px; }

  #abaInsights .ins-company-row { display: flex; align-items: center; gap: 10px; margin-bottom: 9px; font-size: 12.5px; }
  #abaInsights .ins-company-row:last-child { margin-bottom: 0; }
  #abaInsights .ins-company-name { width: 150px; flex-shrink: 0; color: var(--text-muted); }
  #abaInsights .ins-bar-track { flex: 1; background: var(--bg); border-radius: 5px; height: 18px; overflow: hidden; }
  #abaInsights .ins-bar-fill { height: 100%; display: flex; align-items: center; padding-left: 8px; font-size: 10.5px; font-weight: 700; color: #0e1015; white-space: nowrap; }
  #abaInsights .ins-company-result { width: 110px; text-align: right; font-weight: 700; flex-shrink: 0; font-size: 12px; }

  #abaInsights .ins-trend-chart { display: flex; align-items: flex-end; gap: 10px; height: 140px; padding-top: 10px; }
  #abaInsights .ins-trend-col { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; gap: 4px; }
  #abaInsights .ins-trend-bars { display: flex; gap: 3px; align-items: flex-end; height: 108px; }

  #abaInsights .ins-linechart-svg { width: 100%; height: 220px; display: block; }
  #abaInsights .ins-linechart-area { fill: url(#insFaturamentoGradiente); }
  #abaInsights .ins-linechart-linha { fill: none; stroke: var(--laranja); stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
  #abaInsights .ins-linechart-ponto { fill: var(--bg-panel); stroke: var(--laranja); stroke-width: 2; }
  #abaInsights .ins-linechart-ponto-atual { fill: var(--laranja); stroke: var(--bg-panel); stroke-width: 2; }
  #abaInsights .ins-linechart-grade { stroke: var(--border); stroke-width: 1; stroke-dasharray: 2,3; }
  #abaInsights .ins-linechart-label { fill: var(--text-faint); font-size: 9.5px; }
  #abaInsights .ins-linechart-gridlabel { fill: var(--text-faint); font-size: 9px; }
  #abaInsights .ins-linechart-valor-atual { fill: var(--text); font-size: 11.5px; font-weight: 700; }
  #abaInsights .ins-bar { width: 10px; border-radius: 3px 3px 0 0; }
  #abaInsights .ins-bar-rev { background: var(--accent); }
  #abaInsights .ins-bar-exp { background: var(--laranja); }
  #abaInsights .ins-trend-month { font-size: 9.5px; color: var(--text-faint); margin-top: 4px; }
  #abaInsights .ins-legend { display: flex; gap: 16px; font-size: 10.5px; color: var(--text-muted); margin-bottom: 6px; }
  #abaInsights .ins-legend span { display: flex; align-items: center; gap: 5px; }
  #abaInsights .ins-dot { width: 8px; height: 8px; border-radius: 2px; }

  #abaInsights .ins-alert-list { display: flex; flex-direction: column; gap: 7px; }
  #abaInsights .ins-alert-item {
    display: flex; justify-content: space-between; align-items: center;
    background: #16223f; border-radius: 7px; padding: 8px 10px;
    border-left: 3px solid var(--border); font-size: 12px;
  }
  #abaInsights .ins-alert-item.ins-warn { border-left-color: var(--red); }
</style>

<div class="ins-wrap">
  <div id="insAlertaNaoClassificado"></div>
  <div id="insightsRoot"></div>
</div>

<script>
const DRE_RESUMO = __DRE_RESUMO_JSON__;
const CLASSIFICACAO_CUSTOS = __CLASSIFICACAO_JSON__;
const INSIGHTS_CONFIG = __INSIGHTS_CONFIG_JSON__;
const FATURAMENTO_POR_EMPRESA_KIT = __FATURAMENTO_POR_EMPRESA_JSON__;

function toggleDetalheInsight(id) {
  const el = document.getElementById(id);
  if (!el) return;
  document.querySelectorAll('.ins-detail-panel.aberto').forEach(function (p) {
    if (p.id !== id) p.classList.remove('aberto');
  });
  el.classList.toggle('aberto');
}

function insFmtPct(v, casas) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  casas = casas === undefined ? 1 : casas;
  const sinal = v > 0 ? '↑' : (v < 0 ? '↓' : '≈');
  return sinal + ' ' + Math.abs(v).toFixed(casas) + '%';
}

function insClasseVariacao(v) {
  if (v === null || v === undefined) return 'ins-neutro';
  return v > 0 ? 'ins-up' : (v < 0 ? 'ins-down' : 'ins-neutro');
}

// Abre/fecha a tabela mês a mês escondida dentro de um card (Ticket Médio,
// Comissão sobre a Receita). "cardEl" é o próprio card clicado -- alterna
// a classe "aberto" nele, que o CSS usa pra mostrar/esconder o
// ".ins-expandivel-corpo" e girar a setinha.
function insToggleExpandivel(cardEl) {
  cardEl.classList.toggle('aberto');
}

function insIndiceMesAtual() {
  const hoje = new Date();
  let idx;
  if (hoje.getFullYear() === 2026) {
    idx = hoje.getMonth(); // mês corrente real (0-indexado: Jan=0)
  } else {
    idx = DRE_RESUMO.nomesMeses.length - 1;
  }
  // Toda a aba Insights usa sempre o ÚLTIMO MÊS FECHADO como referência
  // (nunca o mês corrente, que ainda está em andamento e não tem dados
  // consolidados) -- por isso subtraímos 1 aqui, de propósito, num único
  // lugar que afeta automaticamente todos os cards da aba.
  idx = idx - 1;
  if (idx < 0) idx = 0; // segurança: evita índice negativo (ex: em janeiro, sem dezembro anterior na série)
  return idx;
}

function insAnoMesPorIndice(i) {
  return { ano: 2026, mes: i + 1 };
}

function insChaveMes(ano, mes) {
  return ano + '-' + String(mes).padStart(2, '0');
}

function insTodosLancamentos() {
  const todos = [];
  DADOS.forEach(function (conta) {
    conta.lancamentos.forEach(function (l) {
      if (l.transferenciaInterna) return;
      todos.push(Object.assign({}, l, { contaNome: conta.nome, contaTipo: conta.tipo, empresaNome: conta.empresa }));
    });
  });
  return todos;
}

function insMesDoLancamento(l) {
  const iso = paraDataISO(l.data);
  if (!iso) return null;
  const partes = iso.split('-').map(Number);
  return { ano: partes[0], mes: partes[1] };
}

function insAgruparPorMes(lancamentos) {
  const mapa = {};
  lancamentos.forEach(function (l) {
    const info = insMesDoLancamento(l);
    if (!info) return;
    const chave = insChaveMes(info.ano, info.mes);
    if (!mapa[chave]) mapa[chave] = [];
    mapa[chave].push(l);
  });
  return mapa;
}

function insListaMesesAteAtual() {
  const idxAtual = insIndiceMesAtual();
  const lista = [];
  for (let i = 0; i <= idxAtual; i++) {
    lista.push({ indice: i, nome: DRE_RESUMO.nomesMeses[i] });
  }
  return lista;
}

// A Omie retorna o nome da categoria com sufixos extras em alguns casos:
// - percentual de rateio, quando o lançamento é dividido entre categorias,
//   ex: "Despesas com KITs (72,483800%)"
// - "(inativa)", quando a categoria foi desativada no cadastro mas ainda
//   aparece em lançamentos antigos, ex: "Salários - MOD (inativa)"
// Sem remover esses sufixos, cada variação virava uma "categoria nova"
// diferente (e nunca batia com a lista de classificacao_custos.json).
// Essa função limpa isso e também ignora maiúsculas/minúsculas na
// comparação, pra não depender da grafia exata usada no Omie.
function insNormalizarCategoria(categoria) {
  if (!categoria) return '-';
  let c = String(categoria).trim();
  // Remove QUALQUER percentual de rateio na string, não só o do final --
  // quando a Omie rateia entre mais de uma categoria (ex: "Insumos para
  // Obras (33%), Despesas com KITs (0,5%)"), o percentual do meio também
  // precisa sair, senão cada combinação de percentuais virava uma
  // "categoria nova" diferente (esse era o motivo da lista de categorias
  // não classificadas vir cheia de quase-duplicatas).
  c = c.replace(/\s*\([\d.,]+\s*%\)/g, '');
  c = c.replace(/\s*\(inativ[ao]\)/gi, '');
  c = c.replace(/\s{2,}/g, ' ').replace(/\s*,\s*,/g, ',').replace(/^\s*,\s*|\s*,\s*$/g, '');
  return c.trim() || '-';
}

function insListaParaConjuntoNormalizado(lista) {
  const conjunto = new Set();
  (lista || []).forEach(function (item) {
    conjunto.add(insNormalizarCategoria(item).toLowerCase());
  });
  return conjunto;
}

const INS_CONJUNTO_FIXO = insListaParaConjuntoNormalizado(CLASSIFICACAO_CUSTOS.fixo);
const INS_CONJUNTO_VARIAVEL = insListaParaConjuntoNormalizado(CLASSIFICACAO_CUSTOS.variavel);
const INS_CONJUNTO_IGNORAR = insListaParaConjuntoNormalizado(CLASSIFICACAO_CUSTOS.ignorar);

function insClassificarCategoria(categoria) {
  const normalizada = insNormalizarCategoria(categoria).toLowerCase();
  if (INS_CONJUNTO_FIXO.has(normalizada)) return 'fixo';
  if (INS_CONJUNTO_VARIAVEL.has(normalizada)) return 'variavel';
  if (INS_CONJUNTO_IGNORAR.has(normalizada)) return 'ignorar';
  return 'nao_classificado';
}

function insCustoPorMes(lancamentosDoMes) {
  let fixo = 0, variavel = 0;
  const naoClassificadas = new Set();
  lancamentosDoMes.forEach(function (l) {
    if (l.valor >= 0) return;
    const categoriaLimpa = insNormalizarCategoria(l.categoria);
    // A Omie às vezes junta mais de uma categoria no mesmo lançamento,
    // separadas por "; " (ex: "Insumos para Obras; Material de Escritório"),
    // sem informar o percentual de cada uma. Nesses casos, rateamos o
    // valor igualmente entre as partes e classificamos cada parte pelo
    // seu próprio peso (em vez de tratar a string combinada como uma
    // categoria nova, que nunca bateria com classificacao_custos.json).
    const partes = categoriaLimpa.split(';').map(function (p) { return p.trim(); }).filter(Boolean);
    const listaPartes = partes.length > 0 ? partes : [categoriaLimpa];
    const valorAbsTotal = Math.abs(l.valor);
    const valorPorParte = valorAbsTotal / listaPartes.length;
    listaPartes.forEach(function (parte) {
      const classe = insClassificarCategoria(parte);
      if (classe === 'fixo') fixo += valorPorParte;
      else if (classe === 'variavel') variavel += valorPorParte;
      else if (classe === 'nao_classificado') naoClassificadas.add(parte);
    });
  });
  return { fixo: fixo, variavel: variavel, naoClassificadas: naoClassificadas };
}

function insCalcularSeriesCusto() {
  const todos = insTodosLancamentos();
  const mapaPorMes = insAgruparPorMes(todos);
  const meses = insListaMesesAteAtual();
  const naoClassificadasGlobal = new Set();

  const serie = meses.map(function (m) {
    const am = insAnoMesPorIndice(m.indice);
    const chave = insChaveMes(am.ano, am.mes);
    const doMes = mapaPorMes[chave] || [];
    const r = insCustoPorMes(doMes);
    r.naoClassificadas.forEach(function (c) { naoClassificadasGlobal.add(c); });
    return { nome: m.nome, indice: m.indice, fixo: r.fixo, variavel: r.variavel };
  });

  return { serie: serie, naoClassificadas: naoClassificadasGlobal };
}

function insPontoEquilibrio(indiceMes, custoFixoMes, custoVariavelMes) {
  const receita = DRE_RESUMO.receitaBruta[indiceMes];
  if (receita === null || receita === undefined || receita === 0) {
    return { valor: null, margemContribuicao: null };
  }
  const margemContribuicao = (receita - custoVariavelMes) / receita;
  if (margemContribuicao <= 0) {
    return { valor: null, margemContribuicao: margemContribuicao };
  }
  return { valor: custoFixoMes / margemContribuicao, margemContribuicao: margemContribuicao };
}

function insSaudeCaixa() {
  const hoje = new Date();
  const hojeISO = hoje.toISOString().split('T')[0];
  const limiteData = new Date(hoje.getTime() + INSIGHTS_CONFIG.diasAReceberPagar * 24 * 60 * 60 * 1000);
  const limiteISO = limiteData.toISOString().split('T')[0];

  const todos = insTodosLancamentos();

  let aReceber = 0, aPagar = 0, inadimplenciaValor = 0, totalTituloReceber = 0;
  const listaAReceber = [], listaAPagar = [], listaInadimplentes = [];

  todos.forEach(function (l) {
    const iso = paraDataISO(l.data);
    const statusUpper = (l.situacaoTitulo || '').toUpperCase();
    const jaLiquidado = statusUpper === 'PAGO' || statusUpper === 'RECEBIDO';
    const atrasado = statusUpper.indexOf('ATRAS') !== -1;

    if (l.natureza === 'Contas a Receber') {
      if (statusUpper && statusUpper !== '-') totalTituloReceber += Math.abs(l.valor);
      if (atrasado) {
        inadimplenciaValor += Math.abs(l.valor);
        listaInadimplentes.push(l);
      }
      if (!jaLiquidado && !atrasado && iso && iso >= hojeISO && iso <= limiteISO) {
        aReceber += Math.abs(l.valor);
        listaAReceber.push(l);
      }
    }
    if (l.natureza === 'Contas a Pagar') {
      if (!jaLiquidado && !atrasado && iso && iso >= hojeISO && iso <= limiteISO) {
        aPagar += Math.abs(l.valor);
        listaAPagar.push(l);
      }
    }
  });

  const taxaInadimplencia = totalTituloReceber > 0 ? (inadimplenciaValor / totalTituloReceber * 100) : 0;

  let saldoBancarioAtual = 0;
  DADOS.forEach(function (conta) {
    if (conta.tipo === 'banco') saldoBancarioAtual += calcularSaldoNaData(conta, hojeISO);
  });

  const idxAtual = insIndiceMesAtual();
  const mapaPorMes = insAgruparPorMes(todos);
  let somaQueima = 0, mesesContados = 0;
  const detalheRunway = [];
  for (let i = 1; i <= INSIGHTS_CONFIG.mesesRunway; i++) {
    const idx = idxAtual - i;
    if (idx < 0) continue;
    const am = insAnoMesPorIndice(idx);
    const doMes = mapaPorMes[insChaveMes(am.ano, am.mes)] || [];
    const entradas = doMes.filter(function (l) { return l.valor > 0; }).reduce(function (s, l) { return s + l.valor; }, 0);
    const saidas = doMes.filter(function (l) { return l.valor < 0; }).reduce(function (s, l) { return s + Math.abs(l.valor); }, 0);
    const queimaLiquida = saidas - entradas;
    somaQueima += queimaLiquida;
    mesesContados++;
    detalheRunway.push({
      nome: DRE_RESUMO.nomesMeses[idx] || insChaveMes(am.ano, am.mes),
      entradas: entradas, saidas: saidas, queimaLiquida: queimaLiquida,
    });
  }
  detalheRunway.reverse(); // mostrar em ordem cronológica (mês mais antigo primeiro)
  const queimaMedia = mesesContados > 0 ? (somaQueima / mesesContados) : 0;
  const runwayMeses = queimaMedia > 0 ? (saldoBancarioAtual / queimaMedia) : null;

  return {
    aReceber: aReceber, listaAReceber: listaAReceber,
    aPagar: aPagar, listaAPagar: listaAPagar,
    taxaInadimplencia: taxaInadimplencia, listaInadimplentes: listaInadimplentes,
    runwayMeses: runwayMeses, saldoBancarioAtual: saldoBancarioAtual,
    queimaMedia: queimaMedia, mesesContadosRunway: mesesContados, detalheRunway: detalheRunway,
  };
}

// ---- Aging de Recebíveis ----
// Quebra TODOS os títulos de Contas a Receber ainda não liquidados (não só
// os "próximos X dias" que o card de A Receber usa) em faixas de atraso --
// o KPI de cobrança mais citado em qualquer material sobre gestão de
// recebíveis, porque "R$ 300 mil a receber" pode estar tudo saudável ou
// tudo podre, e o card de A Receber sozinho não mostra essa diferença.
//
// Calculado por DATA (hoje − data do título), não pelo texto de Situação
// -- o texto "ATRASADO" às vezes não vem preenchido (ver o caso do
// Rosemiro, investigado em 06/08/2026), então contar por data é mais
// confiável que depender só do rótulo de status.
function insAgingRecebiveis() {
  const hoje = new Date();
  const hojeSemHora = new Date(hoje.getFullYear(), hoje.getMonth(), hoje.getDate());
  const todos = insTodosLancamentos();

  const buckets = [
    { chave: 'aVencer', label: 'A Vencer', valor: 0, qtd: 0 },
    { chave: 'b30', label: 'Até 30 dias', valor: 0, qtd: 0 },
    { chave: 'b60', label: '31 a 60 dias', valor: 0, qtd: 0 },
    { chave: 'b90', label: '61 a 90 dias', valor: 0, qtd: 0 },
    { chave: 'b90mais', label: 'Mais de 90 dias', valor: 0, qtd: 0 },
  ];
  const [aVencer, b30, b60, b90, b90mais] = buckets;

  todos.forEach(function (l) {
    if (l.natureza !== 'Contas a Receber') return;
    const statusUpper = (l.situacaoTitulo || '').toUpperCase();
    const jaLiquidado = statusUpper === 'PAGO' || statusUpper === 'RECEBIDO';
    if (jaLiquidado) return;
    const iso = paraDataISO(l.data);
    if (!iso) return;
    const partes = iso.split('-').map(Number);
    const dataTitulo = new Date(partes[0], partes[1] - 1, partes[2]);
    const diasAtraso = Math.round((hojeSemHora - dataTitulo) / (1000 * 60 * 60 * 24));

    let bucket;
    if (diasAtraso <= 0) bucket = aVencer;
    else if (diasAtraso <= 30) bucket = b30;
    else if (diasAtraso <= 60) bucket = b60;
    else if (diasAtraso <= 90) bucket = b90;
    else bucket = b90mais;

    bucket.valor += Math.abs(l.valor);
    bucket.qtd += 1;
  });

  const total = buckets.reduce(function (s, b) { return s + b.valor; }, 0);
  return { buckets: buckets, total: total };
}

function insComparativoEmpresas() {
  const idxAtual = insIndiceMesAtual();
  const am = insAnoMesPorIndice(idxAtual);
  const chave = insChaveMes(am.ano, am.mes);
  const todos = insTodosLancamentos();
  const doMes = todos.filter(function (l) {
    const info = insMesDoLancamento(l);
    return info && insChaveMes(info.ano, info.mes) === chave;
  });

  const porEmpresa = {};
  doMes.forEach(function (l) {
    if (!porEmpresa[l.empresaNome]) porEmpresa[l.empresaNome] = { receita: 0, despesa: 0 };
    if (l.valor > 0) porEmpresa[l.empresaNome].receita += l.valor;
    else porEmpresa[l.empresaNome].despesa += l.valor;
  });

  return Object.keys(porEmpresa).map(function (nome) {
    const v = porEmpresa[nome];
    return { nome: nome, receita: v.receita, despesa: v.despesa, resultado: v.receita + v.despesa };
  }).sort(function (a, b) { return b.receita - a.receita; });
}

// ---- Faturamento por Empresa ----
// Prime Sol Matriz / Lagos / Cabo Frio: o Kit (maior parte da receita) não
// tem título/categoria própria por empresa na Omie -- vem de um número
// manual único consolidado (MANUAL_FATURAMENTO_KIT). Pra quebrar por
// empresa, usamos FATURAMENTO_POR_EMPRESA_KIT, que o Fabrício confirmou
// vir das planilhas mensais de faturamento (coluna "LOJA"), em 06/08/2026.
//
// PS Energia é diferente: as vendas dela já são 100% automáticas via Omie
// (não passa pela planilha de Kit), então calculamos direto dos
// lançamentos, com a mesma regra de categoria/competência que a DRE usa
// pras linhas 8-14 (Receita Operacional Bruta, exceto o Kit em si).
const REVENUE_CATEGORIAS_MENORES = [
  { categoria: 'serviços de engenharia - avulso', competencia: 'Mesmo Mês' },
  { categoria: 'carregador elétrico', competencia: 'Mesmo Mês' },
  { categoria: 'ar condicionado', competencia: 'Mesmo Mês' },
  { categoria: 'ps energia', competencia: 'Não se Aplica' },
  { categoria: 'comissão seguro', competencia: 'Mesmo Mês' },
  { categoria: 'receitas com publicidade e propaganda', competencia: 'Mesmo Mês' },
  { categoria: 'reembolso', competencia: 'Mês Anterior' },
];

function insMesEfetivoGenerico(dataBr, competencia) {
  const iso = paraDataISO(dataBr);
  if (!iso) return null;
  let partes = iso.split('-').map(Number);
  let ano = partes[0], mes = partes[1];
  if (competencia === 'Mês Anterior') {
    mes -= 1;
    if (mes === 0) { mes = 12; ano -= 1; }
  }
  return { ano: ano, mes: mes };
}

function insFaturamentoPSEnergiaPorMes(ano, mes) {
  const todos = insTodosLancamentos();
  let total = 0;
  todos.forEach(function (l) {
    if (l.empresaNome !== 'PS Energia') return;
    const catNorm = insNormalizarCategoria(l.categoria).toLowerCase();
    const config = REVENUE_CATEGORIAS_MENORES.find(function (c) { return c.categoria === catNorm; });
    if (!config) return;
    const eff = insMesEfetivoGenerico(l.data, config.competencia);
    if (eff && eff.ano === ano && eff.mes === mes) total += (l.valor || 0);
  });
  return total;
}

function insFaturamentoPorEmpresa() {
  const meses = insListaMesesAteAtual();
  return meses.map(function (m) {
    const am = insAnoMesPorIndice(m.indice);
    const chave = insChaveMes(am.ano, am.mes);
    const doMesEmpresa = FATURAMENTO_POR_EMPRESA_KIT[chave];
    const psEnergia = insFaturamentoPSEnergiaPorMes(am.ano, am.mes);
    const matriz = doMesEmpresa ? (doMesEmpresa['Prime Sol Matriz'] || 0) : null;
    const lagos = doMesEmpresa ? (doMesEmpresa['Prime Sol Lagos'] || 0) : null;
    const caboFrio = doMesEmpresa ? (doMesEmpresa['Prime Sol Cabo Frio'] || 0) : null;
    // O "Total" da linha usa sempre o número oficial da DRE (o mesmo do
    // gráfico de Faturamento Mensal acima) -- NÃO é a soma das 4 colunas.
    // Isso importa em meses sem planilha por empresa ainda informada: as
    // colunas de empresa ficam com "—", mas o Total continua batendo com o
    // valor real e completo (inclui o Kit consolidado), em vez de mostrar
    // um total artificialmente baixo (só o que desse pra quebrar por
    // empresa naquele mês).
    const totalOficial = DRE_RESUMO.receitaBruta[m.indice];
    const total = (totalOficial === null || totalOficial === undefined) ? null : totalOficial;
    return { nome: m.nome, indice: m.indice, matriz: matriz, lagos: lagos, caboFrio: caboFrio, psEnergia: psEnergia, total: total, temPlanilha: !!doMesEmpresa };
  });
}

function insTopCategoriasDespesa() {
  const idxAtual = insIndiceMesAtual();
  const am = insAnoMesPorIndice(idxAtual);
  const chaveAtual = insChaveMes(am.ano, am.mes);
  const idxAnterior = idxAtual - 1;
  let chaveAnterior = null;
  if (idxAnterior >= 0) {
    const amA = insAnoMesPorIndice(idxAnterior);
    chaveAnterior = insChaveMes(amA.ano, amA.mes);
  }

  const todos = insTodosLancamentos();
  const mapaPorMes = insAgruparPorMes(todos);
  const doMesAtual = mapaPorMes[chaveAtual] || [];
  const doMesAnterior = chaveAnterior ? (mapaPorMes[chaveAnterior] || []) : [];

  function somaPorCategoria(lista) {
    const soma = {};
    lista.forEach(function (l) {
      if (l.valor >= 0) return;
      const categoriaLimpa = insNormalizarCategoria(l.categoria);
      soma[categoriaLimpa] = (soma[categoriaLimpa] || 0) + Math.abs(l.valor);
    });
    return soma;
  }

  const atual = somaPorCategoria(doMesAtual);
  const anterior = somaPorCategoria(doMesAnterior);

  return Object.keys(atual)
    .map(function (categoria) { return [categoria, atual[categoria]]; })
    .sort(function (a, b) { return b[1] - a[1]; })
    .slice(0, 5)
    .map(function (par) {
      const categoria = par[0], valor = par[1];
      const valorAnterior = anterior[categoria] || 0;
      const variacao = valorAnterior > 0 ? ((valor - valorAnterior) / valorAnterior * 100) : null;
      return { categoria: categoria, valor: valor, variacao: variacao };
    });
}

function insDepartamentoMaiorAlta() {
  const idxAtual = insIndiceMesAtual();
  const idxAnterior = idxAtual - 1;
  const todos = insTodosLancamentos();
  const mapaPorMes = insAgruparPorMes(todos);

  function somaPorDepartamento(chave) {
    const lista = mapaPorMes[chave] || [];
    const soma = {};
    lista.forEach(function (l) {
      if (l.valor >= 0) return;
      const valorAbs = Math.abs(l.valor);
      const rateio = (l.departamentosRateio && l.departamentosRateio.length)
        ? l.departamentosRateio
        : [{ nome: l.departamento || '-', percentual: 100 }];
      rateio.forEach(function (d) {
        soma[d.nome] = (soma[d.nome] || 0) + valorAbs * (d.percentual || 0) / 100;
      });
    });
    return soma;
  }

  const am = insAnoMesPorIndice(idxAtual);
  const somaAtual = somaPorDepartamento(insChaveMes(am.ano, am.mes));
  let somaAnterior = {};
  if (idxAnterior >= 0) {
    const amA = insAnoMesPorIndice(idxAnterior);
    somaAnterior = somaPorDepartamento(insChaveMes(amA.ano, amA.mes));
  }

  let maior = null;
  Object.keys(somaAtual).forEach(function (nome) {
    const valor = somaAtual[nome];
    const valorAnterior = somaAnterior[nome] || 0;
    const variacao = valorAnterior > 0 ? ((valor - valorAnterior) / valorAnterior * 100) : null;
    if (variacao !== null && (!maior || variacao > maior.variacao)) {
      maior = { nome: nome, valor: valor, variacao: variacao };
    }
  });
  return maior;
}

function insContasSaldoBaixo() {
  const hojeISO = new Date().toISOString().split('T')[0];
  const resultado = [];
  DADOS.forEach(function (conta) {
    if (conta.tipo !== 'banco') return;
    const saldo = calcularSaldoNaData(conta, hojeISO);
    if (saldo < INSIGHTS_CONFIG.limiteSaldoBaixo) {
      resultado.push({ nome: conta.nome, empresa: conta.empresa, saldo: saldo });
    }
  });
  return resultado.sort(function (a, b) { return a.saldo - b.saldo; });
}

function insMontarTabelaLancamentos(lista, colunas) {
  if (!lista.length) return '<div class="vazio" style="padding:8px 0;">Nenhum lançamento encontrado.</div>';
  const linhas = lista.slice(0, 15).map(function (l) {
    return '<tr>' + colunas.map(function (c) { return '<td>' + c.get(l) + '</td>'; }).join('') + '</tr>';
  }).join('');
  const cab = colunas.map(function (c) { return '<th>' + c.label + '</th>'; }).join('');
  const nota = lista.length > 15 ? '<div style="font-size:10.5px;color:var(--text-faint);margin-top:6px;">mostrando 15 de ' + lista.length + '</div>' : '';
  return '<table class="ins-detail-table"><thead><tr>' + cab + '</tr></thead><tbody>' + linhas + '</tbody></table>' + nota;
}

function renderizarInsights() {
  const idxAtual = insIndiceMesAtual();
  const idxAnterior = idxAtual - 1;
  const nomeMesAtual = DRE_RESUMO.nomesMeses[idxAtual] || '';

  const custos = insCalcularSeriesCusto();
  const saude = insSaudeCaixa();
  const aging = insAgingRecebiveis();
  const comparativo = insComparativoEmpresas();
  const topCategorias = insTopCategoriasDespesa();
  const deptoMaiorAlta = insDepartamentoMaiorAlta();
  const contasBaixas = insContasSaldoBaixo();

  // ---- Faturamento por Empresa (calculado aqui em cima porque o hero
  // card, logo abaixo, precisa tanto do total acumulado quanto da tabela
  // detalhada por empresa) ----
  const faturamentoPorEmpresaSerie = insFaturamentoPorEmpresa();

  function insMediaOuNull(lista) {
    const validos = lista.filter(function (v) { return v !== null && v !== undefined; });
    return validos.length > 0 ? validos.reduce(function (a, b) { return a + b; }, 0) / validos.length : null;
  }
  function insSomaOuNull(lista) {
    const validos = lista.filter(function (v) { return v !== null && v !== undefined; });
    return validos.length > 0 ? validos.reduce(function (a, b) { return a + b; }, 0) : null;
  }
  function celEmpresa(v) {
    return (v !== null && v !== undefined) ? fmtMoeda(v) : '—';
  }

  const somaMatriz = insSomaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.matriz; }));
  const somaLagos = insSomaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.lagos; }));
  const somaCaboFrio = insSomaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.caboFrio; }));
  const somaPSEnergia = insSomaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.psEnergia; }));
  const somaTotalEmpresas = insSomaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.total; }));

  const mediaMatriz = insMediaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.matriz; }));
  const mediaLagos = insMediaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.lagos; }));
  const mediaCaboFrio = insMediaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.caboFrio; }));
  const mediaPSEnergia = insMediaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.psEnergia; }));
  const mediaTotalEmpresas = insMediaOuNull(faturamentoPorEmpresaSerie.map(function (f) { return f.total; }));

  const rotuloTotalEmpresas = faturamentoPorEmpresaSerie.length > 0
    ? 'Total até ' + faturamentoPorEmpresaSerie[faturamentoPorEmpresaSerie.length - 1].nome
    : 'Total';

  const linhasFaturamentoPorEmpresa = faturamentoPorEmpresaSerie.map(function (f) {
    return '<tr><td>' + f.nome + '</td><td>' + celEmpresa(f.matriz) + '</td><td>' + celEmpresa(f.lagos) + '</td><td>' +
      celEmpresa(f.caboFrio) + '</td><td>' + celEmpresa(f.psEnergia) + '</td><td><strong>' + celEmpresa(f.total) + '</strong></td></tr>';
  }).join('') +
    '<tr style="border-top:2px solid var(--border)"><td>' + rotuloTotalEmpresas + '</td><td>' + celEmpresa(somaMatriz) + '</td><td>' +
    celEmpresa(somaLagos) + '</td><td>' + celEmpresa(somaCaboFrio) + '</td><td>' + celEmpresa(somaPSEnergia) + '</td><td><strong>' + celEmpresa(somaTotalEmpresas) + '</strong></td></tr>' +
    '<tr><td>Média</td><td>' + celEmpresa(mediaMatriz) + '</td><td>' + celEmpresa(mediaLagos) + '</td><td>' +
    celEmpresa(mediaCaboFrio) + '</td><td>' + celEmpresa(mediaPSEnergia) + '</td><td><strong>' + celEmpresa(mediaTotalEmpresas) + '</strong></td></tr>';

  const temMesSemPlanilha = faturamentoPorEmpresaSerie.some(function (f) { return !f.temPlanilha; });

  const faturamentoPorEmpresaHtml =
    '<div class="ins-tabela-scroll">' +
    '<table class="ins-mini-table ins-tabela-empresas"><thead><tr>' +
    '<th>Mês</th><th>Prime Sol Matriz</th><th>Prime Sol Lagos</th><th>Prime Sol Cabo Frio</th><th>PS Energia</th><th>Total</th>' +
    '</tr></thead><tbody>' + linhasFaturamentoPorEmpresa + '</tbody></table>' +
    '</div>' +
    (temMesSemPlanilha
      ? '<div class="ins-nota-rodape">* Prime Sol Matriz/Lagos/Cabo Frio ficam em "—" nos meses em que a planilha mensal de faturamento por loja ainda não foi informada. PS Energia é sempre automático (Omie), não depende de planilha.</div>'
      : '');

  // ---- alerta de categorias não classificadas ----
  // Compacto por padrão (só a contagem + botões) -- a lista completa fica
  // escondida atrás de "Ver lista", e "Copiar lista" joga um array JSON
  // pronto pra área de transferência, facilitando colar no
  // classificacao_custos.json.
  const elAlerta = document.getElementById('insAlertaNaoClassificado');
  if (custos.naoClassificadas.size > 0) {
    const todasCategorias = Array.from(custos.naoClassificadas).sort();
    const jsonCategoriasEscapado = JSON.stringify(todasCategorias, null, 2)
      .replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n');
    elAlerta.innerHTML =
      '<div class="ins-alerta-nc">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">' +
      '<span>⚠️ <strong>' + custos.naoClassificadas.size + '</strong> categoria(s) de despesa ainda não classificada(s) em <code>classificacao_custos.json</code> — está(ão) sendo ignorada(s) no cálculo de Custo Fixo/Variável.</span>' +
      '<span style="white-space:nowrap">' +
      '<button type="button" class="ins-btn-alerta" onclick="toggleDetalheInsight(' + "'insListaCategoriasNC'" + ')">Ver lista</button> ' +
      '<button type="button" class="ins-btn-alerta" onclick="navigator.clipboard.writeText(\'' + jsonCategoriasEscapado + '\').then(function(){ this.textContent=\'Copiado!\'; var b=this; setTimeout(function(){ b.textContent=\'Copiar lista\'; }, 1500); }.bind(event.target))">Copiar lista</button>' +
      '</span>' +
      '</div>' +
      '<div class="ins-detail-panel" id="insListaCategoriasNC" style="margin-top:0">' +
      '<div style="padding-top:8px;border-top:1px solid rgba(250,168,33,0.3);margin-top:8px;">' + todasCategorias.join(' · ') + '</div>' +
      '</div>' +
      '</div>';
  } else {
    elAlerta.innerHTML = '';
  }

  // ---- Faturamento Acumulado no Ano (hero) ----
  // Redesenhado em 06/08/2026: antes mostrava só o último mês fechado; o
  // Fabrício preferiu destacar o acumulado do ano (visão mais robusta,
  // não depende de um mês isolado ter sido bom ou ruim) e virou um card
  // expansível -- ao clicar, abre a tabela "Faturamento por Empresa"
  // (que antes vivia numa seção separada com o gráfico de linha, removido
  // a pedido por não estar visualmente bom).
  const receitaAtual = DRE_RESUMO.receitaBruta[idxAtual];

  const heroHtml = '' +
    '<div class="ins-hero ins-card-expandivel" onclick="insToggleExpandivel(this)">' +
    '<div class="ins-hero-top">' +
    '<div><div class="ins-hero-label">Faturamento Acumulado no Ano — Receita Operacional Bruta (DRE)</div>' +
    '<div class="ins-hero-value">' + (somaTotalEmpresas !== null ? fmtMoeda(somaTotalEmpresas) : '—') + '</div>' +
    '<div class="ins-hero-sub">Jan a ' + nomeMesAtual + ' · clique pra ver por empresa <span class="ins-expandir-seta">▾</span></div></div>' +
    '<div class="ins-hero-stats">' +
    '<div class="ins-hero-stat"><div class="l">Média mensal no ano</div><div class="v">' + (mediaTotalEmpresas !== null ? fmtMoeda(mediaTotalEmpresas) : '—') + '</div></div>' +
    '<div class="ins-hero-stat"><div class="l">Último mês fechado (' + nomeMesAtual + ')</div><div class="v">' + (receitaAtual !== null ? fmtMoeda(receitaAtual) : '—') + '</div></div>' +
    '</div></div>' +
    '<div class="ins-expandivel-corpo" onclick="event.stopPropagation()">' +
    '<div class="ins-kpi-label">Faturamento por Empresa</div>' +
    faturamentoPorEmpresaHtml +
    '</div></div>';

  // ---- Rentabilidade ----
  function margem(linha) {
    const atual = linha[idxAtual], anterior = idxAnterior >= 0 ? linha[idxAnterior] : null;
    const receitaA = DRE_RESUMO.receitaBruta[idxAtual], receitaP = idxAnterior >= 0 ? DRE_RESUMO.receitaBruta[idxAnterior] : null;
    const margemAtual = (atual !== null && receitaA) ? (atual / receitaA * 100) : null;
    const margemAnterior = (anterior !== null && receitaP) ? (anterior / receitaP * 100) : null;
    const deltaPP = (margemAtual !== null && margemAnterior !== null) ? (margemAtual - margemAnterior) : null;
    return { margemAtual: margemAtual, deltaPP: deltaPP };
  }
  const mBruta = margem(DRE_RESUMO.lucroBruto);
  const mEbitda = margem(DRE_RESUMO.ebitda);
  const mLiquida = margem(DRE_RESUMO.lucroLiquido);

  function cardMargem(titulo, m) {
    const deltaTxto = m.deltaPP === null ? '—' : (Math.abs(m.deltaPP).toFixed(1) + ' p.p. ' + (m.deltaPP >= 0 ? 'a mais' : 'a menos') + ' vs mês anterior');
    return '<div class="ins-card"><div class="ins-kpi-label">' + titulo + '</div>' +
      '<div class="ins-kpi-value">' + (m.margemAtual !== null ? m.margemAtual.toFixed(1) + '%' : '—') + '</div>' +
      '<div class="ins-delta ' + insClasseVariacao(m.deltaPP) + '">' + deltaTxto + '</div></div>';
  }

  // ---- Rentabilidade mês a mês (tabela com os 4 indicadores lado a lado) ----
  function insRentabilidadeMensal() {
    const meses = insListaMesesAteAtual();
    return meses.map(function (m) {
      const receita = DRE_RESUMO.receitaBruta[m.indice];
      const lb = DRE_RESUMO.lucroBruto[m.indice];
      const eb = DRE_RESUMO.ebitda[m.indice];
      const ll = DRE_RESUMO.lucroLiquido[m.indice];
      function pct(v) { return (v !== null && v !== undefined && receita) ? (v / receita * 100) : null; }
      return {
        nome: m.nome,
        margemBruta: pct(lb),
        margemEbitda: pct(eb),
        margemLiquida: pct(ll),
        lucroLiquido: (ll === null || ll === undefined) ? null : ll,
      };
    });
  }
  function insMediaSimples(lista) {
    const validos = lista.filter(function (v) { return v !== null && v !== undefined; });
    return validos.length > 0 ? validos.reduce(function (a, b) { return a + b; }, 0) / validos.length : null;
  }
  function celRentabPct(v) {
    return v !== null ? '<span class="' + insClasseVariacao(v) + '">' + (v >= 0 ? '+' : '') + v.toFixed(1) + '%</span>' : '<span class="ins-neutro">—</span>';
  }
  function celRentabMoeda(v) {
    return v !== null ? '<span class="' + insClasseVariacao(v) + '">' + fmtMoeda(v) + '</span>' : '<span class="ins-neutro">—</span>';
  }

  const rentabilidadeSerie = insRentabilidadeMensal();
  const mediaMB = insMediaSimples(rentabilidadeSerie.map(function (r) { return r.margemBruta; }));
  const mediaME = insMediaSimples(rentabilidadeSerie.map(function (r) { return r.margemEbitda; }));
  const mediaML = insMediaSimples(rentabilidadeSerie.map(function (r) { return r.margemLiquida; }));
  const mediaLL = insMediaSimples(rentabilidadeSerie.map(function (r) { return r.lucroLiquido; }));

  const linhasRentabilidadeMensal = rentabilidadeSerie.map(function (r) {
    return '<tr><td>' + r.nome + '</td><td>' + celRentabPct(r.margemBruta) + '</td><td>' + celRentabPct(r.margemEbitda) + '</td><td>' + celRentabPct(r.margemLiquida) + '</td><td>' + celRentabMoeda(r.lucroLiquido) + '</td></tr>';
  }).join('') +
    '<tr style="border-top:2px solid var(--border)"><td>Média</td><td>' + celRentabPct(mediaMB) + '</td><td>' + celRentabPct(mediaME) + '</td><td>' + celRentabPct(mediaML) + '</td><td>' + celRentabMoeda(mediaLL) + '</td></tr>';

  const rentabilidadeMensalHtml = '<div class="ins-card ins-rentab-card">' +
    '<div class="ins-kpi-label">Rentabilidade Mês a Mês</div>' +
    '<table class="ins-mini-table ins-rentab-table"><thead><tr>' +
    '<th>Mês</th><th>Margem Bruta</th><th>Margem EBITDA</th><th>Margem Líquida</th><th>Lucro Líquido</th>' +
    '</tr></thead><tbody>' + linhasRentabilidadeMensal + '</tbody></table></div>';

  const rentabilidadeHtml = '<div class="ins-grid">' +
    cardMargem('Margem Bruta', mBruta) + cardMargem('Margem EBITDA', mEbitda) + cardMargem('Margem Líquida', mLiquida) +
    '<div class="ins-card"><div class="ins-kpi-label">Lucro Líquido do Mês</div>' +
    '<div class="ins-kpi-value">' + (DRE_RESUMO.lucroLiquido[idxAtual] !== null ? fmtMoeda(DRE_RESUMO.lucroLiquido[idxAtual]) : '—') + '</div>' +
    '<div class="ins-delta ins-neutro">referência: ' + nomeMesAtual + '</div></div>' +
    '</div>' + rentabilidadeMensalHtml;

  // ---- Saúde de caixa ----
  // O Runway sempre mostra um número: quando o caixa está queimando, mostra
  // "X meses" + a queima média em R$; quando não está queimando (entradas >=
  // saídas), mostra o crescimento médio mensal em R$ em vez de só a frase
  // qualitativa "não está queimando", que não dava nenhuma noção de escala.
  let runwayValorHtml, runwayDeltaHtml;
  if (saude.mesesContadosRunway === 0) {
    runwayValorHtml = '—';
    runwayDeltaHtml = 'sem meses fechados suficientes pra calcular ainda';
  } else if (saude.runwayMeses !== null) {
    runwayValorHtml = saude.runwayMeses.toFixed(1) + ' meses';
    runwayDeltaHtml = 'queima média de ' + fmtMoeda(saude.queimaMedia) + '/mês · saldo bancário ÷ queima média (' + saude.mesesContadosRunway + ' últimos meses)';
  } else {
    const crescimentoMedio = -saude.queimaMedia;
    runwayValorHtml = '<span style="color:var(--green)">Caixa crescendo</span>';
    runwayDeltaHtml = '+' + fmtMoeda(crescimentoMedio) + '/mês em média, nos últimos ' + saude.mesesContadosRunway + ' meses (entradas > saídas)';
  }
  const linhasDetalheRunway = saude.detalheRunway.map(function (m) {
    return '<tr><td>' + m.nome + '</td>' +
      '<td class="valor-col" style="color:var(--green)">' + fmtMoeda(m.entradas) + '</td>' +
      '<td class="valor-col" style="color:var(--red)">-' + fmtMoeda(m.saidas) + '</td>' +
      '<td class="valor-col">' + (m.queimaLiquida >= 0 ? fmtMoeda(m.queimaLiquida) : '<span style="color:var(--green)">' + fmtMoeda(m.queimaLiquida) + '</span>') + '</td></tr>';
  }).join('');
  const detalheRunwayHtml = saude.mesesContadosRunway === 0 ? '' :
    '<div style="padding-top:10px;border-top:1px solid var(--border);margin-top:10px;font-size:12.5px;">' +
    '<div style="margin-bottom:8px;color:var(--text-muted);">Saldo bancário atual (todas as contas banco, na data de hoje): <strong style="color:var(--text)">' + fmtMoeda(saude.saldoBancarioAtual) + '</strong></div>' +
    '<table class="ins-mini-table"><thead><tr><th>Mês</th><th>Entradas</th><th>Saídas</th><th>Queima líquida (saídas − entradas)</th></tr></thead>' +
    '<tbody>' + linhasDetalheRunway + '</tbody></table>' +
    '<div style="margin-top:10px;color:var(--text-muted);">' +
    'Queima média = soma da queima líquida dos ' + saude.mesesContadosRunway + ' meses ÷ ' + saude.mesesContadosRunway + ' = <strong style="color:var(--text)">' + fmtMoeda(saude.queimaMedia) + '/mês</strong>' +
    '</div>' +
    '<div style="margin-top:6px;color:var(--text-muted);">' +
    'Runway = saldo bancário ÷ queima média = ' + fmtMoeda(saude.saldoBancarioAtual) + ' ÷ ' + fmtMoeda(saude.queimaMedia) +
    ' = <strong style="color:var(--text)">' + (saude.runwayMeses !== null ? saude.runwayMeses.toFixed(1) + ' meses' : '—') + '</strong>' +
    '</div></div>';
  const saudeHtml = '<div class="ins-grid">' +
    '<div class="ins-card' + (saude.mesesContadosRunway > 0 ? ' ins-clickable' : '') + '"' +
    (saude.mesesContadosRunway > 0 ? ' onclick="toggleDetalheInsight(' + "'insDetalheRunway'" + ')"' : '') + '>' +
    '<div class="ins-kpi-label">Runway de Caixa</div>' +
    '<div class="ins-kpi-value">' + runwayValorHtml + '</div>' +
    '<div class="ins-delta ins-neutro">' + runwayDeltaHtml + '</div>' +
    '<div class="ins-detail-panel" id="insDetalheRunway">' + detalheRunwayHtml + '</div>' +
    '</div>' +

    '<div class="ins-card ins-clickable" onclick="toggleDetalheInsight(' + "'insDetalheReceber'" + ')">' +
    '<div class="ins-kpi-label">A Receber (em aberto)</div>' +
    '<div class="ins-kpi-value" style="color:var(--green)">' + fmtMoeda(saude.aReceber) + '</div>' +
    '<div class="ins-delta ins-neutro">próximos ' + INSIGHTS_CONFIG.diasAReceberPagar + ' dias</div>' +
    '<div class="ins-detail-panel" id="insDetalheReceber">' +
    insMontarTabelaLancamentos(saude.listaAReceber, [
      { label: 'Cliente', get: function (l) { return l.cliente; } },
      { label: 'Empresa', get: function (l) { return l.empresaNome; } },
      { label: 'Valor', get: function (l) { return fmtMoeda(Math.abs(l.valor)); } },
      { label: 'Previsão', get: function (l) { return l.data; } },
    ]) + '</div></div>' +

    '<div class="ins-card ins-clickable" onclick="toggleDetalheInsight(' + "'insDetalhePagar'" + ')">' +
    '<div class="ins-kpi-label">A Pagar (em aberto)</div>' +
    '<div class="ins-kpi-value" style="color:var(--red)">' + fmtMoeda(saude.aPagar) + '</div>' +
    '<div class="ins-delta ins-neutro">próximos ' + INSIGHTS_CONFIG.diasAReceberPagar + ' dias</div>' +
    '<div class="ins-detail-panel" id="insDetalhePagar">' +
    insMontarTabelaLancamentos(saude.listaAPagar, [
      { label: 'Fornecedor', get: function (l) { return l.cliente; } },
      { label: 'Empresa', get: function (l) { return l.empresaNome; } },
      { label: 'Valor', get: function (l) { return fmtMoeda(Math.abs(l.valor)); } },
      { label: 'Previsão', get: function (l) { return l.data; } },
    ]) + '</div></div>' +

    '<div class="ins-card ins-clickable" onclick="toggleDetalheInsight(' + "'insDetalheInadimplencia'" + ')">' +
    '<div class="ins-kpi-label">Taxa de Inadimplência</div>' +
    '<div class="ins-kpi-value">' + saude.taxaInadimplencia.toFixed(1) + '%</div>' +
    '<div class="ins-delta ins-neutro">' + fmtMoeda(saude.taxaInadimplencia ? (saude.listaInadimplentes.reduce(function(s,l){return s+Math.abs(l.valor);},0)) : 0) + ' em atraso</div>' +
    '<div class="ins-detail-panel" id="insDetalheInadimplencia">' +
    insMontarTabelaLancamentos(saude.listaInadimplentes, [
      { label: 'Cliente', get: function (l) { return l.cliente; } },
      { label: 'Empresa', get: function (l) { return l.empresaNome; } },
      { label: 'Valor', get: function (l) { return fmtMoeda(Math.abs(l.valor)); } },
      { label: 'Venceu em', get: function (l) { return l.data; } },
    ]) + '</div></div>' +
    '</div>' +
    '<div class="ins-card ins-card-largo">' +
    '<div class="ins-kpi-label">Aging de Recebíveis (todos os títulos em aberto, não só os próximos ' + INSIGHTS_CONFIG.diasAReceberPagar + ' dias)</div>' +
    (aging.total > 0
      ? aging.buckets.map(function (b) {
          const pct = aging.total > 0 ? (b.valor / aging.total * 100) : 0;
          const corBarra = (b.chave === 'b90' || b.chave === 'b90mais') ? 'var(--red)' : (b.chave === 'b60' ? 'var(--laranja)' : 'var(--accent)');
          return '<div class="linha-depto">' +
            '<span class="nome-depto">' + b.label + '</span>' +
            '<div class="barra-wrap"><div class="barra" style="width:' + pct.toFixed(1) + '%;background:' + corBarra + '"></div></div>' +
            '<span class="pct-depto">' + pct.toFixed(1) + '%</span>' +
            '<span class="valor-depto">' + fmtMoeda(b.valor) + ' (' + b.qtd + ')</span>' +
            '</div>';
        }).join('')
      : '<div class="ins-delta ins-neutro" style="margin-top:8px">Nenhum título em aberto no momento.</div>') +
    '</div>';

  // ---- Custo Fixo x Variável x Ponto de Equilíbrio ----
  const ultimo = custos.serie[custos.serie.length - 1];
  const penultimo = custos.serie.length > 1 ? custos.serie[custos.serie.length - 2] : null;
  const varFixo = penultimo && penultimo.fixo ? ((ultimo.fixo - penultimo.fixo) / penultimo.fixo * 100) : null;
  const varVariavel = penultimo && penultimo.variavel ? ((ultimo.variavel - penultimo.variavel) / penultimo.variavel * 100) : null;

  function linhasMiniTabelaCusto(campo) {
    const linhas = custos.serie.map(function (m) {
      return '<tr><td>' + m.nome + '</td><td>' + fmtMoeda(m[campo]) + '</td></tr>';
    }).join('');
    // Média simples: soma dos meses disponíveis na série ÷ quantidade de meses.
    const soma = custos.serie.reduce(function (acc, m) { return acc + m[campo]; }, 0);
    const media = custos.serie.length > 0 ? soma / custos.serie.length : 0;
    const linhaMedia = '<tr style="border-top:2px solid var(--border)"><td>Média</td><td>' + fmtMoeda(media) + '</td></tr>';
    return linhas + linhaMedia;
  }

  // Limiar de margem de contribuição abaixo do qual o Ponto de Equilíbrio
  // (Custo Fixo ÷ Margem de Contribuição) fica matematicamente instável --
  // dividir por uma margem muito perto de zero faz o resultado disparar pra
  // valores desproporcionais em relação aos outros meses, mesmo estando
  // "certo" pela fórmula. Nesses meses, mostramos um aviso em vez de deixar
  // o número gigante sem contexto.
  const LIMIAR_MARGEM_INSTAVEL = 0.05; // 5%

  const peAtualInfo = insPontoEquilibrio(idxAtual, ultimo.fixo, ultimo.variavel);
  const pontoEquilibrioAtual = peAtualInfo.valor;

  const linhasPontoEquilibrio = custos.serie.map(function (m) {
    const info = insPontoEquilibrio(m.indice, m.fixo, m.variavel);
    const pe = info.valor;
    const margemBaixa = info.margemContribuicao !== null && info.margemContribuicao > 0 && info.margemContribuicao < LIMIAR_MARGEM_INSTAVEL;
    const receitaMes = DRE_RESUMO.receitaBruta[m.indice];
    let statusHtml = '<span class="ins-neutro">—</span>';
    if (pe !== null && receitaMes !== null) {
      statusHtml = receitaMes >= pe
        ? '<span class="ins-status-ok">✓ Atingido</span>'
        : '<span class="ins-status-bad">✗ Não atingido</span>';
    }
    let peTexto = '—';
    if (pe !== null) {
      peTexto = fmtMoeda(pe);
      if (margemBaixa) {
        const margemPct = (info.margemContribuicao * 100).toFixed(1);
        peTexto += ' <span class="ins-status-bad" title="Margem de contribuição de apenas ' + margemPct + '% neste mês -- o cálculo (Custo Fixo ÷ Margem) fica instável e dispara. Não use este valor como referência.">⚠️</span>';
      }
    }
    return '<tr><td>' + m.nome + '</td><td>' + peTexto + '</td><td>' + (receitaMes !== null ? fmtMoeda(receitaMes) : '—') + '</td><td>' + statusHtml + '</td></tr>';
  }).join('');

  // Média simples do Ponto de Equilíbrio e da Receita Real: soma dos meses
  // disponíveis ÷ quantidade de meses. Meses com PE "—" (margem de
  // contribuição negativa, cálculo inválido) ficam de fora da média do PE,
  // mas contam normalmente na média da Receita Real (que é sempre um dado
  // conhecido, independente do PE ter dado certo naquele mês).
  let somaPE = 0, contPE = 0, somaReceitaPE = 0, contReceitaPE = 0;
  custos.serie.forEach(function (m) {
    const info = insPontoEquilibrio(m.indice, m.fixo, m.variavel);
    if (info.valor !== null) { somaPE += info.valor; contPE++; }
    const receitaMes = DRE_RESUMO.receitaBruta[m.indice];
    if (receitaMes !== null && receitaMes !== undefined) { somaReceitaPE += receitaMes; contReceitaPE++; }
  });
  const mediaPE = contPE > 0 ? somaPE / contPE : null;
  const mediaReceitaPE = contReceitaPE > 0 ? somaReceitaPE / contReceitaPE : null;
  const obsMediaPE = contPE < custos.serie.length
    ? ' <span class="ins-neutro" title="Média calculada só com os ' + contPE + ' mês(es) que têm Ponto de Equilíbrio válido, de ' + custos.serie.length + ' mês(es) no total">(' + contPE + '/' + custos.serie.length + ' meses)</span>'
    : '';
  const linhaMediaPontoEquilibrio = '<tr style="border-top:2px solid var(--border)"><td>Média' + obsMediaPE + '</td><td>' +
    (mediaPE !== null ? fmtMoeda(mediaPE) : '—') + '</td><td>' +
    (mediaReceitaPE !== null ? fmtMoeda(mediaReceitaPE) : '—') + '</td><td></td></tr>';

  const custosHtml = '<div class="ins-grid ins-grid-3">' +
    '<div class="ins-card"><div class="ins-kpi-label">Custo Fixo Mensal</div>' +
    '<div class="ins-kpi-value">' + fmtMoeda(ultimo.fixo) + '</div>' +
    '<div class="ins-delta ' + insClasseVariacao(varFixo) + '">' + insFmtPct(varFixo) + ' vs mês anterior</div>' +
    '<table class="ins-mini-table"><thead><tr><th>Mês</th><th>Valor</th></tr></thead><tbody>' + linhasMiniTabelaCusto('fixo') + '</tbody></table></div>' +

    '<div class="ins-card"><div class="ins-kpi-label">Custo Variável Mensal</div>' +
    '<div class="ins-kpi-value">' + fmtMoeda(ultimo.variavel) + '</div>' +
    '<div class="ins-delta ' + insClasseVariacao(varVariavel) + '">' + insFmtPct(varVariavel) + ' vs mês anterior</div>' +
    '<table class="ins-mini-table"><thead><tr><th>Mês</th><th>Valor</th></tr></thead><tbody>' + linhasMiniTabelaCusto('variavel') + '</tbody></table></div>' +

    '<div class="ins-card"><div class="ins-kpi-label">Ponto de Equilíbrio (' + nomeMesAtual + ')</div>' +
    '<div class="ins-kpi-value" style="color:var(--laranja)">' + (pontoEquilibrioAtual !== null ? fmtMoeda(pontoEquilibrioAtual) : '—') + '</div>' +
    '<div class="ins-delta ins-neutro">receita mínima pra cobrir os custos do mês</div>' +
    '<div class="ins-delta ins-neutro" style="margin-top:2px;line-height:1.4">Fórmula: Custo Fixo ÷ Margem de Contribuição, onde Margem de Contribuição = (Receita − Custo Variável) ÷ Receita. Em meses com margem muito baixa (⚠️ na tabela), o cálculo fica instável e dispara — não use esses valores como referência.</div>' +
    '<table class="ins-mini-table"><thead><tr><th>Mês</th><th>Ponto de Equilíbrio</th><th>Receita Real</th><th>Status</th></tr></thead><tbody>' + linhasPontoEquilibrio + linhaMediaPontoEquilibrio + '</tbody></table></div>' +
    '</div>';

  // ---- Comparativo entre empresas ----
  const maiorReceita = comparativo.length ? Math.max.apply(null, comparativo.map(function (c) { return c.receita; })) : 0;
  const coresEmpresa = ['var(--laranja)', 'var(--accent)', '#7c8ee0', '#4ecfa8'];
  const linhasComparativo = comparativo.map(function (c, i) {
    const pct = maiorReceita > 0 ? (c.receita / maiorReceita * 100) : 0;
    const classeResultado = c.resultado >= 0 ? 'ins-up' : 'ins-down';
    const sinal = c.resultado >= 0 ? '+' : '';
    return '<div class="ins-company-row">' +
      '<span class="ins-company-name">' + c.nome + '</span>' +
      '<div class="ins-bar-track"><div class="ins-bar-fill" style="width:' + pct.toFixed(0) + '%;background:' + coresEmpresa[i % coresEmpresa.length] + '">' + fmtMoeda(c.receita) + '</div></div>' +
      '<span class="ins-company-result ' + classeResultado + '">' + sinal + fmtMoeda(c.resultado) + '</span>' +
      '</div>';
  }).join('');
  const comparativoHtml = '<div class="ins-card">' + (linhasComparativo || '<div class="vazio">Sem lançamentos no mês atual.</div>') + '</div>';

  // ---- Indicadores adicionais: Ticket Médio, Comissão % Receita,
  // Capital de Giro, EBITDA Acumulado (YTD) ----
  const receitaAtualInd = DRE_RESUMO.receitaBruta[idxAtual];

  // Ticket Médio = Receita do mês ÷ número de vendas do mês. O nº de
  // vendas só existe pros meses em que ele foi informado manualmente
  // junto com o faturamento (não dá pra derivar isso com segurança dos
  // lançamentos bancários da Omie, porque uma venda parcelada gera vários
  // lançamentos -- contar lançamentos infla o número de "vendas" e
  // subestima o ticket médio). Montamos a série mês a mês (usada tanto
  // pro card fechado -- que mostra a média -- quanto pra tabela que abre
  // ao clicar).
  function insTicketMedioMensal() {
    return insListaMesesAteAtual().map(function (m) {
      const receita = DRE_RESUMO.receitaBruta[m.indice];
      const vendas = DRE_RESUMO.numeroVendas[m.indice];
      const valor = (receita !== null && receita !== undefined && vendas) ? (receita / vendas) : null;
      return { nome: m.nome, indice: m.indice, valor: valor, receita: receita, vendas: vendas };
    });
  }
  const ticketMedioSerie = insTicketMedioMensal();
  const ticketMedioValidos = ticketMedioSerie.filter(function (t) { return t.valor !== null; });
  // Média simples do card fechado: média dos meses que têm nº de vendas informado.
  const ticketMedioMedia = ticketMedioValidos.length > 0
    ? ticketMedioValidos.reduce(function (s, t) { return s + t.valor; }, 0) / ticketMedioValidos.length
    : null;
  // "Total do período" da tabela expandida: soma da receita de TODOS os
  // meses com nº de vendas informado ÷ soma do nº de vendas desses meses
  // -- um ticket médio "combinado" do período, matematicamente correto
  // (não é a mesma coisa que somar as médias mensais, que não faria
  // sentido pra esse tipo de indicador).
  const ticketMedioSomaReceita = ticketMedioValidos.reduce(function (s, t) { return s + t.receita; }, 0);
  const ticketMedioSomaVendas = ticketMedioValidos.reduce(function (s, t) { return s + t.vendas; }, 0);
  const ticketMedioPeriodo = ticketMedioSomaVendas > 0 ? (ticketMedioSomaReceita / ticketMedioSomaVendas) : null;

  // Comissão como % da Receita (Internas + Externas, ambas já automáticas
  // via Omie -- ver DRE_LINHAS 151/152). Mesma lógica de série mês a mês.
  function insComissaoMensal() {
    return insListaMesesAteAtual().map(function (m) {
      const receita = DRE_RESUMO.receitaBruta[m.indice];
      const comInt = DRE_RESUMO.comissoesInternas[m.indice];
      const comExt = DRE_RESUMO.comissoesExternas[m.indice];
      const comissaoTotal = (comInt !== null || comExt !== null) ? (Math.abs(comInt || 0) + Math.abs(comExt || 0)) : null;
      const pct = (comissaoTotal !== null && receita) ? (comissaoTotal / receita * 100) : null;
      return { nome: m.nome, indice: m.indice, pct: pct, comissaoTotal: comissaoTotal, receita: receita };
    });
  }
  const comissaoSerie = insComissaoMensal();
  const comissaoValidos = comissaoSerie.filter(function (c) { return c.pct !== null; });
  const comissaoMedia = comissaoValidos.length > 0
    ? comissaoValidos.reduce(function (s, c) { return s + c.pct; }, 0) / comissaoValidos.length
    : null;
  // Mesmo raciocínio do ticket médio: "Total do período" = soma da
  // comissão em R$ ÷ soma da receita em R$ (não soma das porcentagens
  // mensais, que não teria significado nenhum).
  const comissaoSomaTotal = comissaoValidos.reduce(function (s, c) { return s + c.comissaoTotal; }, 0);
  const comissaoSomaReceita = comissaoValidos.reduce(function (s, c) { return s + c.receita; }, 0);
  const comissaoPctPeriodo = comissaoSomaReceita > 0 ? (comissaoSomaTotal / comissaoSomaReceita * 100) : null;

  // Capital de Giro = Caixa (saldo bancário real, sem cartão) + A Receber
  // (próximos dias configurados) − A Pagar (mesmo período). É uma foto do
  // momento atual, não um valor "do mês fechado" como os outros cards.
  const capitalDeGiro = saude.saldoBancarioAtual + saude.aReceber - saude.aPagar;

  // EBITDA Acumulado no ano (YTD) = soma do EBITDA de Jan até o mês atual.
  let ebitdaYtd = 0, mesesComEbitda = 0;
  for (let i = 0; i <= idxAtual; i++) {
    const v = DRE_RESUMO.ebitda[i];
    if (v !== null && v !== undefined) { ebitdaYtd += v; mesesComEbitda++; }
  }

  // Média simples do nº de vendas/mês, pro card fechado.
  const numeroVendasMedia = ticketMedioValidos.length > 0
    ? ticketMedioValidos.reduce(function (s, t) { return s + t.vendas; }, 0) / ticketMedioValidos.length
    : null;

  const linhasTicketMedio = ticketMedioSerie.map(function (t) {
    return '<tr><td>' + t.nome + '</td><td>' + (t.valor !== null ? fmtMoeda(t.valor) : '—') + '</td><td>' + (t.vendas ? t.vendas : '—') + '</td></tr>';
  }).join('') +
    '<tr style="border-top:2px solid var(--border)"><td>Total do período</td><td>' +
    (ticketMedioPeriodo !== null ? fmtMoeda(ticketMedioPeriodo) : '—') + '</td><td>' +
    (ticketMedioSomaVendas > 0 ? ticketMedioSomaVendas : '—') + '</td></tr>';

  const linhasComissao = comissaoSerie.map(function (c) {
    return '<tr><td>' + c.nome + '</td><td>' + (c.pct !== null ? c.pct.toFixed(1) + '%' : '—') + '</td></tr>';
  }).join('') +
    '<tr style="border-top:2px solid var(--border)"><td>Total do período</td><td>' +
    (comissaoPctPeriodo !== null ? comissaoPctPeriodo.toFixed(1) + '%' : '—') + '</td></tr>';

  // Despesas Fixas como % da Receita -- termômetro clássico de eficiência
  // estrutural: mostra se o custo fixo está crescendo mais rápido que o
  // faturamento (sinal de alerta) ou se a empresa está diluindo melhor os
  // custos fixos conforme cresce. Reaproveita a mesma série de Custo Fixo
  // (custos.serie) já usada na seção Custo Fixo × Variável.
  function insDespesasFixasMensal() {
    return custos.serie.map(function (m) {
      const receita = DRE_RESUMO.receitaBruta[m.indice];
      const pct = (receita) ? (m.fixo / receita * 100) : null;
      return { nome: m.nome, indice: m.indice, pct: pct, fixo: m.fixo, receita: receita };
    });
  }
  const despesasFixasSerie = insDespesasFixasMensal();
  const despesasFixasValidos = despesasFixasSerie.filter(function (d) { return d.pct !== null; });
  const despesasFixasMedia = despesasFixasValidos.length > 0
    ? despesasFixasValidos.reduce(function (s, d) { return s + d.pct; }, 0) / despesasFixasValidos.length
    : null;
  const despesasFixasSomaCusto = despesasFixasValidos.reduce(function (s, d) { return s + d.fixo; }, 0);
  const despesasFixasSomaReceita = despesasFixasValidos.reduce(function (s, d) { return s + d.receita; }, 0);
  const despesasFixasPctPeriodo = despesasFixasSomaReceita > 0 ? (despesasFixasSomaCusto / despesasFixasSomaReceita * 100) : null;
  const linhasDespesasFixas = despesasFixasSerie.map(function (d) {
    return '<tr><td>' + d.nome + '</td><td>' + (d.pct !== null ? d.pct.toFixed(1) + '%' : '—') + '</td></tr>';
  }).join('') +
    '<tr style="border-top:2px solid var(--border)"><td>Total do período</td><td>' +
    (despesasFixasPctPeriodo !== null ? despesasFixasPctPeriodo.toFixed(1) + '%' : '—') + '</td></tr>';

  const indicadoresHtml = '<div class="ins-grid ins-grid-5">' +
    '<div class="ins-card ins-card-expandivel" onclick="insToggleExpandivel(this)">' +
    '<div class="ins-kpi-label">Ticket Médio de Venda (Média) <span class="ins-expandir-seta">▾</span></div>' +
    '<div class="ins-kpi-value">' + (ticketMedioMedia !== null ? fmtMoeda(ticketMedioMedia) : '—') + '</div>' +
    '<div class="ins-delta ins-neutro">' + (ticketMedioValidos.length > 0 ? 'média de ' + numeroVendasMedia.toFixed(1) + ' venda(s)/mês · ' + ticketMedioValidos.length + ' mês(es) com nº de vendas informado' : 'informe o nº de vendas do mês pra calcular') + ' · clique pra detalhar</div>' +
    '<div class="ins-expandivel-corpo" onclick="event.stopPropagation()">' +
    '<table class="ins-mini-table"><thead><tr><th>Mês</th><th>Ticket Médio</th><th>Nº Vendas</th></tr></thead><tbody>' + linhasTicketMedio + '</tbody></table>' +
    '</div></div>' +

    '<div class="ins-card ins-card-expandivel" onclick="insToggleExpandivel(this)">' +
    '<div class="ins-kpi-label">Comissão sobre a Receita (Média) <span class="ins-expandir-seta">▾</span></div>' +
    '<div class="ins-kpi-value">' + (comissaoMedia !== null ? comissaoMedia.toFixed(1) + '%' : '—') + '</div>' +
    '<div class="ins-delta ins-neutro">' + (comissaoValidos.length > 0 ? 'média de ' + comissaoValidos.length + ' mês(es)' : 'sem dado de comissão') + ' · clique pra detalhar</div>' +
    '<div class="ins-expandivel-corpo" onclick="event.stopPropagation()">' +
    '<table class="ins-mini-table"><thead><tr><th>Mês</th><th>% da Receita</th></tr></thead><tbody>' + linhasComissao + '</tbody></table>' +
    '</div></div>' +

    '<div class="ins-card ins-card-expandivel" onclick="insToggleExpandivel(this)">' +
    '<div class="ins-kpi-label">Despesas Fixas / Receita (Média) <span class="ins-expandir-seta">▾</span></div>' +
    '<div class="ins-kpi-value">' + (despesasFixasMedia !== null ? despesasFixasMedia.toFixed(1) + '%' : '—') + '</div>' +
    '<div class="ins-delta ins-neutro">' + (despesasFixasValidos.length > 0 ? 'média de ' + despesasFixasValidos.length + ' mês(es)' : '—') + ' · clique pra detalhar</div>' +
    '<div class="ins-expandivel-corpo" onclick="event.stopPropagation()">' +
    '<table class="ins-mini-table"><thead><tr><th>Mês</th><th>% da Receita</th></tr></thead><tbody>' + linhasDespesasFixas + '</tbody></table>' +
    '</div></div>' +

    '<div class="ins-card"><div class="ins-kpi-label">Capital de Giro (hoje)</div>' +
    '<div class="ins-kpi-value ' + insClasseVariacao(capitalDeGiro) + '">' + fmtMoeda(capitalDeGiro) + '</div>' +
    '<div class="ins-delta ins-neutro">Caixa + A Receber (' + INSIGHTS_CONFIG.diasAReceberPagar + ' dias) − A Pagar (' + INSIGHTS_CONFIG.diasAReceberPagar + ' dias)</div></div>' +

    '<div class="ins-card"><div class="ins-kpi-label">EBITDA Acumulado no Ano</div>' +
    '<div class="ins-kpi-value ' + insClasseVariacao(ebitdaYtd) + '">' + fmtMoeda(ebitdaYtd) + '</div>' +
    '<div class="ins-delta ins-neutro">Jan a ' + nomeMesAtual + ' (' + mesesComEbitda + ' mês(es))</div></div>' +
    '</div>';

  // ---- Alertas automáticos ----
  const linhasTopCategorias = topCategorias.map(function (c) {
    return '<div class="ins-alert-item"><div><div>' + c.categoria + '</div><div style="font-size:10.5px;color:var(--text-faint)">' + insFmtPct(c.variacao) + ' vs mês anterior</div></div><div>' + fmtMoeda(c.valor) + '</div></div>';
  }).join('');

  const deptoHtml = deptoMaiorAlta
    ? '<div class="ins-alert-item ins-warn"><div><div>' + deptoMaiorAlta.nome + '</div><div style="font-size:10.5px;color:var(--text-faint)">' + insFmtPct(deptoMaiorAlta.variacao) + ' vs mês anterior</div></div><div>' + fmtMoeda(deptoMaiorAlta.valor) + '</div></div>'
    : '<div class="vazio">Sem dado suficiente para comparar.</div>';

  const contasBaixasHtml = contasBaixas.length
    ? contasBaixas.map(function (c) {
        return '<div class="ins-alert-item ins-warn"><div><div>' + c.nome + '</div><div style="font-size:10.5px;color:var(--text-faint)">' + c.empresa + '</div></div><div style="color:var(--red)">' + fmtMoeda(c.saldo) + '</div></div>';
      }).join('')
    : '<div class="vazio">Nenhuma conta abaixo de ' + fmtMoeda(INSIGHTS_CONFIG.limiteSaldoBaixo) + '.</div>';

  const alertasHtml = '<div class="ins-grid ins-grid-3">' +
    '<div class="ins-card"><div class="ins-kpi-label">Top 5 categorias de despesa (' + nomeMesAtual + ')</div><div class="ins-alert-list">' + (linhasTopCategorias || '<div class="vazio">Sem despesas no mês.</div>') + '</div></div>' +
    '<div class="ins-card"><div class="ins-kpi-label">Departamento com maior alta</div><div class="ins-alert-list">' + deptoHtml + '</div></div>' +
    '<div class="ins-card"><div class="ins-kpi-label">Contas com saldo baixo (&lt; ' + fmtMoeda(INSIGHTS_CONFIG.limiteSaldoBaixo) + ')</div><div class="ins-alert-list">' + contasBaixasHtml + '</div></div>' +
    '</div>';

  document.getElementById('insightsRoot').innerHTML =
    heroHtml +
    '<h2>📈 Rentabilidade</h2>' + rentabilidadeHtml +
    '<h2>💰 Saúde de Caixa</h2>' + saudeHtml +
    '<h2>📌 Outros Indicadores</h2>' + indicadoresHtml +
    '<h2>🎯 Custo Fixo × Variável & Ponto de Equilíbrio</h2>' + custosHtml +
    '<h2>🏢 Comparativo entre Empresas (' + nomeMesAtual + ')</h2>' + comparativoHtml +
    '<h2>⚠️ Alertas Automáticos</h2>' + alertasHtml;
}

renderizarInsights();
</script>
</div>
"""

def _credenciais(env_key: str, env_secret: str):
    """Lê um par de credenciais do ambiente. Se não estiverem configuradas
    (ainda não criamos os Secrets no GitHub para aquela empresa), retorna
    (None, None) — a empresa correspondente simplesmente não entra na
    busca, sem quebrar o script."""
    key = os.environ.get(env_key)
    secret = os.environ.get(env_secret)
    return (key, secret) if key and secret else (None, None)


BASE_URL = "https://app.omie.com.br/api/v1"
HEADERS = {"Content-Type": "application/json"}

# Período de busca: sempre de 01/01/2026 até 1 ano a partir do dia da execução
PERIODO_INICIAL = "01/01/2026"
PERIODO_FINAL = (datetime.now() + timedelta(days=365)).strftime("%d/%m/%Y")
# Mesma data, em formato ISO (yyyy-mm-dd), usada como valor padrão do campo
# de data "Até" no HTML/JS (inputs type="date" exigem esse formato).
PERIODO_FINAL_ISO = (datetime.now() + timedelta(days=365)).strftime("%Y-%m-%d")

# Logo da Prime Sol (ícone, fundo transparente), embutida como base64 para
# o arquivo continuar sendo um único script — sem depender de outro arquivo
# no repositório.
LOGO_BASE64 = "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"

# ---------------------------------------------------------------------
# Login simples exibido antes do dashboard (proteção "de fachada" -- NÃO é
# segurança real: quem souber usar "Ver código-fonte" no navegador consegue
# ver os dados sem senha, já que tudo roda no navegador da pessoa e o
# repositório continua público. Serve só para impedir que alguém que
# clique no link sem saber nada de programação veja os dados de cara.
# Edite/adicione usuários livremente no formato "usuario": "senha".
# ---------------------------------------------------------------------
USUARIOS_LOGIN = {
    "fabricio": "fabricio1234",
    "ivan": "ivan1234",
    "pwr": "pwr1234",
    "julia": "julia1234",
}

# Todas as empresas do grupo monitoradas. Cada uma tem suas próprias
# credenciais (App Key/Secret) e sua própria lista de contas bancárias e
# cartões. Empresas cujas credenciais ainda não foram configuradas nos
# GitHub Secrets simplesmente não entram na busca (nenhum erro é gerado).
EMPRESAS = []

# --- Prime Sol Matriz (já configurada e em uso) ---
_key, _secret = _credenciais("OMIE_APP_KEY", "OMIE_APP_SECRET")
if _key and _secret:
    EMPRESAS.append({
        "id": "matriz",
        "nome": "Prime Sol Matriz",
        "app_key": _key,
        "app_secret": _secret,
        "contas": [
            {"id": "sicoob_matriz", "nome": "Sicoob Matriz", "tipo": "banco", "nCodCC": 11213271714},
            {"id": "itau_unibanco", "nome": "Itaú Unibanco", "tipo": "banco", "nCodCC": 11214288866},
            {"id": "santander", "nome": "Santander", "tipo": "banco", "nCodCC": 11236843385},
            {"id": "sicredi", "nome": "Sicredi", "tipo": "banco", "nCodCC": 11239054225},
            {"id": "bradesco", "nome": "Bradesco", "tipo": "banco", "nCodCC": 11431467344},
            {"id": "cartao_sicoob", "nome": "Cartão Sicoob", "tipo": "cartao", "nCodCC": 11574015480},
            {"id": "cartao_itau", "nome": "Cartão Itaú", "tipo": "cartao", "nCodCC": 11693512362},
        ],
    })

# --- Prime Sol Lagos ---
_key, _secret = _credenciais("OMIE_APP_KEY_LAGOS", "OMIE_APP_SECRET_LAGOS")
if _key and _secret:
    EMPRESAS.append({
        "id": "lagos",
        "nome": "Prime Sol Lagos",
        "app_key": _key,
        "app_secret": _secret,
        "contas": [
            {"id": "sicoob_lagos", "nome": "Sicoob Lagos", "tipo": "banco", "nCodCC": 10113758384},
            {"id": "mastercard_lagos", "nome": "MasterCard", "tipo": "cartao", "nCodCC": 10453329672},
        ],
    })

# --- Prime Sol Cabo Frio ---
_key, _secret = _credenciais("OMIE_APP_KEY_CABO_FRIO", "OMIE_APP_SECRET_CABO_FRIO")
if _key and _secret:
    EMPRESAS.append({
        "id": "cabo_frio",
        "nome": "Prime Sol Cabo Frio",
        "app_key": _key,
        "app_secret": _secret,
        "contas": [
            {"id": "sicoob_cabo_frio", "nome": "Sicoob CB Frio", "tipo": "banco", "nCodCC": 11471508388},
        ],
    })

# --- PS Energia ---
_key, _secret = _credenciais("OMIE_APP_KEY_PS_ENERGIA", "OMIE_APP_SECRET_PS_ENERGIA")
if _key and _secret:
    EMPRESAS.append({
        "id": "ps_energia",
        "nome": "PS Energia",
        "app_key": _key,
        "app_secret": _secret,
        "contas": [
            {"id": "banco_inter", "nome": "Banco Inter", "tipo": "banco", "nCodCC": 11441838685},
        ],
    })


def chamar_omie(modulo: str, call: str, param: dict, app_key: str, app_secret: str, tentativas: int = 4, espera_fixa: float = None) -> dict:
    """Faz uma chamada genérica à API da Omie, para a empresa (App Key/
    Secret) indicada.

    Inclui uma pequena pausa antes de cada chamada (para não estourar o
    limite de requisições por segundo da Omie) e tenta de novo, com pausas
    crescentes, se der erro temporário (500, 425, 429 ou timeout) — sem
    isso, uma sequência de erros rápidos vira um "efeito cascata" onde a
    Omie passa a bloquear todas as chamadas seguintes.

    Por padrão a espera cresce a cada tentativa (2s, 4s, 8s...). Passando
    `espera_fixa`, todas as tentativas esperam esse tempo fixo em vez de
    crescer -- útil pra chamadas que sabidamente falham com frequência
    (ex: ListarMovimentos pra meses bem no futuro) e não vale a pena
    insistir tanto tempo.
    """
    url = f"{BASE_URL}/{modulo}/"
    payload = {
        "call": call,
        "app_key": app_key,
        "app_secret": app_secret,
        "param": [param],
    }

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        time.sleep(0.4)  # pausa fixa entre chamadas, para não estourar o limite de requisições
        try:
            resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "faultstring" in data:
                raise RuntimeError(f"Erro Omie ({call}): {data['faultstring']}")
            return data
        except (requests.exceptions.HTTPError, requests.exceptions.Timeout) as erro:
            ultimo_erro = erro
            status = getattr(getattr(erro, "response", None), "status_code", None)
            if status in (429, 425, 500, 502, 503, 504) or isinstance(erro, requests.exceptions.Timeout):
                espera = espera_fixa if espera_fixa is not None else 2 ** tentativa  # 2s, 4s, 8s, 16s (ou fixo, se informado)
                print(f"AVISO: erro temporário ({status or 'timeout'}) em {call}, tentativa {tentativa}/{tentativas}. Aguardando {espera}s...")
                time.sleep(espera)
                continue
            raise  # erro que não é de rate-limit/temporário: propaga na hora

    raise ultimo_erro


def gerar_meses(data_inicial: str, data_final: str) -> list:
    """Gera uma lista de (inicio, fim) por mês, no formato dd/mm/aaaa,
    para evitar que uma única chamada ultrapasse o limite de registros
    da Omie (a API não expõe paginação para o extrato)."""
    d_ini = datetime.strptime(data_inicial, "%d/%m/%Y")
    d_fim = datetime.strptime(data_final, "%d/%m/%Y")

    intervalos = []
    atual = d_ini.replace(day=1)
    while atual <= d_fim:
        if atual.month == 12:
            proximo_mes = atual.replace(year=atual.year + 1, month=1, day=1)
        else:
            proximo_mes = atual.replace(month=atual.month + 1, day=1)
        fim_mes = min(proximo_mes - timedelta(days=1), d_fim)
        inicio_intervalo = max(atual, d_ini)
        intervalos.append((
            inicio_intervalo.strftime("%d/%m/%Y"),
            fim_mes.strftime("%d/%m/%Y"),
        ))
        atual = proximo_mes
    return intervalos


def buscar_extrato(nCodCC: int, app_key: str, app_secret: str) -> dict:
    """Busca o extrato completo de uma conta, mês a mês, para evitar
    truncamento pelo limite de 100 registros por chamada da Omie.

    Retorna também o saldo anterior ao período total (necessário para
    calcular o saldo em datas onde ainda não há lançamento no período).
    """
    meses = gerar_meses(PERIODO_INICIAL, PERIODO_FINAL)
    todos_movimentos = []
    saldo_anterior_total = None

    for i, (ini, fim) in enumerate(meses):
        data = chamar_omie(
            "financas/extrato",
            "ListarExtrato",
            {
                "nCodCC": nCodCC,
                "cCodIntCC": "",
                "dPeriodoInicial": ini,
                "dPeriodoFinal": fim,
            },
            app_key,
            app_secret,
        )
        if i == 0:
            saldo_anterior_total = data.get("nSaldoAnterior", 0)
        todos_movimentos.extend(data.get("listaMovimentos", []) or [])

    return {
        "saldoAnterior": saldo_anterior_total or 0,
        "movimentos": todos_movimentos,
    }


def buscar_departamentos_cadastro(app_key: str, app_secret: str) -> dict:
    """Busca a tabela de departamentos cadastrados (código -> nome), da
    empresa correspondente às credenciais informadas. É uma lista pequena
    e estável, buscada uma única vez por empresa. Se falhar, retorna
    vazio — nesse caso os códigos de departamento aparecem "crus" no
    dashboard em vez do nome, mas o script não trava."""
    todos = []
    pagina = 1
    while True:
        try:
            data = chamar_omie(
                "geral/departamentos",
                "ListarDepartamentos",
                {"pagina": pagina, "registros_por_pagina": 100},
                app_key,
                app_secret,
            )
        except Exception as erro:
            print(f"AVISO: falha ao buscar catálogo de departamentos (página {pagina}): {erro}")
            break
        lote = data.get("departamentos", []) or []
        todos.extend(lote)
        total_paginas = data.get("total_de_paginas", 1)
        if pagina >= total_paginas or not lote:
            break
        pagina += 1
    return {d["codigo"]: d["descricao"] for d in todos}


def buscar_movimentos_financeiros(nCodCC: int, nomes_departamento: dict, app_key: str, app_secret: str) -> dict:
    """Busca os Movimentos Financeiros de uma conta (mês a mês, para evitar
    o limite de registros por chamada), pedindo a distribuição por
    departamento. Monta {codigo_lancamento: {"departamento": ..., "observacao": ..., "status": ...}}.

    O código usado como chave do lookup depende se o título já foi pago:
    - Título JÁ PAGO: existe um movimento bancário real, e a Omie expõe o
      código dele em nCodMovCC — que é o mesmo código que aparece no
      extrato como nCodLancamento (ponte confirmada via teste real).
    - Título ainda NÃO PAGO (Previsto/A Vencer): não existe movimento
      bancário real ainda, então nCodMovCC nem vem na resposta. Nesse
      caso, a Omie usa o próprio nCodTitulo como se fosse o nCodLancamento
      na linha de previsão do extrato — então cruzamos por nCodTitulo
      (confirmado testando o título da SOLARMARKET, previsão 10/08/2026).

    A "observacao" aqui é a observação real do TÍTULO (Contas a Pagar/
    Receber) — diferente da observação do extrato bancário, que às vezes
    só traz uma mensagem genérica de importação/integração do banco.

    Se alguma chamada falhar (a Omie retornou erro 500 em algumas
    combinações de conta/mês/página durante os testes), o erro é
    registrado e a busca segue para o próximo mês, em vez de travar o
    script inteiro — melhor ter dados incompletos do que o dashboard
    inteiro não ser publicado.
    """
    lookup = {}
    hoje = datetime.now()
    # Mês/ano "2 meses à frente de hoje" -- a partir daqui, ListarMovimentos
    # historicamente quase sempre retorna 500 (não tem título previsto tão
    # longe), então não vale a pena insistir com o mesmo empenho de um mês
    # real (4 tentativas com espera crescendo até 16s). Reduzimos pra 2
    # tentativas de 5s fixos nesses meses -- ainda tenta (caso exista algum
    # título previsto de verdade lá na frente), mas sem gastar minutos do
    # workflow em retentativas de algo que quase sempre vai falhar mesmo.
    mes_corte = hoje.month + 2
    ano_corte = hoje.year
    while mes_corte > 12:
        mes_corte -= 12
        ano_corte += 1
    data_corte = datetime(ano_corte, mes_corte, 1)

    for ini, fim in gerar_meses(PERIODO_INICIAL, PERIODO_FINAL):
        mes_futuro_distante = datetime.strptime(ini, "%d/%m/%Y") >= data_corte
        tentativas_mes = 2 if mes_futuro_distante else 4
        espera_mes = 5 if mes_futuro_distante else None
        pagina = 1
        while True:
            try:
                data = chamar_omie(
                    "financas/mf",
                    "ListarMovimentos",
                    {
                        "nPagina": pagina,
                        "nRegPorPagina": 100,
                        "nCodCC": nCodCC,
                        "dDtPrevDe": ini,
                        "dDtPrevAte": fim,
                        "cExibirDepartamentos": "S",
                    },
                    app_key,
                    app_secret,
                    tentativas=tentativas_mes,
                    espera_fixa=espera_mes,
                )
            except Exception as erro:
                print(
                    f"AVISO: falha ao buscar movimentos financeiros (conta {nCodCC}, "
                    f"período {ini}-{fim}, página {pagina}): {erro}"
                )
                break

            movimentos = data.get("movimentos", []) or []
            for mov in movimentos:
                detalhes = mov.get("detalhes", {}) or {}
                # Títulos já PAGOS geram um movimento bancário de verdade, e a
                # Omie expõe o código dele em nCodMovCC (que é o que bate com
                # nCodLancamento no extrato). Títulos ainda não pagos (Previsto/
                # A Vencer) não têm movimento bancário real ainda, então esse
                # campo nem vem na resposta — nesse caso, a Omie usa o próprio
                # nCodTitulo como "nCodLancamento" na linha de previsão do
                # extrato, então é por ele que cruzamos (confirmado testando a
                # SOLARMARKET, com previsão em 10/08/2026).
                cod_mov = detalhes.get("nCodMovCC") or detalhes.get("nCodTitulo")
                if not cod_mov:
                    continue

                deptos = mov.get("departamentos") or []
                if not deptos:
                    desc_depto = "-"
                    lista_depto = [{"nome": "-", "percentual": 100}]
                elif len(deptos) == 1:
                    cod_dep = deptos[0].get("cCodDepartamento")
                    nome_unico = nomes_departamento.get(cod_dep, cod_dep or "-")
                    desc_depto = nome_unico
                    lista_depto = [{"nome": nome_unico, "percentual": 100}]
                else:
                    partes = []
                    lista_depto = []
                    for d in deptos:
                        cod_dep = d.get("cCodDepartamento")
                        nome = nomes_departamento.get(cod_dep, cod_dep or "-")
                        pct = d.get("nDistrPercentual", 0)
                        partes.append(f"{nome} ({pct:.0f}%)")
                        lista_depto.append({"nome": nome, "percentual": pct})
                    desc_depto = "; ".join(partes)

                observacao_titulo = (detalhes.get("observacao") or "").strip()
                status_titulo = (detalhes.get("cStatus") or "").strip()
                data_previsao_titulo = (detalhes.get("dDtPrevisao") or "").strip()

                lookup[cod_mov] = {
                    "departamento": desc_depto,
                    "departamentosRateio": lista_depto,
                    "observacao": observacao_titulo,
                    "status": status_titulo,
                    "dataPrevisao": data_previsao_titulo,
                }

            total_paginas = data.get("nTotPaginas", 1)
            if pagina >= total_paginas or not movimentos:
                break
            pagina += 1
    return lookup


def montar_lookup_titulos(contas: list, app_key: str, app_secret: str) -> dict:
    """Monta {nCodMovCC: {"departamento": ..., "observacao": ...}} cruzando
    o cadastro de departamentos com os Movimentos Financeiros de cada
    conta de UMA empresa (identificada pelas credenciais informadas)."""
    nomes_departamento = buscar_departamentos_cadastro(app_key, app_secret)
    lookup_geral = {}
    for conta in contas:
        lookup_conta = buscar_movimentos_financeiros(conta["nCodCC"], nomes_departamento, app_key, app_secret)
        lookup_geral.update(lookup_conta)
    return lookup_geral


def coletar_dados() -> list:
    """Coleta os dados brutos de todas as EMPRESAS e suas contas, sem
    aplicar nenhum filtro. Cada conta resultante carrega também o nome
    da empresa à qual pertence."""
    resultado = []

    for empresa in EMPRESAS:
        contas = empresa["contas"]
        if not contas:
            print(f"AVISO: {empresa['nome']} não tem contas configuradas ainda — pulando.")
            continue

        # O endpoint de Movimentos Financeiros (usado para departamento e
        # situação do título) só funciona para contas bancárias — em todos
        # os testes, contas do tipo cartão retornaram erro 500 nele. Por
        # isso, pulamos os cartões aqui (eles simplesmente ficam com "-"
        # nesses dois campos), economizando um tempo enorme de tentativas
        # que sempre falhariam mesmo.
        contas_bancarias = [c for c in contas if c["tipo"] == "banco"]
        lookup_titulos = montar_lookup_titulos(contas_bancarias, empresa["app_key"], empresa["app_secret"])

        for conta in contas:
            extrato = buscar_extrato(conta["nCodCC"], empresa["app_key"], empresa["app_secret"])
            lancamentos = []
            for m in extrato["movimentos"]:
                # A Omie insere linhas artificiais de "corte" de saldo no meio
                # do extrato (cliente = "SALDO" ou "SALDO ANTERIOR", valor
                # sempre 0) — não são lançamentos de verdade, só marcadores
                # internos. Pulamos essas.
                if (m.get("cDesCliente") or "").strip() in ("SALDO", "SALDO ANTERIOR"):
                    continue

                # Previsão de faturamento que ainda NÃO virou título formal
                # (Ordem de Serviço não faturada, Pedido de Venda não
                # faturado, etc.) não é um compromisso financeiro real ainda
                # -- é só uma projeção. A Omie identifica isso no cOrigem,
                # sempre no formato "Previsão de <tipo>" (ex: "Previsão de
                # O.S. MATRIZ", "Previsão de Pedido de Venda"). Diferente de
                # uma previsão de verdade (título formal já emitido, só
                # ainda não pago -- cOrigem "Conta a Pagar"/"Conta a
                # Receber", que continua aparecendo normalmente), essas não
                # têm título nenhum por trás ainda, então tiramos elas da
                # base inteira em vez de tentar mostrar uma Situação pra
                # elas. Usamos o prefixo genérico "previsão de" (em vez de
                # travar só em "o.s.") pra pegar qualquer variação desse
                # tipo de documento que a Omie use, sem precisar caçar cada
                # uma manualmente (confirmado com dois casos reais: Rosemiro
                # Azevedo da Cruz — "Previsão de O.S. MATRIZ" — e Anderson
                # de Jesus Silva — "Previsão de Pedido de Venda").
                if (m.get("cOrigem") or "").strip().lower().startswith("previsão de"):
                    continue

                cod_mov = m.get("nCodLancamento") or m.get("nCodLancRelac")
                info_titulo = lookup_titulos.get(cod_mov, {})

                # Prioriza a observação real do título (Contas a Pagar/Receber);
                # se não tiver, cai para a observação do extrato bancário (que
                # às vezes só tem uma mensagem genérica de importação/conciliação).
                observacao_titulo = info_titulo.get("observacao", "")
                observacao_extrato = (m.get("cObservacoes") or "").strip()
                observacao_final = observacao_titulo or observacao_extrato or "-"

                categoria_final = m.get("cDesCategoria", "-") or "-"
                # Transferências entre contas do PRÓPRIO grupo (ex: "Transf.
                # Itaú Unibanco >> Sicoob Matriz") não são entrada/saída de
                # dinheiro de verdade — é o mesmo dinheiro só mudando de
                # banco. A Omie classifica isso com uma categoria própria
                # ("Saída de Transferência" / "Entrada de Transferência").
                # Marcamos aqui para o dashboard poder escondê-las da tabela
                # e dos cards de Entradas/Saídas/Departamento, sem afetar o
                # cálculo do saldo real da conta (que usa nSaldo direto da
                # Omie e continua contando esses lançamentos).
                transferencia_interna = "transferência" in categoria_final.strip().lower()

                natureza_raw = (m.get("cNatureza") or "").strip().upper()
                natureza = {"P": "Contas a Pagar", "R": "Contas a Receber"}.get(natureza_raw, "-")

                # A data mostrada na coluna "Data Previsão/Pagamento" precisa
                # bater com a mesma referência usada para calcular a Situação
                # (Atrasado/Vence Hoje/A Vencer), senão dá incoerência tipo
                # "vence hoje" numa data que já passou. Para títulos JÁ
                # PAGOS, o dDataLancamento do extrato é a data real de
                # pagamento/recebimento — mantemos ele. Para títulos ainda
                # NÃO PAGOS, o extrato às vezes usa uma data diferente da
                # previsão real do título (confirmado em 3 casos reais:
                # SOLARMARKET, MITRA e Leilson Batista Siqueira) — nesse
                # caso usamos o dDtPrevisao do ListarMovimentos, que é a
                # mesma data que gera o status.
                status_titulo = info_titulo.get("status") or "-"
                data_conciliacao_titulo = (m.get("dDataConciliacao") or "").strip()
                # Cartão de crédito nunca tem título/status real (pulamos de
                # propósito a busca no ListarMovimentos pra cartão, já que a
                # Omie sempre retorna erro 500 nesse endpoint pra esse tipo
                # de conta). Assumimos "PAGO" para esses lançamentos, já que
                # um gasto que aparece na fatura já é um gasto efetivado —
                # não existe "a vencer"/"previsto" pra cartão como existe
                # pra título bancário.
                if conta["tipo"] == "cartao":
                    status_titulo = "PAGO"
                elif status_titulo == "-" and data_conciliacao_titulo:
                    # Tarifas, taxas e débitos/créditos diretos (ex: "Tarifas
                    # e Serviços Bancários") não passam pelo módulo de
                    # Contas a Pagar/Receber da Omie, então nunca têm título
                    # e o cruzamento por ListarMovimentos nunca vai achar
                    # nada pra eles. Se o lançamento já está conciliado
                    # (Data de Conciliação preenchida), já é um lançamento
                    # real e liquidado — assumimos PAGO (saída) ou RECEBIDO
                    # (entrada) com base na natureza, em vez de deixar "-".
                    status_titulo = "RECEBIDO" if natureza_raw == "R" else "PAGO"
                # TODO: quando Fabrício mandar o JSON bruto de um lançamento
                # tipo "O.S." não faturada (ex: Rosemiro Azevedo da Cruz,
                # 25/05/2026, R$ 850,00), vamos usar o campo que identifica
                # isso pra EXCLUIR esses lançamentos da base inteira (não são
                # título real ainda, só previsão de faturamento) -- em vez de
                # tentar preencher a Situação pra eles.
                data_prevista_titulo = info_titulo.get("dataPrevisao") or ""
                data_extrato = m.get("dDataLancamento", "")
                if status_titulo not in ("PAGO", "RECEBIDO") and data_prevista_titulo:
                    data_exibida = data_prevista_titulo
                else:
                    data_exibida = data_extrato

                lancamentos.append({
                    "data": data_exibida,
                    "cliente": m.get("cDesCliente", "-") or "-",
                    "valor": m.get("nValorDocumento", 0),
                    "situacao": m.get("cSituacao", "-") or "-",
                    "situacaoTitulo": status_titulo,
                    "dataConciliacao": data_conciliacao_titulo,
                    "saldo": m.get("nSaldo", 0),
                    "categoria": categoria_final,
                    "observacao": observacao_final,
                    "departamento": info_titulo.get("departamento", "-"),
                    "departamentosRateio": info_titulo.get("departamentosRateio") or [{"nome": "-", "percentual": 100}],
                    "transferenciaInterna": transferencia_interna,
                    "natureza": natureza,
                })

            resultado.append({
                "id": f"{empresa['id']}_{conta['id']}",
                "nome": conta["nome"],
                "tipo": conta["tipo"],
                "empresa": empresa["nome"],
                "saldoAnterior": extrato["saldoAnterior"],
                "lancamentos": lancamentos,
            })

    return resultado


def formatar_percentual(p):
    if p is None:
        return '-'
    cor = 'style="color:#ff6b6b"' if p < 0 else ''
    return f'<span {cor}>{p:.1f}%</span>'


def formatar_valor_dre_dfc(v):
    if v is None:
        return '<span class="dre-pendente">preencher</span>'
    if v == 0:
        return '-'
    cor = 'style="color:#ff6b6b"' if v < 0 else ''
    valor_fmt = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sinal = "-" if v < 0 else ""
    return f'<span {cor}>{sinal}R$ {valor_fmt}</span>'


def formatar_variacao_percentual(atual, anterior):
    """Variação percentual em relação ao mês anterior (MoM), pra coluna
    'Δ%' de cada mês a partir do 2º mês da tabela. Diferente da coluna
    '%' (Análise Vertical, que compara com a receita/recebimento do
    MESMO mês) -- essa aqui compara a própria linha com ela mesma no
    mês anterior. Sem base (mês anterior nulo/zero) retorna '-'."""
    if atual is None or anterior is None or anterior == 0:
        return '-'
    variacao = (atual - anterior) / abs(anterior) * 100
    sinal = '+' if variacao >= 0 else ''
    cor = 'style="color:#ff6b6b"' if variacao < 0 else 'style="color:#4ade80"'
    valor_fmt = f"{variacao:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f'<span {cor}>Δ {sinal}{valor_fmt}%</span>'


def montar_alerta_nao_classificados_html(itens: list, id_prefixo: str) -> str:
    if not itens:
        return ""
    total_residual = sum(i["valor_residual"] for i in itens)

    def celula_sugestao(i):
        if i.get("sugestao"):
            return f'<td><span style="color:var(--cyan)">💡 {htmllib.escape(i["sugestao"])}</span> <span style="color:var(--text-faint)">({i["sugestao_parecido_pct"]:.0f}% parecido)</span></td>'
        return "<td>—</td>"

    linhas_tabela = "".join(
        f'<tr><td>{i["data"]}</td><td>{htmllib.escape(i["empresa"])}</td>'
        f'<td>{htmllib.escape(i["categoria"])}</td><td>{htmllib.escape(i["departamento"])}</td>'
        f'<td>{htmllib.escape(i["cliente"])}</td>'
        f'<td class="valor">{formatar_valor_dre_dfc(i["valor_residual"])}</td>{celula_sugestao(i)}</tr>'
        for i in itens
    )
    return f"""
    <div class="dre-alerta-nc">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">
        <span>⚠️ <strong>{len(itens)}</strong> lançamento(s) totalizando <strong>{formatar_valor_dre_dfc(total_residual)}</strong>
        não estão batendo com nenhuma linha desta demonstração (categoria/departamento sem linha correspondente,
        ou capturados em mais de uma linha).</span>
        <button type="button" class="ins-btn-alerta" onclick="toggleDetalheInsight('{id_prefixo}ListaNC')">Ver lançamentos</button>
      </div>
      <div class="ins-detail-panel" id="{id_prefixo}ListaNC">
        <div style="padding-top:8px;border-top:1px solid rgba(250,168,33,0.3);margin-top:8px;overflow-x:auto;">
          <table class="drilldown-tabela">
            <thead><tr><th>Data</th><th>Empresa</th><th>Categoria</th><th>Departamento</th><th>Cliente/Fornecedor</th><th>Valor não classificado</th><th>Sugestão de correção</th></tr></thead>
            <tbody>{linhas_tabela}</tbody>
          </table>
        </div>
      </div>
    </div>
    """


def montar_tabela_dre_dfc_html(titulo: str, linhas_config: list, todos_lancamentos: list,
                                meses_dinamicos: list, manual_faturamento: dict,
                                manual_folha: dict, manual_weg: dict, linha_base_pct: int,
                                alerta_html: str = "") -> str:
    linhas = montar_linhas_tabela(
        linhas_config, todos_lancamentos, meses_dinamicos,
        manual_faturamento, manual_folha, manual_weg,
    )
    nomes_meses = montar_nomes_meses(meses_dinamicos)

    # valores da linha-base (Receita Bruta no DRE, Recebimento Operacional no
    # DFC) mes a mes, usados como denominador do "% AV" (Análise Vertical) --
    # igual a planilha original, que tinha uma coluna de % ao lado de cada mês.
    valores_base = next((l["valores"] for l in linhas if l["linha"] == linha_base_pct), None)

    def av_percent(valor, idx):
        if not valores_base or valor is None:
            return None
        base = valores_base[idx]
        if not base:
            return None
        return valor / base * 100

    # Drill-down: só faz sentido clicar em linhas do tipo "omie_categoria"
    # (linha-folha, ligada a UMA categoria da Omie) e só nos meses
    # dinâmicos (Jul/2026 em diante) -- Jan a Jun são cópia manual da
    # planilha antiga, sem lançamento por trás pra mostrar.
    item_por_linha = {item["linha"]: item for item in linhas_config}
    n_estatico = 6  # Jan..Jun

    def montar_celula_valor(linha_num, idx, valor):
        item = item_por_linha.get(linha_num)
        eh_omie_categoria = item and item["classif"]["tipo"] == "omie_categoria"
        if eh_omie_categoria:
            # Jan-Jun sempre 2026 (linhas "estáticas"/manuais copiadas da
            # planilha antiga); a partir daí segue meses_dinamicos.
            if idx < n_estatico:
                ano, mes = 2026, idx + 1
            else:
                ano, mes = meses_dinamicos[idx - n_estatico]
            categoria_alvo = jsonlib.dumps(item["classif"]["categoria"], ensure_ascii=False)
            competencia = jsonlib.dumps(item["classif"]["competencia"], ensure_ascii=False)
            titulo_js = jsonlib.dumps(item["label"], ensure_ascii=False)
            onclick = (
                f"abrirDrillDownDreDfc({linha_num}, {ano}, {mes}, "
                f"{categoria_alvo}, {competencia}, {titulo_js})"
            )
            # As strings acima (categoria/competência/título) vêm de json.dumps,
            # que usa aspas DUPLAS -- e o atributo onclick também é delimitado
            # por aspas duplas. Sem escapar, o navegador fecha o atributo no
            # primeiro " que aparecer (logo no início) e o clique não funciona
            # (o resto vira HTML solto, inválido). html.escape com quote=True
            # troca esses " internos por &quot;, resolvendo o conflito.
            onclick_seguro = htmllib.escape(onclick, quote=True)
            return f'<td class="dre-cell-click" onclick="{onclick_seguro}">{formatar_valor_dre_dfc(valor)}</td>'
        return f"<td>{formatar_valor_dre_dfc(valor)}</td>"

    cabecalho = "".join(
        f"<th>{m}</th><th class=\"dre-col-pct\">%</th><th class=\"dre-col-delta\">Δ%</th>"
        for m in nomes_meses
    )
    linhas_html = []
    for l in linhas:
        nivel = min(l["nivel"], 4)
        # 3 estilos visuais bem diferenciados:
        # - dre-total: linha de resultado/total (soma seções acima dela) --
        #   negrito forte, fundo mais saturado, SEM seta (não recolhe nada).
        # - dre-secao: contêiner real (agrupa linhas abaixo dela) -- negrito
        #   leve, fundo sutil, COM seta clicável.
        # - sem classe: linha de detalhe, só indentada conforme o nível.
        if l["eh_total"]:
            classe = "dre-total"
        elif l["eh_secao"]:
            classe = "dre-secao"
        else:
            classe = ""
        classe += f" dre-nivel-{nivel}"
        atributo_dentro = f' data-dentro="{" ".join(str(s) for s in l["secoes_pai"])}"' if l["secoes_pai"] else ""
        atributo_secao = f' data-secao="{l["linha"]}" onclick="toggleSecaoDreDfc(this)"' if l["eh_secao"] else ""
        marcador = '<span class="dre-toggle">▾</span>' if l["eh_secao"] else ""
        celulas = "".join(
            f"{montar_celula_valor(l['linha'], i, v)}"
            f'<td class="dre-col-pct">{formatar_percentual(av_percent(v, i))}</td>'
            f'<td class="dre-col-delta">'
            f'{formatar_variacao_percentual(v, l["valores"][i - 1] if i > 0 else None)}</td>'
            for i, v in enumerate(l["valores"])
        )
        linhas_html.append(
            f'<tr class="{classe}"{atributo_dentro}{atributo_secao}>'
            f'<td class="dre-label">{marcador}<span class="dre-label-texto">{l["label"]}</span></td>{celulas}</tr>'
        )

    return f"""
    <div class="dre-dfc-wrap">
      <h2>{titulo}</h2>
      {alerta_html}
      <div class="scroll-topo-wrap dre-scroll-topo"><div class="scroll-topo-inner"></div></div>
      <div class="dre-dfc-scroll">
        <table class="dre-dfc-tabela">
          <thead><tr><th>Categoria</th>{cabecalho}</tr></thead>
          <tbody>{''.join(linhas_html)}</tbody>
        </table>
      </div>
    </div>
    """


def gerar_html(contas: list) -> str:
    agora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    dados_json = jsonlib.dumps(contas, ensure_ascii=False)
    de_para_json = jsonlib.dumps(DE_PARA, ensure_ascii=False)

    # --- DRE / DFC: consolida todos os lançamentos de todas as contas (não
    # segue os filtros da aba "Lançamentos" -- é sempre a visão completa do
    # grupo Prime Sol, igual a planilha original) e calcula os meses a
    # partir de Julho/2026. Limitado até Dezembro/2026 por enquanto -- a
    # partir de Janeiro/2027 crescemos pra outra aba quando fizer sentido.
    todos_lancamentos_grupo = [l for c in contas for l in c["lancamentos"]]
    meses_dinamicos = calcular_meses_dinamicos(todos_lancamentos_grupo, 2026, 12)

    # Cópia rasa só pra auditoria de não-classificados poder mostrar de qual
    # empresa é cada lançamento (o dict original de lançamento não carrega
    # esse campo -- é só anexado no JS pro resto do dashboard).
    lancamentos_com_empresa = [dict(l, empresa=c["empresa"]) for c in contas for l in c["lancamentos"]]

    # No DFC, tudo depois de "Posição de Caixa Acumulada" (linha 195) é só
    # conferência interna (Saldo inicial, Entrada/Saída/Líquido de Extratos,
    # Check) -- não faz parte do relatório em si, então não exibimos.
    dfc_linhas_visiveis = [x for x in DFC_LINHAS if x["linha"] <= 195]

    # "Prova dos 9": lançamentos (Jul/2026 em diante) cujo valor não bateu
    # 100% com nenhuma linha do respectivo relatório -- vira um alerta
    # agregado, com lista expansível, logo acima da tabela.
    alerta_dfc = montar_alerta_nao_classificados_html(
        calcular_nao_classificados(dfc_linhas_visiveis, lancamentos_com_empresa, meses_dinamicos), "dfc",
    )
    alerta_dre = montar_alerta_nao_classificados_html(
        calcular_nao_classificados(DRE_LINHAS, lancamentos_com_empresa, meses_dinamicos), "dre",
    )

    html_dfc = montar_tabela_dre_dfc_html(
        "Demonstrativo de Fluxo de Caixa (DFC)", dfc_linhas_visiveis, todos_lancamentos_grupo,
        meses_dinamicos, MANUAL_FATURAMENTO_KIT, MANUAL_FOLHA, MANUAL_WEG, 8, alerta_dfc,
    )
    html_dre = montar_tabela_dre_dfc_html(
        "Demonstração do Resultado do Exercício (DRE)", DRE_LINHAS, todos_lancamentos_grupo,
        meses_dinamicos, MANUAL_FATURAMENTO_KIT, MANUAL_FOLHA, MANUAL_WEG, 6, alerta_dre,
    )

    # --- Dados para a aba Insights -------------------------------------
    # Reaproveita a mesma engine de cálculo da DRE (montar_linhas_tabela)
    # para pegar Receita Bruta, Lucro Bruto, EBITDA e Lucro Líquido mês a
    # mês -- os mesmos números que já aparecem na aba DRE.
    linhas_dre_calc = montar_linhas_tabela(
        DRE_LINHAS, todos_lancamentos_grupo, meses_dinamicos,
        MANUAL_FATURAMENTO_KIT, MANUAL_FOLHA, MANUAL_WEG,
    )
    nomes_meses_insights = montar_nomes_meses(meses_dinamicos)
    dre_resumo_json = jsonlib.dumps(
        montar_resumo_dre(linhas_dre_calc, nomes_meses_insights, meses_dinamicos, MANUAL_FATURAMENTO_KIT),
        ensure_ascii=False,
    )

    diretorio_script = os.path.dirname(os.path.abspath(__file__))
    classificacao_custos_json = jsonlib.dumps(carregar_classificacao_custos(diretorio_script), ensure_ascii=False)

    insights_config_json = jsonlib.dumps({
        "limiteSaldoBaixo": LIMITE_SALDO_BAIXO,
        "mesesRunway": MESES_RUNWAY,
        "diasAReceberPagar": PROXIMOS_DIAS_A_RECEBER_PAGAR,
    }, ensure_ascii=False)

    faturamento_por_empresa_json = jsonlib.dumps(MANUAL_FATURAMENTO_POR_EMPRESA, ensure_ascii=False)

    html_insights = montar_html_insights(dre_resumo_json, classificacao_custos_json, insights_config_json,
                                          faturamento_por_empresa_json)

    usuarios_login_json = jsonlib.dumps(USUARIOS_LOGIN, ensure_ascii=False)

    empresas_unicas = list(dict.fromkeys(c["empresa"] for c in contas))
    checkboxes_empresa_html = "".join(
        f'<label><input type="checkbox" class="chk-empresa" value="{nome}" checked> {nome}</label>'
        for nome in empresas_unicas
    )

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Dashboard Prime Sol — Financeiro</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    color-scheme: dark;
    --bg: #0b1220;
    --bg-panel: #131c2e;
    --bg-card: #17223a;
    --border: #263248;
    --text: #e7ecf5;
    --text-muted: #8b96ab;
    --text-faint: #5b6780;
    --accent: #4169e8;
    --accent-hover: #365bdd;
    --laranja: #faa821;
    --laranja-hover: #e8960f;
    --green: #4ade80;
    --red: #f87171;
    --cyan: #7dd3fc;
    --radius: 12px;
    --radius-sm: 8px;
    --space: 8px;
  }}

  * {{ box-sizing: border-box; }}

  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: var(--bg); color: var(--text); line-height: 1.4; }}

  header {{ padding: 20px 32px; background: var(--bg-panel); border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }}
  .marca {{ display: flex; align-items: center; gap: 14px; }}
  .marca-logo {{ height: 42px; width: auto; }}
  .marca-texto {{ display: flex; flex-direction: column; line-height: 1.2; }}
  .marca-nome {{ font-size: 19px; font-weight: 800; letter-spacing: 0.02em; color: var(--text); }}
  .marca-destaque {{ color: var(--laranja); }}
  .marca-sub {{ font-size: 11.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-faint); margin-top: 2px; }}
  .atualizado {{ color: var(--text-muted); font-size: 12.5px; }}

  .filtros {{
    padding: 20px 32px;
    background: var(--bg-panel);
    border-bottom: 1px solid var(--border);
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, auto));
    gap: 24px;
    align-items: start;
  }}
  .filtro-bloco {{ display: flex; flex-direction: column; gap: 10px; min-height: 62px; }}
  .filtro-bloco h3 {{
    font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em;
    color: var(--text-faint); margin: 0;
  }}
  .grupo-check {{ display: flex; flex-direction: column; gap: 5px; margin-bottom: 8px; font-size: 13px; }}
  .grupo-check:last-child {{ margin-bottom: 0; }}
  .grupo-check strong {{ font-size: 10.5px; color: var(--text-faint); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 2px; }}
  .grupo-check label {{ display: flex; align-items: center; gap: 8px; cursor: pointer; color: var(--text); }}
  .grupo-check input[type="checkbox"] {{ accent-color: var(--accent); width: 14px; height: 14px; }}

  .datas {{ display: flex; gap: 12px; align-items: center; font-size: 12.5px; flex-wrap: wrap; }}
  .datas label {{ display: flex; align-items: center; gap: 6px; color: var(--text-muted); }}
  .datas input, .input-texto, .input-valor {{
    background: var(--bg); border: 1px solid var(--border); color: var(--text);
    border-radius: var(--radius-sm); padding: 7px 9px; font-size: 12.5px;
  }}
  .datas input:focus, .input-texto:focus, .input-valor:focus {{ outline: none; border-color: var(--accent); }}
  .input-texto {{ width: 180px; }}
  .input-valor {{ width: 88px; }}

  .radios {{ display: flex; flex-direction: column; gap: 7px; font-size: 13px; }}
  .radios label {{ display: flex; align-items: center; gap: 8px; cursor: pointer; }}
  .radios input[type="radio"] {{ accent-color: var(--accent); width: 14px; height: 14px; }}

  .botoes-filtro {{ display: flex; gap: 8px; align-self: end; }}

  #btnAplicar {{
    background: var(--accent); color: white; border: none; border-radius: var(--radius-sm);
    padding: 0 22px; height: 40px; font-size: 13px; font-weight: 600; cursor: pointer;
    transition: background .15s ease;
  }}
  #btnAplicar:hover {{ background: var(--accent-hover); }}

  #btnRedefinir {{
    background: transparent; color: var(--text-muted); border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 0 18px; height: 40px; font-size: 13px; font-weight: 600; cursor: pointer;
    transition: all .15s ease;
  }}
  #btnRedefinir:hover {{ background: var(--border); color: var(--text); }}

  .saldos, .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 14px;
    padding: 14px 32px 0;
  }}

  .box-saldo {{
    background: linear-gradient(180deg, #0f2942 0%, #0d2338 100%);
    border: 1px solid #1d4d7a;
    border-radius: var(--radius-sm);
    padding: 10px 12px;
  }}
  .box-saldo .label {{ font-size: 9.5px; font-weight: 700; color: var(--cyan); text-transform: uppercase; letter-spacing: .04em; }}
  .box-saldo .valor-saldo {{ font-size: 16px; font-weight: 800; margin-top: 4px; color: #f0f9ff; letter-spacing: -0.01em; }}
  .box-saldo .ref {{ font-size: 9.5px; color: var(--text-faint); margin-top: 4px; }}

  .abas-nav {{
    display: flex; gap: 4px; padding: 0 32px; border-bottom: 1px solid var(--border);
  }}
  .aba-btn {{
    background: none; border: none; color: var(--text-faint); font-size: 13px; font-weight: 700;
    padding: 10px 18px; cursor: pointer; border-bottom: 2px solid transparent;
  }}
  .aba-btn:hover {{ color: var(--text); }}
  .aba-ativa {{ color: var(--accent) !important; border-bottom: 2px solid var(--accent) !important; }}

  .dre-dfc-wrap {{ padding: 20px 32px 40px; }}
  .dre-dfc-wrap h2 {{ font-size: 16px; margin-bottom: 14px; }}
  .dre-alerta-nc {{
    background: rgba(250,168,33,0.12); border: 1px solid var(--laranja);
    border-radius: var(--radius-sm); padding: 10px 14px; font-size: 12.5px;
    color: var(--laranja); margin-bottom: 14px;
  }}
  .dre-alerta-nc .ins-btn-alerta {{
    background: rgba(250,168,33,0.18); border: 1px solid var(--laranja); color: var(--laranja);
    font-size: 10.5px; font-weight: 700; padding: 4px 10px; border-radius: 5px; cursor: pointer;
  }}
  .dre-alerta-nc .ins-btn-alerta:hover {{ background: rgba(250,168,33,0.32); }}
  .dre-alerta-nc .ins-detail-panel {{ max-height: 0; overflow: hidden; transition: max-height .25s ease; }}
  .dre-alerta-nc .ins-detail-panel.aberto {{ max-height: 420px; overflow-y: auto; margin-top: 12px; }}
  .dre-dfc-scroll {{ overflow-x: auto; overflow-y: auto; max-height: 65vh; border: 1px solid var(--border); border-radius: var(--radius-sm); }}
  .dre-dfc-tabela {{
    border-collapse: collapse; width: auto; min-width: 100%; font-size: 12px;
    white-space: nowrap; table-layout: auto;
  }}
  .dre-dfc-tabela th {{
    background: var(--bg-card); text-align: right; padding: 8px 10px; font-size: 10.5px;
    text-transform: uppercase; color: var(--text); font-weight: 700; position: sticky; top: 0;
    border-bottom: 2px solid var(--laranja);
  }}
  .dre-dfc-tabela th:first-child {{ text-align: left; position: sticky; left: 0; z-index: 2; }}
  .dre-dfc-tabela td {{ padding: 6px 10px; text-align: right; border-top: 1px solid var(--border); }}
  .dre-dfc-tabela td.dre-label {{
    text-align: left; position: sticky; left: 0; background: var(--bg-card); z-index: 1;
    padding-left: 10px;
  }}
  /* Linha de TOTAL/RESULTADO (soma seções acima dela): negrito forte,
     fundo mais saturado, sem seta -- é só leitura, não recolhe nada. */
  .dre-dfc-tabela tr.dre-total {{ font-weight: 800; background: rgba(54,91,221,0.10); }}
  .dre-dfc-tabela tr.dre-total td.dre-label {{ background: #16223f; }}
  /* Linha de SEÇÃO (contêiner real, agrupa linhas abaixo dela): destaque
     mais leve que o total, pra não competir visualmente -- e com seta. */
  .dre-dfc-tabela tr.dre-secao {{ font-weight: 700; background: rgba(250,168,33,0.05); }}
  .dre-dfc-tabela tr.dre-secao td.dre-label {{ background: #131d38; }}
  .dre-dfc-tabela tr.dre-secao:hover td.dre-label {{ background: #182444; }}
  /* Indentação por nível de aninhamento -- dá a noção de hierarquia que
     antes não existia (seção de 1º nível e sub-seção pareciam iguais). */
  .dre-dfc-tabela tr.dre-nivel-1 td.dre-label {{ padding-left: 26px; }}
  .dre-dfc-tabela tr.dre-nivel-2 td.dre-label {{ padding-left: 42px; }}
  .dre-dfc-tabela tr.dre-nivel-3 td.dre-label {{ padding-left: 58px; }}
  .dre-dfc-tabela tr.dre-nivel-4 td.dre-label {{ padding-left: 74px; }}
  .dre-toggle {{
    display: inline-block; width: 14px; margin-right: 2px; text-align: center;
    color: var(--laranja); font-size: 10px; cursor: pointer; user-select: none;
    transition: transform 0.18s ease;
  }}
  .dre-dfc-tabela tr[data-secao] {{ cursor: pointer; }}
  .dre-dfc-tabela tr[data-secao].dre-secao-fechada .dre-toggle {{ transform: rotate(-90deg); }}
  /* Recolher/expandir com fade em vez de sumir/aparecer seco: a opacidade
     anima primeiro; só depois de terminar a transição a linha sai do fluxo
     (display:none), senão o table-layout "pula" sem suavidade nenhuma. */
  .dre-dfc-tabela tbody tr {{ transition: opacity 0.16s ease; opacity: 1; }}
  .dre-dfc-tabela tbody tr.dre-colapsada {{ opacity: 0; }}
  .dre-dfc-tabela tbody tr.dre-oculta {{ display: none; }}
  .dre-col-pct {{ font-size: 10.5px; color: var(--text-faint); min-width: 50px; }}
  .dre-col-delta {{ font-size: 10.5px; min-width: 60px; }}
  .dre-cell-click {{ cursor: pointer; transition: background .1s, outline-color .1s; }}
  .dre-cell-click:hover {{ background: rgba(250,168,33,0.14); outline: 1px solid var(--laranja); outline-offset: -1px; }}

  #drillDownOverlay {{
    position: fixed; inset: 0; z-index: 9998; background: rgba(0,0,0,0.55);
    display: none; align-items: center; justify-content: center; padding: 24px;
  }}
  #drillDownOverlay.aberto {{ display: flex; }}
  .drilldown-caixa {{
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius);
    width: 100%; max-width: 1300px; max-height: 85vh; display: flex; flex-direction: column;
    overflow: hidden;
  }}
  .drilldown-cabecalho {{
    display: flex; justify-content: space-between; align-items: flex-start;
    padding: 16px 20px; border-bottom: 1px solid var(--border);
  }}
  .drilldown-cabecalho h3 {{ margin: 0 0 4px 0; font-size: 15px; }}
  .drilldown-cabecalho .sub {{ font-size: 12px; color: var(--text-muted); }}
  .drilldown-fechar {{
    background: none; border: 1px solid var(--border); color: var(--text);
    border-radius: var(--radius-sm); width: 28px; height: 28px; cursor: pointer; font-size: 14px;
  }}
  .drilldown-fechar:hover {{ border-color: var(--laranja); color: var(--laranja); }}
  .drilldown-corpo {{ overflow-y: auto; overflow-x: hidden; padding: 0 20px 20px; }}
  .drilldown-tabela {{ border-collapse: collapse; font-size: 12.5px; margin-top: 12px; }}
  .drilldown-tabela th {{
    text-align: left; padding: 6px 22px 6px 10px; color: var(--text-muted); font-weight: 600;
    border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--bg-panel);
    position: relative; white-space: nowrap;
  }}
  .drilldown-tabela td {{ padding: 6px 10px; border-top: 1px solid var(--border); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .drilldown-tabela td.valor {{ text-align: right; font-weight: 600; }}
  .drilldown-total {{ font-weight: 800; background: rgba(54,91,221,0.08); }}
  .drilldown-vazio {{ padding: 24px 0; text-align: center; color: var(--text-muted); font-size: 13px; }}
  .drilldown-aviso {{
    margin-top: 12px; padding: 10px 12px; border-radius: var(--radius-sm);
    background: rgba(250,168,33,0.12); border: 1px solid rgba(250,168,33,0.4);
    font-size: 12px; color: var(--laranja);
  }}
  .dre-pendente {{ color: var(--cyan); font-style: italic; font-size: 10.5px; }}
  .dd-tag-rateio {{
    display: inline-block; margin-left: 6px; padding: 1px 6px; border-radius: 4px;
    background: rgba(65,105,232,0.18); border: 1px solid var(--accent); color: var(--accent-hover);
    font-size: 9.5px; font-weight: 700; text-transform: uppercase; cursor: help;
  }}

  .saldo-total-wrap {{ padding: 14px 32px 0; }}
  .saldo-total-linha {{ display: flex; gap: 14px; align-items: stretch; flex-wrap: wrap; }}
  #saldoOntemContainer {{ flex: 0 0 240px; }}
  #saldoTotalContainer {{ flex: 1; min-width: 260px; }}

  .card-saldo-ontem {{
    background: linear-gradient(180deg, #0f2942 0%, #0d2338 100%);
    border: 1px solid #1d4d7a;
    border-radius: var(--radius-sm);
    padding: 13px 18px;
    height: 100%;
    display: flex; flex-direction: column; justify-content: center;
  }}
  .card-saldo-ontem .label {{
    font-size: 11px; font-weight: 800; color: var(--cyan); text-transform: uppercase; letter-spacing: .05em;
  }}
  .card-saldo-ontem .ref {{ font-size: 10px; color: var(--text-faint); margin-top: 3px; }}
  .card-saldo-ontem .valor-saldo-ontem {{ font-size: 20px; font-weight: 800; color: #f0f9ff; letter-spacing: -0.01em; margin-top: 4px; }}

  .card-saldo-total {{
    background: linear-gradient(135deg, #1a2f14 0%, #16321f 60%, #0f2942 100%);
    border: 1px solid #2d6a3e;
    border-radius: var(--radius-sm);
    padding: 13px 18px;
    height: 100%;
    display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px;
  }}
  .card-saldo-total .label {{
    font-size: 11px; font-weight: 800; color: #6ee7a0; text-transform: uppercase; letter-spacing: .05em;
  }}
  .card-saldo-total .ref {{ font-size: 10px; color: var(--text-faint); margin-top: 3px; }}
  .card-saldo-total .valor-saldo-total {{ font-size: 23px; font-weight: 800; color: #eafff2; letter-spacing: -0.01em; }}

  .card {{
    background: var(--bg-card); border-radius: var(--radius-sm); padding: 10px 12px;
    border: 1px solid var(--border); display: flex; flex-direction: column;
  }}
  .card .label {{
    font-size: 9.5px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;
    letter-spacing: .04em; margin-bottom: 6px; padding-bottom: 5px; border-bottom: 1px solid var(--border);
  }}
  .linha-valor {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; padding: 1px 0; }}
  .mini-label {{ font-size: 10px; color: var(--text-faint); }}
  .valor {{ font-size: 12px; font-weight: 700; }}
  .valor.entrada {{ color: var(--green); }}
  .valor.saida {{ color: var(--red); }}
  .valor.saldo {{ color: var(--text); font-size: 13.5px; }}
  .card .qtd {{ font-size: 9.5px; color: var(--text-faint); margin-top: 6px; padding-top: 5px; border-top: 1px solid var(--border); }}

  .card-departamento-wrap {{ padding: 0 32px; margin-top: 14px; }}
  .card-departamento {{ max-width: none; padding-top: 0; cursor: pointer; transition: border-color 0.15s; }}
  .card-departamento:hover {{ border-color: var(--laranja); }}
  .card-departamento .label {{ margin-bottom: 8px; }}
  .depto-resumo {{ font-size: 13px; color: var(--text); margin-top: 2px; }}
  .depto-resumo strong {{ color: var(--laranja); }}
  .depto-resumo-sub {{ font-size: 11.5px; color: var(--text-faint); margin-top: 4px; display: flex; align-items: center; gap: 6px; }}
  .depto-seta {{ display: inline-block; font-size: 10px; transition: transform 0.2s; }}
  .card-departamento.aberto .depto-seta {{ transform: rotate(180deg); }}
  .depto-corpo {{ display: none; margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border); cursor: default; }}
  .card-departamento.aberto .depto-corpo {{ display: block; }}
  .linha-depto {{ display: flex; align-items: center; gap: 12px; padding: 6px 0; font-size: 13px; }}
  .linha-depto .nome-depto {{ flex: 0 0 200px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .linha-depto .barra-wrap {{ flex: 1; background: var(--bg); border-radius: 4px; height: 8px; overflow: hidden; }}
  .linha-depto .barra {{ height: 100%; background: var(--accent); border-radius: 4px; }}
  .linha-depto .pct-depto {{ flex: 0 0 52px; text-align: right; font-weight: 700; color: var(--text); }}
  .linha-depto .valor-depto {{ flex: 0 0 110px; text-align: right; color: var(--text-faint); font-size: 12px; }}

  section {{ padding: 12px 32px 40px; }}
  h2 {{
    font-size: 14.5px; font-weight: 700; color: var(--text); border-bottom: 1px solid var(--border);
    padding-bottom: 10px; margin: 32px 0 14px;
  }}
  section > div:first-child h2 {{ margin-top: 8px; }}

  .tabela-wrap {{ overflow-x: auto; border-radius: var(--radius-sm); }}
  #tabelaWrap {{ overflow-y: auto; max-height: 65vh; border: 1px solid var(--border); }}
  #tabelaWrap th {{
    position: sticky; top: 0; z-index: 2; background: var(--bg-panel);
    border-bottom: 2px solid var(--laranja); color: var(--text); font-weight: 700;
  }}
  .scroll-topo-wrap {{ overflow-x: auto; overflow-y: hidden; height: 14px; margin-bottom: 2px; }}
  .scroll-topo-inner {{ height: 1px; }}
  table {{ table-layout: fixed; border-collapse: collapse; margin-bottom: 8px; font-size: 12.5px; background: var(--bg-panel); }}
  th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  th {{ position: relative; color: var(--text-muted); font-weight: 600; white-space: nowrap; font-size: 11.5px; text-transform: uppercase; letter-spacing: .03em; }}
  th .th-ordenavel {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; padding: 2px 4px; border-radius: 4px; }}
  th .th-ordenavel:hover {{ background: var(--border); color: var(--text); }}
  th .th-ordenavel.ativo {{ color: var(--laranja); }}
  tr:last-child td {{ border-bottom: none; }}
  tr.linha-total td {{ font-weight: 700; color: var(--text); background: var(--bg-panel); border-top: 2px solid var(--border); border-bottom: none; }}
  tr.linha-total .valor-total {{ color: var(--cyan); }}
  .vazio {{ color: var(--text-faint); font-size: 13px; padding: 16px 0; }}

  .resize-handle {{ position: absolute; top: 0; right: -4px; width: 14px; height: 100%; cursor: col-resize; user-select: none; z-index: 5; }}
  .resize-handle:hover, .resize-handle.resizando {{ background: var(--accent); opacity: 0.5; }}

  .btn-filtro-col {{ background: none; border: none; color: var(--text-faint); cursor: pointer; font-size: 11px; padding: 2px 5px; border-radius: 4px; margin-left: 3px; vertical-align: middle; }}
  .btn-filtro-col:hover {{ background: var(--border); color: var(--text); }}
  .btn-filtro-col.ativo {{ color: var(--green); font-weight: 700; }}

  .btn-largura {{ background: var(--border); border: none; color: var(--text); cursor: pointer; font-size: 11px; width: 16px; height: 16px; line-height: 16px; border-radius: 3px; padding: 0; margin-left: 2px; vertical-align: middle; }}
  .btn-largura:hover {{ background: var(--accent); }}

  .dropdown-filtro {{ position: fixed; background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 10px; z-index: 1000; width: 240px; box-shadow: 0 12px 32px rgba(0,0,0,0.5); }}
  .dropdown-busca {{ width: 100%; box-sizing: border-box; background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 6px; padding: 6px 8px; font-size: 12px; margin-bottom: 8px; }}
  .dropdown-busca:focus {{ outline: none; border-color: var(--accent); }}
  .dropdown-acoes {{ display: flex; justify-content: space-between; margin-bottom: 6px; }}
  .dropdown-acoes button {{ background: none; border: none; color: var(--cyan); font-size: 11px; cursor: pointer; padding: 2px 4px; }}
  .dropdown-acoes button:hover {{ text-decoration: underline; }}
  .dropdown-lista {{ max-height: 220px; overflow-y: auto; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); padding: 6px 0; margin-bottom: 8px; }}
  .dropdown-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; padding: 4px 3px; cursor: pointer; border-radius: 4px; }}
  .dropdown-item:hover {{ background: var(--bg); }}
  .dropdown-item input {{ accent-color: var(--accent); }}
  .dropdown-ok {{ width: 100%; background: var(--accent); color: white; border: none; border-radius: 6px; padding: 7px; font-size: 12px; font-weight: 600; cursor: pointer; }}
  .dropdown-ok:hover {{ background: var(--accent-hover); }}

  /* Tela de login (proteção de fachada, ver comentário de USUARIOS_LOGIN no Python) */
  body.nao-autenticado > *:not(#loginOverlay):not(script) {{ display: none !important; }}
  #loginOverlay {{
    position: fixed; inset: 0; z-index: 9999;
    background: var(--bg); display: flex; align-items: center; justify-content: center;
  }}
  .login-caixa {{
    background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 36px 34px; width: 320px; max-width: 90vw; text-align: center;
  }}
  .login-caixa img {{ height: 44px; margin-bottom: 14px; }}
  .login-caixa h1 {{ font-size: 16px; margin: 0 0 4px 0; }}
  .login-caixa .login-sub {{ font-size: 12px; color: var(--text-muted); margin-bottom: 20px; }}
  .login-caixa input {{
    width: 100%; box-sizing: border-box; background: var(--bg); border: 1px solid var(--border);
    color: var(--text); border-radius: var(--radius-sm); padding: 10px 12px; font-size: 13px;
    margin-bottom: 10px;
  }}
  .login-caixa input:focus {{ outline: none; border-color: var(--accent); }}
  .login-caixa button {{
    width: 100%; background: var(--laranja); color: #1a1a1a; border: none; border-radius: var(--radius-sm);
    padding: 10px; font-size: 13px; font-weight: 700; cursor: pointer; margin-top: 6px;
  }}
  .login-caixa button:hover {{ background: var(--laranja-hover); }}
  .login-erro {{ color: var(--red); font-size: 12px; margin-top: 10px; min-height: 16px; }}
</style>
</head>
<body class="nao-autenticado">
<div id="loginOverlay">
  <div class="login-caixa">
    <img src="data:image/png;base64,{LOGO_BASE64}" alt="Prime Sol">
    <h1>PRIME SOL</h1>
    <div class="login-sub">Acesso restrito</div>
    <form id="formLogin">
      <input type="text" id="loginUsuario" placeholder="Usuário" autocomplete="username" required>
      <input type="password" id="loginSenha" placeholder="Senha" autocomplete="current-password" required>
      <button type="submit">Entrar</button>
      <div class="login-erro" id="loginErro"></div>
    </form>
  </div>
</div>
<script>
  const USUARIOS_LOGIN = {usuarios_login_json};

  // Algumas visualizações restritas de navegador (ex: previews sandboxadas)
  // bloqueiam sessionStorage e lançam erro ao tentar usá-lo. Isso é só um
  // "lembrete" de login pra não pedir de novo a cada recarregada -- não é
  // essencial, então qualquer falha aqui é ignorada silenciosamente em vez
  // de travar o restante do script (o formulário de login continua
  // funcionando normalmente mesmo sem essa memória entre recarregadas).
  function tentarLerLoginSalvo() {{
    try {{
      return sessionStorage.getItem('primeSolLogado') === '1';
    }} catch (erro) {{
      return false;
    }}
  }}

  function tentarSalvarLogin() {{
    try {{
      sessionStorage.setItem('primeSolLogado', '1');
    }} catch (erro) {{
      // ambiente bloqueou o armazenamento -- sem problema, só não vai
      // lembrar o login na próxima recarregada da página.
    }}
  }}

  if (tentarLerLoginSalvo()) {{
    document.body.classList.remove('nao-autenticado');
    document.getElementById('loginOverlay').style.display = 'none';
  }}

  document.getElementById('formLogin').addEventListener('submit', function (e) {{
    e.preventDefault();
    const usuario = document.getElementById('loginUsuario').value.trim();
    const senha = document.getElementById('loginSenha').value;
    if (USUARIOS_LOGIN[usuario] && USUARIOS_LOGIN[usuario] === senha) {{
      tentarSalvarLogin();
      document.body.classList.remove('nao-autenticado');
      document.getElementById('loginOverlay').style.display = 'none';
    }} else {{
      document.getElementById('loginErro').textContent = 'Usuário ou senha incorretos.';
    }}
  }});
</script>
<header>
  <div class="marca">
    <img src="data:image/png;base64,{LOGO_BASE64}" alt="Prime Sol" class="marca-logo">
    <div class="marca-texto">
      <div class="marca-nome">PRIME <span class="marca-destaque">SOL</span></div>
      <div class="marca-sub">Dashboard Financeiro</div>
    </div>
  </div>
  <div class="atualizado">Última atualização: {agora} · Dados brutos coletados de {PERIODO_INICIAL} até {PERIODO_FINAL}</div>
</header>

<div class="abas-nav">
  <button class="aba-btn aba-ativa" data-aba="Lancamentos" onclick="mostrarAba('Lancamentos')">📋 Lançamentos</button>
  <button class="aba-btn" data-aba="DFC" onclick="mostrarAba('DFC')">💵 DFC</button>
  <button class="aba-btn" data-aba="DRE" onclick="mostrarAba('DRE')">📊 DRE</button>
  <button class="aba-btn" data-aba="Insights" onclick="mostrarAba('Insights')">💡 Insights</button>
</div>

<div id="abaLancamentos">
<div class="filtros">
  <div class="filtro-bloco">
    <h3>Empresas</h3>
    <div class="grupo-check">
      {checkboxes_empresa_html}
    </div>
  </div>

  <div class="filtro-bloco">
    <h3>Tipo</h3>
    <div class="grupo-check">
      <label><input type="checkbox" class="chk-tipo" value="Contas a Pagar" checked> Contas a Pagar</label>
      <label><input type="checkbox" class="chk-tipo" value="Contas a Receber" checked> Contas a Receber</label>
    </div>
  </div>

  <div class="filtro-bloco">
    <h3>Período (data de previsão/pagamento)</h3>
    <div class="datas">
      <label>De: <input type="date" id="dataDe" value="2026-01-01"></label>
      <label>Até: <input type="date" id="dataAte" value="{PERIODO_FINAL_ISO}"></label>
    </div>
  </div>

  <div class="filtro-bloco">
    <h3>Data de Conciliação</h3>
    <div class="datas">
      <label>De: <input type="date" id="conciliDe"></label>
      <label>Até: <input type="date" id="conciliAte"></label>
    </div>
  </div>

  <div class="filtro-bloco">
    <h3>Cliente / Fornecedor</h3>
    <input type="text" id="filtroCliente" placeholder="Buscar por nome..." class="input-texto">
  </div>

  <div class="filtro-bloco">
    <h3>Valor</h3>
    <div class="datas">
      <label>Mín: <input type="number" id="valorMin" step="0.01" placeholder="-9999" class="input-valor"></label>
      <label>Máx: <input type="number" id="valorMax" step="0.01" placeholder="9999" class="input-valor"></label>
    </div>
  </div>

  <div class="botoes-filtro">
    <button id="btnAplicar">Aplicar filtros</button>
    <button id="btnRedefinir" type="button">Redefinir filtros</button>
  </div>
</div>

<div class="saldo-total-wrap">
  <div class="saldo-total-linha">
    <div id="saldoOntemContainer"></div>
    <div id="saldoTotalContainer"></div>
  </div>
</div>

<div class="saldos" id="saldosContainer"></div>

<div class="cards" id="cardsContainer"></div>

<div class="card-departamento-wrap">
  <div class="card card-departamento" id="cardDepartamentoContainer"></div>
</div>

<section id="secoesContainer"></section>

<script>
const DADOS = {dados_json};
const DRE_DE_PARA = {de_para_json};
const CLASSIFICACAO_CUSTOS_LANC = {classificacao_custos_json};

// ---- Drill-down DRE/DFC: clique numa célula de valor (Jul/2026 em diante,
// só nas linhas ligadas a UMA categoria da Omie) pra ver os lançamentos que
// compõem aquele número. Espelha a MESMA lógica de soma do
// engine_dre_dfc.py (normalizar_categoria + mes_efetivo), senão a lista
// mostrada não bateria com o valor exibido na tabela.
function dreNormalizarCategoria(bruta) {{
  const c = (bruta || '').trim();
  return Object.prototype.hasOwnProperty.call(DRE_DE_PARA, c) ? DRE_DE_PARA[c] : c;
}}

function dreMesEfetivo(dataBr, competencia) {{
  const iso = paraDataISO(dataBr);
  if (!iso) return null;
  let [ano, mes] = iso.split('-').map(Number);
  if (competencia === 'Mês Anterior') {{
    mes -= 1;
    if (mes === 0) {{ mes = 12; ano -= 1; }}
  }}
  return {{ ano, mes }};
}}

// ---- Rateio por departamento: MESMA lógica de engine_dre_dfc.py
// (_chave_comparacao, dividir_categoria_composta, _base_e_sufixo,
// _capturado_para_linha). Sem isso, o drill-down não encontrava os
// lançamentos de linhas tipo "Vale Alimentação - MOI" etc -- que não têm
// essa categoria "crua" na Omie, e sim a categoria BASE ("Vale
// Alimentação") ratada entre departamentos, capturada só em parte por
// cada linha. Ver o comentário equivalente em engine_dre_dfc.py.
const DD_DEPARTAMENTO_OMIE_PARA_SUFIXO = {{
  'Comercial': 'Comercial',
  'Comercial Externo': 'Comercial',
  'Comercial Interno': 'Comercial',
  'Administrativo': 'ADM',
  'Mão de Obra Direta - MOD': 'MOD',
  'Mão de Obra Indireta - MOI': 'MOI',
  'Corporativo': 'Corporativo',
}};
const DD_SUFIXOS_DEPARTAMENTO = new Set(['MOD', 'MOI', 'ADM', 'Comercial', 'Corporativo']);

function ddChaveComparacao(s) {{
  return (s || '').toString().trim().toLowerCase()
    .normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')
    .replace(/\\s+/g, ' ');
}}

function ddSufixoDepartamento(nomeDepartamento) {{
  const chave = ddChaveComparacao(nomeDepartamento);
  for (const nomeOmie in DD_DEPARTAMENTO_OMIE_PARA_SUFIXO) {{
    if (ddChaveComparacao(nomeOmie) === chave) return DD_DEPARTAMENTO_OMIE_PARA_SUFIXO[nomeOmie];
  }}
  return null;
}}

function ddBaseESufixo(categoriaLinha) {{
  const idx = categoriaLinha.lastIndexOf(' - ');
  if (idx === -1) return [null, null];
  const base = categoriaLinha.slice(0, idx);
  const sufixo = categoriaLinha.slice(idx + 3);
  return DD_SUFIXOS_DEPARTAMENTO.has(sufixo) ? [base, sufixo] : [null, null];
}}

function ddDividirCategoriaComposta(categoriaBruta) {{
  const bruta = (categoriaBruta || '').trim();
  if (!bruta) return [['-', 1.0]];
  let partesBrutas = bruta.split(';').map(p => p.trim()).filter(Boolean);
  if (!partesBrutas.length) partesBrutas = [bruta];

  const partesComPct = partesBrutas.map(parte => {{
    const semInativa = parte.replace(/\\s*\\(inativ[ao]\\)/i, '').trim();
    const m = semInativa.match(/\\(([\\d.,]+)\\s*%\\)\\s*$/);
    if (m) {{
      const pctStr = m[1];
      const pct = pctStr.includes(',') ? parseFloat(pctStr.replace(/\\./g, '').replace(',', '.')) : parseFloat(pctStr);
      const categoriaLimpa = semInativa.replace(/\\(([\\d.,]+)\\s*%\\)\\s*$/, '').trim();
      return [categoriaLimpa, pct];
    }}
    return [semInativa, null];
  }});

  const temAlgumPct = partesComPct.some(p => p[1] !== null);
  let pesos;
  if (temAlgumPct) {{
    const somaPct = partesComPct.reduce((s, p) => s + (p[1] !== null ? p[1] : 0), 0);
    const nSemPct = partesComPct.filter(p => p[1] === null).length;
    const pctRestante = nSemPct ? Math.max(0, 100 - somaPct) / nSemPct : 0;
    pesos = partesComPct.map(p => [p[0], (p[1] !== null ? p[1] : pctRestante) / 100]);
  }} else {{
    const pesoIgual = 1.0 / partesComPct.length;
    pesos = partesComPct.map(p => [p[0], pesoIgual]);
  }}
  return pesos.map(p => [dreNormalizarCategoria(p[0]), p[1]]);
}}

function ddCapturadoParaLinha(l, categoriaLinha, baseSemDepartamento, sufixoAlvo) {{
  const valorTotal = l.valor || 0;
  const chaveCategoriaLinha = ddChaveComparacao(categoriaLinha);
  const chaveBase = baseSemDepartamento ? ddChaveComparacao(baseSemDepartamento) : null;
  let capturado = 0;
  ddDividirCategoriaComposta(l.categoria).forEach(function (par) {{
    const catNorm = par[0], peso = par[1];
    const valorParte = valorTotal * peso;
    const chaveCat = ddChaveComparacao(catNorm);
    if (chaveCat === chaveCategoriaLinha) {{
      capturado += valorParte;
    }} else if (sufixoAlvo && chaveCat === chaveBase) {{
      const rateio = (l.departamentosRateio && l.departamentosRateio.length)
        ? l.departamentosRateio
        : [{{ nome: l.departamento || '-', percentual: 100 }}];
      rateio.forEach(function (d) {{
        if (ddSufixoDepartamento(d.nome) === sufixoAlvo) {{
          capturado += valorParte * (d.percentual || 0) / 100;
        }}
      }});
    }}
  }});
  return capturado;
}}

// Mesma ideia de ddCapturadoParaLinha, mas SEM filtrar por departamento --
// soma a fração inteira da categoria BASE (todos os setores somados).
// Usada só pra mostrar, no aviso de rateio, o valor total da CONTA na
// Omie (ex: "FGTS" somando Comercial+ADM+MOD+MOI+Corporativo), já que a
// linha da tabela mostra só a fatia de um setor.
function ddCapturadoContaTotal(l, categoriaLinha, baseSemDepartamento) {{
  const valorTotal = l.valor || 0;
  const chaveCategoriaLinha = ddChaveComparacao(categoriaLinha);
  const chaveBase = baseSemDepartamento ? ddChaveComparacao(baseSemDepartamento) : null;
  let capturado = 0;
  ddDividirCategoriaComposta(l.categoria).forEach(function (par) {{
    const catNorm = par[0], peso = par[1];
    const chaveCat = ddChaveComparacao(catNorm);
    if (chaveCat === chaveCategoriaLinha || chaveCat === chaveBase) {{
      capturado += valorTotal * peso;
    }}
  }});
  return capturado;
}}

// ---- Tabela interativa do modal de drill-down (DRE/DFC) -- mesmas
// funcionalidades da tabela de Lançamentos (ordenar, filtro estilo Excel,
// redimensionar colunas, rolagem dupla), mas com estado próprio (prefixo
// dd*) pra não interferir na tabela principal, já que os nomes de coluna
// e os dados de origem são diferentes.
const DD_COLUNAS = [
  {{ key: 'data', label: 'Data', get: l => l.data || '-' }},
  {{ key: 'empresa', label: 'Empresa', get: l => l.empresaNome || '-' }},
  {{ key: 'conta', label: 'Banco/Cartão', get: l => l.contaNome || '-' }},
  {{ key: 'cliente', label: 'Cliente/Fornecedor', get: l => l.cliente || '-' }},
  {{ key: 'departamento', label: 'Departamento', get: l => l.departamento || '-' }},
  {{ key: 'observacao', label: 'Observação', get: l => l.observacao || '-' }},
  {{ key: 'valor', label: 'Valor', get: l => fmtMoeda(l._valorCapturado !== undefined ? l._valorCapturado : (l.valor || 0)) }},
];
const DD_LARGURA_PADRAO = {{
  data: 110, empresa: 150, conta: 140, cliente: 190, departamento: 130, observacao: 260, valor: 120,
}};
let ddColFiltros = {{}};
let ddColWidths = {{}};
let ddOrdenacao = {{ coluna: null, direcao: null }};
let ddResizando = null;
let ddDadosBase = [];   // todos os lançamentos do mês/categoria atual (antes do filtro de coluna)
let ddTituloAtual = '';
let ddAvisoManualAtual = '';

function ddValorOrdenacao(l, colKey) {{
  switch (colKey) {{
    case 'valor': return l._valorCapturado !== undefined ? l._valorCapturado : (l.valor || 0);
    case 'data': return paraDataISO(l.data) || '';
    case 'cliente': return (l.cliente || '').toLowerCase();
    case 'departamento': return (l.departamento || '').toLowerCase();
    case 'conta': return (l.contaNome || '').toLowerCase();
    case 'empresa': return (l.empresaNome || '').toLowerCase();
    case 'observacao': return (l.observacao || '').toLowerCase();
    default: return '';
  }}
}}

function ddAplicarFiltrosColuna(lista) {{
  return lista.filter(l => DD_COLUNAS.every(col => {{
    const permitidos = ddColFiltros[col.key];
    if (!permitidos) return true;
    return permitidos.has(col.get(l));
  }}));
}}

function ddFecharDropdowns() {{
  document.querySelectorAll('.dropdown-filtro').forEach(d => d.remove());
}}

function ddAbrirDropdownColuna(col, btnEl) {{
  ddFecharDropdowns();
  const selecionadosAtuais = ddColFiltros[col.key];
  const todosValores = [...new Set(ddDadosBase.map(col.get))].sort();

  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown-filtro';
  const rect = btnEl.getBoundingClientRect();
  dropdown.style.top = (rect.bottom + 4) + 'px';
  dropdown.style.left = Math.min(rect.left, window.innerWidth - 260) + 'px';

  dropdown.innerHTML = `
    <input type="text" class="dropdown-busca" placeholder="Buscar...">
    <div class="dropdown-acoes">
      <button type="button" class="dropdown-todos">Selecionar tudo</button>
      <button type="button" class="dropdown-nenhum">Limpar</button>
    </div>
    <div class="dropdown-lista"></div>
    <button type="button" class="dropdown-ok">Aplicar</button>
  `;

  const lista = dropdown.querySelector('.dropdown-lista');
  function montarLista(filtroTexto) {{
    const termo = (filtroTexto || '').toLowerCase();
    lista.innerHTML = todosValores
      .filter(v => String(v).toLowerCase().includes(termo))
      .map(v => {{
        const marcado = !selecionadosAtuais || selecionadosAtuais.has(v);
        return `<label class="dropdown-item"><input type="checkbox" value="${{String(v).replace(/"/g, '&quot;')}}" ${{marcado ? 'checked' : ''}}> ${{v || '(vazio)'}}</label>`;
      }}).join('');
  }}
  montarLista('');

  dropdown.querySelector('.dropdown-busca').addEventListener('input', (e) => montarLista(e.target.value));
  dropdown.querySelector('.dropdown-todos').addEventListener('click', () => {{
    lista.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = true);
  }});
  dropdown.querySelector('.dropdown-nenhum').addEventListener('click', () => {{
    lista.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = false);
  }});
  dropdown.querySelector('.dropdown-ok').addEventListener('click', () => {{
    const marcados = new Set(Array.from(lista.querySelectorAll('input[type=checkbox]:checked')).map(c => c.value));
    if (marcados.size === todosValores.length) {{
      delete ddColFiltros[col.key];
    }} else {{
      ddColFiltros[col.key] = marcados;
    }}
    ddFecharDropdowns();
    ddRenderizarTabela();
  }});

  document.body.appendChild(dropdown);
}}

function ddRenderizarTabela() {{
  const corpo = document.getElementById('drillDownCorpo');
  let filtrados = ddAplicarFiltrosColuna(ddDadosBase);

  if (ddOrdenacao.coluna) {{
    const dir = ddOrdenacao.direcao === 'asc' ? 1 : -1;
    filtrados = [...filtrados].sort((a, b) => {{
      const va = ddValorOrdenacao(a, ddOrdenacao.coluna);
      const vb = ddValorOrdenacao(b, ddOrdenacao.coluna);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    }});
  }} else {{
    filtrados = [...filtrados].sort((a, b) => (a.data || '').localeCompare(b.data || ''));
  }}

  document.getElementById('drillDownSubtitulo').textContent =
    ddTituloAtual + ` — ${{filtrados.length}} de ${{ddDadosBase.length}} lançamento(s)`;

  if (!ddDadosBase.length) {{
    corpo.innerHTML = ddAvisoManualAtual + '<div class="drilldown-vazio">Nenhum lançamento encontrado pra esse mês/categoria.</div>';
    return;
  }}
  if (!filtrados.length) {{
    corpo.innerHTML = ddAvisoManualAtual + '<div class="drilldown-vazio">Nenhum lançamento bate com os filtros de coluna atuais.</div>';
    return;
  }}

  const total = filtrados.reduce((s, l) => s + (l._valorCapturado !== undefined ? l._valorCapturado : (l.valor || 0)), 0);

  const headerHtml = DD_COLUNAS.map(col => {{
    const ativo = ddColFiltros[col.key] ? 'ativo' : '';
    const largura = ddColWidths[col.key] || DD_LARGURA_PADRAO[col.key] || 150;
    const ordenandoEsta = ddOrdenacao.coluna === col.key;
    const seta = ordenandoEsta ? (ddOrdenacao.direcao === 'asc' ? ' ▲' : ' ▼') : '';
    const classeOrdenacao = ordenandoEsta ? 'th-ordenavel ativo' : 'th-ordenavel';
    return `<th style="width:${{largura}}px">
      <span class="${{classeOrdenacao}}" data-col="${{col.key}}" title="Clique para ordenar">${{col.label}}${{seta}}</span>
      <button type="button" class="btn-filtro-col ${{ativo}}" data-col="${{col.key}}">▾</button>
      <button type="button" class="btn-largura" data-col="${{col.key}}" data-delta="-20" title="Diminuir largura">−</button>
      <button type="button" class="btn-largura" data-col="${{col.key}}" data-delta="20" title="Aumentar largura">+</button>
      <div class="resize-handle" data-col="${{col.key}}"></div>
    </th>`;
  }}).join('');

  const larguraTotalTabela = DD_COLUNAS.reduce(
    (soma, col) => soma + (ddColWidths[col.key] || DD_LARGURA_PADRAO[col.key] || 150), 0
  );

  const linhasHtml = filtrados.map(l => {{
    const valorExibido = l._valorCapturado !== undefined ? l._valorCapturado : (l.valor || 0);
    const marcaRateio = l._ehRateio
      ? ` <span class="dd-tag-rateio" title="Este lançamento é rateado por departamento -- valor mostrado é só a fração desta linha. Valor total do lançamento: ${{fmtMoeda(l.valor || 0)}}">rateado</span>`
      : '';
    return `
    <tr>
      <td>${{l.data || '-'}}</td>
      <td>${{l.empresaNome || '-'}}</td>
      <td>${{l.contaNome || '-'}}</td>
      <td>${{l.cliente || '-'}}</td>
      <td>${{l.departamento || '-'}}${{marcaRateio}}</td>
      <td class="celula-obs" title="${{(l.observacao || '').replace(/"/g, '&quot;')}}">${{l.observacao || '-'}}</td>
      <td class="valor">${{fmtMoeda(valorExibido)}}</td>
    </tr>
  `;
  }}).join('');

  corpo.innerHTML = ddAvisoManualAtual + `
    <div class="scroll-topo-wrap" id="ddScrollTopoWrap">
      <div class="scroll-topo-inner" id="ddScrollTopoInner"></div>
    </div>
    <div class="tabela-wrap" id="ddTabelaWrap">
      <table class="drilldown-tabela" style="width:${{larguraTotalTabela}}px">
        <thead><tr>${{headerHtml}}</tr></thead>
        <tbody>
          ${{linhasHtml}}
          <tr class="drilldown-total">
            <td colspan="6">Total (${{filtrados.length}} lançamento(s))</td>
            <td class="valor">${{fmtMoeda(total)}}</td>
          </tr>
        </tbody>
      </table>
    </div>
  `;

  const scrollTopoWrap = document.getElementById('ddScrollTopoWrap');
  const scrollTopoInner = document.getElementById('ddScrollTopoInner');
  const tabelaWrapEl = document.getElementById('ddTabelaWrap');
  scrollTopoInner.style.width = larguraTotalTabela + 'px';
  let ddSincronizando = false;
  scrollTopoWrap.addEventListener('scroll', () => {{
    if (ddSincronizando) return;
    ddSincronizando = true;
    tabelaWrapEl.scrollLeft = scrollTopoWrap.scrollLeft;
    ddSincronizando = false;
  }});
  tabelaWrapEl.addEventListener('scroll', () => {{
    if (ddSincronizando) return;
    ddSincronizando = true;
    scrollTopoWrap.scrollLeft = tabelaWrapEl.scrollLeft;
    ddSincronizando = false;
  }});
}}

// Delegação de clique (ordenar / filtro / largura) dentro do modal.
// Presa no "document" (não no #drillDownCorpo) de propósito: esse <div>
// só é criado mais adiante no HTML, e nesse ponto do script ele ainda não
// existe -- getElementById retornaria null e quebraria a página inteira.
// O filtro closest('#drillDownCorpo') garante que só reage a cliques
// dentro do modal, sem interferir na tabela de Lançamentos.
document.addEventListener('click', (e) => {{
  if (!e.target.closest('#drillDownCorpo')) return;

  const thOrdenavel = e.target.closest('.th-ordenavel');
  if (thOrdenavel) {{
    const colKey = thOrdenavel.dataset.col;
    if (ddOrdenacao.coluna !== colKey) {{
      ddOrdenacao = {{ coluna: colKey, direcao: 'asc' }};
    }} else if (ddOrdenacao.direcao === 'asc') {{
      ddOrdenacao = {{ coluna: colKey, direcao: 'desc' }};
    }} else {{
      ddOrdenacao = {{ coluna: null, direcao: null }};
    }}
    ddRenderizarTabela();
    return;
  }}

  const btnLargura = e.target.closest('.btn-largura');
  if (btnLargura) {{
    const colKey = btnLargura.dataset.col;
    const delta = parseInt(btnLargura.dataset.delta, 10);
    const larguraAtual = ddColWidths[colKey] || DD_LARGURA_PADRAO[colKey] || 150;
    ddColWidths[colKey] = Math.max(60, larguraAtual + delta);
    ddRenderizarTabela();
    return;
  }}

  const btn = e.target.closest('.btn-filtro-col');
  if (!btn) return;
  const colKey = btn.dataset.col;
  const col = DD_COLUNAS.find(c => c.key === colKey);
  if (col) ddAbrirDropdownColuna(col, btn);
}});

// Redimensionamento por arraste (mesma lógica da tabela principal, com
// estado ddResizando próprio pra não colidir com o "resizando" dela).
// Mesmo motivo acima: escutando no document, filtrando por closest().
document.addEventListener('mousedown', (e) => {{
  if (!e.target.closest('#drillDownCorpo')) return;
  const handle = e.target.closest('.resize-handle');
  if (!handle) return;
  e.preventDefault();
  const th = handle.closest('th');
  ddResizando = {{ colKey: handle.dataset.col, startX: e.clientX, startWidth: th.offsetWidth, th, handle }};
  handle.classList.add('resizando');
  document.body.style.cursor = 'col-resize';
}});
document.addEventListener('mousemove', (e) => {{
  if (!ddResizando) return;
  const delta = e.clientX - ddResizando.startX;
  const novaLargura = Math.max(60, ddResizando.startWidth + delta);
  ddResizando.th.style.width = novaLargura + 'px';
}});
document.addEventListener('mouseup', () => {{
  if (!ddResizando) return;
  ddColWidths[ddResizando.colKey] = ddResizando.th.offsetWidth;
  ddResizando.handle.classList.remove('resizando');
  ddResizando = null;
  document.body.style.cursor = '';
  ddRenderizarTabela();
}});

function abrirDrillDownDreDfc(linha, ano, mes, categoriaAlvo, competencia, tituloLinha) {{
  const todos = insTodosLancamentos();
  const [baseSemDepartamento, sufixoAlvo] = ddBaseESufixo(categoriaAlvo);

  const ddNoPeriodo = function (l) {{
    if (l.transferenciaInterna) return false;
    const eff = dreMesEfetivo(l.data, competencia);
    return !!eff && eff.ano === ano && eff.mes === mes;
  }};

  const filtrados = todos
    .filter(function (l) {{
      if (!ddNoPeriodo(l)) return false;
      const capturado = ddCapturadoParaLinha(l, categoriaAlvo, baseSemDepartamento, sufixoAlvo);
      return Math.abs(capturado) > 0.01;
    }})
    .map(function (l) {{
      const capturado = ddCapturadoParaLinha(l, categoriaAlvo, baseSemDepartamento, sufixoAlvo);
      const bateDireto = ddChaveComparacao(dreNormalizarCategoria(l.categoria)) === ddChaveComparacao(categoriaAlvo);
      return Object.assign({{}}, l, {{ _valorCapturado: capturado, _ehRateio: !bateDireto }});
    }});

  const nomesMes = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
  const ehManual = (ano < 2026) || (ano === 2026 && mes < 7);
  document.getElementById('drillDownTitulo').textContent = tituloLinha;

  ddTituloAtual = `${{nomesMes[mes - 1]}}/${{ano}} — categoria "${{categoriaAlvo}}"`;

  const avisos = [];
  if (ehManual) {{
    avisos.push('<div class="drilldown-aviso">⚠️ O valor dessa célula foi digitado manualmente (cópia da planilha antiga) — a soma dos lançamentos abaixo vem direto da Omie e pode não bater exatamente com ele.</div>');
  }}
  if (sufixoAlvo) {{
    const totalRateado = filtrados.reduce((s, l) => s + (l._valorCapturado || 0), 0);
    // Total da CONTA na Omie somando TODOS os setores (não só o desta
    // linha) -- percorre "todos" (não "filtrados", que já veio restrito
    // ao setor alvo) pra pegar as frações que caíram em outros departamentos.
    const totalConta = todos
      .filter(ddNoPeriodo)
      .reduce((s, l) => s + ddCapturadoContaTotal(l, categoriaAlvo, baseSemDepartamento), 0);
    avisos.push(
      '<div class="drilldown-aviso">ℹ️ Esta conta é rateada por departamento: a categoria na Omie é '
      + `"<strong>${{baseSemDepartamento}}</strong>", dividida entre setores (Comercial/ADM/MOD/MOI/Corporativo) `
      + `conforme o rateio de cada lançamento. `
      + `Valor total da conta no mês (todos os setores): <strong>${{fmtMoeda(totalConta)}}</strong>. `
      + `Abaixo só a fração "${{sufixoAlvo}}" de cada lançamento — total desta linha: <strong>${{fmtMoeda(totalRateado)}}</strong>. `
      + 'A coluna "Departamento" mostra o rateio completo (todos os setores) de cada lançamento.</div>'
    );
  }}
  ddAvisoManualAtual = avisos.join('');
  ddDadosBase = filtrados;
  ddColFiltros = {{}};
  ddColWidths = {{}};
  ddOrdenacao = {{ coluna: null, direcao: null }};
  ddRenderizarTabela();

  document.getElementById('drillDownOverlay').classList.add('aberto');
}}

function fecharDrillDownDreDfc() {{
  document.getElementById('drillDownOverlay').classList.remove('aberto');
}}

// Definição das colunas da tabela, usadas tanto para montar o cabeçalho
// quanto para o filtro estilo Excel de cada coluna.
// ---- Classificação Custo Fixo/Variável, usada na coluna da tabela de
// Lançamentos. Duplicada (não reaproveitada) da mesma lógica usada na aba
// Insights porque aquele bloco de script só é carregado mais adiante na
// página -- copiamos aqui a parte mínima necessária (normalizar nome de
// categoria + procurar nas 3 listas do classificacao_custos.json).
function lancNormalizarCategoria(categoria) {{
  if (!categoria) return '-';
  let c = String(categoria).trim();
  c = c.replace(/\\s*\\([\\d.,]+\\s*%\\)/g, '');
  c = c.replace(/\\s*\\(inativ[ao]\\)/gi, '');
  c = c.replace(/\\s{{2,}}/g, ' ').replace(/\\s*,\\s*,/g, ',').replace(/^\\s*,\\s*|\\s*,\\s*$/g, '');
  return c.trim() || '-';
}}
const LANC_CONJUNTO_FIXO = new Set((CLASSIFICACAO_CUSTOS_LANC.fixo || []).map(c => lancNormalizarCategoria(c).toLowerCase()));
const LANC_CONJUNTO_VARIAVEL = new Set((CLASSIFICACAO_CUSTOS_LANC.variavel || []).map(c => lancNormalizarCategoria(c).toLowerCase()));
const LANC_CONJUNTO_IGNORAR = new Set((CLASSIFICACAO_CUSTOS_LANC.ignorar || []).map(c => lancNormalizarCategoria(c).toLowerCase()));

function lancClassificarCusto(l) {{
  // Só faz sentido classificar SAÍDAS (despesas) -- entradas (receita) não
  // são "custo fixo" nem "variável". "Não se Aplica" em vez de "-" pra não
  // confundir com "Ignorado" (que é uma classificação de despesa de
  // verdade) na hora de filtrar essa coluna.
  if (l.valor >= 0) return 'Não se Aplica';
  // A Omie às vezes junta categorias no mesmo lançamento, separadas por
  // "; " (ex: "Insumos para Obras; Material de Escritório"). Pra uma
  // única célula da tabela, classificamos pela primeira categoria da
  // lista -- é uma simplificação da mesma regra usada (com rateio) na
  // aba Insights, mas suficiente pra filtrar/visualizar na tabela.
  const partes = lancNormalizarCategoria(l.categoria).split(';').map(p => p.trim()).filter(Boolean);
  const primeira = (partes[0] || '-').toLowerCase();
  if (LANC_CONJUNTO_FIXO.has(primeira)) return 'Custo Fixo';
  if (LANC_CONJUNTO_VARIAVEL.has(primeira)) return 'Custo Variável';
  if (LANC_CONJUNTO_IGNORAR.has(primeira)) return 'Ignorado';
  return 'Não Classificado';
}}

const COLUNAS = [
  {{ key: 'situacaoTitulo', label: 'Situação', get: l => l.situacaoTitulo }},
  {{ key: 'data', label: 'Data Previsão/Pagamento', get: l => l.data }},
  {{ key: 'empresa', label: 'Empresa', get: l => l.empresaNome }},
  {{ key: 'cliente', label: 'Cliente/Fornecedor', get: l => l.cliente }},
  {{ key: 'valor', label: 'Valor', get: l => fmtMoeda(l.valor) }},
  {{ key: 'categoria', label: 'Categoria', get: l => l.categoria }},
  {{ key: 'departamento', label: 'Departamento', get: l => l.valor >= 0 ? 'Não se Aplica' : (l.departamento || '-') }},
  {{ key: 'custoFixoVariavel', label: 'Custo Fixo/Variável', get: l => lancClassificarCusto(l) }},
  {{ key: 'conta', label: 'Banco/Cartão', get: l => l.contaNome }},
  {{ key: 'observacao', label: 'Observação', get: l => l.observacao }},
  {{ key: 'dataConciliacao', label: 'Data Conciliação', get: l => l.dataConciliacao || '-' }},
];

// Estado dos filtros de coluna (estilo Excel): chave "coluna" -> Set de valores permitidos.
// Ausência da chave = nenhum filtro ativo nessa coluna (mostra tudo).
let colFiltros = {{}};
// Guarda, a cada renderização, a lista combinada (todas as contas selecionadas)
// já filtrada pelo painel superior, usada para montar as opções de cada dropdown.
let baseFiltradosGlobal = [];

// Larguras de coluna (redimensionáveis pelo usuário, arrastando a borda do cabeçalho).
// Chave "coluna" -> largura em px. Sem entrada = usa o padrão abaixo.
let colWidths = {{}};
const LARGURA_PADRAO = {{
  situacaoTitulo: 130, conta: 150, empresa: 160, data: 150, cliente: 200, valor: 110, categoria: 160,
  observacao: 240, dataConciliacao: 150, custoFixoVariavel: 150,
}};
let resizando = null;

// Estado de ordenação da tabela: null = ordem natural (como veio da Omie).
let ordenacao = {{ coluna: null, direcao: null }};

// Se o card "Distribuição por Departamento" está expandido -- guardado
// fora da função de render porque esse card é reconstruído do zero toda
// vez que um filtro muda (senão fecharia sozinho a cada filtro novo).
let departamentoAberto = false;

/** Extrai o valor "comparável" de um lançamento para uma coluna, usado na
 * ordenação — números continuam números, datas viram AAAA-MM-DD (ordena
 * cronologicamente certo), e texto vira minúsculo (ordena A-Z ignorando
 * maiúscula/minúscula). */
function valorOrdenacao(l, colKey) {{
  switch (colKey) {{
    case 'valor': return l.valor;
    case 'data': return paraDataISO(l.data) || '';
    case 'dataConciliacao': return paraDataISO(l.dataConciliacao) || '';
    case 'cliente': return (l.cliente || '').toLowerCase();
    case 'categoria': return (l.categoria || '').toLowerCase();
    case 'departamento': return (l.departamento || '').toLowerCase();
    case 'conta': return (l.contaNome || '').toLowerCase();
    case 'empresa': return (l.empresaNome || '').toLowerCase();
    case 'observacao': return (l.observacao || '').toLowerCase();
    case 'situacaoTitulo': return (l.situacaoTitulo || '').toLowerCase();
    case 'custoFixoVariavel': return (lancClassificarCusto(l) || '').toLowerCase();
    default: return '';
  }}
}}

function paraDataISO(brDate) {{
  if (!brDate) return null;
  const partes = brDate.split('/');
  if (partes.length !== 3) return null;
  return `${{partes[2]}}-${{partes[1]}}-${{partes[0]}}`;
}}

function fmtMoeda(v) {{
  return 'R$ ' + v.toLocaleString('pt-BR', {{minimumFractionDigits: 2, maximumFractionDigits: 2}});
}}

function fmtDataBR(iso) {{
  if (!iso) return '';
  const [ano, mes, dia] = iso.split('-');
  return `${{dia}}/${{mes}}/${{ano}}`;
}}

/**
 * Calcula o saldo real da conta na data informada (dataAteISO), usando
 * TODOS os lançamentos da conta (não filtrados por conciliação), já que
 * o saldo bancário existe independente de já ter sido conciliado ou não.
 */
function calcularSaldoNaData(conta, dataAteISO) {{
  const ordenados = [...conta.lancamentos]
    .map(l => ({{...l, _iso: paraDataISO(l.data)}}))
    .filter(l => l._iso)
    .sort((a, b) => a._iso.localeCompare(b._iso));

  let saldo = conta.saldoAnterior;
  for (const l of ordenados) {{
    if (dataAteISO && l._iso > dataAteISO) break;
    saldo = l.saldo;
  }}
  return saldo;
}}

function empresasSelecionadas() {{
  return Array.from(document.querySelectorAll('.chk-empresa:checked')).map(el => el.value);
}}

function tiposSelecionados() {{
  return Array.from(document.querySelectorAll('.chk-tipo:checked')).map(el => el.value);
}}

function aplicarFiltrosColuna(lista) {{
  return lista.filter(l => {{
    return COLUNAS.every(col => {{
      const permitidos = colFiltros[col.key];
      if (!permitidos) return true;
      return permitidos.has(col.get(l));
    }});
  }});
}}

function fecharDropdowns() {{
  document.querySelectorAll('.dropdown-filtro').forEach(d => d.remove());
}}

document.addEventListener('click', (e) => {{
  if (!e.target.closest('.dropdown-filtro') && !e.target.closest('.btn-filtro-col')) {{
    fecharDropdowns();
  }}
}});

function abrirDropdownColuna(col, btnEl) {{
  fecharDropdowns();
  const selecionadosAtuais = colFiltros[col.key];
  const valoresUnicos = [...new Set(baseFiltradosGlobal.map(col.get))].sort();

  const dropdown = document.createElement('div');
  dropdown.className = 'dropdown-filtro';
  const rect = btnEl.getBoundingClientRect();
  dropdown.style.top = (rect.bottom + 4) + 'px';
  dropdown.style.left = Math.min(rect.left, window.innerWidth - 260) + 'px';

  dropdown.innerHTML = `
    <input type="text" class="dropdown-busca" placeholder="Buscar...">
    <div class="dropdown-acoes">
      <button type="button" class="dropdown-todos">Selecionar tudo</button>
      <button type="button" class="dropdown-nenhum">Limpar</button>
    </div>
    <div class="dropdown-lista"></div>
    <button type="button" class="dropdown-ok">Aplicar</button>
  `;

  const lista = dropdown.querySelector('.dropdown-lista');

  function renderLista(filtroTexto) {{
    lista.innerHTML = '';
    valoresUnicos
      .filter(v => !filtroTexto || (v || '').toLowerCase().includes(filtroTexto.toLowerCase()))
      .forEach(v => {{
        const marcado = !selecionadosAtuais || selecionadosAtuais.has(v);
        const item = document.createElement('label');
        item.className = 'dropdown-item';
        const valorEscapado = (v || '').replace(/"/g, '&quot;');
        item.innerHTML = `<input type="checkbox" value="${{valorEscapado}}" ${{marcado ? 'checked' : ''}}> <span>${{v || '(vazio)'}}</span>`;
        lista.appendChild(item);
      }});
  }}
  renderLista('');

  dropdown.querySelector('.dropdown-busca').addEventListener('input', (ev) => renderLista(ev.target.value));
  dropdown.querySelector('.dropdown-todos').addEventListener('click', () => {{
    dropdown.querySelectorAll('.dropdown-item input').forEach(chk => chk.checked = true);
  }});
  dropdown.querySelector('.dropdown-nenhum').addEventListener('click', () => {{
    dropdown.querySelectorAll('.dropdown-item input').forEach(chk => chk.checked = false);
  }});
  dropdown.querySelector('.dropdown-ok').addEventListener('click', () => {{
    const marcados = Array.from(dropdown.querySelectorAll('.dropdown-item input:checked')).map(c => c.value);
    if (marcados.length === valoresUnicos.length) {{
      delete colFiltros[col.key];
    }} else {{
      colFiltros[col.key] = new Set(marcados);
    }}
    fecharDropdowns();
    renderizar();
  }});

  document.body.appendChild(dropdown);
}}

function renderizar() {{
  const dataDe = document.getElementById('dataDe').value;
  const dataAte = document.getElementById('dataAte').value;
  const conciliDe = document.getElementById('conciliDe').value;
  const conciliAte = document.getElementById('conciliAte').value;
  const textoCliente = document.getElementById('filtroCliente').value.trim().toLowerCase();
  const valorMinStr = document.getElementById('valorMin').value;
  const valorMaxStr = document.getElementById('valorMax').value;
  const valorMin = valorMinStr === '' ? null : parseFloat(valorMinStr);
  const valorMax = valorMaxStr === '' ? null : parseFloat(valorMaxStr);

  const cardsContainer = document.getElementById('cardsContainer');
  const saldosContainer = document.getElementById('saldosContainer');
  const secoesContainer = document.getElementById('secoesContainer');
  cardsContainer.innerHTML = '';
  saldosContainer.innerHTML = '';
  secoesContainer.innerHTML = '';

  const rotuloSaldo = (dataDe === dataAte)
    ? `Saldo em ${{fmtDataBR(dataAte)}}`
    : `Saldo ao final do período (${{fmtDataBR(dataAte)}})`;

  // Combina os lançamentos de TODAS as contas (não há mais checkbox de seleção
  // de conta — quem decide quais bancos/cartões aparecem é o filtro estilo
  // Excel na própria coluna "Banco/Cartão" da tabela).
  let todosLancamentos = [];
  const empresasChecadas = empresasSelecionadas();
  const tiposChecados = tiposSelecionados();
  DADOS.forEach(conta => {{
    if (!empresasChecadas.includes(conta.empresa)) return;

    const baseFiltrados = conta.lancamentos.filter(l => {{
      // Transferências entre contas do próprio grupo (Itaú >> Sicoob, etc.)
      // não são entrada/saída real de dinheiro — ficam de fora da tabela e
      // dos cards. O saldo real da conta (calcularSaldoNaData) NÃO usa esse
      // filtro, então continua contando essas transferências normalmente.
      if (l.transferenciaInterna) return false;
      if (l.natureza !== '-' && !tiposChecados.includes(l.natureza)) return false;

      const dataISO = paraDataISO(l.data);
      if (dataDe && dataISO && dataISO < dataDe) return false;
      if (dataAte && dataISO && dataISO > dataAte) return false;

      if (conciliDe || conciliAte) {{
        const conciliISO = paraDataISO(l.dataConciliacao);
        if (!conciliISO) return false;
        if (conciliDe && conciliISO < conciliDe) return false;
        if (conciliAte && conciliISO > conciliAte) return false;
      }}

      if (textoCliente && !(l.cliente || '').toLowerCase().includes(textoCliente)) return false;

      if (valorMin !== null && l.valor < valorMin) return false;
      if (valorMax !== null && l.valor > valorMax) return false;

      return true;
    }});

    baseFiltrados.forEach(l => todosLancamentos.push({{
      ...l,
      contaNome: conta.nome,
      contaTipo: conta.tipo,
      empresaNome: conta.empresa,
    }}));
  }});

  // baseFiltradosGlobal (antes do filtro de coluna) alimenta as opções dos
  // dropdowns estilo Excel — assim a lista de bancos no dropdown da coluna
  // "Banco/Cartão" sempre mostra todos os disponíveis, mesmo os que estão
  // desmarcados no momento (comportamento padrão do Excel).
  baseFiltradosGlobal = todosLancamentos;
  const filtrados = aplicarFiltrosColuna(todosLancamentos);

  // --- Card único de distribuição por Departamento (% sobre o valor absoluto total filtrado) ---
  // Lançamentos rateados entre mais de um departamento (ex: "Comercial 42%;
  // Administrativo 58%") entram AQUI já divididos proporcionalmente entre
  // cada departamento individual — assim "Comercial" de um lançamento
  // rateado soma com "Comercial" de outro lançamento 100% Comercial, em vez
  // de virar uma categoria à parte por combinação.
  //
  // O card vem FECHADO por padrão (só um resumo de 1-2 linhas) e expande
  // ao clicar, mostrando a lista completa de departamentos com barra. O
  // estado aberto/fechado fica guardado em `departamentoAberto` (fora
  // desta função) porque esse card é reconstruído do zero toda vez que um
  // filtro muda -- sem isso, o card fecharia sozinho a cada filtro novo.
  //
  // Só entram SAÍDAS (despesas) nessa distribuição -- receita não tem um
  // "departamento" de verdade (qualquer entrada acaba caindo em
  // "Comercial" por natureza da venda), então incluir receita aqui
  // infla artificialmente o peso do Comercial e mascara a distribuição
  // real de CUSTO entre os departamentos, que é o que esse card quer
  // mostrar.
  const cardDepto = document.getElementById('cardDepartamentoContainer');
  const filtradosDespesaDepto = filtrados.filter(l => l.valor < 0);
  const totalAbsDepto = filtradosDespesaDepto.reduce((s, l) => s + Math.abs(l.valor), 0);
  const somaPorDepto = {{}};
  filtradosDespesaDepto.forEach(l => {{
    const valorAbs = Math.abs(l.valor);
    const rateio = (l.departamentosRateio && l.departamentosRateio.length)
      ? l.departamentosRateio
      : [{{ nome: l.departamento || '-', percentual: 100 }}];
    rateio.forEach(d => {{
      const chave = d.nome || '-';
      const valorParte = valorAbs * (d.percentual || 0) / 100;
      somaPorDepto[chave] = (somaPorDepto[chave] || 0) + valorParte;
    }});
  }});
  const entradasDepto = Object.entries(somaPorDepto).sort((a, b) => b[1] - a[1]);
  const linhasDepto = entradasDepto
    .map(([nome, valor]) => {{
      const pct = totalAbsDepto > 0 ? (valor / totalAbsDepto * 100) : 0;
      return `
        <div class="linha-depto">
          <span class="nome-depto">${{nome}}</span>
          <div class="barra-wrap"><div class="barra" style="width:${{pct.toFixed(1)}}%"></div></div>
          <span class="pct-depto">${{pct.toFixed(1)}}%</span>
          <span class="valor-depto">${{fmtMoeda(valor)}}</span>
        </div>
      `;
    }}).join('');

  // Maior departamento NOMEADO (fora o "-", que é só "sem departamento
  // atribuído" -- nem todo lançamento precisa ter um, então não faz
  // sentido destacar ele como se fosse um problema).
  const maiorNomeado = entradasDepto.find(([nome]) => nome !== '-');
  const resumoDepto = totalAbsDepto > 0
    ? `<strong>${{fmtMoeda(totalAbsDepto)}}</strong> em despesas no filtro atual` +
      (maiorNomeado ? ` · maior: ${{maiorNomeado[0]}} (${{(maiorNomeado[1] / totalAbsDepto * 100).toFixed(1)}}%)` : '')
    : 'Nenhuma despesa encontrada com os filtros atuais.';

  cardDepto.classList.toggle('aberto', departamentoAberto);
  cardDepto.innerHTML = `
    <div class="label">Distribuição por Departamento (só despesas do filtro atual)</div>
    <div class="depto-resumo">${{resumoDepto}}</div>
    ${{totalAbsDepto > 0 ? `<div class="depto-resumo-sub">clique pra ver os ${{entradasDepto.length}} departamento(s) <span class="depto-seta">▾</span></div>` : ''}}
    <div class="depto-corpo">${{linhasDepto}}</div>
  `;
  cardDepto.onclick = () => {{
    departamentoAberto = !departamentoAberto;
    cardDepto.classList.toggle('aberto', departamentoAberto);
  }};

  // As caixas de saldo (e os cards de Entradas/Saídas logo abaixo) agora
  // mostram SEMPRE todas as contas das empresas marcadas no painel de cima
  // -- não dependem mais de haver algum lançamento dentro do período/tipo
  // filtrado. Antes, se uma conta não tivesse nenhum lançamento batendo
  // com o filtro (ex: Santander sem movimento no dia escolhido), a conta
  // inteira sumia do dashboard, saldo incluso -- o que não fazia sentido,
  // já que o saldo da conta existe independente de ter ou não lançamento
  // naquele recorte. O único filtro que ainda pode "esconder" uma conta
  // aqui é o filtro estilo Excel da própria coluna "Banco/Cartão" (colFiltros.conta),
  // porque esse sim é uma escolha explícita do usuário de quais contas ver.
  const contasSaldoSempre = DADOS
    .filter(c => empresasChecadas.includes(c.empresa))
    .filter(c => !colFiltros['conta'] || colFiltros['conta'].has(c.nome))
    .map(c => c.nome);

  // Soma do saldo real (não é a soma de entradas/saídas do período — é o
  // saldo bancário de fato) de todas as contas BANCÁRIAS visíveis no
  // momento. Cartão de crédito fica de fora, porque saldo de cartão não é
  // "dinheiro em caixa" de verdade.
  let saldoTotalBancos = 0;

  contasSaldoSempre.forEach(nomeConta => {{
    const dadosConta = DADOS.find(c => c.nome === nomeConta);
    const doConta = filtrados.filter(l => l.contaNome === nomeConta);

    if (dadosConta) {{
      const saldoConta = calcularSaldoNaData(dadosConta, dataAte);
      if (dadosConta.tipo === 'banco') {{
        saldoTotalBancos += saldoConta;
      }}
      // Cartões de crédito não geram caixa de "saldo real" nem card de
      // Entradas/Saídas/Saldo no topo — ficavam redundantes, sempre
      // zerados. Continuam disponíveis no filtro de coluna "Banco/Cartão"
      // da tabela normalmente.
      if (dadosConta.tipo === 'banco') {{
        const boxSaldo = document.createElement('div');
        boxSaldo.className = 'box-saldo';
        boxSaldo.innerHTML = `
          <div class="label">${{nomeConta}}</div>
          <div class="valor-saldo">${{fmtMoeda(saldoConta)}}</div>
          <div class="ref">${{rotuloSaldo}}</div>
        `;
        saldosContainer.appendChild(boxSaldo);
      }}
    }}

    if (dadosConta && dadosConta.tipo === 'cartao') return;

    const entradas = doConta.filter(l => l.valor > 0).reduce((s, l) => s + l.valor, 0);
    const saidas = doConta.filter(l => l.valor < 0).reduce((s, l) => s + l.valor, 0);
    const saldo = entradas + saidas;
    const tipoLabel = (dadosConta && dadosConta.tipo === 'banco') ? 'Conta Bancária' : 'Cartão de Crédito';

    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="label">${{nomeConta}} — ${{tipoLabel}}</div>
      <div class="linha-valor"><span class="mini-label">Entradas</span><span class="valor entrada">${{fmtMoeda(entradas)}}</span></div>
      <div class="linha-valor"><span class="mini-label">Saídas</span><span class="valor saida">${{fmtMoeda(saidas)}}</span></div>
      <div class="linha-valor"><span class="mini-label">Saldo</span><span class="valor saldo">${{fmtMoeda(saldo)}}</span></div>
      <div class="qtd">${{doConta.length}} lançamento(s)</div>
    `;
    cardsContainer.appendChild(card);
  }});

  const saldoTotalContainer = document.getElementById('saldoTotalContainer');
  const rotuloSaldoTotal = (dataDe === dataAte)
    ? `Saldo em caixa em ${{fmtDataBR(dataAte)}} — soma de todas as contas bancárias visíveis`
    : `Saldo em caixa ao final do período (${{fmtDataBR(dataAte)}}) — soma de todas as contas bancárias visíveis`;
  saldoTotalContainer.innerHTML = `
    <div class="card-saldo-total">
      <div>
        <div class="label">Saldo Total</div>
        <div class="ref">${{rotuloSaldoTotal}}</div>
      </div>
      <div class="valor-saldo-total">${{fmtMoeda(saldoTotalBancos)}}</div>
    </div>
  `;

  // Saldo de Ontem: sempre o dia anterior ao dia real de HOJE (não ao filtro
  // "Até" selecionado) — serve de referência fixa para comparar com o Saldo
  // Total acima, que já segue o período filtrado. Usa o mesmo conjunto de
  // contas bancárias visíveis no momento, pra ser uma comparação justa.
  const saldoOntemContainer = document.getElementById('saldoOntemContainer');
  const hojeReal = new Date();
  hojeReal.setDate(hojeReal.getDate() - 1);
  const ontemISO = hojeReal.toISOString().split('T')[0];
  let saldoTotalBancosOntem = 0;
  contasSaldoSempre.forEach(nomeConta => {{
    const dadosConta = DADOS.find(c => c.nome === nomeConta);
    if (dadosConta && dadosConta.tipo === 'banco') {{
      saldoTotalBancosOntem += calcularSaldoNaData(dadosConta, ontemISO);
    }}
  }});
  saldoOntemContainer.innerHTML = `
    <div class="card-saldo-ontem">
      <div class="label">Saldo do Dia Anterior</div>
      <div class="ref">${{fmtDataBR(ontemISO)}} — mesmas contas visíveis</div>
      <div class="valor-saldo-ontem">${{fmtMoeda(saldoTotalBancosOntem)}}</div>
    </div>
  `;

  // Ordenação (se o usuário clicou em algum cabeçalho de coluna) — feita
  // sobre TODOS os filtrados, antes de cortar para a amostra de 100, para
  // que o corte reflita de verdade os maiores/menores ou A-Z/Z-A.
  if (ordenacao.coluna) {{
    filtrados.sort((a, b) => {{
      const va = valorOrdenacao(a, ordenacao.coluna);
      const vb = valorOrdenacao(b, ordenacao.coluna);
      const cmp = (typeof va === 'number' && typeof vb === 'number')
        ? va - vb
        : String(va).localeCompare(String(vb), 'pt-BR');
      return ordenacao.direcao === 'desc' ? -cmp : cmp;
    }});
  }}

  const amostra = filtrados.slice(0, 100);

  const totalFiltrado = filtrados.reduce((s, l) => s + l.valor, 0);
  const indiceColunaValor = COLUNAS.findIndex(c => c.key === 'valor');
  const celulasTotal = COLUNAS.map((col, i) => {{
    if (i === indiceColunaValor) return `<td class="valor-total">${{fmtMoeda(totalFiltrado)}}</td>`;
    if (i === 0) return `<td>Total filtrado (${{filtrados.length}} lançamento(s))</td>`;
    return '<td></td>';
  }}).join('');
  const linhaTotalHtml = `<tr class="linha-total">${{celulasTotal}}</tr>`;

  const linhasHtml = amostra.map(l => `
    <tr>
      <td>${{l.situacaoTitulo}}</td>
      <td>${{l.data}}</td>
      <td>${{l.empresaNome}}</td>
      <td>${{l.cliente}}</td>
      <td>${{fmtMoeda(l.valor)}}</td>
      <td>${{l.categoria}}</td>
      <td>${{l.valor >= 0 ? 'Não se Aplica' : (l.departamento || '-')}}</td>
      <td>${{lancClassificarCusto(l)}}</td>
      <td>${{l.contaNome}}</td>
      <td class="celula-obs" title="${{(l.observacao || '').replace(/"/g, '&quot;')}}">${{l.observacao}}</td>
      <td>${{l.dataConciliacao || '-'}}</td>
    </tr>
  `).join('');

  const headerHtml = COLUNAS.map(col => {{
    const ativo = colFiltros[col.key] ? 'ativo' : '';
    const largura = colWidths[col.key] || LARGURA_PADRAO[col.key] || 150;
    const ordenandoEsta = ordenacao.coluna === col.key;
    const seta = ordenandoEsta ? (ordenacao.direcao === 'asc' ? ' ▲' : ' ▼') : '';
    const classeOrdenacao = ordenandoEsta ? 'th-ordenavel ativo' : 'th-ordenavel';
    return `<th style="width:${{largura}}px">
      <span class="${{classeOrdenacao}}" data-col="${{col.key}}" title="Clique para ordenar">${{col.label}}${{seta}}</span>
      <button type="button" class="btn-filtro-col ${{ativo}}" data-col="${{col.key}}">▾</button>
      <button type="button" class="btn-largura" data-col="${{col.key}}" data-delta="-20" title="Diminuir largura">−</button>
      <button type="button" class="btn-largura" data-col="${{col.key}}" data-delta="20" title="Aumentar largura">+</button>
      <div class="resize-handle" data-col="${{col.key}}"></div>
    </th>`;
  }}).join('');

  // table-layout:fixed só respeita as larguras das colunas se a própria <table>
  // tiver uma largura explícita — sem isso, o navegador ignora nossos valores
  // e recalcula pelo conteúdo. Por isso somamos e aplicamos aqui.
  const larguraTotalTabela = COLUNAS.reduce(
    (soma, col) => soma + (colWidths[col.key] || LARGURA_PADRAO[col.key] || 150),
    0
  );

  const secao = document.createElement('div');
  secao.innerHTML = `
    <h2>Lançamentos (amostra de até 100, de ${{filtrados.length}} filtrados)</h2>
    ${{amostra.length ? `
      <div class="scroll-topo-wrap" id="scrollTopoWrap">
        <div class="scroll-topo-inner" id="scrollTopoInner"></div>
      </div>
      <div class="tabela-wrap" id="tabelaWrap">
        <table style="width:${{larguraTotalTabela}}px">
          <tr>${{headerHtml}}</tr>
          ${{linhasHtml}}
          ${{linhaTotalHtml}}
        </table>
      </div>
    ` : '<div class="vazio">Nenhum lançamento encontrado com os filtros atuais.</div>'}}
  `;
  secoesContainer.appendChild(secao);

  // Sincroniza a barra de rolagem de cima com a de baixo, para não precisar
  // descer até o fim da tabela toda vez que quiser arrastar para o lado.
  if (amostra.length) {{
    const scrollTopoWrap = document.getElementById('scrollTopoWrap');
    const scrollTopoInner = document.getElementById('scrollTopoInner');
    const tabelaWrapEl = document.getElementById('tabelaWrap');
    scrollTopoInner.style.width = larguraTotalTabela + 'px';

    let sincronizando = false;
    scrollTopoWrap.addEventListener('scroll', () => {{
      if (sincronizando) return;
      sincronizando = true;
      tabelaWrapEl.scrollLeft = scrollTopoWrap.scrollLeft;
      sincronizando = false;
    }});
    tabelaWrapEl.addEventListener('scroll', () => {{
      if (sincronizando) return;
      sincronizando = true;
      scrollTopoWrap.scrollLeft = tabelaWrapEl.scrollLeft;
      sincronizando = false;
    }});
  }}
}}

// Delegação de clique para os botões de filtro de coluna (eles são recriados a cada renderização).
document.getElementById('secoesContainer').addEventListener('click', (e) => {{
  const thOrdenavel = e.target.closest('.th-ordenavel');
  if (thOrdenavel) {{
    const colKey = thOrdenavel.dataset.col;
    if (ordenacao.coluna !== colKey) {{
      ordenacao = {{ coluna: colKey, direcao: 'asc' }};
    }} else if (ordenacao.direcao === 'asc') {{
      ordenacao = {{ coluna: colKey, direcao: 'desc' }};
    }} else {{
      ordenacao = {{ coluna: null, direcao: null }};
    }}
    renderizar();
    return;
  }}

  const btnLargura = e.target.closest('.btn-largura');
  if (btnLargura) {{
    const colKey = btnLargura.dataset.col;
    const delta = parseInt(btnLargura.dataset.delta, 10);
    const larguraAtual = colWidths[colKey] || LARGURA_PADRAO[colKey] || 150;
    colWidths[colKey] = Math.max(60, larguraAtual + delta);
    renderizar();
    return;
  }}

  const btn = e.target.closest('.btn-filtro-col');
  if (!btn) return;
  const colKey = btn.dataset.col;
  const col = COLUNAS.find(c => c.key === colKey);
  if (col) abrirDropdownColuna(col, btn);
}});

// Redimensionamento de colunas (arrastar a borda direita do cabeçalho).
document.getElementById('secoesContainer').addEventListener('mousedown', (e) => {{
  const handle = e.target.closest('.resize-handle');
  if (!handle) return;
  e.preventDefault();
  const th = handle.closest('th');
  resizando = {{
    colKey: handle.dataset.col,
    startX: e.clientX,
    startWidth: th.offsetWidth,
    th,
    handle,
  }};
  handle.classList.add('resizando');
  document.body.style.cursor = 'col-resize';
}});

document.addEventListener('mousemove', (e) => {{
  if (!resizando) return;
  const delta = e.clientX - resizando.startX;
  const novaLargura = Math.max(60, resizando.startWidth + delta);
  resizando.th.style.width = novaLargura + 'px';
}});

document.addEventListener('mouseup', () => {{
  if (!resizando) return;
  colWidths[resizando.colKey] = resizando.th.offsetWidth;
  resizando.handle.classList.remove('resizando');
  resizando = null;
  document.body.style.cursor = '';
  renderizar();
}});

window.addEventListener('blur', () => {{
  if (!resizando) return;
  resizando.handle.classList.remove('resizando');
  resizando = null;
  document.body.style.cursor = '';
}});

// Data "até" default = fim do período buscado (1 ano à frente), para já
// mostrar também lançamentos futuros/agendados desde a abertura da página.
document.getElementById('dataAte').value = '{PERIODO_FINAL_ISO}';

function redefinirFiltros() {{
  // Painel superior: volta cada campo ao estado inicial.
  document.getElementById('dataDe').value = '2026-01-01';
  document.getElementById('dataAte').value = '{PERIODO_FINAL_ISO}';
  document.getElementById('conciliDe').value = '';
  document.getElementById('conciliAte').value = '';
  document.getElementById('filtroCliente').value = '';
  document.getElementById('valorMin').value = '';
  document.getElementById('valorMax').value = '';
  document.querySelectorAll('.chk-empresa').forEach(chk => chk.checked = true);
  document.querySelectorAll('.chk-tipo').forEach(chk => chk.checked = true);

  // Estado interno: filtro estilo Excel por coluna, larguras e ordenação.
  colFiltros = {{}};
  colWidths = {{}};
  ordenacao = {{ coluna: null, direcao: null }};

  fecharDropdowns();
  renderizar();
}}

document.getElementById('btnAplicar').addEventListener('click', renderizar);
document.getElementById('btnRedefinir').addEventListener('click', redefinirFiltros);
renderizar();
</script>
</div>

<div id="abaDFC" style="display:none">{html_dfc}</div>
<div id="abaDRE" style="display:none">{html_dre}</div>

<div id="drillDownOverlay" onclick="if(event.target===this) fecharDrillDownDreDfc()">
  <div class="drilldown-caixa">
    <div class="drilldown-cabecalho">
      <div>
        <h3 id="drillDownTitulo"></h3>
        <div class="sub" id="drillDownSubtitulo"></div>
      </div>
      <button class="drilldown-fechar" onclick="fecharDrillDownDreDfc()">✕</button>
    </div>
    <div class="drilldown-corpo" id="drillDownCorpo"></div>
  </div>
</div>
{html_insights}

<script>
function mostrarAba(nome) {{
  document.getElementById('abaLancamentos').style.display = (nome === 'Lancamentos') ? '' : 'none';
  document.getElementById('abaDFC').style.display = (nome === 'DFC') ? '' : 'none';
  document.getElementById('abaDRE').style.display = (nome === 'DRE') ? '' : 'none';
  document.getElementById('abaInsights').style.display = (nome === 'Insights') ? '' : 'none';
  document.querySelectorAll('.aba-btn').forEach(btn => {{
    btn.classList.toggle('aba-ativa', btn.dataset.aba === nome);
  }});
  // A largura da barra de cima só dá pra medir certo com a aba já visível
  // (enquanto está com display:none, o navegador mede tudo como 0) -- por
  // isso recalculamos aqui, toda vez que troca de aba.
  if (nome === 'DFC' || nome === 'DRE') {{
    atualizarLarguraScrollDreDfc();
  }}
}}

function toggleSecaoDreDfc(trSecao) {{
  const linha = trSecao.dataset.secao;
  const tabela = trSecao.closest('table');
  // Recolhe todas as linhas aninhadas dentro desta seção, em qualquer
  // nível (padrão normal de acordeão). O que mudou (ver engine_dre_dfc.py,
  // dict "containers"): só entram aqui seções que são contêineres DE
  // VERDADE -- linhas de total/resultado (que somam seções JÁ mostradas
  // acima, tipo "Resultado do Período") não geram entrada nenhuma, então
  // não têm mais seta nem colapsam a tabela inteira por engano.
  const filhos = Array.from(tabela.querySelectorAll('tr[data-dentro]')).filter(tr =>
    tr.dataset.dentro.split(' ').includes(linha)
  );
  if (!filhos.length) return;
  const vaiFechar = !filhos[0].classList.contains('dre-colapsada');
  trSecao.classList.toggle('dre-secao-fechada', vaiFechar);

  if (vaiFechar) {{
    // 1) opacidade some com transição; 2) só depois de terminar a
    // animação a linha sai do fluxo da tabela (display:none) -- fazer os
    // dois ao mesmo tempo cortava a transição pela metade.
    filhos.forEach(tr => tr.classList.add('dre-colapsada'));
    setTimeout(() => {{
      filhos.forEach(tr => {{
        if (tr.classList.contains('dre-colapsada')) tr.classList.add('dre-oculta');
      }});
    }}, 170);
  }} else {{
    filhos.forEach(tr => tr.classList.remove('dre-oculta'));
    // Força o navegador a aplicar o display:block antes de tirar a
    // opacidade, senão as duas mudanças são agrupadas num único frame e a
    // transição de entrada não roda (some o fade-in).
    void tabela.offsetWidth;
    filhos.forEach(tr => tr.classList.remove('dre-colapsada'));
  }}
}}

// Barra de rolagem duplicada (em cima e embaixo) nas tabelas de DRE/DFC,
// igual já fazíamos na tabela de Lançamentos. A sincronização de scroll é
// ligada uma vez só; a largura é recalculada toda vez que a aba abre (ver
// mostrarAba acima).
function atualizarLarguraScrollDreDfc() {{
  document.querySelectorAll('.dre-dfc-wrap').forEach(wrap => {{
    const scrollBaixo = wrap.querySelector('.dre-dfc-scroll');
    const scrollTopoInner = wrap.querySelector('.dre-scroll-topo .scroll-topo-inner');
    const tabela = scrollBaixo.querySelector('table');
    scrollTopoInner.style.width = tabela.scrollWidth + 'px';
  }});
}}

document.querySelectorAll('.dre-dfc-wrap').forEach(wrap => {{
  const scrollBaixo = wrap.querySelector('.dre-dfc-scroll');
  const scrollTopoWrap = wrap.querySelector('.dre-scroll-topo');

  let sincronizando = false;
  scrollTopoWrap.addEventListener('scroll', () => {{
    if (sincronizando) return;
    sincronizando = true;
    scrollBaixo.scrollLeft = scrollTopoWrap.scrollLeft;
    sincronizando = false;
  }});
  scrollBaixo.addEventListener('scroll', () => {{
    if (sincronizando) return;
    sincronizando = true;
    scrollTopoWrap.scrollLeft = scrollBaixo.scrollLeft;
    sincronizando = false;
  }});
}});
</script>

</body>
</html>
"""


def main():
    contas = coletar_dados()
    html = gerar_html(contas)

    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Dashboard gerado em public/index.html")


if __name__ == "__main__":
    main()
