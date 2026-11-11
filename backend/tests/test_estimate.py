import math
import random
from dataclasses import replace

import pytest

from app.models import PackType, Rarity, Sticker
from app.services.estimate import (
    EstimateParams,
    PackSpec,
    drawer_yield,
    estimate_album,
    expected_distinct,
    packs_received,
    premium_fraction,
    sticker_probabilities,
)
from app.services.packs import draw_stickers


def _uniform(n: int, per_pack: int = 1, **kwargs) -> EstimateParams:
    base = dict(
        stickers_by_rarity={"a": n},
        common=PackSpec(per_pack, {"a": 1}),
        refill_minutes=1440,
        stock_cap=1,
        welcome_packs=0,
    )
    return EstimateParams(**{**base, **kwargs})


def test_caso_uniforme_bate_com_a_formula_fechada():
    for n, k in [(10, 1), (10, 10), (50, 120), (200, 37)]:
        assert expected_distinct(_uniform(n), k) == pytest.approx(n * (1 - (1 - 1 / n) ** k))


def test_um_sorteio_da_uma_distinta_e_zero_sorteios_nenhuma():
    params = _uniform(30)
    assert expected_distinct(params, 0) == 0
    assert expected_distinct(params, 1) == pytest.approx(1)


def test_probabilidade_por_figurinha_segue_peso_e_quantidade():
    probabilities = sticker_probabilities(PackSpec(6, {"c": 80, "r": 17, "l": 3}), {"c": 100, "r": 20, "l": 5})
    assert probabilities["c"] == pytest.approx(0.80 / 100)
    assert probabilities["r"] == pytest.approx(0.17 / 20)
    assert probabilities["l"] == pytest.approx(0.03 / 5)
    assert sum(probabilities[r] * n for r, n in {"c": 100, "r": 20, "l": 5}.items()) == pytest.approx(1)


def test_soma_dos_pesos_ignora_raridade_sem_figurinha_ou_sem_peso():
    # Sem lendárias no álbum, os 3% delas se redistribuem (como no sorteio real).
    probabilities = sticker_probabilities(PackSpec(6, {"c": 80, "r": 17, "l": 3}), {"c": 10, "r": 5, "l": 0})
    assert probabilities == {"c": pytest.approx(80 / 97 / 10), "r": pytest.approx(17 / 97 / 5)}
    zero = sticker_probabilities(PackSpec(6, {"c": 1}), {"c": 4, "r": 2})
    assert zero == {"c": pytest.approx(0.25), "r": 0.0}


def test_duas_raridades_caso_analitico():
    params = EstimateParams(
        stickers_by_rarity={"c": 4, "r": 1},
        common=PackSpec(1, {"c": 3, "r": 1}),
        refill_minutes=1440, stock_cap=1, welcome_packs=0,
    )
    k = 8
    expected = 4 * (1 - (1 - 0.75 / 4) ** k) + 1 * (1 - (1 - 0.25) ** k)
    assert expected_distinct(params, k) == pytest.approx(expected)


def test_pacotes_recebidos_boas_vindas_ritmo_e_fracao_aberta():
    params = _uniform(100, refill_minutes=720, stock_cap=3, welcome_packs=2)
    assert drawer_yield(params) == 1  # 2 ciclos por dia cabem na gaveta de 3
    assert packs_received(params, 0) == 2
    assert packs_received(params, 10) == 2 + 20
    assert packs_received(params, 10, entry_day=4) == 2 + 12
    assert packs_received(params, 3, entry_day=4) == 0  # ainda não entrou
    assert packs_received(replace(params, open_fraction=0.5), 10) == 11
    assert packs_received(replace(params, welcome_packs=9), 0) == 3  # boas-vindas não passa do limite


def test_limite_da_gaveta_reduz_o_ritmo():
    # Timer de 1 h, gaveta de 4, uma visita por dia: 24 ciclos, só 4 rendem pacote.
    params = _uniform(100, refill_minutes=60, stock_cap=4)
    assert drawer_yield(params) == pytest.approx(4 / 24)
    assert packs_received(params, 30) == pytest.approx(30 * 4)
    # Visitando 6 vezes por dia (a cada 4 h) nada se perde.
    assert drawer_yield(replace(params, visits_per_day=6)) == 1


