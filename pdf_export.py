"""
Genera un PDF profesional del resultado de optimización.
Stack: fpdf2 (layout) + matplotlib (gráficas estáticas en PNG).
"""

import base64
import io
import json
import logging
import re
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # backend sin GUI
import matplotlib.pyplot as plt
from fpdf import FPDF

logger = logging.getLogger(__name__)

NOMBRES_PRENDAS = {
    "pantalon_diario": "Pantalon Diario",
    "camisa_diario":   "Camisa Diaria",
    "pantalon_ef":     "Pantalon E.F.",
    "sueter_ef":       "Sueter E.F.",
}
NOMBRES_INSUMOS = {
    "drill":    "Tela Drill",
    "popelina": "Tela Popelina",
    "licra":    "Tela Licra",
    "hilo":     "Hilo",
    "botones":  "Botones",
}
UTILIDADES = {
    "pantalon_diario": 20_000,
    "camisa_diario":   15_000,
    "pantalon_ef":     12_000,
    "sueter_ef":       11_000,
}
COLORES_PLAN = ["#2563EB", "#7C3AED", "#059669", "#D97706"]
PLAN_KEYS = list(NOMBRES_PRENDAS.keys())


# ── Imágenes matplotlib ───────────────────────────────────────────────────────

def _img_plan(plan: dict, utilidad: float) -> bytes:
    labels = [NOMBRES_PRENDAS[k] for k in PLAN_KEYS]
    values = [plan.get(k, 0) for k in PLAN_KEYS]

    fig, ax = plt.subplots(figsize=(9, 4))
    bars = ax.bar(labels, values, color=COLORES_PLAN, edgecolor="white", linewidth=1.5, width=0.55)

    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(values) * 0.02,
                str(val), ha="center", va="bottom", fontweight="bold", fontsize=11,
            )

    ax.set_title(f"Plan de Produccion - Utilidad: ${utilidad:,.0f} COP",
                 fontsize=12, fontweight="bold", pad=14)
    ax.set_ylabel("Unidades", fontsize=10)
    ax.set_ylim(0, max(values + [1]) * 1.18)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_facecolor("#F9FAFB")
    fig.patch.set_facecolor("white")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _img_region_factible_from_html(html_str: str | None) -> bytes | None:
    """Extrae el PNG base64 embebido en el HTML de la región factible."""
    if not html_str:
        return None
    m = re.search(r'data:image/png;base64,([^"]+)', html_str)
    if not m:
        return None
    try:
        return base64.b64decode(m.group(1))
    except Exception:
        return None


def _img_recursos(recursos: dict) -> bytes | None:
    if not recursos:
        return None

    labels = [NOMBRES_INSUMOS.get(k, k.title()) for k in recursos]
    pcts = [
        (v.get("utilizacion_pct", 0) if isinstance(v, dict) else 0)
        for v in recursos.values()
    ]
    colors = ["#EF4444" if p >= 95 else "#F59E0B" if p >= 75 else "#10B981" for p in pcts]

    fig, ax = plt.subplots(figsize=(9, max(3, len(labels) * 0.7 + 1.5)))
    bars = ax.barh(labels, pcts, color=colors, edgecolor="white", linewidth=1, height=0.55)
    ax.axvline(x=100, color="red", linestyle="--", alpha=0.5, linewidth=1.2)

    for bar, pct in zip(bars, pcts):
        ax.text(
            min(pct + 1.5, 102), bar.get_y() + bar.get_height() / 2,
            f"{pct:.1f}%", va="center", fontsize=10, fontweight="bold",
        )

    ax.set_title("Utilizacion de Insumos", fontsize=12, fontweight="bold", pad=14)
    ax.set_xlabel("Porcentaje utilizado (%)", fontsize=10)
    ax.set_xlim(0, 120)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_facecolor("#F9FAFB")
    fig.patch.set_facecolor("white")
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ── FPDF class ────────────────────────────────────────────────────────────────

