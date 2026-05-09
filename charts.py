"""
Genera las gráficas del plan de optimización:
  1. Plotly interactivo (barras) — plan de producción + utilización de insumos.
  2. Región factible matplotlib — método gráfico PL proyectado en las 2 primeras variables.
"""

import base64
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go
import plotly.offline as pyo
from plotly.subplots import make_subplots

# Nombres amigables de insumos (fallback: usar la clave en title case)
NOMBRES_INSUMOS = {
    "drill":      "Tela Drill",
    "popelina":   "Tela Popelina",
    "licra":      "Tela Licra",
    "lana":       "Tela Lana",
    "hilo":       "Hilo",
    "botones":    "Botones",
    "entretela":  "Entretela",
    "cierres":    "Cierres",
    "elastico":   "Elástico",
    "cinta":      "Cinta",
    "etiquetas":  "Etiquetas",
}

COLORES_PLAN = ["#2563EB", "#7C3AED", "#EC4899", "#059669", "#D97706",
                "#0891B2", "#DC2626", "#16A34A", "#9333EA", "#EA580C"]


def generar_grafica_optimizacion(resultado: dict, var_labels: dict | None = None, insumo_labels: dict | None = None) -> str:
    """
    Genera gráfica Plotly con subplots:
    - Izquierda: plan de producción (barras, etiquetas dinámicas del colegio)
    - Derecha:   utilización de insumos (% apilado, color por nivel de alerta)

    var_labels: {var_key: "Pantalón Diario"} — si None usa la clave en title case.
    """
    plan = resultado.get("plan") or {}
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()

    recursos = resultado.get("recursos") or {}
    utilidad = resultado.get("utilidad_total", 0)
    labels   = var_labels    or {}
    ins_labs = insumo_labels or {}

    # ── Plan de producción ────────────────────────────────────────────────────
    plan_keys   = list(plan.keys())
    plan_labels = [labels.get(k, k.replace("_", " ").title()) for k in plan_keys]
    plan_values = [plan.get(k, 0) for k in plan_keys]
    colores_bar = [COLORES_PLAN[i % len(COLORES_PLAN)] for i in range(len(plan_keys))]

    # ── Utilización de insumos ────────────────────────────────────────────────
    rec_keys   = list(recursos.keys())
    def _rv(r, field, default=0):
        return r.get(field, default) if isinstance(r, dict) else getattr(r, field, default)

    rec_labels = [
        f"{ins_labs.get(k, NOMBRES_INSUMOS.get(k, k.replace('_', ' ').title()))} ({_rv(recursos[k], 'unidad_medida', 'un') or 'un'})"
        for k in rec_keys
    ]
    rec_used  = [_rv(recursos[k], "usado")           for k in rec_keys]
    rec_avail = [_rv(recursos[k], "disponible")       for k in rec_keys]
    rec_pct   = [_rv(recursos[k], "utilizacion_pct")  for k in rec_keys]

    def color_pct(p: float) -> str:
        if p >= 95: return "#EF4444"
        if p >= 75: return "#F59E0B"
        return "#10B981"

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["Plan de producción (unidades)", "Utilización de insumos (%)"],
        horizontal_spacing=0.12,
    )

    fig.add_trace(
        go.Bar(
            x=plan_labels, y=plan_values,
            marker_color=colores_bar,
            text=plan_values, textposition="outside",
            hovertemplate="%{x}: <b>%{y} unidades</b><extra></extra>",
            name="Prendas",
        ),
        row=1, col=1,
    )

    if rec_keys:
        rec_holgura = [max(0, a - u) for a, u in zip(rec_avail, rec_used)]
        rec_colors  = [color_pct(p) for p in rec_pct]
        fig.add_trace(
            go.Bar(
                x=rec_labels, y=rec_used,
                marker_color=rec_colors, name="Usado",
                text=[f"{p:.1f}%" for p in rec_pct], textposition="outside",
                hovertemplate="%{x}<br>Usado: <b>%{y}</b><br>%{text}<extra></extra>",
            ),
            row=1, col=2,
        )
        fig.add_trace(
            go.Bar(
                x=rec_labels, y=rec_holgura,
                marker_color="#E5E7EB", name="Holgura",
                hovertemplate="%{x}<br>Disponible: %{y}<extra></extra>",
            ),
            row=1, col=2,
        )
        fig.update_layout(barmode="stack")

    fig.update_layout(
        title={
            "text": f"Optimización de Producción | Utilidad: <b>${utilidad:,.0f} COP</b>",
            "x": 0.5,
            "font": {"size": 14, "family": "Inter, system-ui, sans-serif", "color": "#111827"},
        },
        showlegend=False, height=400,
        margin={"l": 40, "r": 40, "t": 80, "b": 50},
        plot_bgcolor="white", paper_bgcolor="white",
        font={"family": "Inter, system-ui, sans-serif", "color": "#374151"},
    )
    fig.update_xaxes(showgrid=False, tickangle=-20)
    fig.update_yaxes(showgrid=True, gridcolor="#F3F4F6", zeroline=False)

    div_content = pyo.plot(
        fig, output_type="div", include_plotlyjs=False,
        config={"displayModeBar": False, "responsive": True},
    )
    fig.data = []
    fig.layout = {}
    del fig

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="https://cdn.plot.ly/plotly-2.26.0.min.js" charset="utf-8"></script>
<style>body{{margin:0;padding:4px;background:white;font-family:Inter,system-ui,sans-serif;}}</style>
</head>
<body>{div_content}</body>
</html>"""


# ─────────────────────────────────────────────────────────────────────────────
# Gráfica 2: Región Factible — proyectada en las 2 primeras variables del plan
# ─────────────────────────────────────────────────────────────────────────────

_PALETTE = ["#DC2626", "#16A34A", "#D97706", "#7C3AED", "#0891B2", "#DB2777"]


def _build_region_png(
    coef_matrix: dict,
    stocks: dict,
    plan: dict,
    var_labels: dict | None = None,
    insumo_labels: dict | None = None,
) -> bytes:
    """
    Región factible en 2D usando las 2 primeras variables del plan.
    Si hay menos de 2 variables, devuelve una imagen de placeholder.
    """
    labels   = var_labels    or {}
    ins_labs = insumo_labels or {}
    plan_d = plan if isinstance(plan, dict) else (plan.model_dump() if hasattr(plan, "model_dump") else {})

    var_list = list(coef_matrix.keys())
    if len(var_list) < 2:
        # Con 1 variable no hay región 2D — genera imagen simple
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.text(0.5, 0.5, "Región factible 2D\nrequiere ≥ 2 tipos de prenda",
                ha="center", va="center", fontsize=12, color="#6B7280",
                transform=ax.transAxes)
        ax.axis("off")
        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        data = buf.read()
        buf.close()
        return data

    VAR_X, VAR_Y = var_list[0], var_list[1]
    FIXED = var_list[2:]
    label_x = labels.get(VAR_X, VAR_X.replace("_", " ").title())
    label_y = labels.get(VAR_Y, VAR_Y.replace("_", " ").title())

    # Descontar consumo de variables fijas del stock disponible
    remaining: dict[str, float] = {}
    for ins, stock in stocks.items():
        consumed = sum(coef_matrix.get(v, {}).get(ins, 0) * plan_d.get(v, 0) for v in FIXED)
        r = float(stock) - consumed
        if r > 1e-6:
            remaining[ins] = r

    constraints: list[tuple[str, float, float, float]] = []
    for ins, rem in remaining.items():
        cx = coef_matrix.get(VAR_X, {}).get(ins, 0)
        cy = coef_matrix.get(VAR_Y, {}).get(ins, 0)
        if cx > 0 or cy > 0:
            constraints.append((ins, cx, cy, rem))

    if not constraints:
        constraints = [("limite", 1.0, 0.0, 50.0), ("limite", 0.0, 1.0, 50.0)]

    x_bounds = [rem / cx for _, cx, _, rem in constraints if cx > 0]
    y_bounds = [rem / cy for _, _, cy, rem in constraints if cy > 0]
    max_x = min(max(x_bounds + [10.0]) * 1.2, 600.0)
    max_y = min(max(y_bounds + [10.0]) * 1.2, 600.0)

    xs = np.linspace(0, max_x, 260)
    ys = np.linspace(0, max_y, 260)
    X, Y = np.meshgrid(xs, ys)
    feasible = np.ones_like(X, dtype=bool)
    for _, cx, cy, rem in constraints:
        feasible &= (cx * X + cy * Y <= rem + 1e-9)

    fig, ax = plt.subplots(figsize=(10, 7.5))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#F8FAFC")

    ax.contourf(X, Y, feasible.astype(float), levels=[0.5, 1.5], colors=["#93C5FD"], alpha=0.40)

    x_range = np.linspace(0, max_x, 600)
    for i, (ins, cx, cy, rem) in enumerate(constraints):
        color = _PALETTE[i % len(_PALETTE)]
        label = ins_labs.get(ins, NOMBRES_INSUMOS.get(ins, ins.replace("_", " ").title()))
        if cy > 0:
            y_line = (rem - cx * x_range) / cy
            mask = (y_line >= -0.5) & (y_line <= max_y * 1.05) & (x_range >= 0)
            if mask.sum() > 5:
                ax.plot(x_range[mask], y_line[mask], "-", color=color, linewidth=2.3, label=label)
                mid = len(x_range[mask]) // 2
                ax.annotate(f"  {label}", (x_range[mask][mid], y_line[mask][mid]),
                            fontsize=8.5, color=color, fontweight="bold")
        elif cx > 0:
            xv = rem / cx
            ax.axvline(x=xv, color=color, linewidth=2.3, linestyle="--", label=label)
            ax.text(xv + max_x * 0.01, max_y * 0.5, f"  {label}",
                    fontsize=8.5, color=color, fontweight="bold")

    ax.axhline(y=0, color="#1F2937", linewidth=1.8, zorder=3)
    ax.axvline(x=0, color="#1F2937", linewidth=1.8, zorder=3)

    opt_x = plan_d.get(VAR_X, 0)
    opt_y = plan_d.get(VAR_Y, 0)
    # Línea isoprofit usando las utilidades del plan (proporción 1:1 si no hay info)
    z_opt = opt_x + opt_y   # simplificado — proporción visual
    if z_opt > 0:
        y_obj = z_opt - x_range
        mask_obj = (y_obj >= 0) & (y_obj <= max_y)
        ax.plot(x_range[mask_obj], y_obj[mask_obj],
                "--", color="#6B7280", linewidth=1.8, alpha=0.8, label="F.O. (isoprofit)")

    ax.scatter([opt_x], [opt_y], color="#EF4444", s=180, zorder=7,
               label=f"Óptimo ({opt_x}, {opt_y})")
    ax.annotate(f"  Z*=({opt_x}, {opt_y})", (opt_x, opt_y),
                fontsize=10, fontweight="bold", color="#EF4444",
                xytext=(8, 8), textcoords="offset points")

    ax.set_xlim(0, max_x)
    ax.set_ylim(0, max_y)
    ax.set_xlabel(f"x₁  =  {label_x} (unidades)", fontsize=11)
    ax.set_ylabel(f"x₂  =  {label_y} (unidades)", fontsize=11)
    ax.set_title(
        f"Región Factible — Método Gráfico PL\n"
        f"Proyección (x₁={label_x}, x₂={label_y})"
        + (f"  |  {len(FIXED)} var(s) fijas en óptimo" if FIXED else ""),
        fontsize=11, fontweight="bold", pad=14,
    )
    ax.legend(loc="upper right", fontsize=8.5, framealpha=0.92, edgecolor="#D1D5DB")
    ax.grid(True, alpha=0.22, linestyle="--", color="#9CA3AF")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    plt.close("all")
    buf.seek(0)
    data = buf.read()
    buf.close()
    return data


def generar_grafica_region_factible_html(
    coef_matrix: dict,
    stocks: dict,
    plan: dict,
    var_labels: dict | None = None,
    insumo_labels: dict | None = None,
) -> str:
    """Retorna HTML con la imagen PNG de región factible en base64."""
    png_bytes = _build_region_png(coef_matrix, stocks, plan, var_labels, insumo_labels)
    img_b64 = base64.b64encode(png_bytes).decode()
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<style>body{margin:0;padding:4px;background:white;'
        'display:flex;justify-content:center;align-items:flex-start;}'
        'img{max-width:100%;height:auto;}</style></head><body>'
        f'<img src="data:image/png;base64,{img_b64}" alt="Region Factible">'
        '</body></html>'
    )
