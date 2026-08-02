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
from datetime import datetime, timezone, timedelta
import requests

APP_KEY = os.environ["OMIE_APP_KEY"]
APP_SECRET = os.environ["OMIE_APP_SECRET"]

BASE_URL = "https://app.omie.com.br/api/v1"
HEADERS = {"Content-Type": "application/json"}

# Período de busca: sempre de 01/01/2026 até o dia da execução
PERIODO_INICIAL = "01/01/2026"
PERIODO_FINAL = datetime.now().strftime("%d/%m/%Y")

# Todas as contas monitoradas (bancárias + cartões)
CONTAS = [
    {"id": "sicoob_matriz", "nome": "Sicoob Matriz", "tipo": "banco", "nCodCC": 11213271714},
    {"id": "itau_unibanco", "nome": "Itaú Unibanco", "tipo": "banco", "nCodCC": 11214288866},
    {"id": "santander", "nome": "Santander", "tipo": "banco", "nCodCC": 11236843385},
    {"id": "sicredi", "nome": "Sicredi", "tipo": "banco", "nCodCC": 11239054225},
    {"id": "bradesco", "nome": "Bradesco", "tipo": "banco", "nCodCC": 11431467344},
    {"id": "cartao_sicoob", "nome": "Cartão Sicoob", "tipo": "cartao", "nCodCC": 11574015480},
    {"id": "cartao_itau", "nome": "Cartão Itaú", "tipo": "cartao", "nCodCC": 11693512362},
]


def chamar_omie(modulo: str, call: str, param: dict) -> dict:
    """Faz uma chamada genérica à API da Omie."""
    url = f"{BASE_URL}/{modulo}/"
    payload = {
        "call": call,
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
        "param": [param],
    }
    resp = requests.post(url, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "faultstring" in data:
        raise RuntimeError(f"Erro Omie ({call}): {data['faultstring']}")
    return data


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


def buscar_extrato(nCodCC: int) -> dict:
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
        )
        if i == 0:
            saldo_anterior_total = data.get("nSaldoAnterior", 0)
        todos_movimentos.extend(data.get("listaMovimentos", []) or [])

    return {
        "saldoAnterior": saldo_anterior_total or 0,
        "movimentos": todos_movimentos,
    }


def buscar_departamentos_cadastro() -> dict:
    """Busca a tabela de departamentos cadastrados (código -> nome).
    É uma lista pequena e estável, buscada uma única vez. Se falhar,
    retorna vazio — nesse caso os códigos de departamento aparecem
    "crus" no dashboard em vez do nome, mas o script não trava."""
    todos = []
    pagina = 1
    while True:
        try:
            data = chamar_omie(
                "geral/departamentos",
                "ListarDepartamentos",
                {"pagina": pagina, "registros_por_pagina": 100},
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


def buscar_movimentos_departamento(nCodCC: int, nomes_departamento: dict) -> dict:
    """Busca os Movimentos Financeiros de uma conta (mês a mês, para evitar
    o limite de registros por chamada), pedindo a distribuição por
    departamento. Monta {nCodMovCC: descrição do departamento}.

    nCodMovCC é o mesmo código que aparece no extrato bancário como
    nCodLancamento — essa é a ponte confirmada entre os dois endpoints.

    Se alguma chamada falhar (a Omie retornou erro 500 em algumas
    combinações de conta/mês/página durante os testes), o erro é
    registrado e a busca segue para o próximo mês, em vez de travar o
    script inteiro — melhor ter departamento incompleto do que o
    dashboard inteiro não ser publicado.
    """
    lookup = {}
    for ini, fim in gerar_meses(PERIODO_INICIAL, PERIODO_FINAL):
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
                        "dDtPagtoDe": ini,
                        "dDtPagtoAte": fim,
                        "cExibirDepartamentos": "S",
                    },
                )
            except Exception as erro:
                print(
                    f"AVISO: falha ao buscar departamento (conta {nCodCC}, "
                    f"período {ini}-{fim}, página {pagina}): {erro}"
                )
                break

            movimentos = data.get("movimentos", []) or []
            for mov in movimentos:
                detalhes = mov.get("detalhes", {}) or {}
                cod_mov = detalhes.get("nCodMovCC")
                deptos = mov.get("departamentos") or []
                if not cod_mov or not deptos:
                    continue
                if len(deptos) == 1:
                    cod_dep = deptos[0].get("cCodDepartamento")
                    desc = nomes_departamento.get(cod_dep, cod_dep or "-")
                else:
                    partes = []
                    for d in deptos:
                        cod_dep = d.get("cCodDepartamento")
                        nome = nomes_departamento.get(cod_dep, cod_dep or "-")
                        pct = d.get("nDistrPercentual", 0)
                        partes.append(f"{nome} ({pct:.0f}%)")
                    desc = "; ".join(partes)
                lookup[cod_mov] = desc

            total_paginas = data.get("nTotPaginas", 1)
            if pagina >= total_paginas or not movimentos:
                break
            pagina += 1
    return lookup


