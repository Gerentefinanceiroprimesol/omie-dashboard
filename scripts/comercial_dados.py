#!/usr/bin/env python3
"""
comercial_dados.py -- Aba "Comercial" do dashboard Prime Sol.

Independente da aba Lançamentos: não usa os filtros nem os lançamentos
brutos de Contas a Pagar/Receber. Os dados de venda/comissão vêm das
planilhas mensais "Controle de Comissionamento" (uma por mês), gravados
aqui embaixo em COMERCIAL_VENDAS -- não é buscado nada em tempo de
execução pra isso.

A ÚNICA parte buscada ao vivo é a comissão da Ellen (gerente), que não
passa pela planilha de comissão -- ela é lançamento direto no financeiro
da Omie (categoria "Comissões Internas"). A função extrair_comissao_ellen
reaproveita a lista `contas` que o atualizar_dashboard.py já busca pra
aba Lançamentos (não faz nenhuma chamada nova à API).

------------------------------------------------------------------------
COMO ATUALIZAR TODO MÊS
------------------------------------------------------------------------
1. Adicione o nome do mês novo em MESES_COMERCIAL (ex: "Ago").
2. Acrescente as linhas do mês novo em COMERCIAL_VENDAS, no formato:
       (mes, loja, vendedor, modalidade, valor_venda, comissao_liquida)
   Use a coluna TOTAL (comissão + bônus) da aba "Faturamento" da
   planilha de Controle de Comissionamento como comissao_liquida.
   Se a célula da planilha tiver erro de fórmula (#N/A, #VALUE!), use
   None em comissao_liquida.
3. Não precisa mexer em mais nada -- os cards, a tabela e os totais
   são todos calculados a partir dessas duas listas.
------------------------------------------------------------------------
"""

import re
from collections import defaultdict

MESES_COMERCIAL = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul"]  # adicionar mês novo aqui

# Lojas na ordem em que devem aparecer nas tabelas (Matriz -> Lagos -> Cabo
# Frio). Usada pelos cards que quebram faturamento/ticket médio por loja.
LOJAS_COMERCIAL = ["Prime Sol Matriz", "Prime Sol Lagos", "Prime Sol Cabo Frio"]

NOME_ELLEN = "ELLEN CRISTINA VALENTIM NEVES"

# Lojas padronizadas (igual ao DRE/DFC): Prime Sol Matriz / Prime Sol
# Lagos / Prime Sol Cabo Frio. As planilhas de comissão usavam nomes
# variados por mês (Loja Matriz, Prime Sol Campos, Prime Sol RDO, Prime
# Cabo Frio, Prime Sol CF) -- já padronizado nos dados abaixo.