class _PDF(FPDF):
    def header(self):
        self.set_fill_color(11, 61, 145)
        self.rect(0, 0, 210, 26, "F")
        self.set_font("Helvetica", "B", 17)
        self.set_text_color(255, 255, 255)
        self.set_xy(10, 7)
        self.cell(100, 9, "CostuSoft Control", ln=False)
        self.set_font("Helvetica", "", 9)
        self.set_xy(10, 17)
        self.cell(0, 5, "Taller de Confecciones La Senora Piedad - Cartagena")
        self.set_text_color(0, 0, 0)
        self.ln(20)

    def footer(self):
        self.set_y(-13)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(160, 160, 160)
        self.cell(
            0, 8,
            f"Pagina {self.page_no()}  |  Reporte generado automaticamente  |  Motor ILP (PuLP + CBC)",
            align="C",
        )


    # helpers
    def section_title(self, title: str):
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(11, 61, 145)
        self.cell(0, 8, title, ln=True)
        self.set_draw_color(11, 61, 145)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_text_color(17, 24, 39)
        self.ln(3)

    def th(self, widths: list[int], headers: list[str]):
        self.set_fill_color(37, 99, 235)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 9)
        for w, h in zip(widths, headers):
            self.cell(w, 8, h, fill=True, border=0, align="C")
        self.ln()
        self.set_text_color(17, 24, 39)

    def td(self, widths: list[int], values: list, aligns: list[str], fill: bool):
        self.set_fill_color(249, 250, 251) if fill else self.set_fill_color(255, 255, 255)
        self.set_font("Helvetica", "", 9)
        for w, v, a in zip(widths, values, aligns):
            self.cell(w, 8, str(v), fill=True, border=0, align=a)
        self.ln()


# ── Main function ─────────────────────────────────────────────────────────────

