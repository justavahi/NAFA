# -*- coding: utf-8 -*-
"""Загрузка конфигурации и поиск папок с данными/справочниками."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

PKG_ROOT = Path(__file__).resolve().parent.parent   # da_scripts/

# Дефолты дублируют config/*.yaml, чтобы пакет работал даже без папки config.
_DEFAULT_WEIGHTS = {
    "fm13": {"weights": {"Rel": 0.40, "Imp": 0.25, "Urg": 0.10, "Nov": 0.10,
                         "Cred": 0.15},
             "rel_threshold": 0.25, "top_k": 5, "mmr_lambda": 0.7},
    "fm24": {"weights": {"IndustryMatch": 0.35, "ScaleFactor": 0.15,
                         "ProductRelevance": 0.30, "SupplyChainRisk": 0.20}},
    "fm14": {"rate_threshold": 0.15},
    "fm16": {"conflict_delta": {"RATE": 0.25, "default": 0.0}},
    "fm08_half_life_days": {"news": 3, "macro_release": 14, "alfa_weekly": 30,
                            "alfa_strategy": 60},
    "fm09_tau_days": 7,
    "fm10_source_tiers": {"regulator": 1.0, "federal_media": 0.85,
                          "business_media": 0.6, "social": 0.4, "indep_bonus": 0.1},
    "fm11_window_days": 14,
    "fm17_dedup": {"jaccard_shingles": 0.8, "tfidf_cosine": 0.6,
                   "min_shared_entities": 2, "dt_hours": 48},
    "fm20_confidence": {"high": 0.75, "mid": 0.50},
    "fm22_shock": {"gamma": 0.5, "depth": 2},
    "fm23_group_delta": 0.5,
    "fm18_calibration": {"step": 0.05, "lambda_reg": 0.01, "bootstrap_iters": 1000},
    "f13_completeness": {"REVENUE_COMPANY": 3, "MAINOKVED_CODE": 3, "REGION": 2,
                         "FTS_FLAG": 2, "CL_TYPE": 2, "CITY": 1, "ECO_MARK_LABEL": 1,
                         "COMPANY_AGE_YEARS_GRADATION": 1,
                         "AMOUNT_WORKERS_FNS_GRADATION": 1, "OKFS_NAME": 1,
                         "GROUP_ID": 1},
}

_DEFAULT_EXPOSURE = {
    "e01_debt": {"LOA": 0.6, "LEA": 0.3, "FACTORING": 0.3},
    "e02_dep": 0.6,
    "e03_fx": 0.8,
    "e08_labor": {"base": 0.7, "zpp_bonus": 0.3},
    "e09_credit": {"growth_flag": 0.4, "LOA": 0.3, "LEA": 0.2,
                   "FACTORING": 0.1, "clip": 1.0},
    "e10_budget": {"GAR": 0.5, "state_related": 0.3, "prior_norm": 0.2},
    "e12_geopol": {"fx_factor": 0.8,
                   "risk_tiers": {"high": 1.0, "elevated": 0.7,
                                  "moderate": 0.4, "low": 0.2},
                   "country_tier": {}},
    "e13_cpty": {"hhi_in": 0.6, "hhi_out": 0.4,
                 "small_counterparty_threshold": 5,
                 "small_boost_per_missing": 0.05},
    "e14_alpha": {"own": 0.4, "prior": 0.4, "chain": 0.2},
}

# Порядок 12 факторов — из docs/data/factors.csv (важно для радара CH-06 и матриц)
FACTOR_ORDER = ["RATE", "FX", "INFL", "ENERGY", "COMMOD", "DEMAND",
                "LABOR", "CREDIT", "BUDGET", "REG", "GEOPOL", "CPTY"]


def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_weights(cfg_dir: Path | None = None) -> dict:
    """Веса/пороги формул: config/weights.yaml поверх встроенных дефолтов."""
    cfg_dir = cfg_dir or (PKG_ROOT / "config")
    f = cfg_dir / "weights.yaml"
    data = {}
    if f.exists():
        with open(f, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    return _deep_merge(_DEFAULT_WEIGHTS, data)


def load_exposure(cfg_dir: Path | None = None) -> dict:
    """Веса экспозиций E-xx: config/exposure_weights.yaml поверх дефолтов."""
    cfg_dir = cfg_dir or (PKG_ROOT / "config")
    f = cfg_dir / "exposure_weights.yaml"
    data = {}
    if f.exists():
        with open(f, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    return _deep_merge(_DEFAULT_EXPOSURE, data)


def find_ref_dir(repo_root: Path | None = None) -> Path | None:
    """Ищет папку машиночитаемых справочников (docs/data из репозитория NAFA).

    Порядок: переменная окружения NAFA_REF_DIR -> repo_root/docs/data ->
    repo_root/data -> da_scripts/ref_data. Возвращает None, если не найдено.
    """
    env = os.environ.get("NAFA_REF_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    roots = []
    if repo_root:
        roots.append(Path(repo_root))
    roots.append(Path.cwd())
    roots.append(PKG_ROOT)
    for r in roots:
        for cand in (r / "docs" / "data", r / "data", r / "ref_data"):
            if (cand / "sector_sensitivity_matrix.csv").exists():
                return cand
    return None


def find_repo_root() -> Path:
    """Корень репозитория: папка, внутри которой лежит docs/ или analytics/."""
    for start in (Path.cwd(), PKG_ROOT):
        for p in [start, *start.parents]:
            if (p / "docs").is_dir() or (p / "docs" / "data").is_dir():
                return p
    return Path.cwd()
