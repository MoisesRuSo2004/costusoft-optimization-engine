"""
Genera las gráficas del plan de optimización:
  1. Plotly interactivo (barras) — para iframe en frontend.
  2. Región factible matplotlib — método gráfico PL para iframe en frontend.
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

NOMBRES_PRENDAS = {
    "pantalon_diario": "Pantalón Diario",
    "camisa_diario":   "Camisa Diaria",
    "pantalon_ef":     "Pantalón E.F.",
    "sueter_ef":       "Suéter E.F.",
}

NOMBRES_INSUMOS = {
    "drill":    "Tela Drill",
    "popelina": "Tela Popelina",
    "licra":    "Tela Licra",
    "hilo":     "Hilo",
    "botones":  "Botones",
}

COLORES_PLAN = ["#2563EB", "#7C3AED", "#059669", "#D97706"]

UTILIDADES_ILP = {
    "pantalon_diario": 20_000,
    "camisa_diario":   15_000,
    "pantalon_ef":     12_000,
    "sueter_ef":       11_000,
}


def generar_grafica_optimizacion(resultado: dict) -> str:
    plan = resultado.get("plan") or {}
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()

    recursos = resultado.get("recursos") or {}

    talla = resultado.get("talla", "M")
    utilidad = resultado.get("utilidad_total", 0)

    # ── Datos del plan de producción ─────────────────────────────────────
    plan_labels = [NOMBRES_PRENDAS[k] for k in NOMBRES_PRENDAS]
    plan_values = [plan.get(k, 0) for k in NOMBRES_PRENDAS]

    # ── Datos de utilización de recursos ─────────────────────────────────
    rec_keys   = list(recursos.keys())
    rec_labels = [NOMBRES_INSUMOS.get(k, k.title()) for k in rec_keys]
    rec_used   = [recursos[k].get("usado", 0) if isinstance(recursos[k], dict) else recursos[k].usado for k in rec_keys]
    rec_avail  = [recursos[k].get("disponible", 0) if isinstance(recursos[k], dict) else recursos[k].disponible for k in rec_keys]
    rec_pct    = [recursos[k].get("utilizacion_pct", 0) if isinstance(recursos[k], dict) else recursos[k].utilizacion_pct for k in rec_keys]

    def color_pct(p: float) -> str:
        if p >= 95:
            return "#EF4444"  # rojo — casi agotado
        if p >= 75:
            return "#F59E0B"  # naranja — atención
        return "#10B981"      # verde — OK

    # ── Subplots: izquierda plan, derecha recursos ────────────────────────
    fig = make_subplots(
        rows=1,
        cols=2,
        subplot_titles=[
            "Plan de producción (unidades)",
            "Utilización de insumos (%)",
        ],
        horizontal_spacing=0.12,
    )

    # Gráfica 1 — Plan de producción
    fig.add_trace(
        go.Bar(
            x=plan_labels,
            y=plan_values,
            marker_color=COLORES_PLAN,
            text=plan_values,
            textposition="outside",
            hovertemplate="%{x}: <b>%{y} unidades</b><extra></extra>",
            name="Prendas",
        ),
        row=1, col=1,
    )

    # Gráfica 2 — Utilización por insumo (barras apiladas: usado + holgura)
    if rec_keys:
        rec_holgura = [max(0, a - u) for a, u in zip(rec_avail, rec_used)]
        rec_colors  = [color_pct(p) for p in rec_pct]

        fig.add_trace(
            go.Bar(
                x=rec_labels,
                y=rec_used,
                marker_color=rec_colors,
                name="Usado",
                text=[f"{p:.1f}%" for p in rec_pct],
                textposition="outside",
                hovertemplate="%{x}<br>Usado: <b>%{y}</b><br>%{text}<extra></extra>",
            ),
            row=1, col=2,
        )
        fig.add_trace(
            go.Bar(
                x=rec_labels,
                y=rec_holgura,
                marker_color="#E5E7EB",
                name="Holgura",
                hovertemplate="%{x}<br>Disponible: %{y}<extra></extra>",
            ),
            row=1, col=2,
        )
        fig.update_layout(barmode="stack")

    fig.update_layout(
        title={
            "text": (
                f"Optimización de Producción — Talla {talla} | "
                f"Utilidad: <b>${utilidad:,.0f} COP</b>"
            ),
            "x": 0.5,
            "font": {"size": 14, "family": "Inter, system-ui, sans-serif", "color": "#111827"},
        },
        showlegend=False,
        height=400,
        margin={"l": 40, "r": 40, "t": 80, "b": 50},
        plot_bgcolor="white",
        paper_bgcolor="white",
        font={"family": "Inter, system-ui, sans-serif", "color": "#374151"},
    )
    fig.update_xaxes(showgrid=False, tickangle=-20)
    fig.update_yaxes(showgrid=True, gridcolor="#F3F4F6", zeroline=False)

    div_content = pyo.plot(
        fig,
        output_type="div",
        include_plotlyjs=False,
        config={"displayModeBar": False, "responsive": True},
    )
    # Retorna HTML completo para iframe (dangerouslySetInnerHTML no ejecuta scripts)
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
# Gráfica 2: Región Factible — Método Gráfico PL (matplotlib + base64 PNG)
# ─────────────────────────────────────────────────────────────────────────────

_PALETTE = ["#DC2626", "#16A34A", "#D97706", "#7C3AED", "#0891B2", "#DB2777"]
_INS_LABELS = {
    "drill":    "Tela Drill",
    "popelina": "Tela Popelina",
    "licra":    "Tela Licra",
    "hilo":     "Hilo",
    "botones":  "Botones",
}


def _build_region_png(coef_matrix: dict, stocks: dict, plan: dict, talla: str) -> bytes:
    """Genera la imagen PNG de la región factible proyectada en (x1=pantalon_diario, x2=camisa_diario)."""
    VAR_X, VAR_Y = "pantalon_diario", "camisa_diario"
    FIXED = ["pantalon_ef", "sueter_ef"]

    plan_d = plan if isinstance(plan, dict) else (plan.model_dump() if hasattr(plan, "model_dump") else {})

    # Descontar consumo de variables fijas del stock disponible
    remaining: dict[str, float] = {}
    for ins, stock in stocks.items():
        consumed = sum(coef_matrix.get(v, {}).get(ins, 0) * plan_d.get(v, 0) for v in FIXED)
        r = float(stock) - consumed
        if r > 1e-6:
            remaining[ins] = r

    # Restricciones activas sobre (x1, x2)
    constraints: list[tuple[str, float, float, float]] = []
    for ins, rem in remaining.items():
        cx = coef_matrix.get(VAR_X, {}).get(ins, 0)
        cy = coef_matrix.get(VAR_Y, {}).get(ins, 0)
        if cx > 0 or cy > 0:
            constraints.append((ins, cx, cy, rem))

    if not constraints:
        constraints = [("limite", 1.0, 0.0, 50.0), ("limite", 0.0, 1.0, 50.0)]

    # Límites del gráfico
    x_bounds = [rem / cx for _, cx, _, rem in constraints if cx > 0]
    y_bounds = [rem / cy for _, _, cy, rem in constraints if cy > 0]
    max_x = min(max(x_bounds + [10.0]) * 1.2, 600.0)
    max_y = min(max(y_bounds + [10.0]) * 1.2, 600.0)

    # Grilla de factibilidad
    xs = np.linspace(0, max_x, 350)
    ys = np.linspace(0, max_y, 350)
    X, Y = np.meshgrid(xs, ys)
    feasible = np.ones_like(X, dtype=bool)
    for _, cx, cy, rem in constraints:
        feasible &= (cx * X + cy * Y <= rem + 1e-9)

    # ── Figura ────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8.5, 7))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#F8FAFC")

    # Región factible sombreada
    ax.contourf(X, Y, feasible.astype(float), levels=[0.5, 1.5], colors=["#93C5FD"], alpha=0.40)

    # Líneas de restricción
    x_range = np.linspace(0, max_x, 800)
    for i, (ins, cx, cy, rem) in enumerate(constraints):
        color = _PALETTE[i % len(_PALETTE)]
        label = _INS_LABELS.get(ins, ins.title())
        if cy > 0:
            y_line = (rem - cx * x_range) / cy
            mask = (y_line >= -0.5) & (y_line <= max_y * 1.05) & (x_range >= 0)
            if mask.sum() > 5:
                ax.plot(x_range[mask], y_line[mask], "-", color=color, linewidth=2.3, label=label)
                mid = len(x_range[mask]) // 2
                ax.annotate(
                    f"  {label}",
                    (x_range[mask][mid], y_line[mask][mid]),
                    fontsize=8.5, color=color, fontweight="bold",
                )
        elif cx > 0:
            xv = rem / cx
            ax.axvline(x=xv, color=color, linewidth=2.3, linestyle="--", label=label)
            ax.text(xv + max_x * 0.01, max_y * 0.5, f"  {label}", fontsize=8.5, color=color, fontweight="bold")

    # Ejes de no-negatividad
    ax.axhline(y=0, color="#1F2937", linewidth=1.8, zorder=3)
    ax.axvline(x=0, color="#1F2937", linewidth=1.8, zorder=3)

    # Función objetivo (línea isoprofit por el óptimo)
    opt_x = plan_d.get(VAR_X, 0)
    opt_y = plan_d.get(VAR_Y, 0)
    u_x = UTILIDADES_ILP.get(VAR_X, 20000)
    u_y = UTILIDADES_ILP.get(VAR_Y, 15000)
    z_opt = u_x * opt_x + u_y * opt_y
    if z_opt > 0 and u_y > 0:
        y_obj = (z_opt - u_x * x_range) / u_y
        mask_obj = (y_obj >= 0) & (y_obj <= max_y)
        ax.plot(
            x_range[mask_obj], y_obj[mask_obj],
            "--", color="#6B7280", linewidth=1.8, alpha=0.8,
            label=f"F.O. = {z_opt:,.0f}",
        )

    # Punto óptimo
    ax.scatter([opt_x], [opt_y], color="#EF4444", s=180, zorder=7, label=f"Óptimo ({opt_x}, {opt_y})")
    ax.annotate(
        f"  Z*=({opt_x}, {opt_y})",
        (opt_x, opt_y),
        fontsize=10, fontweight="bold", color="#EF4444",
        xytext=(8, 8), textcoords="offset points",
    )

    ax.set_xlim(0, max_x)
    ax.set_ylim(0, max_y)
    ax.set_xlabel("x₁  =  Pantalón Diario (unidades)", fontsize=11)
    ax.set_ylabel("x₂  =  Camisa Diaria (unidades)", fontsize=11)
    ax.set_title(
        f"Región Factible — Método Gráfico PL\n"
        f"Talla {talla}  |  Proyección (x₁, x₂)  con  x₃, x₄ fijos en óptimo",
        fontsize=12, fontweight="bold", pad=14,
    )
    ax.legend(loc="upper right", fontsize=8.5, framealpha=0.92, edgecolor="#D1D5DB")
    ax.grid(True, alpha=0.22, linestyle="--", color="#9CA3AF")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def generar_grafica_region_factible_html(
    coef_matrix: dict,
    stocks: dict,
    plan: dict,
    talla: str,
) -> str:
    """Retorna un HTML completo con la imagen de región factible como base64 PNG (apto para iframe srcDoc)."""
    png_bytes = _build_region_png(coef_matrix, stocks, plan, talla)
    img_b64 = base64.b64encode(png_bytes).decode()
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<style>body{margin:0;padding:4px;background:white;'
        'display:flex;justify-content:center;align-items:flex-start;}'
        'img{max-width:100%;height:auto;}</style></head><body>'
        f'<img src="data:image/png;base64,{img_b64}" alt="Region Factible">'
        '</body></html>'
    )