# Cada linha: (mes, loja, vendedor, modalidade, valor_venda, comissao_liquida)
# comissao_liquida = None quando a planilha original tinha erro de
# fórmula (#N/A/#VALUE!) naquela linha -- ver observação no fim do
# arquivo com a lista dessas linhas.
COMERCIAL_VENDAS = [
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 13760, 708.8),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 14980, 898.8),
    ("Jan", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 18860, 680.74),
    ("Jan", "Prime Sol Cabo Frio", "LUCIANO", "EXTERNO", 18690, 0),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13480, 482.26),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 32980, 2438.8),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 17018.47, 696.32),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 16980, 921.86),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 12340, 482.26),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11293.39, 298.66),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 19323.72, 1037.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 12588.46, 373.32),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 16380, 331.06),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 11560, 317.32),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 18360, 950.72),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 20845.06, 950.72),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 16980, 1478.8),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 12000, 1158.8),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 11280, 383.52),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 15675.69, 509.32),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12179.42, 560.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11480, 560.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 15000, 300),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 19244.9, 835.26),
    ("Jan", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10674.03, 129.74),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 14980, 898.8),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 6480, 558.8),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 21980, 1778.8),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 16980, 1478.8),
    ("Jan", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 12560, 319.24),
    ("Jan", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 6800, 84.24),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 6480, 390.32),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10730, 364.82),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 14280, 485.52),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12873.18, 560.32),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 15469.61, 501.77),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 12060, 501.77),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 22286.81, 950.72),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 29517.64, 1394.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10980, 373.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 32480, 1564.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 17259.94, 543.32),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 16340, 591.06),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11677.09, 298.66),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 12340, 477.97),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 12340, 482.26),
    ("Jan", "Prime Sol Cabo Frio", "LUCIANO", "EXTERNO", 19980, 0),
    ("Jan", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 11480, None),
    ("Jan", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 9980, None),
    ("Jan", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 11750, None),
    ("Jan", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 9980, None),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 66980, 2277.32),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 11920, 423.71),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 16019.43, 308.96),
    ("Jan", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 13436.36, 319.24),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 16980, 1478.8),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 35980, 1683.32),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11480, 858.8),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10980, 373.32),
    ("Jan", "Prime Sol Lagos", "PAULA", "INTERNO", 11480, 319.24),
    ("Jan", "Prime Sol Lagos", "PAULA", "INTERNO", 13283.69, 306.24),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 18556, 950.72),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 15980, 684.08),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 16493.08, 509.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 16980, 1037.32),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 53480, 3035.23),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 17000, 1020),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 21000, 1260),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 10000, 770),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 193980, 14398.8),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13187.61, 373.32),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 15480, 509.32),
    ("Jan", "Prime Sol Lagos", "PAULA", "INTERNO", 14480, 358.24),
    ("Jan", "Prime Sol Lagos", "PAULA", "INTERNO", 11920, 319.24),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 10480, 472.87),
    ("Jan", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 12560, 319.24),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11480, 482.26),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 15980, 543.32),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 12235.37, 482.26),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 16360, 432.92),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 20480, 1017.06),
    ("Jan", "Prime Sol Matriz", "MARCELO", "EXTERNO", 125000, 3960),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 153373, 7110),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 12820, 858.8),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 15380, 1092.8),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 12384, 658.8),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 15480, 1098.8),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 14980, 898.8),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 20393.94, 1478.8),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 11970, 598.8),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 66000, 3330),
    ("Jan", "Prime Sol Lagos", "PAULA", "INTERNO", 12060, 319.24),
    ("Jan", "Prime Sol Lagos", "PAULA", "INTERNO", 10480, 306.24),
    ("Jan", "Prime Sol Lagos", "PAULA", "INTERNO", 11480, 304.95),
    ("Jan", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 13130.35, 146.64),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 18360, 835.26),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 14484.47, 423.71),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 12060, 501.77),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 13576.93, 501.77),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 23360, 1095.22),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 11420, 317.32),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 12060, 501.77),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 12060, 501.77),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 21980, 1057.86),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11480, 482.26),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 14821.04, 505.65),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10980, 298.66),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 16980, 1037.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 9400, 319.6),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10980, 373.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 5980, 203.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10980, 373.32),
    ("Jan", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11253.71, 373.32),
    ("Jan", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 15480, None),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 21575.2, 544.48),
    ("Jan", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 12060, 501.77),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 11760, 242.66),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 12870.32, 423.71),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 6660, 132.16),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 15480, 696.32),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10980, 373.32),
    ("Jan", "Prime Sol Matriz", "MOISES", "EXTERNO", 23672.31, 1264.72),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13778, 858.8),
    ("Jan", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 28980, 1738.8),
    ("Jan", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 10999.55, 209.51),
    ("Jan", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13019.46, 464.85),
    ("Jan", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 330000, 5480),
    ("Fev", "Prime Sol Matriz", "MOISES", "EXTERNO", 10460, 627.6),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 299.74),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13788, 858.8),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 22000, 610.55),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 40000, 1189.3),
    ("Fev", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 9980, None),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 14980, 679.32),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 35980, 1683.32),
    ("Fev", "Prime Sol Matriz", "MOISES", "EXTERNO", 47980, 3855.75),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 17480, 935.46),
    ("Fev", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 40480, 1463.53),
    ("Fev", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 36980, 480.74),
    ("Fev", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 15860, 358.24),
    ("Fev", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 7920, 267.24),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 14480, 662.32),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 12060, 253.71),
    ("Fev", "Prime Sol Matriz", "NELSON NAHIM", "EXTERNO", 19480, 761.24),
    ("Fev", "Prime Sol Matriz", "MOISES", "EXTERNO", 12400, 343.8),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 400000, 24000),
    ("Fev", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 12000, 149.24),
    ("Fev", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 17217.51, 182.52),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11980, 568.4),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 16980, 577.32),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 14487.27, 477.12),
    ("Fev", "Prime Sol Lagos", "PAULA", "INTERNO", 5480, 65),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 30680, 1005.6),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 14382.11, 380.26),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11480, 390.32),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 54980, 4338.8),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 43980, 3098.8),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 29980, 2258.8),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 330000, 16350),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 660000, 32700),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11480, 390.32),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11480, 390.32),
    ("Fev", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 9920, 293.24),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13980, 380.26),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11480, 312.26),
    ("Fev", "Prime Sol Lagos", "PAULA", "INTERNO", 18360, 220.74),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 15886.02, 380.26),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 19480, 662.32),
    ("Fev", "Prime Sol Matriz", "MOISES", "EXTERNO", 19215, 1072.3),
    ("Fev", "Prime Sol Lagos", "PAULA", "INTERNO", 12060, 149.24),
    ("Fev", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 14484.75, 149.24),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13980, 475.32),
    ("Fev", "Prime Sol Lagos", "PAULA", "INTERNO", 19073.17, 659.42),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 12560, 434.76),
    ("Fev", "Prime Sol Matriz", "MOISES", "EXTERNO", 195000, 9540),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11980, 495.86),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 17650, 229.45),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11480, 390.32),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13692.05, 387.6),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 12840, 495.86),
    ("Fev", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 9980, None),
    ("Fev", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 15000, None),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13980, 380.26),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 15480, 371.24),
    ("Fev", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 11059.81, 293.24),
    ("Fev", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 12138.09, 150.02),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9480, 293.24),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 17391.3, 563.86),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 30480, 829.06),
    ("Fev", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 9655.05, 117.84),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10851, 136.24),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12620.93, 149.24),
    ("Fev", "Prime Sol Matriz", "MOISES", "EXTERNO", 11026.72, 598.8),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13026, 888.8),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13788.13, 390.32),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10480, 136.24),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 16800, 838.8),
    ("Fev", "Prime Sol Lagos", "PAULA", "INTERNO", 9980, 299.74),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13980, 475.32),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 182000, 10920),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 17280.21, 466.14),
    ("Fev", "Prime Sol Lagos", "PAULA", "INTERNO", 19860, 700.24),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 8980, 305.32),
    ("Fev", "Prime Sol Matriz", "MOISES", "EXTERNO", 14805.45, 1038.8),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 15860, 490.01),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13376.51, 495.86),
    ("Fev", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 15480, None),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 28980, 1738.8),
    ("Fev", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 17588, 958.8),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 22279.61, 220.74),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 15080, 661.06),
    ("Fev", "Prime Sol Lagos", "PAULA", "INTERNO", 12000, 149.24),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 28972.49, 1209.62),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 7614.66, 267.24),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10360, 386.14),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 2000, 68),
    ("Fev", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 14980, 364.74),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 13467.83, 253.71),
    ("Fev", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 15900, 253.71),
    ("Fev", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13788.13, 390.32),
    ("Fev", "Prime Sol Matriz", "LEANDRO", "INTERNO", 2149, 50),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 331.89),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 14388.66, 407.32),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 339.32),
    ("Mar", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 10000, 221),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 18969.63, 646.27),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10981.26, 339.32),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10178.37, 288.42),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13501.5, 346.22),
    ("Mar", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 72000, 2099.27),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 11684, 339.29),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 21980, 1688.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 32000, 1737.6),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 18000, 1080),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 199.6),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 9980, 339.32),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 41328.71, 1010.92),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 399.2),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 12480, 732.4),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13980, 404.02),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 45327.89, 1559.24),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 7560, 371.72),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 9980, 598.8),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 23980, 1275.32),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11980, 577.32),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13980, 404.02),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 9980, 169.66),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 19192.89, 461.82),
    ("Mar", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 13500, 336.14),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 24580, 1934.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 9980, 598.8),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 30980, 1297.52),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 14989.19, 424.32),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 13833.44, 545.12),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11970, 768.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13480, 948.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 9980, 768.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 24980, 1498.8),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 11182, 598.8),
    ("Mar", "Prime Sol Matriz", "SUELLEN", "EXTERNO", 12980, 689.2),
    ("Mar", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 16760, 332.24),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 29480, 1462.32),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 11986.55, 339.32),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 199.6),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 8383, 588.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 31068, 2228.8),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13720.32, 360.67),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 13934.79, 424.32),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 29980, 1798.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 28250, 0),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 15613.74, 436.21),
    ("Mar", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10839, 133.37),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 12280, 354.89),
    ("Mar", "Prime Sol Matriz", "IVAN COSTA", "EXTERNO", 11980, None),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 150000, 7350),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 12480, 424.32),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 17720, 646.27),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13000, 950),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 320000, 8915.07),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 16286.33, 623.91),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 21000, 1182),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 16415.85, 662.32),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 37232.58, 2320),
    ("Mar", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 9980, None),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 9480, 492.32),
    ("Mar", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 17795.85, 490.01),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13501.46, 409.6),
    ("Mar", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 12560, 325.74),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11980, 516.22),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11980, 577.32),
    ("Mar", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 15160.72, 662.32),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 17980, 819.6),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 9309.22, 259.52),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 350000, 17550),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 5500, 0),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 143980, 9558.8),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11502.12, 362.04),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 16610.4, 411.21),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11480, 379.77),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 108846.5, 3179.0),
    ("Mar", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 45235.05, 1005.74),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 19980, 1139.32),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 11480, 688.8),
    ("Mar", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 9980, None),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11840, 543.32),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 11480, 688.8),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 31000, 1860),
    ("Mar", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 9980, None),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13980, 404.02),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 20488.4, 979.62),
    ("Mar", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 12900, 434.76),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 11480, 688.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11980, 888.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 12993.84, 888.8),
    ("Mar", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 20009.61, 507.69),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 10785.49, 538.8),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 4780.21, 135.32),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13682, 888.8),
    ("Mar", "Prime Sol Matriz", "MOISES", "EXTERNO", 11480, 688.8),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 12773.09, 331.77),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 17702.42, 608.53),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 77480, 1317.16),
    ("Mar", "Prime Sol Cabo Frio", "MARIANNA DE ARAUJO", "INTERNO", 23780, None),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 99883.5, 2352.8),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 14596.57, 662.32),
    ("Mar", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 14024.12, 434.76),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 20480, 1688.8),
    ("Mar", "Prime Sol Matriz", "LEANDRO", "INTERNO", 15421.57, 577.32),
    ("Mar", "Prime Sol Matriz", "DIANGELO", "INTERNO", 14980, 588.47),
    ("Mar", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 19720, 704.07),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 24597, 1688.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11386, 738.8),
    ("Mar", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 12983, 688.8),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 12980, 649),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 13433.84, 649),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 12453, 524),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 14585, 649),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 13750, 365.81),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 32660, 1496.32),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 271.46),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 19200, 983.26),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 15386, 978.8),
    ("Abr", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 10060, 293.24),
    ("Abr", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 13560, 168.74),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 13087.08, 285.06),
    ("Abr", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 21508.9, 390.74),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 25768.7, 1156.32),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 11701.65, 285.06),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 11700, 1180.92),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 36500.79, 1581.32),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 21480, 1074),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 16190.16, 978.8),
    ("Abr", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10500, 136.24),
    ("Abr", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 18860, 390.74),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 14060, 628.32),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11701.65, 798.8),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 60980, 2774),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 19793.42, 448.26),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 10480, 302.87),
    ("Abr", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 15881.58, 345.24),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12980, 353.06),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 12980, 611.32),
    ("Abr", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 11060, 306.24),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13980, 978.8),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 24192.68, 1688.8),
    ("Abr", "Prime Sol Matriz", "LEANDRO", "INTERNO", 15980, 0),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 16790.78, 978.8),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 271.46),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 23480, 1098.66),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 14934.4, 628.32),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 10480, 288.42),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 19980, 1139.32),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 9980, 339.32),
    ("Abr", "Prime Sol Matriz", "LEANDRO", "INTERNO", 8980, 0),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 10480, 302.87),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 12453, 524),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 10480, 524),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 10920, 526.32),
    ("Abr", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 14000, 345.24),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 13427.08, 285.06),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 12587.08, 798.8),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 47980, 3338.8),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 10480, 241.98),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 44380, 1184.32),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 13830, 649),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10250, 278.8),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 110000, 3017.5),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 26480, 1360.32),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 10931.4, 598.8),
    ("Abr", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 20360, 246.74),
    ("Abr", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 13420, 338.74),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 23980, 1898.8),
    ("Abr", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 13876.69, 338.74),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11000, 660),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 44184.87, 1768.32),
    ("Abr", "Prime Sol Matriz", "MOISES", "EXTERNO", 9980, 199.6),
    ("Abr", "Prime Sol Matriz", "IVAN COSTA", "EXTERNO", 19980, None),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 12980, 611.32),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 30000, 1202.85),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 69500, 1910.98),
    ("Abr", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 9980, 99.8),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 29192.89, 1934.8),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 100950, 6977),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 40174.18, 2066.37),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 9980, 271.46),
    ("Abr", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 9980, 129.74),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 16480, 1158.8),
    ("Abr", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13934.79, 748.8),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 23997.12, 1003.46),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 17246.68, 1020.32),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 17336.57, 738.3),
    ("Abr", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 30000, 352.35),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 36856, 710.07),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 13756, 567.55),
    ("Abr", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 15860, 190.06),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12480, 339.46),
    ("Abr", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 11160, 526.32),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 11986.55, 271.46),
    ("Abr", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 9980, 288.42),
    ("Abr", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 16726.22, 338.74),
    ("Abr", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 19980, 1003.46),
    ("Mai", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 11670, 129.38),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13250, 344.11),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 12480, 305.61),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 68440, 4658.37),
    ("Mai", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 13000, 124.8),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 14480, 999.29),
    ("Mai", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 16056, 188.24),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11585.24, 241.74),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 61380, 2508.34),
    ("Mai", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 13450, 167.05),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 21860, 1051.87),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11340, 285.06),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11340, 285.06),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10500, 267.24),
    ("Mai", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 16056, 348.81),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 12000, 268.2),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 16500, 825),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 7851.7, 182.12),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 20980, 1664.56),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10480, 255.16),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 9825.84, 440.0),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10480, 285.06),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 19980, 509.49),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 16980, 849),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 23480, 1174),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 250000, 12960),
    ("Mai", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 10480, 136.24),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 14560, 526.49),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 7000, 350),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 11235.68, 524),
    ("Mai", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 10480, 136.24),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 18473.84, 552.91),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 13560, 330.99),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 21080, 577.42),
    ("Mai", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 14000, 175.24),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 62480, 4208.8),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 11480, 437.24),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 15000, 526.49),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 14673.7, 478.96),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 14917, 978.8),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13627.09, 366.66),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 23997.12, 543.46),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 7665.81, 152.49),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 19790, 958.8),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 11024.63, 231.61),
    ("Mai", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 21556, 719.74),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 17380, 271.68),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10480, 285.06),
    ("Mai", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 10530.82, 136.24),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 13187.61, 487.32),
    ("Mai", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 25940, 281.26),
    ("Mai", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 11351.94, 136.24),
    ("Mai", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 18720, 397.24),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 16980, 451.74),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 11404.76, 267.75),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 16980, 545.26),
    ("Mai", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 10480, 104.8),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 20410.9, 824.21),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11468, 628.8),
    ("Mai", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 11000, 136.24),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 20393.95, 375.26),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10892.84, 267.24),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 26039.39, 905.74),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 19980, 543.46),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10480, 285.06),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 13480, 467.91),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 19360, 905.74),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 14645.7, 366.66),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 32520, 1599),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13480, 343.74),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13480, 343.74),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 32980, 897.06),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 14118.87, 330.99),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 19480, 496.74),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 21000, 1049.08),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 20860, 562.97),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 20860, 562.97),
    ("Mai", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 27860, 804.24),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13415.64, 628.8),
    ("Mai", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 15282.06, 175.24),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 14819.7, 467.91),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 16190.25, 978.8),
    ("Mai", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 13920, 345.24),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12980, 286.86),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 10480, 524),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 10480, 288.42),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 320000, 15900),
    ("Mai", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 10480, 798.8),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 4000, 102),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 14060, 559.57),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 6980, 177.99),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 15061.7, 559.62),
    ("Mai", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 20000, 240.24),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 18857.53, 602.99),
    ("Mai", "Prime Sol Matriz", "MOISES", "EXTERNO", 17900, 875.4),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13480, 513.74),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 18360, 660.72),
    ("Mai", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 10480, 108.1),
    ("Mai", "Prime Sol Matriz", "DIANGELO", "INTERNO", 16825.86, 420.24),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 11986.55, 271.46),
    ("Mai", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 12980, 136.24),
    ("Mai", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 11340, 136.24),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10798.15, 267.24),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10648.82, 271.46),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 31980, 924.22),
    ("Mai", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 22061.01, 390.74),
    ("Mai", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10648.82, 271.46),
    ("Mai", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10480, 255.16),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 14795, 371.35),
    ("Mai", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12390.04, 231.61),
    ("Mai", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 14792.01, 559.57),
    ("Mai", "Prime Sol Matriz", "MARCELO", "EXTERNO", 183480, 7971.9),
    ("Mai", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 45000, 2700),
    ("Jun", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 11701.65, 136.24),
    ("Jun", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 176739.23, 4573.0),
    ("Jun", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 25200, 1258.32),
    ("Jun", "Prime Sol Matriz", "SUELLEN", "EXTERNO", 33980, 1819.2),
    ("Jun", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 50360, 2057.32),
    ("Jun", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13947.11, 458.32),
    ("Jun", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 28860, 1394.32),
    ("Jun", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 15609.65, 645.32),
    ("Jun", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 12480, 424.32),
    ("Jun", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 19500, 687.24),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13480, 789.92),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13480, 808.8),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 15050, 808.8),
    ("Jun", "Prime Sol Matriz", "DIANGELO", "INTERNO", 22980, 1090.66),
    ("Jun", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 56500, 1135.74),
    ("Jun", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Matriz", "DIANGELO", "INTERNO", 66980, 2281.86),
    ("Jun", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 11756, 312.74),
    ("Jun", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13555.75, 366.66),
    ("Jun", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 11701.65, 136.24),
    ("Jun", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 29226.23, 817.24),
    ("Jun", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 13046.2, 136.24),
    ("Jun", "Prime Sol Matriz", "DIANGELO", "INTERNO", 32980, 897.06),
    ("Jun", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 14000, 802.03),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 110000, 5370),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 270000, 15600),
    ("Jun", "Prime Sol Matriz", "MOISES", "EXTERNO", 17750, 579.2),
    ("Jun", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 10480, 120.36),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 110000, 5370),
    ("Jun", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 16056, 188.24),
    ("Jun", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 26260, 778.24),
    ("Jun", "Prime Sol Matriz", "MARCELO", "EXTERNO", 300000, 15300),
    ("Jun", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 14160, 0),
    ("Jun", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 11402.9, 104.8),
    ("Jun", "Prime Sol Matriz", "LEANDRO", "INTERNO", 14479, 458.32),
    ("Jun", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 11247, 135.46),
    ("Jun", "Prime Sol Matriz", "LEANDRO", "INTERNO", 130000, 3646.5),
    ("Jun", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 17200, 205.33),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 45980, 3128.8),
    ("Jun", "Prime Sol Matriz", "LEANDRO", "INTERNO", 136000, 3646.5),
    ("Jun", "Prime Sol Cabo Frio", "LUIZ CARLOS", "INTERNO", 12480, None),
    ("Jun", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 18368, 220.74),
    ("Jun", "Prime Sol Cabo Frio", "LUIZ CARLOS", "INTERNO", 11000, None),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 109980, 6000),
    ("Jun", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Matriz", "LEANDRO", "INTERNO", 20393.95, 577.32),
    ("Jun", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 14016.33, 136.24),
    ("Jun", "Prime Sol Matriz", "MOISES", "EXTERNO", 10480, 419.2),
    ("Jun", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 10480, 136.24),
    ("Jun", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 31803.99, 344.24),
    ("Jun", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 13480, 175.24),
    ("Jun", "Prime Sol Matriz", "MOISES", "EXTERNO", 10480, 419.2),
    ("Jun", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10127.74, 94.8),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 91980, 5518.8),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 53999.5, 3362.44),
    ("Jun", "Prime Sol Matriz", "LEANDRO", "INTERNO", 66000, 1929.5),
    ("Jun", "Prime Sol Matriz", "DIANGELO", "INTERNO", 20480, 1003.46),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11118.8, 628.8),
    ("Jun", "Prime Sol Matriz", "DIANGELO", "INTERNO", 16637.87, 366.66),
    ("Jun", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 14000, 172.6),
    ("Jun", "Prime Sol Matriz", "LEANDRO", "INTERNO", 7480, 254.32),
    ("Jun", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 26980, 2138.8),
    ("Jun", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 42356, 519.74),
    ("Jun", "Prime Sol Matriz", "DIANGELO", "INTERNO", 17480, 645.46),
    ("Jun", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10480, 285.06),
    ("Jun", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 10480, 109.16),
    ("Jun", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 13480, 175.24),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 11560, 451.54),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10480, 271.58),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 17967.81, 550.26),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 20980, 1651),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "EXTERNO", 10180, 270.83),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 9855.57, 268.07),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10480, 271.58),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 9855.57, 591.33),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 30472.2, 1167.47),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13480, 366.66),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 22780, 1053.26),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 26250, 1167.47),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13630.43, 349.49),
    ("Jul", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 21360, 416.86),
    ("Jul", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 7480, 97.24),
    ("Jul", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 32000, 1144.66),
    ("Jul", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 15698.98, 297.91),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10355.17, 127.83),
    ("Jul", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 19980, 1139.32),
    ("Jul", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 66000, 779.19),
    ("Jul", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 10480, 104.8),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 19980, 1155.52),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 14136, 972.66),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 19480, 680.95),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13480, 346.77),
    ("Jul", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12980, 587.38),
    ("Jul", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 21360, 441.56),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 25800, 1844.84),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10480, 285.06),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 13800, 998),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 17000, 1070),
    ("Jul", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 3349.71, 66.3),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 9855.57, 128.12),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 14560, 529.6),
    ("Jul", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 10480, 231.61),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 23030, 795.68),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10480, 285.06),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "EXTERNO", 18160, 562.63),
    ("Jul", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 10480, 219.05),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 17060, 550.26),
    ("Jul", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 200000, 5678),
    ("Jul", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 66000, 660),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 13480, 346.77),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 17480, 1508.8),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 17480, 1508.8),
    ("Jul", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 8505.2, 239.24),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 12500, 340),
    ("Jul", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 100000, 2771),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 22760, 1007.96),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 31480, 2018.8),
    ("Jul", "Prime Sol Lagos", "LUIS AUGUSTO", "INTERNO", 10480, 0),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 22780, 1037.42),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 10980, 793.54),
    ("Jul", "Prime Sol Matriz", "MOISES", "EXTERNO", 7480, 299.2),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 13480, 165.73),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10480, 274.04),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 17480, 208.41),
    ("Jul", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 12277.41, 356.32),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 180000, 10800),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 25000, 1066.32),
    ("Jul", "Prime Sol Matriz", "MOISES", "EXTERNO", 12222.94, 419.2),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 89000, 5340),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 10480, 285.06),
    ("Jul", "Prime Sol Cabo Frio", "MIGUEL BRITES", "INTERNO", 13256, 162.24),
    ("Jul", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 13700, 275.81),
    ("Jul", "Prime Sol Matriz", "ROSIÉE", "INTERNO", 293000, 8262),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 13199.62, 285.06),
    ("Jul", "Prime Sol Matriz", "NELSON NAHIM", "EXTERNO", 153460, None),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 11312.93, 630.0),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 195000, 9870),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "EXTERNO", 85000, 2033.12),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10480, 136.24),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10480, 285.06),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 14660, 556.78),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 8895.4, 97.24),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 26480, 1588.8),
    ("Jul", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 16060, 320.1),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 74000, 4074.05),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 10678.77, 285.06),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10000, 123.24),
    ("Jul", "Prime Sol Matriz", "MOISES", "EXTERNO", 13480, 539.2),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 26700, 250.3),
    ("Jul", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 21724, 901.56),
    ("Jul", "Prime Sol Matriz", "DIANGELO", "INTERNO", 10480, 136.24),
    ("Jul", "Prime Sol Lagos", "CÁSSIO", "INTERNO", 29500, 1082.68),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 19980, 543.46),
    ("Jul", "Prime Sol Cabo Frio", "THIAGO MARQUES", "INTERNO", 25056, 493.88),
    ("Jul", "Prime Sol Matriz", "JAMIL JUNIOR", "EXTERNO", 17480, 1508.8),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 15282.73, 550.26),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 22540, 1090.66),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 14932, 574.02),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 9403.37, 387.51),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 16500, 448.8),
    ("Jul", "Prime Sol Matriz", "MOISES", "EXTERNO", 10480, 419.2),
    ("Jul", "Prime Sol Matriz", "LEANDRO", "INTERNO", 7980, 297.68),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 9398, 650.4),
    ("Jul", "Prime Sol Lagos", "GLEIDSON", "INTERNO", 3800, 0),
    ("Jul", "Prime Sol Lagos", "ALEXANDER", "INTERNO", 3900, 0),
]


