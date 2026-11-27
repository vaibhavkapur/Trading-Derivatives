"""Deterministic numerical root-finding utilities (zero external dependencies)."""

from __future__ import annotations

import math
from typing import Callable, Tuple

from .errors import ConvergenceError


def brentq(
    func: Callable[[float], float],
    a: float,
    b: float,
    *,
    tol: float = 1e-12,
    max_iter: int = 200,
    label: str = "brentq",
) -> Tuple[float, int]:
    """Find a root of func in [a, b] using Brent's method.

    Returns:
        (root, iterations)
    """
    fa = func(a)
    fb = func(b)

    if not (math.isfinite(fa) and math.isfinite(fb)):
        raise ConvergenceError(
            f"{label}: non-finite endpoint evaluation",
            details={"a": a, "b": b, "fa": str(fa), "fb": str(fb)},
        )

    if abs(fa) <= tol:
        return a, 0
    if abs(fb) <= tol:
        return b, 0

    if fa * fb > 0.0:
        raise ConvergenceError(
            f"{label}: root is not bracketed in [{a}, {b}]",
            details={"a": a, "b": b, "fa": fa, "fb": fb},
        )

    c = a
    fc = fa
    d = b - a
    e = d

    for iteration in range(1, max_iter + 1):
        if fb * fc > 0.0:
            c = a
            fc = fa
            d = b - a
            e = d

        if abs(fc) < abs(fb):
            a, b, c = b, c, b
            fa, fb, fc = fb, fc, fb

        tol1 = 2.0 * 2.220446049250313e-16 * abs(b) + 0.5 * tol
        xm = 0.5 * (c - b)

        if abs(xm) <= tol1 or abs(fb) <= tol:
            return b, iteration

        if abs(e) >= tol1 and abs(fa) > abs(fb):
            s = fb / fa
            if a == c:
                p = 2.0 * xm * s
                q = 1.0 - s
            else:
                q = fa / fc
                r = fb / fc
                p = s * (2.0 * xm * q * (q - r) - (b - a) * (r - 1.0))
                q = (q - 1.0) * (r - 1.0) * (s - 1.0)
            if p > 0.0:
                q = -q
            p = abs(p)
            cond1 = 2.0 * p < min(3.0 * xm * q - abs(tol1 * q), abs(e * q))
            if cond1 and q != 0.0:
                e = d
                d = p / q
            else:
                d = xm
                e = d
        else:
            d = xm
            e = d

        a = b
        fa = fb
        if abs(d) > tol1:
            b += d
        else:
            b += tol1 if xm > 0.0 else -tol1
        fb = func(b)

        if not math.isfinite(fb):
            raise ConvergenceError(
                f"{label}: non-finite function value during iteration",
                details={"iteration": iteration, "x": b, "fx": str(fb)},
            )

    raise ConvergenceError(
        f"{label}: failed to converge within {max_iter} iterations",
        details={"a": a, "b": b, "fb": fb, "tol": tol, "max_iter": max_iter},
    )


def newton_safeguarded(
    func: Callable[[float], float],
    dfunc: Callable[[float], float],
    a: float,
    b: float,
    x0: float,
    *,
    tol: float = 1e-12,
    max_iter: int = 200,
    label: str = "newton_safeguarded",
) -> Tuple[float, int]:
    """Bracketed Newton-Raphson with bisection safeguard for smooth monotonic problems."""
    fa = func(a)
    fb = func(b)
    if abs(fa) <= tol:
        return a, 0
    if abs(fb) <= tol:
        return b, 0
    if fa * fb > 0.0:
        raise ConvergenceError(
            f"{label}: root is not bracketed in [{a}, {b}]",
            details={"a": a, "b": b, "fa": fa, "fb": fb},
        )

    low, high = (a, b) if fa < 0.0 else (b, a)
    x = min(max(x0, min(a, b)), max(a, b))
    if x == a or x == b:
        x = 0.5 * (a + b)

    for iteration in range(1, max_iter + 1):
        fx = func(x)
        if abs(fx) <= tol or abs(high - low) <= tol:
            return x, iteration

        if fx < 0.0:
            low = x
        else:
            high = x

        dfx = dfunc(x)
        use_bisection = False
        if not math.isfinite(dfx) or abs(dfx) < 1e-18:
            use_bisection = True
        else:
            x_new = x - fx / dfx
            lo_bound, hi_bound = (low, high) if low < high else (high, low)
            if not (lo_bound < x_new < hi_bound):
                use_bisection = True

        x = 0.5 * (low + high) if use_bisection else x_new

    raise ConvergenceError(
        f"{label}: failed to converge within {max_iter} iterations",
        details={"low": low, "high": high, "x": x, "tol": tol},
    )