def montar_lookup_departamento() -> dict:
    """Monta {nCodMovCC: descrição do departamento} cruzando o cadastro de
    departamentos com os Movimentos Financeiros de cada conta."""
    nomes_departamento = buscar_departamentos_cadastro()
    lookup_geral = {}
    for conta in CONTAS:
        lookup_conta = buscar_movimentos_departamento(conta["nCodCC"], nomes_departamento)
        lookup_geral.update(lookup_conta)
    return lookup_geral


def coletar_dados() -> list:
    """Coleta os dados brutos de todas as contas, sem aplicar nenhum filtro."""
    lookup_departamento = montar_lookup_departamento()

    resultado = []
    for conta in CONTAS:
        extrato = buscar_extrato(conta["nCodCC"])
        lancamentos = [
            {
                "data": m.get("dDataLancamento", ""),
                "cliente": m.get("cDesCliente", "-") or "-",
                "valor": m.get("nValorDocumento", 0),
                "situacao": m.get("cSituacao", "-") or "-",
                "dataConciliacao": (m.get("dDataConciliacao") or "").strip(),
                "saldo": m.get("nSaldo", 0),
                "categoria": m.get("cDesCategoria", "-") or "-",
                "observacao": (m.get("cObservacoes") or "").strip() or "-",
                "departamento": lookup_departamento.get(
                    m.get("nCodLancamento") or m.get("nCodLancRelac"), "-"
                ),
            }
            for m in extrato["movimentos"]
        ]
        resultado.append({
            "id": conta["id"],
            "nome": conta["nome"],
            "tipo": conta["tipo"],
            "saldoAnterior": extrato["saldoAnterior"],
            "lancamentos": lancamentos,
        })
    return resultado


