"""Core quantitative analytics isolated from I/O and service orchestration."""

from __future__ import annotations

from .binomial import BinomialOutput, binomial_convergence_study, price_binomial
from .black_scholes import BlackScholesOutput, price_black_scholes
from .day_count import discount_factor, intrinsic_payoff, year_fraction
from .monte_carlo import MonteCarloOutput, monte_carlo_coverage_study, price_monte_carlo
from .pde_fd import PdeOutput, pde_convergence_study, price_pde_fd

__all__ = [
    "BinomialOutput",
    "BlackScholesOutput",
    "MonteCarloOutput",
    "PdeOutput",
    "binomial_convergence_study",
    "discount_factor",
    "intrinsic_payoff",
    "monte_carlo_coverage_study",
    "pde_convergence_study",
    "price_binomial",
    "price_black_scholes",
    "price_monte_carlo",
    "price_pde_fd",
    "year_fraction",
]