def test_mistura_com_premium():
    base = EstimateParams(
        stickers_by_rarity={"c": 40, "r": 10},
        common=PackSpec(6, {"c": 9, "r": 1}),
        premium=PackSpec(7, {"c": 1, "r": 1}),
        premium_every_n_days=2,
        refill_minutes=720, stock_cap=3, welcome_packs=0,
    )
    # 2 pacotes por dia, premium dia sim dia não, só o primeiro do dia: 1 em cada 4.
    assert premium_fraction(base) == pytest.approx(0.25)
    packs = 40
    kc, kp = packs * 0.75 * 6, packs * 0.25 * 7
    expected = 40 * (1 - (1 - 0.9 / 40) ** kc * (1 - 0.5 / 40) ** kp) + 10 * (1 - (1 - 0.1 / 10) ** kc * (1 - 0.5 / 10) ** kp)
    assert expected_distinct(base, packs) == pytest.approx(expected)
    # Sem premium (ou desligado) a fração é zero e o premium não influencia.
    assert premium_fraction(replace(base, premium_every_n_days=0)) == 0
    assert premium_fraction(replace(base, premium=None)) == 0
    assert expected_distinct(base, packs) > expected_distinct(replace(base, premium=None), packs)  # premium traz mais raras


REAL = EstimateParams(
    stickers_by_rarity={"comum": 110, "rara": 35, "lendária": 10},
    common=PackSpec(6, {"comum": 80, "rara": 17, "lendária": 3}),
    premium=PackSpec(7, {"comum": 60, "rara": 32, "lendária": 8}),
    premium_every_n_days=2,
    refill_minutes=720, stock_cap=3, welcome_packs=2,
)


def test_monotonicidade():
    values = [expected_distinct(REAL, packs) for packs in range(0, 400, 5)]
    assert all(b > a for a, b in zip(values, values[1:]))
    assert values[-1] < 155
    by_day = [estimate for estimate in (expected_distinct(REAL, packs_received(REAL, day)) for day in range(0, 91))]
    assert all(b >= a for a, b in zip(by_day, by_day[1:]))
    # Entrar mais tarde nunca ajuda; abrir menos pacotes também não.
    late = [expected_distinct(REAL, packs_received(REAL, 90, entry)) for entry in (0, 30, 60, 89)]
    assert late == sorted(late, reverse=True)
    lazy = [expected_distinct(REAL, packs_received(replace(REAL, open_fraction=f), 90)) for f in (0.2, 0.5, 1.0)]
    assert lazy == sorted(lazy)
    # Mais pacotes por dia, mais álbum.
    faster = replace(REAL, refill_minutes=360, stock_cap=6)
    assert expected_distinct(faster, packs_received(faster, 30)) > expected_distinct(REAL, packs_received(REAL, 30))


def test_resumo_tem_cenarios_marcos_e_totais():
    result = estimate_album(REAL)
    assert result["total_stickers"] == 155
    assert [s["entry_day"] for s in result["scenarios"]] == [0, 30, 60]
    assert [[m["day"] for m in s["milestones"]] for s in result["scenarios"]] == [[30, 60, 90], [30, 60, 90], [60, 90]]
    first = result["scenarios"][0]["milestones"]
    assert first[0]["percent"] < first[1]["percent"] < first[2]["percent"] <= 100
    # Mesmo dia-marco, entrada mais tarde: menos álbum.
    assert result["scenarios"][0]["milestones"][2]["percent"] > result["scenarios"][1]["milestones"][2]["percent"]
    assert result["scenarios"][1]["milestones"][0]["packs"] == 2  # entrou hoje: só boas-vindas
    days = result["days_to"]
    assert days["80"]["day"] < days["95"]["day"] < days["100"]["day"]
    totals = result["totals"]
    assert totals["packs"] == 182 and totals["percent"] == first[2]["percent"]
    assert totals["duplicates"] == pytest.approx(totals["stickers_received"] - totals["distinct"], abs=0.11)


def test_duracao_diferente_usa_marcos_proporcionais():
    result = estimate_album(replace(REAL, duration_days=60))
    assert [s["entry_day"] for s in result["scenarios"]] == [0, 20, 40]
    assert [m["day"] for m in result["scenarios"][0]["milestones"]] == [20, 40, 60]


