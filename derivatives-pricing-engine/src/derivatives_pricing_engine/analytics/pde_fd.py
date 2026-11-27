"""Finite-difference Black-Scholes PDE solver with boundary conditions and stability checks."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.linalg import solve_banded

from ..contracts import ExerciseStyle, OptionType
from ..errors import NumericalStabilityError, UnsupportedFeatureError, ValidationError
from .black_scholes import price_black_scholes
from .day_count import discount_factor, intrinsic_payoff

_SUPPORTED_SCHEMES = ("crank_nicolson", "implicit", "explicit")


@dataclass(frozen=True)
class PdeOutput:
    """Finite-difference PDE valuation, Greeks, and stability diagnostics."""

    price: float
    intrinsic: float
    time_value: float
    discount_factor: float
    scheme: str
    spot_steps: int
    time_steps: int
    s_max: float
    grid_ds: float
    grid_dt: float
    cfl_number: float
    is_stable: bool
    rannacher_steps: int
    psor_max_iterations_used: int
    psor_converged: bool
    delta: Optional[float]
    gamma: Optional[float]
    runtime_ms: float
    regime: str


def _payoff_array(spots: np.ndarray, strike: float, option_type: OptionType) -> np.ndarray:
    if option_type == OptionType.CALL:
        return np.maximum(spots - strike, 0.0)
    return np.maximum(strike - spots, 0.0)


def _boundary_values(
    tau: float,
    s_max: float,
    strike: float,
    risk_free_rate: float,
    dividend_yield: float,
    option_type: OptionType,
    exercise_style: ExerciseStyle,
) -> tuple[float, float]:
    """Dirichlet boundary values V(0, tau) and V(S_max, tau) at remaining maturity tau."""
    df_r = math.exp(-risk_free_rate * tau)
    df_q = math.exp(-dividend_yield * tau)
    if option_type == OptionType.CALL:
        v_low = 0.0
        euro_high = max(s_max * df_q - strike * df_r, 0.0)
        v_high = max(s_max - strike, euro_high) if exercise_style == ExerciseStyle.AMERICAN else euro_high
    else:
        v_low = strike if exercise_style == ExerciseStyle.AMERICAN else strike * df_r
        v_high = 0.0
    return v_low, v_high


def _solve_tridiagonal_psor_redblack(
    sub: np.ndarray,
    diag: np.ndarray,
    sup: np.ndarray,
    rhs: np.ndarray,
    lower_bound: np.ndarray,
    x_init: np.ndarray,
    omega: float,
    tol: float,
    max_iter: int,
) -> tuple[np.ndarray, int, bool]:
    """Vectorized Red-Black Projected SOR for tridiagonal linear complementarity problems."""
    x = np.maximum(x_init.copy(), lower_bound)
    n = x.size
    inv_diag = 1.0 / diag

    even_idx = np.arange(0, n, 2)
    odd_idx = np.arange(1, n, 2)

    # Precompute slices for neighbor lookups
    even_has_left = even_idx > 0
    even_has_right = even_idx < n - 1
    odd_has_left = odd_idx > 0
    odd_has_right = odd_idx < n - 1

    e_l = even_idx[even_has_left]
    e_r = even_idx[even_has_right]
    o_l = odd_idx[odd_has_left]
    o_r = odd_idx[odd_has_right]

    for iteration in range(1, max_iter + 1):
        max_diff = 0.0

        # Red sweep (even indices depend only on odd neighbors)
        r_even = rhs[even_idx].copy()
        r_even[even_has_left] -= sub[e_l] * x[e_l - 1]
        r_even[even_has_right] -= sup[e_r] * x[e_r + 1]
        cand_even = (1.0 - omega) * x[even_idx] + omega * r_even * inv_diag[even_idx]
        new_even = np.maximum(cand_even, lower_bound[even_idx])
        diff_e = float(np.max(np.abs(new_even - x[even_idx]))) if even_idx.size > 0 else 0.0
        x[even_idx] = new_even

        # Black sweep (odd indices depend only on updated even neighbors)
        r_odd = rhs[odd_idx].copy()
        r_odd[odd_has_left] -= sub[o_l] * x[o_l - 1]
        r_odd[odd_has_right] -= sup[o_r] * x[o_r + 1]
        cand_odd = (1.0 - omega) * x[odd_idx] + omega * r_odd * inv_diag[odd_idx]
        new_odd = np.maximum(cand_odd, lower_bound[odd_idx])
        diff_o = float(np.max(np.abs(new_odd - x[odd_idx]))) if odd_idx.size > 0 else 0.0
        x[odd_idx] = new_odd

        max_diff = max(diff_e, diff_o)
        if max_diff <= tol:
            return x, iteration, True

    return x, max_iter, False


def _build_banded_matrix(sub: np.ndarray, diag: np.ndarray, sup: np.ndarray) -> np.ndarray:
    """Assemble (3, n) banded matrix for scipy.linalg.solve_banded((1, 1), ab, rhs)."""
    n = diag.size
    ab = np.zeros((3, n), dtype=np.float64)
    ab[0, 1:] = sup[:-1]
    ab[1, :] = diag
    ab[2, :-1] = sub[1:]
    return ab


def price_pde_fd(
    spot: float,
    strike: float,
    time_to_maturity: float,
    risk_free_rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionType,
    exercise_style: ExerciseStyle = ExerciseStyle.EUROPEAN,
    spot_steps: int = 400,
    time_steps: int = 400,
    scheme: str = "crank_nicolson",
    s_max_multiplier: float = 4.0,
    rannacher_steps: int = 4,
    psor_omega: float = 1.2,
    psor_tol: float = 1e-10,
    psor_max_iter: int = 10000,
) -> PdeOutput:
    """Solve the Black-Scholes PDE with explicit boundary conditions and stability checks."""
    t_start = time.perf_counter()

    scheme_norm = scheme.strip().lower() if isinstance(scheme, str) else ""
    if scheme_norm not in _SUPPORTED_SCHEMES:
        raise UnsupportedFeatureError(
            f"Unsupported finite-difference scheme '{scheme}'. Supported: {list(_SUPPORTED_SCHEMES)}.",
            details={"scheme": scheme, "supported": list(_SUPPORTED_SCHEMES)},
        )
    if isinstance(spot_steps, bool) or not isinstance(spot_steps, int) or spot_steps < 10:
        raise ValidationError("'spot_steps' must be an integer >= 10.", details={"spot_steps": spot_steps})
    if isinstance(time_steps, bool) or not isinstance(time_steps, int) or time_steps < 2:
        raise ValidationError("'time_steps' must be an integer >= 2.", details={"time_steps": time_steps})
    if s_max_multiplier < 2.0:
        raise ValidationError("'s_max_multiplier' must be >= 2.0.", details={"s_max_multiplier": s_max_multiplier})
    if not (0.0 < psor_omega < 2.0):
        raise ValidationError("'psor_omega' must be in (0, 2).", details={"psor_omega": psor_omega})
    if psor_tol <= 0.0 or psor_max_iter < 1:
        raise ValidationError("Invalid PSOR solver tolerance or max iterations.")
    if spot <= 0.0 or strike <= 0.0:
        raise ValidationError("spot and strike must be strictly positive.")
    if time_to_maturity < 0.0:
        raise ValidationError("time_to_maturity must be non-negative.")
    if volatility < 0.0:
        raise ValidationError("volatility must be non-negative.")

    intrinsic = intrinsic_payoff(spot, strike, option_type)
    df_r = discount_factor(risk_free_rate, time_to_maturity)
    s_max = float(s_max_multiplier * max(spot, strike))
    ds = s_max / spot_steps
    dt = time_to_maturity / time_steps if time_to_maturity > 0.0 else 0.0

    # Zero-maturity limit
    if time_to_maturity == 0.0:
        if option_type == OptionType.CALL:
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        return PdeOutput(
            price=intrinsic,
            intrinsic=intrinsic,
            time_value=0.0,
            discount_factor=1.0,
            scheme=scheme_norm,
            spot_steps=spot_steps,
            time_steps=time_steps,
            s_max=s_max,
            grid_ds=ds,
            grid_dt=0.0,
            cfl_number=0.0,
            is_stable=True,
            rannacher_steps=0,
            psor_max_iterations_used=0,
            psor_converged=True,
            delta=delta,
            gamma=0.0,
            runtime_ms=elapsed_ms,
            regime="zero_maturity_limit",
        )

    # Zero-volatility hyperbolic limit
    if volatility == 0.0:
        if exercise_style == ExerciseStyle.EUROPEAN:
            fwd_spot = spot * math.exp((risk_free_rate - dividend_yield) * time_to_maturity)
            price = df_r * intrinsic_payoff(fwd_spot, strike, option_type)
        else:
            times = np.linspace(0.0, time_to_maturity, time_steps + 1)
            spots_path = spot * np.exp((risk_free_rate - dividend_yield) * times)
            discounted_payoffs = np.exp(-risk_free_rate * times) * _payoff_array(spots_path, strike, option_type)
            price = float(np.max(discounted_payoffs))
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        return PdeOutput(
            price=price,
            intrinsic=intrinsic,
            time_value=price - intrinsic,
            discount_factor=df_r,
            scheme=scheme_norm,
            spot_steps=spot_steps,
            time_steps=time_steps,
            s_max=s_max,
            grid_ds=ds,
            grid_dt=dt,
            cfl_number=0.0,
            is_stable=True,
            rannacher_steps=0,
            psor_max_iterations_used=0,
            psor_converged=True,
            delta=None,
            gamma=None,
            runtime_ms=elapsed_ms,
            regime="zero_volatility_limit",
        )

    # Spatial grid S_i = i * ds, i = 0..M; interior indices i = 1..M-1
    s_grid = np.linspace(0.0, s_max, spot_steps + 1)
    idx = np.arange(1, spot_steps, dtype=np.float64)
    sigma2_i2 = (volatility * volatility) * (idx * idx)
    drift_i = (risk_free_rate - dividend_yield) * idx

    a_coef = 0.5 * (sigma2_i2 - drift_i)
    b_coef = -(sigma2_i2 + risk_free_rate)
    c_coef = 0.5 * (sigma2_i2 + drift_i)

    # Stability metric (explicit von Neumann / CFL condition: dt * (sigma^2 * (M-1)^2 + max(r, 0)) <= 1)
    max_diag_rate = float(volatility * volatility * ((spot_steps - 1) ** 2) + max(risk_free_rate, 0.0))
    cfl_number = dt * max_diag_rate

    if scheme_norm == "explicit":
        min_diag_weight = float(np.min(1.0 + dt * b_coef))
        if cfl_number > 1.0 or min_diag_weight < 0.0:
            min_required_time_steps = int(math.ceil(time_to_maturity * max_diag_rate)) + 1
            raise NumericalStabilityError(
                "Explicit finite-difference scheme violates CFL stability condition.",
                details={
                    "scheme": "explicit",
                    "cfl_number": cfl_number,
                    "min_diag_weight": min_diag_weight,
                    "grid_dt": dt,
                    "max_stable_dt": 1.0 / max_diag_rate,
                    "spot_steps": spot_steps,
                    "time_steps": time_steps,
                    "min_required_time_steps": min_required_time_steps,
                },
            )

    payoff_full = _payoff_array(s_grid, strike, option_type)
    payoff_int = payoff_full[1:-1]
    values = payoff_full.copy()

    psor_max_used = 0
    psor_all_converged = True
    effective_rannacher = 0

    def _implicit_or_cn_step(
        v_curr: np.ndarray,
        step_dt: float,
        theta: float,
        tau_curr: float,
        tau_next: float,
    ) -> np.ndarray:
        nonlocal psor_max_used, psor_all_converged

        v_low_curr, v_high_curr = _boundary_values(
            tau_curr, s_max, strike, risk_free_rate, dividend_yield, option_type, exercise_style
        )
        v_low_next, v_high_next = _boundary_values(
            tau_next, s_max, strike, risk_free_rate, dividend_yield, option_type, exercise_style
        )

        # LHS matrix: (I - theta * dt * L)
        lhs_sub = -theta * step_dt * a_coef
        lhs_diag = 1.0 - theta * step_dt * b_coef
        lhs_sup = -theta * step_dt * c_coef

        # RHS vector: (I + (1 - theta) * dt * L) v_curr + boundary terms
        v_int = v_curr[1:-1]
        if theta < 1.0:
            one_m_theta = 1.0 - theta
            rhs = (
                one_m_theta * step_dt * a_coef * v_curr[:-2]
                + (1.0 + one_m_theta * step_dt * b_coef) * v_int
                + one_m_theta * step_dt * c_coef * v_curr[2:]
            )
            # Ensure curr boundary values are reflected cleanly
            rhs[0] += one_m_theta * step_dt * a_coef[0] * (v_low_curr - v_curr[0])
            rhs[-1] += one_m_theta * step_dt * c_coef[-1] * (v_high_curr - v_curr[-1])
        else:
            rhs = v_int.copy()

        # Move known next-time boundary values from LHS to RHS
        rhs[0] -= lhs_sub[0] * v_low_next
        rhs[-1] -= lhs_sup[-1] * v_high_next

        if exercise_style == ExerciseStyle.EUROPEAN:
            ab = _build_banded_matrix(lhs_sub, lhs_diag, lhs_sup)
            v_int_next = solve_banded((1, 1), ab, rhs, overwrite_ab=True, overwrite_b=True, check_finite=False)
        else:
            v_int_next, iters, converged = _solve_tridiagonal_psor_redblack(
                sub=lhs_sub,
                diag=lhs_diag,
                sup=lhs_sup,
                rhs=rhs,
                lower_bound=payoff_int,
                x_init=v_int,
                omega=psor_omega,
                tol=psor_tol,
                max_iter=psor_max_iter,
            )
            if iters > psor_max_used:
                psor_max_used = iters
            if not converged:
                psor_all_converged = False

        v_next = np.empty_like(v_curr)
        v_next[0] = v_low_next
        v_next[1:-1] = v_int_next
        v_next[-1] = v_high_next
        return v_next

    tau = 0.0
    if scheme_norm == "explicit":
        for _ in range(time_steps):
            tau_next = tau + dt
            v_low_next, v_high_next = _boundary_values(
                tau_next, s_max, strike, risk_free_rate, dividend_yield, option_type, exercise_style
            )
            v_int_next = (
                dt * a_coef * values[:-2]
                + (1.0 + dt * b_coef) * values[1:-1]
                + dt * c_coef * values[2:]
            )
            if exercise_style == ExerciseStyle.AMERICAN:
                v_int_next = np.maximum(v_int_next, payoff_int)
            values[0] = v_low_next
            values[1:-1] = v_int_next
            values[-1] = v_high_next
            tau = tau_next
    elif scheme_norm == "implicit":
        for _ in range(time_steps):
            tau_next = tau + dt
            values = _implicit_or_cn_step(values, dt, 1.0, tau, tau_next)
            tau = tau_next
    else:
        # Crank-Nicolson with Rannacher half-step implicit smoothing near the expiry kink
        rannacher_pairs = min(rannacher_steps // 2, time_steps)
        effective_rannacher = 2 * rannacher_pairs
        half_dt = 0.5 * dt
        for _ in range(effective_rannacher):
            tau_next = tau + half_dt
            values = _implicit_or_cn_step(values, half_dt, 1.0, tau, tau_next)
            tau = tau_next
        for _ in range(time_steps - rannacher_pairs):
            tau_next = tau + dt
            values = _implicit_or_cn_step(values, dt, 0.5, tau, tau_next)
            tau = tau_next

    if exercise_style == ExerciseStyle.AMERICAN and not psor_all_converged:
        raise NumericalStabilityError(
            "PSOR solver did not converge within psor_max_iter during American PDE backward march.",
            details={
                "psor_max_iter": psor_max_iter,
                "psor_tol": psor_tol,
                "psor_omega": psor_omega,
                "spot_steps": spot_steps,
                "time_steps": time_steps,
            },
        )

    # Interpolate price and sensitivities at S0 using cubic spline
    spline = CubicSpline(s_grid, values)
    price = float(spline(spot))
    if exercise_style == ExerciseStyle.AMERICAN:
        price = max(price, intrinsic)
    else:
        price = max(price, 0.0)
    delta = float(spline(spot, 1))
    gamma = float(spline(spot, 2))
    elapsed_ms = (time.perf_counter() - t_start) * 1000.0

    return PdeOutput(
        price=price,
        intrinsic=intrinsic,
        time_value=price - intrinsic,
        discount_factor=df_r,
        scheme=scheme_norm,
        spot_steps=spot_steps,
        time_steps=time_steps,
        s_max=s_max,
        grid_ds=ds,
        grid_dt=dt,
        cfl_number=cfl_number,
        is_stable=True,
        rannacher_steps=effective_rannacher,
        psor_max_iterations_used=psor_max_used,
        psor_converged=psor_all_converged,
        delta=delta,
        gamma=gamma,
        runtime_ms=elapsed_ms,
        regime=f"pde_{scheme_norm}",
    )


def pde_convergence_study(
    spot: float,
    strike: float,
    time_to_maturity: float,
    risk_free_rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: OptionType,
    exercise_style: ExerciseStyle,
    resolutions: Sequence[tuple[int, int]] = ((100, 100), (200, 200), (400, 400)),
    scheme: str = "crank_nicolson",
    reference_price: Optional[float] = None,
) -> dict[str, object]:
    """Run PDE grid refinement across >= 3 resolutions and measure error convergence."""
    if len(resolutions) < 2:
        raise ValidationError("resolutions must contain at least 2 (spot_steps, time_steps) pairs.")

    if reference_price is not None:
        ref_val = float(reference_price)
        ref_source = "external_reference"
    elif exercise_style == ExerciseStyle.EUROPEAN:
        ref_val = price_black_scholes(
            spot=spot,
            strike=strike,
            time_to_maturity=time_to_maturity,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            option_type=option_type,
        ).price
        ref_source = "black_scholes_analytic"
    else:
        max_m = max(m for m, _ in resolutions)
        max_n = max(n for _, n in resolutions)
        ref_val = price_pde_fd(
            spot=spot,
            strike=strike,
            time_to_maturity=time_to_maturity,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            option_type=option_type,
            exercise_style=exercise_style,
            spot_steps=max_m * 2,
            time_steps=max_n * 2,
            scheme=scheme,
        ).price
        ref_source = f"pde_{scheme}_{max_m * 2}x{max_n * 2}"

    rows: list[dict[str, object]] = []
    prev_abs_err: Optional[float] = None
    prev_ds: Optional[float] = None

    for m, n in resolutions:
        out = price_pde_fd(
            spot=spot,
            strike=strike,
            time_to_maturity=time_to_maturity,
            risk_free_rate=risk_free_rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            option_type=option_type,
            exercise_style=exercise_style,
            spot_steps=m,
            time_steps=n,
            scheme=scheme,
        )
        signed_err = out.price - ref_val
        abs_err = abs(signed_err)
        order: Optional[float] = None
        if prev_abs_err is not None and prev_ds is not None and abs_err > 0.0 and prev_abs_err > 0.0 and prev_ds != out.grid_ds:
            order = math.log(prev_abs_err / abs_err) / math.log(prev_ds / out.grid_ds)

        rows.append(
            {
                "spot_steps": m,
                "time_steps": n,
                "grid_ds": out.grid_ds,
                "grid_dt": out.grid_dt,
                "price": out.price,
                "signed_error": signed_err,
                "abs_error": abs_err,
                "rel_error": abs_err / max(abs(ref_val), 1e-12),
                "empirical_order": order,
                "cfl_number": out.cfl_number,
                "psor_max_iterations_used": out.psor_max_iterations_used,
                "runtime_ms": out.runtime_ms,
            }
        )
        prev_abs_err = abs_err
        prev_ds = out.grid_ds

    return {
        "method": "pde_fd",
        "scheme": scheme,
        "option_type": option_type.value,
        "exercise_style": exercise_style.value,
        "reference_price": ref_val,
        "reference_source": ref_source,
        "resolutions": rows,
    }
