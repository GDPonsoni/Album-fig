from itertools import combinations

import pytest

from app.seed import PAGE_TEMPLATES


@pytest.mark.parametrize("template", PAGE_TEMPLATES)
def test_espacos_da_pagina_nao_se_sobrepoem_nem_vazam(template):
    for _, x, y, w, h in template:
        assert x + w <= 100 and y + h <= 100
    for (_, ax, ay, aw, ah), (_, bx, by, bw, bh) in combinations(template, 2):
        assert ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay
