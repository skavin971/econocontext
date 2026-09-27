from datetime import date

import pytest

from econocontext import config
from econocontext.pricing.rates import ratios
from tests.unit.conftest import CONFIG_DIR


def test_loads_and_fingerprint_is_stable(cfg):
    again = config.load(CONFIG_DIR)
    assert cfg.fingerprint == again.fingerprint and len(cfg.fingerprint) == 64
    assert cfg.card.model == "gemini-3.6-flash" and cfg.card.min_cacheable_tokens == 4096


def test_price_period_is_chosen_by_date(cfg):
    promo = cfg.card.tier(date(2026, 9, 27), 10_000)
    standard = cfg.card.tier(date(2027, 1, 2), 10_000)
    assert promo.input_per_mtok == 0.75 and standard.input_per_mtok == 1.50


def test_ratios_are_relative_to_uncached_input(cfg):
    r = ratios(cfg.card, date(2026, 9, 27))
    assert r.input_ratio == 1.0 and r.cache_read_ratio == pytest.approx(0.1)
    assert r.output_ratio == pytest.approx(5.0) and r.cache_write_ratio == pytest.approx(1.0)


def test_missing_required_key_is_an_error(tmp_path):
    import shutil
    shutil.copy(CONFIG_DIR / "billing_rates.yaml", tmp_path / "billing_rates.yaml")
    (tmp_path / "econocontext.yaml").write_text("mode: observe\n")
    with pytest.raises(config.ConfigError):
        config.load(tmp_path)