def generar_pdf_optimizacion(item: dict) -> bytes:
    grafica_region_html = item.get("grafica_region_html")
    stocks_usados = item.get("stocks_usados") or {}
    if isinstance(stocks_usados, str):
        try:
            stocks_usados = json.loads(stocks_usados)
        except Exception:
            stocks_usados = {}

    plan = {
        "pantalon_diario": item.get("x1_pantalon_diario") or 0,
        "camisa_diario":   item.get("x2_camisa_diario")   or 0,
        "pantalon_ef":     item.get("x3_pantalon_ef")     or 0,
        "sueter_ef":       item.get("x4_sueter_ef")       or 0,
    }
    utilidad     = float(item.get("utilidad_total") or 0)
    estado       = str(item.get("estado_solucion") or "—")
    talla        = str(item.get("talla") or "M")
    mensaje      = str(item.get("mensaje") or "")
    record_id    = item.get("id", "—")
    total_prendas = sum(plan.values())

    fecha = item.get("fecha_ejecucion") or item.get("created_at") or datetime.now()
    fecha_str = (
        fecha.strftime("%d/%m/%Y %H:%M")
        if hasattr(fecha, "strftime")
        else str(fecha)[:16].replace("T", " ")
    )

    pdf = _PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # ── Título y metadatos ────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, "Reporte de Optimizacion de Produccion", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(107, 114, 128)
    pdf.cell(0, 6, f"Fecha: {fecha_str}   |   ID #{record_id}   |   Talla: {talla}", ln=True)
    pdf.set_text_color(17, 24, 39)
    pdf.ln(3)

    # Estado pill
    col = {"OPTIMAL": (22, 163, 74), "INFEASIBLE": (146, 64, 14)}.get(estado, (107, 114, 128))
    pdf.set_fill_color(*col)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(30, 7, f" {estado} ", fill=True, border=0, align="C")
    pdf.set_text_color(17, 24, 39)
    pdf.ln(10)

    # KPIs
    pdf.set_fill_color(239, 246, 255)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(95, 18, f"Utilidad Total:  ${utilidad:,.0f} COP", fill=True, border=0, align="C")
    pdf.cell(95, 18, f"Total Prendas:  {total_prendas} unidades", fill=True, border=0, align="C")
    pdf.ln(22)

    if mensaje:
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(107, 114, 128)
        pdf.multi_cell(0, 5, mensaje)
        pdf.set_text_color(17, 24, 39)
        pdf.ln(4)

    # ── Plan de producción ────────────────────────────────────────────
    pdf.section_title("Plan de Produccion")
    pdf.th([85, 40, 65], ["Prenda", "Unidades", "Utilidad Parcial"])
    for i, key in enumerate(PLAN_KEYS):
        qty = plan.get(key, 0)
        pdf.td(
            [85, 40, 65],
            [NOMBRES_PRENDAS[key], qty, f"${qty * UTILIDADES.get(key, 0):,.0f}"],
            ["L", "C", "R"],
            bool(i % 2),
        )
    # Totals row
    pdf.set_fill_color(37, 99, 235)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(85, 8, "TOTAL", fill=True, border=0)
    pdf.cell(40, 8, str(total_prendas), fill=True, border=0, align="C")
    pdf.cell(65, 8, f"${utilidad:,.0f}", fill=True, border=0, align="R")
    pdf.set_text_color(17, 24, 39)
    pdf.ln(10)

    # ── Recursos ──────────────────────────────────────────────────────
    if stocks_usados:
        pdf.section_title("Utilizacion de Insumos")
        pdf.th([60, 30, 35, 35, 30], ["Insumo", "Usado", "Disponible", "Holgura", "Uso %"])
        for i, (key, rec) in enumerate(stocks_usados.items()):
            if not isinstance(rec, dict):
                continue
            pct = rec.get("utilizacion_pct", 0)
            pdf.set_fill_color(249, 250, 251) if i % 2 else pdf.set_fill_color(255, 255, 255)
            pdf.set_font("Helvetica", "", 9)
            for w, v, a in zip(
                [60, 30, 35, 35],
                [NOMBRES_INSUMOS.get(key, key.title()), rec.get("usado", 0),
                 rec.get("disponible", 0), rec.get("holgura", 0)],
                ["L", "C", "C", "C"],
            ):
                pdf.cell(w, 8, str(v), fill=True, border=0, align=a)
            c = (220, 38, 38) if pct >= 95 else (217, 119, 6) if pct >= 75 else (5, 150, 105)
            pdf.set_text_color(*c)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(30, 8, f"{pct:.1f}%", fill=True, border=0, align="C")
            pdf.set_text_color(17, 24, 39)
            pdf.ln()
        pdf.ln(8)

    # ── Gráficas ──────────────────────────────────────────────────────
    try:
        pdf.section_title("Grafica — Plan de Produccion")
        img_bytes = _img_plan(plan, utilidad)
        pdf.image(io.BytesIO(img_bytes), x=10, w=190)
        pdf.ln(6)
    except Exception as e:
        logger.warning("No se pudo generar grafica plan: %s", e)

    if stocks_usados:
        try:
            img_rec = _img_recursos(stocks_usados)
            if img_rec:
                pdf.section_title("Grafica — Utilizacion de Insumos")
                pdf.image(io.BytesIO(img_rec), x=10, w=190)
        except Exception as e:
            logger.warning("No se pudo generar grafica recursos: %s", e)

    # Region factible (metodo grafico PL)
    try:
        img_region = _img_region_factible_from_html(grafica_region_html)
        if img_region:
            pdf.add_page()
            pdf.section_title("Grafica — Region Factible (Metodo Grafico PL)")
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(107, 114, 128)
            pdf.multi_cell(
                0, 5,
                "Proyeccion bidimensional (x1=Pantalon Diario, x2=Camisa Diaria) "
                "con x3 y x4 fijados en sus valores optimos. "
                "Area azul = region factible. Punto rojo = solucion optima.",
            )
            pdf.set_text_color(17, 24, 39)
            pdf.ln(3)
            pdf.image(io.BytesIO(img_region), x=10, w=190)
    except Exception as e:
        logger.warning("No se pudo incluir grafica region factible en PDF: %s", e)

    return bytes(pdf.output())