def extrair_comissao_ellen(contas: list) -> dict:
    """Varre os lançamentos JÁ BUSCADOS da Omie (mesma lista `contas` que
    a aba Lançamentos usa -- não dispara nenhuma chamada nova à API)
    procurando os pagamentos de comissão da Ellen, e devolve
    {mes_competencia: valor}.

    A observação do lançamento traz o mês de competência de forma
    explícita (ex: "Comissão 03/2026", "COMISSÃO JAN/2026|NOME:..."), e
    usamos isso em vez de inferir pela data de pagamento -- mais
    confiável. Se não der pra casar a observação com um mês/ano
    reconhecido, cai no fallback (mês de pagamento - 1, que é a regra
    combinada: comissão de um mês é paga no mês seguinte).
    """
    nomes_mes = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
                 7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}
    abrev_para_num = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
                       "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}
    padrao_num = re.compile(r"(\d{1,2})[./](\d{4})")
    padrao_nome_mes = re.compile(r"(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)[A-Z]*[/. ](\d{4})", re.IGNORECASE)

    resultado = {}
    for conta in contas:
        for lanc in conta.get("lancamentos", []):
            cliente = (lanc.get("cliente") or "").upper()
            categoria = (lanc.get("categoria") or "").strip()
            if "ELLEN" not in cliente or categoria != "Comissões Internas":
                continue

            obs = lanc.get("observacao") or ""
            mes_num, ano = None, None
            m = padrao_num.search(obs)
            if m:
                mes_num, ano = int(m.group(1)), int(m.group(2))
            else:
                m2 = padrao_nome_mes.search(obs)
                if m2:
                    mes_num = abrev_para_num.get(m2.group(1).upper()[:3])
                    ano = int(m2.group(2))

            if mes_num and 1 <= mes_num <= 12:
                mes_nome = nomes_mes[mes_num]
            else:
                data = lanc.get("data") or ""
                try:
                    _, mes_pgto, _ano = data.split("/")
                    mes_pgto = int(mes_pgto)
                    mes_num = 12 if mes_pgto == 1 else mes_pgto - 1
                    mes_nome = nomes_mes[mes_num]
                except Exception:
                    continue

            valor = abs(lanc.get("valor") or 0)
            resultado[mes_nome] = resultado.get(mes_nome, 0) + valor

    return resultado


def _agregar(dados_ellen: dict) -> dict:
    """Consolida COMERCIAL_VENDAS + a comissão da Ellen em todos os
    agrupamentos usados pelos cards e pela tabela."""
    por_loja_mes = defaultdict(lambda: defaultdict(float))
    por_vendedor_mes = defaultdict(lambda: defaultdict(float))
    modalidade_vendedor = {}
    loja_vendedor = {}
    por_modalidade = defaultdict(lambda: {"faturamento": 0.0, "comissao": 0.0, "vendas": 0})
    fat_mes_total = defaultdict(float)
    com_mes_total = defaultdict(float)
    fat_loja_total = defaultdict(float)
    com_loja_total = defaultdict(float)

    for mes, loja, vendedor, modalidade, valor, comissao in COMERCIAL_VENDAS:
        modalidade_vendedor[vendedor] = modalidade
        loja_vendedor[vendedor] = loja
        if valor is not None:
            por_loja_mes[loja][mes] += valor
            por_vendedor_mes[vendedor][mes] += valor
            fat_mes_total[mes] += valor
            fat_loja_total[loja] += valor
            if modalidade in ("INTERNO", "EXTERNO"):
                por_modalidade[modalidade]["faturamento"] += valor
                por_modalidade[modalidade]["vendas"] += 1
        if comissao is not None:
            com_mes_total[mes] += comissao
            com_loja_total[loja] += comissao
            if modalidade in ("INTERNO", "EXTERNO"):
                por_modalidade[modalidade]["comissao"] += comissao

    for mes, valor in (dados_ellen or {}).items():
        com_mes_total[mes] += valor
        com_loja_total["Gerência (todas as lojas)"] += valor

    return dict(
        por_loja_mes=por_loja_mes, por_vendedor_mes=por_vendedor_mes,
        modalidade_vendedor=modalidade_vendedor, loja_vendedor=loja_vendedor,
        por_modalidade=por_modalidade, fat_mes_total=fat_mes_total,
        com_mes_total=com_mes_total, fat_loja_total=fat_loja_total,
        com_loja_total=com_loja_total,
    )


