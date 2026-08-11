"""
Coleta dados do GDash (usinas + faturas de energia) + SunWEG (geração real) e
gera o public/ps_energia.html com os dados embutidos, no mesmo padrão do
dashboard financeiro (Omie).

Requer variáveis de ambiente:
  GDASH_API_KEY     -> chave da API pública do GDash (Configurações > Chave de API)
  SUNWEG_USERNAME   -> e-mail de login do SunWEG (opcional -- sem isso, a
  SUNWEG_PASSWORD   -> senha do SunWEG                coluna de geração fica vazia)

Uso local (teste):
  GDASH_API_KEY=xxxx SUNWEG_USERNAME=xxx SUNWEG_PASSWORD=xxx python atualizar_ps_energia.py

No GitHub Actions, todas devem ser Secrets do repositório -- nunca comitar
usuário/senha/chave no código.

IMPORTANTE (segurança): a integração com a SunWEG usa a biblioteca não-oficial
`sunweg` (pip), que reproduz o login do site sunweg.net. Não é uma API
suportada oficialmente pela WEG -- pode parar de funcionar se o site mudar.
Guarde usuário/senha só como Secret, nunca em texto no repositório.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError

BASE_URL = "https://public-api.gdash.io/api/v1"
API_KEY = os.environ.get("GDASH_API_KEY", "").strip()
SUNWEG_USERNAME = os.environ.get("SUNWEG_USERNAME", "").strip()
SUNWEG_PASSWORD = os.environ.get("SUNWEG_PASSWORD", "").strip()

# Template e mapeamento ficam ao lado deste script (scripts/); o HTML final
# vai para public/ na RAIZ do repositório -- por isso OUTPUT_PATH usa o
# diretório de trabalho (cwd), não SCRIPT_DIR. O workflow do GitHub Actions
# roda `python scripts/atualizar_ps_energia.py` a partir da raiz do repo,
# então cwd == raiz do repo nesse momento.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_PATH = os.path.join(SCRIPT_DIR, "ps_energia_template.html")
MAPEAMENTO_SUNWEG_PATH = os.path.join(SCRIPT_DIR, "mapeamento_sunweg.json")
LOGO_PATH = os.path.join(SCRIPT_DIR, "logo_ps_energia_base64.txt")
OUTPUT_PATH = os.path.join("public", "ps_energia.html")


def chamar_gdash(caminho, params=None, tentativas=4):
    """Chama um endpoint da API do GDash com retry simples em erro/timeout."""
    params = dict(params or {})
    params["apikey"] = API_KEY
    url = f"{BASE_URL}{caminho}?{urlencode(params)}"

    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            req = Request(url, headers={"accept": "application/json"})
            with urlopen(req, timeout=30) as resp:
                corpo = resp.read().decode("utf-8")
                return json.loads(corpo)
        except (HTTPError, URLError, TimeoutError) as e:
            ultimo_erro = e
            espera = 2 ** tentativa
            print(f"  [aviso] falha ao chamar {caminho} (tentativa {tentativa}/{tentativas}): {e}. "
                  f"Aguardando {espera}s...")
            time.sleep(espera)
    raise RuntimeError(f"Falha ao chamar {caminho} após {tentativas} tentativas: {ultimo_erro}")


def buscar_usinas():
    """GET /solar/plants -- lista de usinas da organização."""
    print("Buscando usinas (plants)...")
    resultado = chamar_gdash("/solar/plants")
    usinas = resultado.get("data", [])
    print(f"  {len(usinas)} usinas encontradas.")
    return usinas


def buscar_faturas():
    """GET /solar/energy-billing -- paginado via nextCursor."""
    print("Buscando faturas de energia (energy-billing)...")
    todas = []
    cursor = None
    pagina = 1
    while True:
        params = {"cursor": cursor} if cursor else {}
        resultado = chamar_gdash("/solar/energy-billing", params=params)
        lote = resultado.get("data", [])
        todas.extend(lote)
        print(f"  página {pagina}: {len(lote)} faturas (acumulado: {len(todas)})")

        cursor = resultado.get("nextCursor")
        if not cursor or not lote:
            break
        pagina += 1
        time.sleep(0.3)  # não martelar a API

    print(f"  total: {len(todas)} faturas.")
    return todas


def mes_mais_recente(faturas_da_instalacao):
    """Entre as faturas de uma instalação, pega a de referenceMonth mais recente."""
    return max(faturas_da_instalacao, key=lambda f: f.get("referenceMonth", ""))


def carregar_mapeamento_sunweg():
    """Carrega o mapeamento nome-da-usina-no-GDash -> id-da-usina-no-SunWEG."""
    if not os.path.exists(MAPEAMENTO_SUNWEG_PATH):
        print(f"  [aviso] {MAPEAMENTO_SUNWEG_PATH} não encontrado -- geração ficará vazia.")
        return {}
    with open(MAPEAMENTO_SUNWEG_PATH, "r", encoding="utf-8") as f:
        dados = json.load(f)
    mapa = {}
    for item in dados.get("usinas", []):
        if item.get("portal") == "SunWeg":
            mapa[item["nomeUsinaGdash"]] = item["idPortal"]
    return mapa


def buscar_geracao_sunweg(nomes_usinas, mes_por_usina):
    """
    Busca, pra cada usina mapeada, a produção do mês de referência da fatura
    correspondente (mes_por_usina: {nomeUsinaGdash: 'YYYY-MM'}) e o total
    acumulado da usina. Usa a biblioteca não-oficial `sunweg` (pip install sunweg).

    Retorna: {nomeUsinaGdash: {"geracaoMesKwh": float, "geracaoTotalKwh": float,
                                "economiaSunwegReais": float}}
    """
    if not SUNWEG_USERNAME or not SUNWEG_PASSWORD:
        print("  [aviso] SUNWEG_USERNAME/SUNWEG_PASSWORD não configurados -- pulando geração.")
        return {}

    try:
        from sunweg.api import APIHelper
    except ImportError:
        print("  [aviso] biblioteca 'sunweg' não instalada (pip install sunweg) -- pulando geração.")
        return {}

    mapa_ids = carregar_mapeamento_sunweg()
    if not mapa_ids:
        return {}

    print("Autenticando na SunWEG...")
    api = APIHelper(username=SUNWEG_USERNAME, password=SUNWEG_PASSWORD)
    if not api.authenticate():
        print("  [erro] falha ao autenticar na SunWEG -- verifique usuário/senha.")
        return {}

    resultado = {}
    for nome_usina in nomes_usinas:
        plant_id = mapa_ids.get(nome_usina)
        if plant_id is None:
            continue  # usina não mapeada (ex: portal diferente, tipo PV_HUB)

        try:
            plant = api.plant(int(plant_id))
        except Exception as e:
            print(f"  [aviso] falha ao buscar usina SunWEG id={plant_id} ({nome_usina}): {e}")
            continue
        if plant is None:
            continue

        geracao_mes_kwh = None
        mes_ref = mes_por_usina.get(nome_usina)  # formato "YYYY-MM"
        if mes_ref:
            try:
                ano, mes = (int(x) for x in mes_ref.split("-"))
                stats = api.month_stats_production_by_id(ano, mes, int(plant_id))
                geracao_mes_kwh = sum(s.production for s in stats)
            except Exception as e:
                print(f"  [aviso] falha ao buscar produção mensal de {nome_usina}: {e}")

        resultado[nome_usina] = {
            "geracaoMesKwh": geracao_mes_kwh,
            "geracaoTotalKwh": plant.total_energy,
            "economiaSunwegReais": plant.saving,
        }
        time.sleep(0.3)  # não martelar a API não-oficial

    print(f"  geração coletada para {len(resultado)} usinas.")
    return resultado


def montar_resumo(usinas, faturas, geracao_por_usina=None):
    """
    Agrupa faturas por instalação (installationNumber) e monta uma linha de
    resumo por usina geradora, com o mês de referência mais recente disponível:
      - energia injetada HFP no mês (kWh)               -> hfp.injection
      - energia injetada HFP faturada (kWh)              -> hfp.billedInjection
      - saldo de crédito acumulado (kWh)                 -> hfp.creditBalance
      - sobra/excedente de crédito gerado no mês (kWh)   -> hfp.creditSurplus
      - valor da conta (R$)                              -> accountValue
      - economia no mês (R$)                             -> savings
      - geração real no mês / total (kWh)                -> SunWEG (comparativo com o HFP injetado)

    OBS: os nomes exatos de "saldo utilizado" podem precisar de ajuste fino
    depois de comparar com o relatório visual do GDash -- os campos brutos
    estão todos preservados em `bruto` pra facilitar comparação.
    """
    geracao_por_usina = geracao_por_usina or {}
    por_instalacao = {}
    for f in faturas:
        num = f.get("installationNumber")
        por_instalacao.setdefault(num, []).append(f)

    linhas = []
    for usina in usinas:
        for inst in usina.get("installations", []) or []:
            if not inst.get("unidadeGeradora"):
                continue
            num = inst.get("numeroInstalacao")
            faturas_inst = por_instalacao.get(num, [])
            if not faturas_inst:
                continue
            fatura = mes_mais_recente(faturas_inst)
            hfp = fatura.get("hfp", {}) or {}
            geracao = geracao_por_usina.get(usina.get("name"), {})

            linhas.append({
                "usina": usina.get("name"),
                "instalacao": inst.get("nome") or fatura.get("installationName"),
                "numeroInstalacao": num,
                "mesReferencia": fatura.get("referenceMonth"),
                "energiaInjetadaHfpKwh": hfp.get("injection"),
                "energiaInjetadaFaturadaKwh": hfp.get("billedInjection"),
                "saldoCreditoKwh": hfp.get("creditBalance"),
                "sobraExcedenteKwh": hfp.get("creditSurplus"),
                "valorConta": fatura.get("accountValue"),
                "economiaMes": fatura.get("savings"),
                "concessionaria": fatura.get("utilityCompany"),
                "geracaoMesKwh": geracao.get("geracaoMesKwh"),
                "geracaoTotalKwh": geracao.get("geracaoTotalKwh"),
                "bruto": fatura,
            })

    linhas.sort(key=lambda x: (x["usina"] or "", x["mesReferencia"] or ""))
    return linhas


def extrair_mes_por_usina(usinas, faturas):
    """{nomeUsinaGdash: 'YYYY-MM'} com o mês de referência mais recente, pra
    saber de qual mês pedir a produção na SunWEG."""
    por_instalacao = {}
    for f in faturas:
        por_instalacao.setdefault(f.get("installationNumber"), []).append(f)

    mes_por_usina = {}
    for usina in usinas:
        for inst in usina.get("installations", []) or []:
            if not inst.get("unidadeGeradora"):
                continue
            faturas_inst = por_instalacao.get(inst.get("numeroInstalacao"), [])
            if not faturas_inst:
                continue
            fatura = mes_mais_recente(faturas_inst)
            mes_por_usina[usina.get("name")] = fatura.get("referenceMonth")
    return mes_por_usina


def gerar_html(linhas):
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(
            f"Template não encontrado em {TEMPLATE_PATH}. "
            "Copie ps_energia.html para ps_energia_template.html e adicione o "
            "marcador {{DADOS_JSON}} no lugar dos dados."
        )

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template = f.read()

    dados_json = json.dumps({
        "geradoEm": datetime.now(timezone.utc).isoformat(),
        "linhas": linhas,
    }, ensure_ascii=False)

    html_final = template.replace("__DADOS_PS_ENERGIA_JSON__", dados_json)

    logo_base64 = ""
    if os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "r", encoding="utf-8") as f:
            logo_base64 = f.read().strip()
    html_final = html_final.replace("__LOGO_PS_ENERGIA_BASE64__", logo_base64)

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html_final)

    print(f"Arquivo gerado: {OUTPUT_PATH}")


def main():
    if not API_KEY:
        print("ERRO: defina a variável de ambiente GDASH_API_KEY antes de rodar.", file=sys.stderr)
        sys.exit(1)

    usinas = buscar_usinas()
    faturas = buscar_faturas()
    mes_por_usina = extrair_mes_por_usina(usinas, faturas)
    geracao_por_usina = buscar_geracao_sunweg(list(mes_por_usina.keys()), mes_por_usina)
    linhas = montar_resumo(usinas, faturas, geracao_por_usina)
    print(f"Resumo montado: {len(linhas)} usinas com fatura recente.")
    gerar_html(linhas)


if __name__ == "__main__":
    main()
