"""Alpha score computation with behavioral penalty application."""

from __future__ import annotations

import logging

import config
from models import WalletMetrics
from utils.helpers import clamp

logger = logging.getLogger(__name__)

# Normalisation caps for components
_MAX_PROFIT_FACTOR = 5.0   # Cap at 5 for scoring (beyond = treat same)
_MAX_SORTINO       = 5.0   # Cap at 5
_MIN_CONSISTENCY   = 0.0


def _normalise_profit_factor(pf: float) -> float:
    """Map profit_factor to 0-1 range (capped at MAX_PROFIT_FACTOR)."""
    if pf <= 0:
        return 0.0
    return min(pf / _MAX_PROFIT_FACTOR, 1.0)


def _normalise_sortino(sortino: float) -> float:
    """Map sortino to 0-1 range (capped at MAX_SORTINO)."""
    if sortino <= 0:
        return 0.0
    return min(sortino / _MAX_SORTINO, 1.0)


def compute_alpha_score(wm: WalletMetrics) -> float:
    """Compute and return the alpha score for a WalletMetrics object.

    Formula:
        base = (win_rate * 40) + (profit_factor_norm * 20) + (sortino_norm * 20) + (consistency * 20)
    Penalties applied multiplicatively:
        martingale    -30%
        revenge       -25%
        fomo          -20%
        concentration -15%
        sybil         -100% (score = 0)
    Final score clamped to [0, 100].
    """
    if wm.flag_sybil:
        logger.debug("Wallet %s flagged as Sybil → score = 0", wm.address)
        return 0.0

    pf_norm = _normalise_profit_factor(wm.profit_factor)
    sortino_norm = _normalise_sortino(wm.sortino_ratio)
    consistency = max(_MIN_CONSISTENCY, wm.consistency_score)

    base = (
        wm.win_rate * config.SCORE_WEIGHT_WIN_RATE
        + pf_norm * config.SCORE_WEIGHT_PROFIT_FACTOR
        + sortino_norm * config.SCORE_WEIGHT_SORTINO
        + consistency * config.SCORE_WEIGHT_CONSISTENCY
    )

    # Apply behavioural penalties multiplicatively
    multiplier = 1.0
    if wm.flag_martingale:
        multiplier *= (1.0 - config.PENALTY_MARTINGALE)
        logger.debug("Wallet %s: martingale penalty applied", wm.address)
    if wm.flag_revenge:
        multiplier *= (1.0 - config.PENALTY_REVENGE)
        logger.debug("Wallet %s: revenge penalty applied", wm.address)
    if wm.flag_fomo:
        multiplier *= (1.0 - config.PENALTY_FOMO)
        logger.debug("Wallet %s: fomo penalty applied", wm.address)
    if wm.flag_concentration:
        multiplier *= (1.0 - config.PENALTY_CONCENTRATION)
        logger.debug("Wallet %s: concentration penalty applied", wm.address)

    score = clamp(base * multiplier, 0.0, 100.0)
    logger.debug(
        "Wallet %s: base=%.2f multiplier=%.3f score=%.2f",
        wm.address, base, multiplier, score,
    )
    return score


def score_wallets(wallets: list[WalletMetrics]) -> list[WalletMetrics]:
    """Compute alpha_score for each wallet and return sorted descending."""
    for wm in wallets:
        wm.alpha_score = compute_alpha_score(wm)
    ranked = sorted(wallets, key=lambda w: w.alpha_score, reverse=True)
    logger.info(
        "Scored %d wallets; top score=%.2f",
        len(ranked),
        ranked[0].alpha_score if ranked else 0.0,
    )
    return ranked