def _fmt_moeda(v):
    if v is None:
        return "-"
    return f"R$ {v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _fmt_moeda_compacta(v):
    if v is None:
        return "-"
    if abs(v) >= 1_000_000:
        return f"R$ {v/1_000_000:,.2f}M".replace(",", "_").replace(".", ",").replace("_", ".")
    if abs(v) >= 1_000:
        return f"R$ {v/1000:,.0f}K".replace(",", ".")
    return f"R$ {v:,.0f}".replace(",", ".")


def _linha_delta(atual, anterior):
    if anterior in (None, 0) or atual is None:
        return "<span class=\'com-delta-vazio\'>-</span>"
    delta = (atual - anterior) / anterior * 100
    seta = "&#9650;" if delta >= 0 else "&#9660;"
    classe = "com-up" if delta >= 0 else "com-down"
    return f"<span class=\'com-delta {classe}\'>{seta} {delta:+.1f}%</span>"


def gerar_html_aba_comercial(dados_ellen: dict) -> str:
    """Devolve o HTML/CSS/JS completo da aba Comercial (cards + tabela
    de desempenho por vendedor), pronto pra ser embutido no ponto onde
    as outras abas (Lançamentos/DFC/DRE/Insights) são montadas em
    gerar_html()."""
    ag = _agregar(dados_ellen)
    meses = MESES_COMERCIAL

    fat_total = sum(ag["fat_mes_total"].values())
    com_total = sum(ag["com_mes_total"].values())
    n_meses = len(meses)

    def _valor_loja_mes(loja, mes):
        return ag["por_loja_mes"].get(loja, {}).get(mes, 0)

    # ---- Card hero: comissão total + mini-tabela por mês, com faturamento
    # quebrado por loja (Matriz/Lagos/Cabo Frio) + Total, e linhas de Total
    # e Média no fim do período ----
    linhas_mes = "".join(
        f"<tr><td>{m}</td>"
        + "".join(f"<td>{_fmt_moeda(_valor_loja_mes(loja, m))}</td>" for loja in LOJAS_COMERCIAL)
        + f"<td>{_fmt_moeda(ag['fat_mes_total'].get(m, 0))}</td>"
        f"<td>{_fmt_moeda(ag['com_mes_total'].get(m, 0))}</td></tr>"
        for m in meses
    )
    total_por_loja = {loja: sum(_valor_loja_mes(loja, m) for m in meses) for loja in LOJAS_COMERCIAL}
    linha_total_mini = (
        "<tr class='com-total'><td>Total</td>"
        + "".join(f"<td>{_fmt_moeda(total_por_loja[loja])}</td>" for loja in LOJAS_COMERCIAL)
        + f"<td>{_fmt_moeda(fat_total)}</td>"
        f"<td>{_fmt_moeda(com_total)}</td></tr>"
    )
    linha_media_mini = (
        "<tr class='com-total'><td>Média</td>"
        + "".join(f"<td>{_fmt_moeda(total_por_loja[loja] / n_meses if n_meses else 0)}</td>" for loja in LOJAS_COMERCIAL)
        + f"<td>{_fmt_moeda(fat_total / n_meses if n_meses else 0)}</td>"
        f"<td>{_fmt_moeda(com_total / n_meses if n_meses else 0)}</td></tr>"
    )
    card_hero = (
        "<div class=\'com-card com-hero com-card-exp\' onclick=\'comToggle(this)\'>"
        f"<div class=\'com-card-head\'><span class=\'com-label\'>Comissão comercial total ({meses[0]}-{meses[-1]}/26)</span>"
        "<span class=\'com-hint\'>detalhar <span class=\'com-seta\'>&#9662;</span></span></div>"
        f"<div class=\'com-hero-val\'>{_fmt_moeda(com_total)}</div>"
        f"<div class=\'com-sub\'>sobre {_fmt_moeda(fat_total)} de faturamento &middot; clique pra ver por mês</div>"
        f"<div class=\'com-corpo\'><table class=\'com-mini\'><thead><tr><th>Mês</th><th>Prime Sol Matriz</th><th>Prime Sol Lagos</th><th>Prime Sol Cabo Frio</th><th>Faturamento Total</th><th>Comissão</th></tr></thead>"
        f"<tbody>{linhas_mes}{linha_total_mini}{linha_media_mini}</tbody></table></div></div>"
    )

    # ---- Card: faturamento por loja -- resumo (loja que mais faturou no
    # período) no card fechado; ao expandir, tabela com o % que cada loja
    # representou em cada mês, mais uma linha de Média ----
    lojas_ordenadas = sorted(ag["fat_loja_total"].items(), key=lambda x: -x[1])
    loja_top, loja_top_val = lojas_ordenadas[0] if lojas_ordenadas else ("-", 0)
    pct_top = (loja_top_val / fat_total * 100) if fat_total else 0

    def _pct_loja_mes(loja, mes):
        total_mes = ag["fat_mes_total"].get(mes, 0)
        if not total_mes:
            return None
        return _valor_loja_mes(loja, mes) / total_mes * 100

    linhas_pct_loja = "".join(
        f"<tr><td>{m}</td>"
        + "".join(
            f"<td>{(f'{_pct_loja_mes(loja, m):.1f}%' if _pct_loja_mes(loja, m) is not None else '—')}</td>"
            for loja in LOJAS_COMERCIAL
        )
        + "</tr>"
        for m in meses
    )
    medias_pct_loja = {}
    for loja in LOJAS_COMERCIAL:
        valores = [_pct_loja_mes(loja, m) for m in meses if _pct_loja_mes(loja, m) is not None]
        medias_pct_loja[loja] = sum(valores) / len(valores) if valores else None
    linha_media_pct_loja = (
        "<tr class='com-total'><td>Média</td>"
        + "".join(
            f"<td>{(f'{medias_pct_loja[loja]:.1f}%' if medias_pct_loja[loja] is not None else '—')}</td>"
            for loja in LOJAS_COMERCIAL
        )
        + "</tr>"
    )
    card_loja = (
        "<div class='com-card com-card-exp' onclick='comToggle(this)'>"
        "<div class='com-card-head'><span class='com-label'>Faturamento por Loja</span>"
        "<span class='com-hint'>detalhar <span class='com-seta'>&#9662;</span></span></div>"
        f"<div class='com-value'>{loja_top}</div>"
        f"<div class='com-sub'>{pct_top:.0f}% do faturamento total ({meses[0]}-{meses[-1]}/26) &middot; clique pra ver % mês a mês</div>"
        f"<div class='com-corpo'><table class='com-mini'><thead><tr><th>Mês</th><th>Prime Sol Matriz</th><th>Prime Sol Lagos</th><th>Prime Sol Cabo Frio</th></tr></thead>"
        f"<tbody>{linhas_pct_loja}{linha_media_pct_loja}</tbody></table></div></div>"
    )

    # ---- Card: vendedor que mais faturou -- ao expandir, um bloco por mês
    # com o top5 vendedores DAQUELE mês (nome, faturamento, % do mês),
    # seguido de um bloco Total com o top5 do período inteiro (%do período
    # + média do % mensal que cada um representou, só nos meses em que
    # teve venda) ----
    fat_vendedor_total = {v: sum(meses_v.values()) for v, meses_v in ag["por_vendedor_mes"].items()}
    n_vendas_vendedor = defaultdict(int)
    com_vendedor_total = defaultdict(float)
    for mes, loja, vendedor, modalidade, valor, comissao in COMERCIAL_VENDAS:
        if valor is not None:
            n_vendas_vendedor[vendedor] += 1
        if comissao is not None:
            com_vendedor_total[vendedor] += comissao
    ranking_vendedor = sorted(fat_vendedor_total.items(), key=lambda x: -x[1])
    top5 = ranking_vendedor[:5]
    vend_top, vend_top_val = ranking_vendedor[0] if ranking_vendedor else ("-", 0)

    def _pct_vendedor_mes(vendedor, mes):
        total_mes = ag["fat_mes_total"].get(mes, 0)
        if not total_mes:
            return None
        valor = ag["por_vendedor_mes"].get(vendedor, {}).get(mes, 0)
        if not valor:
            return None
        return valor / total_mes * 100

    # Ranking mensal (top 5) de cada mês, guardado num dict pra montar a
    # tabela única abaixo -- antes cada mês virava um mini-tabela solta
    # lado a lado (7 tabelinhas separadas); combinado com Fabrício em
    # 13/08/2026 pra juntar tudo numa tabela só, no mesmo estilo (mês
    # centralizado + divisória) da tabela "Desempenho por Vendedor" logo
    # abaixo na página, pra ficar visualmente mais harmonioso.
    rankings_mensais = {}
    for mes in meses:
        ranking_mes = sorted(
            ((v, ag["por_vendedor_mes"].get(v, {}).get(mes, 0)) for v in ag["por_vendedor_mes"]),
            key=lambda x: -x[1],
        )
        rankings_mensais[mes] = [(v, val) for v, val in ranking_mes if val > 0][:5]

    # Linhas = posição no ranking (1º a 5º); colunas = mês (Vendedor,
    # Faturamento, % do mês) -- célula "—" quando aquele mês não teve 5
    # vendedores com venda.
    thead_meses_rank = "".join(f"<th colspan='3' class='com-col-divisor'>{m}</th>" for m in meses)
    thead_sub_rank = "<th>#</th>" + "<th class='com-col-divisor'>Vendedor</th><th>Faturamento</th><th>% do mês</th>" * len(meses)

    linhas_ranking = []
    for pos in range(5):
        celulas = []
        for mes in meses:
            total_mes = ag["fat_mes_total"].get(mes, 0)
            ranking_mes = rankings_mensais[mes]
            if pos < len(ranking_mes):
                v, val = ranking_mes[pos]
                pct = (val / total_mes * 100) if total_mes else 0
                celulas.append(
                    f"<td class='com-col-divisor'>{v.title()}</td><td>{_fmt_moeda_compacta(val)}</td><td>{pct:.1f}%</td>"
                )
            else:
                celulas.append("<td class='com-col-divisor'>—</td><td>—</td><td>—</td>")
        linhas_ranking.append(f"<tr><td>{pos + 1}&ordm;</td>{''.join(celulas)}</tr>")
    linhas_ranking_html = "".join(linhas_ranking)

    html_meses_vendedor = (
        "<div class='com-tabela-scroll'><table class='com-tabela'>"
        f"<thead><tr><th></th>{thead_meses_rank}</tr><tr>{thead_sub_rank}</tr></thead>"
        f"<tbody>{linhas_ranking_html}</tbody></table></div>"
    )

    def _media_pct_vendedor(vendedor):
        valores = [p for p in (_pct_vendedor_mes(vendedor, m) for m in meses) if p is not None]
        return sum(valores) / len(valores) if valores else None

    linhas_total_top5 = "".join(
        f"<tr><td>{v.title()}</td><td>{_fmt_moeda_compacta(val)}</td>"
        f"<td>{(val / fat_total * 100 if fat_total else 0):.1f}%</td>"
        f"<td>{(f'{_media_pct_vendedor(v):.1f}%' if _media_pct_vendedor(v) is not None else '—')}</td></tr>"
        for v, val in top5
    )
    bloco_total_vendedor = (
        f"<div class='com-mes-bloco com-mes-bloco-total'><div class='com-mes-titulo'>Total ({meses[0]}-{meses[-1]}/26)</div>"
        f"<table class='com-mini'><thead><tr><th>Vendedor</th><th>Faturamento</th><th>% do período</th><th>Média % mensal</th></tr></thead>"
        f"<tbody>{linhas_total_top5}</tbody></table></div>"
    )
    # com-card-full: único card que precisa expandir pra largura total ao
    # abrir (a tabela tem muitas colunas -- 7 meses x 3 cada). Os outros
    # cards da aba só crescem pra baixo (ver regra .com-card-exp.aberto no
    # CSS) -- combinado com Fabrício em 13/08/2026, que achou o antigo
    # comportamento (todo card virando tela cheia) exagerado.
    card_vendedor = (
        "<div class='com-card com-card-exp com-card-full' onclick='comToggle(this)'>"
        "<div class='com-card-head'><span class='com-label'>Ranking de Vendedores</span>"
        "<span class='com-hint'>detalhar <span class='com-seta'>&#9662;</span></span></div>"
        f"<div class='com-value' style='color:var(--laranja)'>{vend_top.title()}</div>"
        f"<div class='com-sub'>{_fmt_moeda_compacta(vend_top_val)} &middot; {n_vendas_vendedor.get(vend_top, 0)} vendas &middot; {ag['modalidade_vendedor'].get(vend_top, '-').title()}</div>"
        f"<div class='com-corpo'>{html_meses_vendedor}{bloco_total_vendedor}</div></div>"
    )

    # ---- Card: interno x externo ----
    fat_int = ag["por_modalidade"]["INTERNO"]["faturamento"]
    fat_ext = ag["por_modalidade"]["EXTERNO"]["faturamento"]
    total_modal = fat_int + fat_ext
    pct_int = (fat_int / total_modal * 100) if total_modal else 0
    pct_ext = 100 - pct_int
    card_modalidade = (
        "<div class='com-card com-card-exp' onclick='comToggle(this)'>"
        "<div class='com-card-head'><span class='com-label'>Interno x Externo</span>"
        "<span class='com-hint'>detalhar <span class='com-seta'>&#9662;</span></span></div>"
        f"<div class='com-value'>{pct_int:.0f}% / {pct_ext:.0f}%</div>"
        f"<div class='com-sub'>faturamento interno vs externo</div>"
        f"<div class='com-corpo'><table class='com-mini'><thead><tr><th>Modalidade</th><th>Faturamento</th><th>Comissão</th><th>Vendas</th></tr></thead><tbody>"
        f"<tr><td>Interno</td><td>{_fmt_moeda_compacta(fat_int)}</td><td>{_fmt_moeda_compacta(ag['por_modalidade']['INTERNO']['comissao'])}</td><td>{ag['por_modalidade']['INTERNO']['vendas']}</td></tr>"
        f"<tr><td>Externo</td><td>{_fmt_moeda_compacta(fat_ext)}</td><td>{_fmt_moeda_compacta(ag['por_modalidade']['EXTERNO']['comissao'])}</td><td>{ag['por_modalidade']['EXTERNO']['vendas']}</td></tr>"
        f"</tbody></table></div></div>"
    )

    # ---- Card: Ticket Médio -- geral no card fechado; ao expandir, tabela
    # mês a mês com o ticket médio de cada loja + coluna Total (todas as
    # lojas daquele mês). Ticket médio = faturamento ÷ nº de vendas
    # (contagem de linhas em COMERCIAL_VENDAS) -- fonte diferente do
    # "Ticket Médio de Venda" da aba Insights (que usa numeroVendas
    # informado manualmente na DRE), mas o faturamento total dos dois deve
    # bater, já que vêm da mesma base de receita.
    vendas_loja_mes = defaultdict(lambda: defaultdict(int))
    for mes, loja, vendedor, modalidade, valor, comissao in COMERCIAL_VENDAS:
        if valor is not None:
            vendas_loja_mes[loja][mes] += 1

    total_vendas_periodo = sum(vendas_loja_mes[loja][mes] for loja in LOJAS_COMERCIAL for mes in meses)
    ticket_medio_geral = (fat_total / total_vendas_periodo) if total_vendas_periodo else None

    def _ticket_medio_loja_mes(loja, mes):
        n = vendas_loja_mes[loja].get(mes, 0)
        if not n:
            return None
        return _valor_loja_mes(loja, mes) / n

    def _ticket_medio_total_mes(mes):
        n_total = sum(vendas_loja_mes[loja].get(mes, 0) for loja in LOJAS_COMERCIAL)
        if not n_total:
            return None
        return ag['fat_mes_total'].get(mes, 0) / n_total

    linhas_ticket_medio = "".join(
        f"<tr><td>{m}</td>"
        + "".join(
            f"<td>{(_fmt_moeda(_ticket_medio_loja_mes(loja, m)) if _ticket_medio_loja_mes(loja, m) is not None else '—')}</td>"
            for loja in LOJAS_COMERCIAL
        )
        + f"<td>{(_fmt_moeda(_ticket_medio_total_mes(m)) if _ticket_medio_total_mes(m) is not None else '—')}</td></tr>"
        for m in meses
    )
    card_ticket_medio = (
        "<div class='com-card com-card-exp' onclick='comToggle(this)'>"
        "<div class='com-card-head'><span class='com-label'>Ticket Médio</span>"
        "<span class='com-hint'>detalhar <span class='com-seta'>&#9662;</span></span></div>"
        f"<div class='com-value'>{(_fmt_moeda(ticket_medio_geral) if ticket_medio_geral is not None else '—')}</div>"
        f"<div class='com-sub'>média geral ({meses[0]}-{meses[-1]}/26) &middot; clique pra ver por loja e mês</div>"
        f"<div class='com-corpo'><table class='com-mini'><thead><tr><th>Mês</th><th>Prime Sol Matriz</th><th>Prime Sol Lagos</th><th>Prime Sol Cabo Frio</th><th>Total</th></tr></thead>"
        f"<tbody>{linhas_ticket_medio}</tbody></table></div></div>"
    )

    # ---- Tabela "Desempenho por Vendedor" (estilo DRE/DFC): ordem
    # alfabética, cabeçalho fixo (2 linhas), primeira coluna (nome) fixa
    # lateral, altura máxima com scroll vertical, e barra de rolagem
    # horizontal duplicada (topo + embaixo), sincronizadas ----
    # class='com-col-divisor' na primeira coluna de cada grupo de mês (Total
    # Vendas) desenha uma linha vertical mais forte separando um mês do
    # outro -- sem isso, os 3 x 6 = 18 números seguidos ficavam difíceis de
    # ler, sem nenhuma pista visual de onde um mês termina e o outro começa.
    thead_meses = "".join(f"<th colspan=\'3\' class=\'com-col-divisor\'>{m}</th>" for m in meses)
    thead_sub = "<th>Vendedor</th>" + "<th class='com-col-divisor'>Total Vendas</th><th>% Fat.</th><th>Variação</th>" * len(meses)

    ranking_tabela = sorted(fat_vendedor_total.items(), key=lambda x: x[0].title())
    linhas_tabela = []
    for vendedor, _ in ranking_tabela:
        celulas = []
        anterior = None
        for mes in meses:
            valor_mes = ag["por_vendedor_mes"][vendedor].get(mes, 0)
            fat_mes = ag["fat_mes_total"].get(mes, 0)
            pct = (valor_mes / fat_mes * 100) if fat_mes else 0
            celulas.append(
                f"<td class='com-col-divisor'>{_fmt_moeda_compacta(valor_mes)}</td><td>{pct:.1f}%</td><td>{_linha_delta(valor_mes, anterior)}</td>"
            )
            anterior = valor_mes
        linhas_tabela.append(f"<tr><td>{vendedor.title()}</td>{''.join(celulas)}</tr>")

    linha_total = []
    anterior_total = None
    for mes in meses:
        total_mes = ag["fat_mes_total"].get(mes, 0)
        linha_total.append(f"<td class='com-col-divisor'>{_fmt_moeda_compacta(total_mes)}</td><td>100.0%</td><td>{_linha_delta(total_mes, anterior_total)}</td>")
        anterior_total = total_mes
    linha_total_html = "".join(linha_total)
    linhas_tabela.append(f"<tr class='com-total'><td>Total (todos vendedores)</td>{linha_total_html}</tr>")

    linhas_tabela_html = "".join(linhas_tabela)
    tabela_desempenho = (
        "<div class='com-tabela-titulo'>Desempenho por Vendedor &middot; faturamento mensal, % do total e variação vs mês anterior</div>"
        "<div class='com-scroll-topo'><div class='com-scroll-topo-inner'></div></div>"
        "<div class='com-tabela-wrap'><table class='com-tabela'>"
        f"<thead><tr><th></th>{thead_meses}</tr><tr>{thead_sub}</tr></thead>"
        f"<tbody>{linhas_tabela_html}</tbody></table></div>"
    )

    css = """
  #abaComercial .com-wrap { padding: 20px 32px 40px; }
  #abaComercial .com-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 16px; align-items: start; }
  #abaComercial .com-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
  #abaComercial .com-card-exp { cursor: pointer; transition: border-color .15s, grid-column .15s; position: relative; }
  /* Altura mínima igual pros cards fechados -- a largura já é igual (a
     grade distribui certinho), mas a altura variava porque a legenda
     (com-sub) de alguns cards cabe numa linha só e de outros quebra em
     duas (ex: "Interno x Externo" vs "Faturamento por Loja"), deixando a
     fileira com fundo de card desalinhado. Não afeta o card aberto (que
     já cresce além disso naturalmente com o conteúdo detalhado). */
  #abaComercial .com-card-exp:not(.com-hero) { min-height: 104px; }
  #abaComercial .com-card-exp:hover { border-color: var(--laranja); }
  /* Cabeçalho do card em flex (rótulo à esquerda, dica "detalhar" à
     direita) -- antes a dica era um ::after com position:absolute no
     canto do card, que ficava por CIMA do rótulo sempre que o texto do
     rótulo era comprido (ex: "Vendedor que mais faturou" num card
     estreito), colando as duas frases. Em flex, os dois nunca se
     sobrepõem: a dica fica fixa à direita e o rótulo quebra linha se
     precisar, em vez de passar por baixo dela. */
  #abaComercial .com-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; margin-bottom: 6px; }
  /* Por padrão, ao abrir, o card só cresce PRA BAIXO (a tabela dentro dele
     tem largura própria via .com-mini/.com-tabela-scroll, não precisa que
     o card inteiro estique) -- ocupar a linha inteira da grade pra uma
     tabela de 3-4 colunas ficava exagerado e vazio (a "Faturamento por
     Loja" e a "Ticket Médio" tomavam a tela toda pra mostrar 3 colunas de
     %). Só o card "Vendedor que mais faturou" (com muito mais colunas --
     6-7 meses x 3 cada) realmente precisa da largura total pra não
     truncar; ele leva a classe extra "com-card-full" abaixo, que é a
     única que aciona esse comportamento. */
  #abaComercial .com-card-exp.com-card-full.aberto { grid-column: 1 / -1; }
  /* Os demais cards expandidos ocupam 2 colunas da grade em vez de 1 --
     dobra a largura (dá espaço suficiente pra tabela de 4-5 colunas não
     truncar) sem tomar a linha inteira nem sobrar vazio do lado. */
  #abaComercial .com-card-exp.aberto:not(.com-card-full):not(.com-hero) { grid-column: span 2; }
  #abaComercial .com-label { font-size: 10.5px; color: var(--text-muted); text-transform: uppercase; letter-spacing: .03em; }
  #abaComercial .com-hint { font-size: 9.5px; color: var(--text-faint); white-space: nowrap; flex-shrink: 0; }
  #abaComercial .com-value { font-size: 21px; font-weight: 800; }
  #abaComercial .com-sub { font-size: 11.5px; color: var(--text-muted); margin-top: 2px; }
  #abaComercial .com-seta { display: inline-block; font-size: 9px; transition: transform .2s; color: var(--text-faint); }
  #abaComercial .com-card-exp.aberto .com-seta { transform: rotate(180deg); }
  #abaComercial .com-corpo { display: none; margin-top: 10px; }
  #abaComercial .com-card-exp.aberto .com-corpo { display: block; }
  #abaComercial .com-mini { width: 100%; border-collapse: collapse; font-size: 11.5px; margin-top: 8px; }
  #abaComercial .com-mini th { text-align: right; color: var(--text-faint); font-weight: 600; padding: 4px; border-bottom: 1px solid var(--border); font-size: 10.5px; white-space: nowrap; }
  #abaComercial .com-mini th:first-child, #abaComercial .com-mini td:first-child { text-align: left; }
  #abaComercial .com-mini td { text-align: right; padding: 5px; border-bottom: 1px solid var(--border); white-space: nowrap; }
  /* Só a linha com class="com-total" (Total/Média de verdade) fica em
     negrito -- antes qualquer última linha de QUALQUER tabela .com-mini
     ficava em negrito automaticamente, o que deixava o 5º colocado de
     cada ranking top-5 (que não é total nenhum) destacado por engano. */
  #abaComercial .com-total td { font-weight: 800; background: var(--bg-panel); border-bottom: none; }
  #abaComercial .com-mes-bloco { margin-top: 10px; }
  #abaComercial .com-mes-titulo { font-size: 10.5px; font-weight: 700; color: var(--laranja); text-transform: uppercase; letter-spacing: .03em; margin-bottom: 4px; }
  #abaComercial .com-mes-bloco-total { border-top: 1px solid var(--border); padding-top: 10px; margin-top: 14px; }
  /* Wrapper de rolagem horizontal da tabela única de "Ranking de
     Vendedores" (reaproveita a classe .com-tabela, mesmo estilo de
     cabeçalho centralizado + divisórias por mês da tabela "Desempenho por
     Vendedor" mais abaixo -- combinado com Fabrício em 13/08/2026 pra
     ficar visualmente harmonioso, uma tabela só em vez de 7 soltas). */
  #abaComercial .com-tabela-scroll { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius-sm); margin-top: 4px; }
  #abaComercial .com-tabela-scroll::-webkit-scrollbar { height: 8px; }
  #abaComercial .com-tabela-scroll::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  #abaComercial .com-tabela-scroll::-webkit-scrollbar-track { background: transparent; }
  #abaComercial .com-hero { grid-column: 1 / -1; background: rgba(250,168,33,0.08); border: 1px solid var(--laranja); }
  #abaComercial .com-hero-val { font-size: 30px; font-weight: 800; color: var(--laranja); }
  #abaComercial .com-tabela-titulo { font-size: 13px; color: var(--text-muted); margin: 4px 0 10px; }
  #abaComercial .com-scroll-topo { overflow-x: auto; overflow-y: hidden; height: 14px; }
  #abaComercial .com-scroll-topo-inner { height: 1px; }
  #abaComercial .com-tabela-wrap { overflow-x: auto; overflow-y: auto; max-height: 65vh; border: 1px solid var(--border); border-radius: var(--radius); }
  #abaComercial .com-tabela { border-collapse: collapse; width: auto; min-width: 100%; font-size: 12px; white-space: nowrap; table-layout: auto; }
  #abaComercial .com-tabela th { background: var(--bg-panel); color: var(--text-muted); font-weight: 700; text-transform: uppercase; font-size: 10px; letter-spacing: .03em; padding: 8px 6px; border-bottom: 2px solid var(--laranja); text-align: right; position: sticky; z-index: 2; }
  /* Cabeçalho de mês (1ª linha, colspan=3) centralizado sobre o grupo de 3
     colunas -- antes herdava text-align:right da regra acima e ficava
     "colado" na borda direita do grupo, parecendo desalinhado com os
     números embaixo. */
  #abaComercial .com-tabela thead tr:first-child th { top: 0; text-align: center; }
  #abaComercial .com-tabela thead tr:nth-child(2) th { top: 29px; }
  #abaComercial .com-tabela th:first-child { text-align: left; position: sticky; left: 0; z-index: 3; background: var(--bg-panel); }
  #abaComercial .com-tabela td { padding: 6px 8px; border-bottom: 1px solid var(--border); text-align: right; }
  #abaComercial .com-tabela td:first-child { text-align: left; font-weight: 700; position: sticky; left: 0; background: var(--bg); z-index: 1; }
  /* Linha vertical separando a coluna fixa "Vendedor" do restante da
     tabela (que rola horizontalmente) -- sem isso, ficava pouco claro
     onde a coluna fixa terminava e os dados começavam a rolar. */
  #abaComercial .com-tabela th:first-child, #abaComercial .com-tabela td:first-child { border-right: 2px solid var(--border); }
  /* Linha vertical separando um mês do outro (3 colunas cada) -- sem
     isso, os 18 números em sequência (6 meses x 3 colunas) ficavam
     difíceis de agrupar visualmente por mês. */
  #abaComercial .com-tabela th.com-col-divisor, #abaComercial .com-tabela td.com-col-divisor { border-left: 2px solid var(--border); }
  #abaComercial .com-tabela tr:hover td { background: var(--bg-card); }
  #abaComercial .com-delta-vazio { color: var(--text-faint); }
  #abaComercial .com-up { color: var(--green); }
  #abaComercial .com-down { color: var(--red); }
"""

    script = """
<script>
function comToggle(cardEl) { cardEl.classList.toggle('aberto'); }
function atualizarLarguraScrollComercial() {
  const wrap = document.querySelector('.com-tabela-wrap');
  const scrollTopo = document.querySelector('.com-scroll-topo');
  const scrollTopoInner = document.querySelector('.com-scroll-topo-inner');
  if (!wrap || !scrollTopo || !scrollTopoInner) return;
  scrollTopoInner.style.width = wrap.querySelector('table').scrollWidth + 'px';
  let sincronizando = false;
  scrollTopo.onscroll = function () {
    if (sincronizando) return;
    sincronizando = true;
    wrap.scrollLeft = scrollTopo.scrollLeft;
    sincronizando = false;
  };
  wrap.onscroll = function () {
    if (sincronizando) return;
    sincronizando = true;
    scrollTopo.scrollLeft = wrap.scrollLeft;
    sincronizando = false;
  };
}
</script>
"""

    html = f"""<style>{css}</style>
<div id="abaComercial" style="display:none;">
  <div class="com-wrap">
    <div class="com-grid">
      {card_hero}
      {card_loja}
      {card_modalidade}
      {card_ticket_medio}
      {card_vendedor}
    </div>
    {tabela_desempenho}
  </div>
</div>
{script}"""

    return html
