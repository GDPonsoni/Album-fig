"""Estimativa de progresso de um álbum a partir dos parâmetros, sem banco e sem sorteio.

Serve para o superadmin calibrar um álbum antes de publicá-lo: dado o conteúdo
(quantas figurinhas por raridade), os pacotes e o ritmo da gaveta, quanto do
álbum um jogador deve ter colado em cada dia.

Modelo
------
Cada figurinha i de raridade r tem, por sorteio, chance

    p_i = (w_r / Σ w) / n_r

onde w_r é o peso da raridade no tipo de pacote, n_r o número de figurinhas da
raridade, e a soma Σ w só considera raridades que têm figurinhas e peso > 0
(é assim que `draw_stickers` monta o sorteio). Depois de k sorteios, o número
esperado de figurinhas distintas é Σ_i (1 − (1 − p_i)^k).

Pacotes premium entram como mistura: se uma fração φ dos pacotes é premium, os
sorteios se dividem em k_comum = pacotes·(1−φ)·figurinhas_por_pacote_comum e
k_premium = pacotes·φ·figurinhas_por_pacote_premium, e a chance de ainda não
ter a figurinha i é (1 − p_i,comum)^k_comum · (1 − p_i,premium)^k_premium.

Pacotes recebidos até o dia d por quem entrou no dia e (d ≥ e):

    boas_vindas + floor((d − e)·1440 / refill_minutes) · aproveitamento · f

`f` é a fração dos pacotes disponíveis que o jogador de fato abre.

Aproximações (todas documentadas para quem for ler o número)
-----------------------------------------------------------
* Limite da gaveta: o timer nunca para, e o ciclo que termina com a gaveta cheia
  não entrega nada. Supõe-se que o jogador abre o app em intervalos regulares
  (`visits_per_day`) e esvazia a gaveta a cada visita. Entre duas visitas cabem
  V / refill_minutes ciclos, dos quais no máximo `stock_cap` rendem pacote; daí
  aproveitamento = min(1, stock_cap · refill_minutes / V), com V = 1440 / visits_per_day.
* Premium: nos dias de premium (1 a cada `premium_every_n_days`), só o primeiro
  pacote do dia é premium. Com D pacotes por dia, φ = (1/n) · min(1, 1/D).
  Os pacotes de boas-vindas usam a mesma fração.
* A garantia mínima de raridade do pacote é ignorada.
* Dentro de um pacote o sorteio real evita repetir figurinha; aqui os sorteios
  são independentes.
* **Troca não entra no cálculo.** Tudo isso faz a estimativa ser conservadora:
  o jogador real tende a completar um pouco mais rápido do que o previsto.
"""
import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PackSpec:
    stickers_per_pack: int
    weights: dict[str, float]  # raridade -> peso; raridade ausente vale 0


@dataclass(frozen=True)
class EstimateParams:
    stickers_by_rarity: dict[str, int]  # raridade -> quantidade de figurinhas ativas
    common: PackSpec
    refill_minutes: int
    stock_cap: int
    welcome_packs: int
    premium: PackSpec | None = None
    premium_every_n_days: int = 0
    duration_days: int = 90
    open_fraction: float = 1.0  # f: fração dos pacotes disponíveis que o jogador abre
    visits_per_day: float = 1.0
    generous_threshold: float = 0.95
    tight_threshold: float = 0.50
    entry_days: tuple[int, ...] = field(default=())  # vazio = 0, 1/3 e 2/3 da duração
    milestone_days: tuple[int, ...] = field(default=())  # vazio = 1/3, 2/3 e o fim

    def entries(self) -> tuple[int, ...]:
        return self.entry_days or _thirds(self.duration_days, (0, 1, 2))

    def milestones(self) -> tuple[int, ...]:
        return self.milestone_days or _thirds(self.duration_days, (1, 2, 3))


