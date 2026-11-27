"""Interactive Plotly visualization for calibrated volatility surfaces and slice fits."""

from __future__ import annotations

from pathlib import Path
from typing import Union

import numpy as np

from volatility_surface_engine.surface import VolatilitySurface


def export_surface_html(
    surface: VolatilitySurface,
    output_path: Union[str, Path],
    k_points: int = 41,
    t_points: int = 15,
    model: str = "svi",
) -> Path:
    """Render a 3D implied-volatility surface and observed quote scatter to an interactive HTML file."""
    try:
        import plotly.graph_objects as go
    except ImportError as exc:
        raise ImportError("Plotly is required for HTML surface export. Install with `pip install plotly`.") from exc

    k_grid, t_grid, iv_mat = surface.sample_grid(k_points=k_points, t_points=t_points, model=model)
    status_label = "VALID (0 arbitrage violations)" if surface.is_valid else "INVALID (arbitrage violation detected)"

    fig = go.Figure()
    fig.add_trace(
        go.Surface(
            x=k_grid,
            y=t_grid,
            z=iv_mat * 100.0,
            colorscale="Viridis",
            opacity=0.88,
            name=f"{model.upper()} Surface",
            colorbar={"title": "Implied Vol (%)"},
        )
    )

    obs_k = []
    obs_t = []
    obs_iv = []
    for sl in surface.slices:
        for q in sl.fitted_quotes:
            obs_k.append(q.log_moneyness)
            obs_t.append(q.time_to_expiry_years)
            obs_iv.append(q.implied_vol * 100.0)

    if obs_k:
        fig.add_trace(
            go.Scatter3d(
                x=np.array(obs_k),
                y=np.array(obs_t),
                z=np.array(obs_iv),
                mode="markers",
                marker={"size": 3, "color": "crimson"},
                name="Cleaned Market IV",
            )
        )

    fig.update_layout(
        title=f"Volatility Surface ({surface.snapshot.underlying}) — {surface.surface_id} [{status_label}]",
        scene={
            "xaxis_title": "Log-Moneyness k = ln(K/F)",
            "yaxis_title": "Time to Expiry T (years)",
            "zaxis_title": "Implied Volatility (%)",
        },
        margin={"l": 10, "r": 10, "b": 10, "t": 50},
    )

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path), include_plotlyjs="cdn")
    return out_path