def gerar_html(contas: list) -> str:
    agora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    dados_json = jsonlib.dumps(contas, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Dashboard Prime Sol — Financeiro</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #0b1220;
    --bg-panel: #131c2e;
    --bg-card: #17223a;
    --border: #263248;
    --text: #e7ecf5;
    --text-muted: #8b96ab;
    --text-faint: #5b6780;
    --accent: #3b82f6;
    --accent-hover: #2563eb;
    --green: #4ade80;
    --red: #f87171;
    --cyan: #7dd3fc;
    --radius: 12px;
    --radius-sm: 8px;
    --space: 8px;
  }}

  * {{ box-sizing: border-box; }}

  body {{ font-family: -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0; background: var(--bg); color: var(--text); line-height: 1.4; }}

  header {{ padding: 24px 32px; background: var(--bg-panel); border-bottom: 1px solid var(--border); }}
  h1 {{ margin: 0; font-size: 19px; font-weight: 700; letter-spacing: -0.01em; }}
  .atualizado {{ color: var(--text-muted); font-size: 12.5px; margin-top: 6px; }}

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

  #btnAplicar {{
    background: var(--accent); color: white; border: none; border-radius: var(--radius-sm);
    padding: 0 22px; height: 40px; font-size: 13px; font-weight: 600; cursor: pointer;
    align-self: end; transition: background .15s ease;
  }}
  #btnAplicar:hover {{ background: var(--accent-hover); }}

  .saldos, .cards {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(210px, 1fr));
    gap: 14px;
    padding: 20px 32px 0;
  }}

  .box-saldo {{
    background: linear-gradient(180deg, #0f2942 0%, #0d2338 100%);
    border: 1px solid #1d4d7a;
    border-radius: var(--radius);
    padding: 16px 18px;
  }}
  .box-saldo .label {{ font-size: 11px; font-weight: 700; color: var(--cyan); text-transform: uppercase; letter-spacing: .05em; }}
  .box-saldo .valor-saldo {{ font-size: 22px; font-weight: 800; margin-top: 8px; color: #f0f9ff; letter-spacing: -0.01em; }}
  .box-saldo .ref {{ font-size: 11px; color: var(--text-faint); margin-top: 6px; }}

  .card {{
    background: var(--bg-card); border-radius: var(--radius); padding: 16px 18px;
    border: 1px solid var(--border); display: flex; flex-direction: column;
  }}
  .card .label {{
    font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;
    letter-spacing: .05em; margin-bottom: 10px; padding-bottom: 8px; border-bottom: 1px solid var(--border);
  }}
  .linha-valor {{ display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 2px 0; }}
  .mini-label {{ font-size: 11.5px; color: var(--text-faint); }}
  .valor {{ font-size: 14px; font-weight: 700; }}
  .valor.entrada {{ color: var(--green); }}
  .valor.saida {{ color: var(--red); }}
  .valor.saldo {{ color: var(--text); font-size: 16px; }}
  .card .qtd {{ font-size: 11px; color: var(--text-faint); margin-top: 10px; padding-top: 8px; border-top: 1px solid var(--border); }}

  .card-departamento-wrap {{ padding: 0 32px; margin-top: 4px; }}
  .card-departamento {{ max-width: none; }}
  .card-departamento .label {{ margin-bottom: 12px; }}
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
  table {{ table-layout: fixed; border-collapse: collapse; margin-bottom: 8px; font-size: 12.5px; background: var(--bg-panel); }}
  th, td {{ text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  th {{ position: relative; color: var(--text-muted); font-weight: 600; white-space: nowrap; font-size: 11.5px; text-transform: uppercase; letter-spacing: .03em; }}
  th .th-label {{ overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
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
</style>
</head>
<body>
<header>
  <h1>Dashboard Prime Sol — Financeiro</h1>
  <div class="atualizado">Última atualização: {agora} · Dados brutos coletados de {PERIODO_INICIAL} até {PERIODO_FINAL}</div>
</header>

<div class="filtros">
  <div class="filtro-bloco">
    <h3>Período (data de pagamento/recebimento)</h3>
    <div class="datas">
      <label>De: <input type="date" id="dataDe" value="2026-01-01"></label>
      <label>Até: <input type="date" id="dataAte"></label>
    </div>
  </div>

  <div class="filtro-bloco">
    <h3>Conciliação</h3>
    <div class="radios">
      <label><input type="radio" name="conciliacao" value="todos"> Todos</label>
      <label><input type="radio" name="conciliacao" value="conciliados" checked> Apenas conciliados</label>
      <label><input type="radio" name="conciliacao" value="nao_conciliados"> Apenas não conciliados</label>
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

  <div class="filtro-bloco">
    <h3>Situação</h3>
    <input type="text" id="filtroSituacao" placeholder="Ex: Conciliado, Pago..." class="input-texto">
  </div>

  <button id="btnAplicar">Aplicar filtros</button>
</div>

<div class="saldos" id="saldosContainer"></div>

<div class="cards" id="cardsContainer"></div>

<div class="card-departamento-wrap">
  <div class="card card-departamento" id="cardDepartamentoContainer"></div>
</div>

<section id="secoesContainer"></section>

<script>
const DADOS = {dados_json};

// Definição das colunas da tabela, usadas tanto para montar o cabeçalho
// quanto para o filtro estilo Excel de cada coluna.
const COLUNAS = [
  {{ key: 'data', label: 'Data Pagamento/Recebimento', get: l => l.data }},
  {{ key: 'cliente', label: 'Cliente/Fornecedor', get: l => l.cliente }},
  {{ key: 'valor', label: 'Valor', get: l => fmtMoeda(l.valor) }},
  {{ key: 'categoria', label: 'Categoria', get: l => l.categoria }},
  {{ key: 'departamento', label: 'Departamento', get: l => l.departamento }},
  {{ key: 'conta', label: 'Banco/Cartão', get: l => l.contaNome }},
  {{ key: 'observacao', label: 'Observação', get: l => l.observacao }},
  {{ key: 'situacao', label: 'Situação', get: l => l.situacao }},
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
  conta: 150, data: 150, cliente: 200, valor: 110, categoria: 160,
  observacao: 240, situacao: 120, dataConciliacao: 150,
}};
let resizando = null;

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
  const modoConciliacao = document.querySelector('input[name="conciliacao"]:checked').value;
  const conciliDe = document.getElementById('conciliDe').value;
  const conciliAte = document.getElementById('conciliAte').value;
  const textoCliente = document.getElementById('filtroCliente').value.trim().toLowerCase();
  const textoSituacao = document.getElementById('filtroSituacao').value.trim().toLowerCase();
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
  DADOS.forEach(conta => {{
    const baseFiltrados = conta.lancamentos.filter(l => {{
      const dataISO = paraDataISO(l.data);
      if (dataDe && dataISO && dataISO < dataDe) return false;
      if (dataAte && dataISO && dataISO > dataAte) return false;

      if (modoConciliacao === 'conciliados' && !l.dataConciliacao) return false;
      if (modoConciliacao === 'nao_conciliados' && l.dataConciliacao) return false;

      if (conciliDe || conciliAte) {{
        const conciliISO = paraDataISO(l.dataConciliacao);
        if (!conciliISO) return false;
        if (conciliDe && conciliISO < conciliDe) return false;
        if (conciliAte && conciliISO > conciliAte) return false;
      }}

      if (textoCliente && !(l.cliente || '').toLowerCase().includes(textoCliente)) return false;
      if (textoSituacao && !(l.situacao || '').toLowerCase().includes(textoSituacao)) return false;

      if (valorMin !== null && l.valor < valorMin) return false;
      if (valorMax !== null && l.valor > valorMax) return false;

      return true;
    }});

    baseFiltrados.forEach(l => todosLancamentos.push({{ ...l, contaNome: conta.nome, contaTipo: conta.tipo }}));
  }});

  // baseFiltradosGlobal (antes do filtro de coluna) alimenta as opções dos
  // dropdowns estilo Excel — assim a lista de bancos no dropdown da coluna
  // "Banco/Cartão" sempre mostra todos os disponíveis, mesmo os que estão
  // desmarcados no momento (comportamento padrão do Excel).
  baseFiltradosGlobal = todosLancamentos;
  const filtrados = aplicarFiltrosColuna(todosLancamentos);

  // --- Card único de distribuição por Departamento (% sobre o valor absoluto total filtrado) ---
  const cardDepto = document.getElementById('cardDepartamentoContainer');
  const totalAbsDepto = filtrados.reduce((s, l) => s + Math.abs(l.valor), 0);
  const somaPorDepto = {{}};
  filtrados.forEach(l => {{
    const chave = l.departamento || '-';
    somaPorDepto[chave] = (somaPorDepto[chave] || 0) + Math.abs(l.valor);
  }});
  const linhasDepto = Object.entries(somaPorDepto)
    .sort((a, b) => b[1] - a[1])
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
  cardDepto.innerHTML = `
    <div class="label">Distribuição por Departamento (sobre o total filtrado)</div>
    ${{linhasDepto || '<div class="vazio">Nenhum lançamento encontrado com os filtros atuais.</div>'}}
  `;

  // Cards e caixas de saldo agora seguem o resultado FINAL da tabela
  // (incluindo o filtro de coluna "Banco/Cartão"), agrupando por conta.
  const contasPresentes = [...new Set(filtrados.map(l => l.contaNome))];

  contasPresentes.forEach(nomeConta => {{
    const dadosConta = DADOS.find(c => c.nome === nomeConta);
    const doConta = filtrados.filter(l => l.contaNome === nomeConta);

    if (dadosConta) {{
      const saldoConta = calcularSaldoNaData(dadosConta, dataAte);
      const boxSaldo = document.createElement('div');
      boxSaldo.className = 'box-saldo';
      boxSaldo.innerHTML = `
        <div class="label">${{nomeConta}}</div>
        <div class="valor-saldo">${{fmtMoeda(saldoConta)}}</div>
        <div class="ref">${{rotuloSaldo}}</div>
      `;
      saldosContainer.appendChild(boxSaldo);
    }}

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
      <td>${{l.data}}</td>
      <td>${{l.cliente}}</td>
      <td>${{fmtMoeda(l.valor)}}</td>
      <td>${{l.categoria}}</td>
      <td>${{l.departamento}}</td>
      <td>${{l.contaNome}}</td>
      <td class="celula-obs" title="${{(l.observacao || '').replace(/"/g, '&quot;')}}">${{l.observacao}}</td>
      <td>${{l.situacao}}</td>
      <td>${{l.dataConciliacao || '-'}}</td>
    </tr>
  `).join('');

  const headerHtml = COLUNAS.map(col => {{
    const ativo = colFiltros[col.key] ? 'ativo' : '';
    const largura = colWidths[col.key] || LARGURA_PADRAO[col.key] || 150;
    return `<th style="width:${{largura}}px">
      <span class="th-label">${{col.label}}</span>
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
      <div class="tabela-wrap">
        <table style="width:${{larguraTotalTabela}}px">
          <tr>${{headerHtml}}</tr>
          ${{linhasHtml}}
          ${{linhaTotalHtml}}
        </table>
      </div>
    ` : '<div class="vazio">Nenhum lançamento encontrado com os filtros atuais.</div>'}}
  `;
  secoesContainer.appendChild(secao);
}}

// Delegação de clique para os botões de filtro de coluna (eles são recriados a cada renderização).
document.getElementById('secoesContainer').addEventListener('click', (e) => {{
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

// Data "até" default = hoje
document.getElementById('dataAte').value = new Date().toISOString().split('T')[0];

document.getElementById('btnAplicar').addEventListener('click', renderizar);
renderizar();
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