def test_alertas():
    def codes(params):
        return [a["code"] for a in estimate_album(params)["alerts"]]

    assert codes(REAL) == ["too_generous"]
    assert codes(replace(REAL, refill_minutes=1440 * 6, welcome_packs=0)) == ["too_tight"]
    balanced = replace(REAL, refill_minutes=2880, welcome_packs=1)
    ratio = estimate_album(balanced)["totals"]["percent"]
    assert 50 <= ratio < 95 and codes(balanced) == []
    # Limiares configuráveis.
    assert codes(replace(balanced, generous_threshold=0.5)) == ["too_generous"]
    assert codes(replace(balanced, tight_threshold=0.94, generous_threshold=0.99)) == ["too_tight"]
    assert codes(replace(REAL, stickers_by_rarity={})) == ["empty"]
    unreachable = replace(REAL, common=PackSpec(6, {"comum": 1}), premium=None)
    assert "unreachable" in codes(unreachable)


def test_dia_de_completar_pode_ficar_fora_do_album():
    slow = replace(REAL, refill_minutes=1440 * 3, welcome_packs=0)
    days = estimate_album(slow)["days_to"]
    assert days["80"]["day"] is not None and days["80"]["within_album"] is False
    never = estimate_album(replace(REAL, refill_minutes=1440 * 400, welcome_packs=0))["days_to"]
    assert never["100"] == {"day": None, "within_album": False}


# ---- Monte Carlo com o sorteio de verdade

def _real_album():
    """155 figurinhas (110/35/10) e os dois tipos de pacote, em memória: draw_stickers não usa banco."""
    rarities = [Rarity(id=1, name="comum", rank=1, weight=80), Rarity(id=2, name="rara", rank=2, weight=17),
                Rarity(id=3, name="lendária", rank=3, weight=3)]
    stickers, number = [], 0
    for rarity, count in zip(rarities, (110, 35, 10)):
        for _ in range(count):
            number += 1
            stickers.append(Sticker(id=number, org_id=1, number=number, name=str(number), rarity_id=rarity.id))
    common = PackType(id=1, slug="comum", name="c", stickers_per_pack=6, rarity_weights={}, guaranteed_min_rarity=None, org_filter=None)
    premium = PackType(id=2, slug="premium", name="p", stickers_per_pack=7, rarity_weights={"1": 60, "2": 32, "3": 8},
                       guaranteed_min_rarity=None, org_filter=None)
    return rarities, stickers, common, premium


def _simulate(runs: int, schedule: list[PackType], rarities, stickers, seed: int) -> float:
    rng = random.Random(seed)
    total = 0
    for _ in range(runs):
        owned: set[int] = set()
        for pack in schedule:
            owned.update(s.id for s in draw_stickers(pack, rarities, stickers, rng))
        total += len(owned)
    return total / runs


@pytest.mark.parametrize("packs", [8, 30])
def test_monte_carlo_so_pacote_comum(packs):
    rarities, stickers, common, _ = _real_album()
    params = replace(REAL, premium=None)
    simulated = _simulate(2000, [common] * packs, rarities, stickers, seed=1234 + packs)
    estimated = expected_distinct(params, packs)
    # 2 pontos percentuais do álbum; a estimativa é conservadora (o sorteio real evita repetir no pacote).
    assert abs(simulated - estimated) / 155 < 0.02
    assert estimated <= simulated + 0.3


def test_monte_carlo_com_mistura_de_premium():
    rarities, stickers, common, premium = _real_album()
    packs = 40
    phi = premium_fraction(REAL)
    assert phi == pytest.approx(0.25)
    schedule = [premium if i % 4 == 0 else common for i in range(packs)]
    simulated = _simulate(2000, schedule, rarities, stickers, seed=99)
    assert abs(simulated - expected_distinct(REAL, packs)) / 155 < 0.02


def test_formula_fechada_e_numericamente_estavel():
    assert math.isfinite(expected_distinct(_uniform(1), 10_000))
    assert expected_distinct(_uniform(1), 10_000) == pytest.approx(1)
    assert expected_distinct(_uniform(5, per_pack=6), 2.5) == pytest.approx(5 * (1 - 0.8 ** 15))