def _thirds(duration: int, parts: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(dict.fromkeys(round(duration * part / 3) for part in parts))


def sticker_probabilities(pack: PackSpec, stickers_by_rarity: dict[str, int]) -> dict[str, float]:
    """p_i por raridade: chance de UM sorteio do pacote sair uma figurinha específica dela."""
    drawable = {
        rarity: float(pack.weights.get(rarity, 0))
        for rarity, count in stickers_by_rarity.items()
        if count > 0 and float(pack.weights.get(rarity, 0)) > 0
    }
    total = sum(drawable.values())
    return {
        rarity: (drawable[rarity] / total / count if rarity in drawable else 0.0)
        for rarity, count in stickers_by_rarity.items()
        if count > 0
    }


def drawer_yield(params: EstimateParams) -> float:
    """Fração dos ciclos do timer que rende pacote, dado o limite da gaveta."""
    if params.visits_per_day <= 0 or params.stock_cap <= 0:
        return 0.0
    between_visits = 1440 / params.visits_per_day
    return min(1.0, params.stock_cap * max(1, params.refill_minutes) / between_visits)


def packs_per_day(params: EstimateParams) -> float:
    """Pacotes da gaveta por dia, já com o limite de estoque e antes de `open_fraction`."""
    return 1440 / max(1, params.refill_minutes) * drawer_yield(params)


def premium_fraction(params: EstimateParams) -> float:
    if params.premium is None or params.premium_every_n_days <= 0:
        return 0.0
    per_day = packs_per_day(params)
    if per_day <= 0:
        return 1 / params.premium_every_n_days  # só boas-vindas: o primeiro do dia
    return min(1.0, (1 / params.premium_every_n_days) * min(1.0, 1 / per_day))


def packs_received(params: EstimateParams, day: float, entry_day: float = 0) -> float:
    """Pacotes abertos até `day` por quem entrou em `entry_day`. Zero antes de entrar."""
    if day < entry_day:
        return 0.0
    fraction = min(1.0, max(0.0, params.open_fraction))
    welcome = min(max(0, params.welcome_packs), max(0, params.stock_cap))
    cycles = math.floor((day - entry_day) * 1440 / max(1, params.refill_minutes))
    return (welcome + cycles * drawer_yield(params)) * fraction


def expected_distinct(params: EstimateParams, packs: float) -> float:
    """Número esperado de figurinhas distintas depois de `packs` pacotes."""
    phi = premium_fraction(params)
    draws_common = packs * (1 - phi) * params.common.stickers_per_pack
    p_common = sticker_probabilities(params.common, params.stickers_by_rarity)
    if params.premium is not None and phi > 0:
        draws_premium = packs * phi * params.premium.stickers_per_pack
        p_premium = sticker_probabilities(params.premium, params.stickers_by_rarity)
    else:
        draws_premium, p_premium = 0.0, {}
    total = 0.0
    for rarity, count in params.stickers_by_rarity.items():
        if count <= 0:
            continue
        missing = (1 - p_common.get(rarity, 0.0)) ** draws_common * (1 - p_premium.get(rarity, 0.0)) ** draws_premium
        total += count * (1 - missing)
    return total


def stickers_received(params: EstimateParams, packs: float) -> float:
    phi = premium_fraction(params)
    premium_size = params.premium.stickers_per_pack if params.premium is not None else 0
    return packs * ((1 - phi) * params.common.stickers_per_pack + phi * premium_size)


def _day_to_reach(params: EstimateParams, target: float, limit: int) -> int | None:
    """Primeiro dia inteiro (entrada no dia 0) em que o esperado alcança `target` figurinhas."""
    if expected_distinct(params, packs_received(params, limit)) < target:
        return None
    low, high = 0, limit  # o esperado é monótono no dia: busca binária
    while low < high:
        middle = (low + high) // 2
        if expected_distinct(params, packs_received(params, middle)) >= target:
            high = middle
        else:
            low = middle + 1
    return low


def estimate_album(params: EstimateParams) -> dict:
    """Resumo para o painel do superadmin. Tudo em valores esperados; troca não entra."""
    total = sum(count for count in params.stickers_by_rarity.values() if count > 0)
    duration = max(1, params.duration_days)

    def percent(distinct: float) -> float:
        return round(100 * distinct / total, 1) if total else 0.0

    scenarios = []
    for entry in params.entries():
        points = []
        for day in params.milestones():
            if day < entry:
                continue
            packs = packs_received(params, day, entry)
            points.append({"day": day, "packs": round(packs, 1), "percent": percent(expected_distinct(params, packs))})
        scenarios.append({"entry_day": entry, "milestones": points})

    # 100% em valor esperado é assintótico; "completo" aqui é faltar menos de meia figurinha.
    horizon = duration * 10
    targets = {"80": 0.80 * total, "95": 0.95 * total, "100": total - 0.5}
    days_to = {}
    for label, target in targets.items():
        day = _day_to_reach(params, target, horizon) if total else None
        days_to[label] = {"day": day, "within_album": day is not None and day <= duration}

    packs_at_end = packs_received(params, duration)
    distinct_at_end = expected_distinct(params, packs_at_end)
    received = stickers_received(params, packs_at_end)
    final_ratio = distinct_at_end / total if total else 0.0

    alerts = []
    if not total:
        alerts.append({"level": "error", "code": "empty", "message": "O álbum não tem figurinhas ativas."})
    elif final_ratio >= params.generous_threshold:
        alerts.append(
            {
                "level": "warning",
                "code": "too_generous",
                "message": f"Generoso demais: quem entra no dia 0 chega a {percent(distinct_at_end)}% em {duration} dias, sem trocar.",
            }
        )
    elif final_ratio < params.tight_threshold:
        alerts.append(
            {
                "level": "warning",
                "code": "too_tight",
                "message": f"Apertado demais: quem entra no dia 0 só chega a {percent(distinct_at_end)}% em {duration} dias.",
            }
        )
    unreachable = [
        rarity
        for rarity, count in params.stickers_by_rarity.items()
        if count > 0 and sticker_probabilities(params.common, params.stickers_by_rarity).get(rarity, 0) == 0
        and (params.premium is None or premium_fraction(params) == 0
             or sticker_probabilities(params.premium, params.stickers_by_rarity).get(rarity, 0) == 0)
    ]
    if unreachable:
        alerts.append(
            {
                "level": "error",
                "code": "unreachable",
                "message": "Há raridades com figurinhas que nenhum pacote da gaveta sorteia: " + ", ".join(sorted(unreachable)) + ".",
            }
        )

    return {
        "total_stickers": total,
        "duration_days": duration,
        "packs_per_day": round(packs_per_day(params) * min(1.0, max(0.0, params.open_fraction)), 2),
        "premium_fraction": round(premium_fraction(params), 3),
        "drawer_yield": round(drawer_yield(params), 3),
        "scenarios": scenarios,
        "days_to": days_to,
        "totals": {
            "packs": round(packs_at_end, 1),
            "stickers_received": round(received, 1),
            "distinct": round(distinct_at_end, 1),
            "duplicates": round(max(0.0, received - distinct_at_end), 1),
            "percent": percent(distinct_at_end),
        },
        "alerts": alerts,
        "notes": [
            "Valores esperados, sem trocas e sem a garantia mínima do pacote: a estimativa é conservadora.",
            f"Supõe {params.visits_per_day:g} visita(s) por dia esvaziando a gaveta e abertura de {round(100 * min(1.0, max(0.0, params.open_fraction)))}% dos pacotes.",
        ],
    }
