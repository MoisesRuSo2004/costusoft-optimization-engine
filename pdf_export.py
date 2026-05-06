"""
Genera un PDF profesional del resultado de optimización.
Stack: fpdf2 (layout) + matplotlib (gráficas estáticas en PNG).
"""

import base64
import io
import json
import logging
import os
import re
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # backend sin GUI
import matplotlib.pyplot as plt
from fpdf import FPDF

logger = logging.getLogger(__name__)

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
_LOGO_PATH  = os.path.join(_ASSETS_DIR, "logo.png")   # white-on-transparent PNG

NOMBRES_PRENDAS = {
    "pantalon_diario": "Pantalon Diario",
    "camisa_diario":   "Camisa Diaria",
    "sueter_diario":   "Sueter Diario",
    "pantalon_ef":     "Pantalon E.F.",
    "sueter_ef":       "Sueter E.F.",
}
NOMBRES_INSUMOS = {
    "drill":      "Tela Drill",
    "popelina":   "Tela Popelina",
    "licra":      "Tela Licra",
    "lana":       "Tela Lana",
    "hilo":       "Hilo",
    "botones":    "Botones",
    "entretela":  "Entretela",
    "cierres":    "Cierres",
    "elastico":   "Elastico",
    "cinta":      "Cinta",
    "etiquetas":  "Etiquetas",
}
UTILIDADES = {
    "pantalon_diario": 20_000,
    "camisa_diario":   15_000,
    "sueter_diario":   13_000,
    "pantalon_ef":     12_000,
    "sueter_ef":       11_000,
}
COLORES_PLAN = ["#2563EB", "#7C3AED", "#EC4899", "#059669", "#D97706"]
PLAN_KEYS = list(NOMBRES_PRENDAS.keys())


# ── Imágenes matplotlib ───────────────────────────────────────────────────────

def _img_plan(plan: dict, utilidad: float) -> bytes:
    labels = [NOMBRES_PRENDAS[k] for k in PLAN_KEYS]
    values = [plan.get(k, 0) for k in PLAN_KEYS]
    colors = COLORES_PLAN[: len(labels)]          # siempre mismo largo que las barras

    fig, ax = plt.subplots(figsize=(10, 4.5))
    bars = ax.bar(labels, values, color=colors, edgecolor="white", linewidth=1.5, width=0.55)

    max_val = max(values + [1])
    for bar, val in zip(bars, values):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_val * 0.02,
                str(val), ha="center", va="bottom", fontweight="bold", fontsize=11,
            )

    ax.set_title(f"Plan de Produccion - Utilidad Maxima: ${utilidad:,.0f} COP",
                 fontsize=12, fontweight="bold", pad=14)
    ax.set_ylabel("Unidades a producir", fontsize=10)
    ax.set_ylim(0, max_val * 1.22)
    ax.tick_params(axis="x", labelsize=9)
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

    def _rv(r, field, default=0):
        return r.get(field, default) if isinstance(r, dict) else getattr(r, field, default)
    labels = [
        f"{NOMBRES_INSUMOS.get(k, k.title())} ({_rv(v, 'unidad_medida', 'un') or 'un'})"
        for k, v in recursos.items()
    ]
    pcts = [_rv(v, "utilizacion_pct", 0) for v in recursos.values()]
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

_HEADER_H   = 32   # mm — total header height
_LOGO_W     = 30   # mm — logo width (auto height ~23mm with 1.28 aspect)
_BLUE_DARK  = (11,  61, 145)   # #0B3D91
_BLUE_MID   = (30,  90, 190)   # accent stripe
_GOLD       = (217, 143,  20)  # decorative stripe
_GREY_TEXT  = (120, 130, 148)


class _PDF(FPDF):

    # ── Header ────────────────────────────────────────────────────────────────
    def header(self):
        # --- background rectangle (full width) ---
        self.set_fill_color(*_BLUE_DARK)
        self.rect(0, 0, 210, _HEADER_H, "F")

        # --- gold accent stripe at bottom of header ---
        self.set_fill_color(*_GOLD)
        self.rect(0, _HEADER_H - 2, 210, 2, "F")

        # --- logo (left zone, white-on-transparent PNG, vertically centered) ---
        # Logo aspect ratio is ~1.28 wide:tall; at w=30mm → h≈23mm → center in 32mm header
        logo_y = (_HEADER_H - 23) / 2
        has_logo = os.path.isfile(_LOGO_PATH)
        if has_logo:
            try:
                self.image(_LOGO_PATH, x=6, y=logo_y, w=_LOGO_W)
            except Exception:
                has_logo = False
        if not has_logo:
            # fallback: styled text badge
            self.set_font("Helvetica", "B", 14)
            self.set_text_color(255, 255, 255)
            self.set_xy(6, 10)
            self.cell(_LOGO_W, 12, "CostuSoft", align="C")

        # --- vertical separator ---
        sep_x = 6 + _LOGO_W + 4
        self.set_draw_color(255, 255, 255)
        self.set_line_width(0.4)
        self.line(sep_x, 5, sep_x, _HEADER_H - 4)

        # --- title block (center-right zone) ---
        txt_x = sep_x + 4
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(255, 255, 255)
        self.set_xy(txt_x, 7)
        self.cell(0, 8, "CostuSoft Control", ln=True)

        self.set_font("Helvetica", "", 8)
        self.set_text_color(200, 215, 240)
        self.set_xy(txt_x, 16)
        self.cell(0, 5, "Taller de Confecciones La Senora Piedad  -  Cartagena, Colombia")

        # --- page number (bottom-right corner) ---
        self.set_font("Helvetica", "", 7)
        self.set_text_color(180, 200, 240)
        self.set_xy(150, 25)
        self.cell(55, 5, f"Pagina {self.page_no()}", align="R")

        self.set_text_color(0, 0, 0)
        self.ln(_HEADER_H + 2)

    # ── Footer ────────────────────────────────────────────────────────────────
    def footer(self):
        # thin gold line separator
        self.set_draw_color(*_GOLD)
        self.set_line_width(0.6)
        self.line(10, self.h - 14, 200, self.h - 14)

        self.set_y(self.h - 12)

        # left: company
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*_GREY_TEXT)
        self.cell(70, 6, "CostuSoft  -  Sistema de Gestion de Produccion", align="L")

        # center: page
        self.set_font("Helvetica", "B", 7)
        self.set_text_color(*_BLUE_DARK)
        self.cell(70, 6, f"Pagina  {self.page_no()}", align="C")

        # right: motor info
        self.set_font("Helvetica", "I", 6.5)
        self.set_text_color(*_GREY_TEXT)
        self.cell(60, 6, "Motor ILP: PuLP + CBC (Optimizacion Exacta)", align="R")


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
        "sueter_diario":   item.get("x5_sueter_diario")   or 0,
        "pantalon_ef":     item.get("x3_pantalon_ef")     or 0,
        "sueter_ef":       item.get("x4_sueter_ef")       or 0,
    }
    utilidad     = float(item.get("utilidad_total") or 0)
    estado       = str(item.get("estado_solucion") or "-")
    mensaje      = str(item.get("mensaje") or "")
    record_id    = item.get("id", "-")
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
    pdf.cell(0, 6, f"Fecha: {fecha_str}   |   ID #{record_id}   |   Todas las tallas", ln=True)
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
        pdf.th([55, 18, 28, 30, 30, 29], ["Insumo", "Ud.", "Usado", "Disponible", "Holgura", "Uso %"])
        for i, (key, rec) in enumerate(stocks_usados.items()):
            if not isinstance(rec, dict):
                continue
            pct   = rec.get("utilizacion_pct", 0)
            udm   = rec.get("unidad_medida", "un") or "un"
            pdf.set_fill_color(249, 250, 251) if i % 2 else pdf.set_fill_color(255, 255, 255)
            pdf.set_font("Helvetica", "", 9)
            for w, v, a in zip(
                [55, 18, 28, 30, 30],
                [NOMBRES_INSUMOS.get(key, key.title()), udm,
                 rec.get("usado", 0), rec.get("disponible", 0), rec.get("holgura", 0)],
                ["L", "C", "C", "C", "C"],
            ):
                pdf.cell(w, 8, str(v), fill=True, border=0, align=a)
            c = (220, 38, 38) if pct >= 95 else (217, 119, 6) if pct >= 75 else (5, 150, 105)
            pdf.set_text_color(*c)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(29, 8, f"{pct:.1f}%", fill=True, border=0, align="C")
            pdf.set_text_color(17, 24, 39)
            pdf.ln()
        pdf.ln(8)

    # ── Gráficas ──────────────────────────────────────────────────────
    # Gráfica 1: Plan de producción
    try:
        img_bytes = _img_plan(plan, utilidad)
        pdf.add_page()
        pdf.section_title("Grafica - Plan de Produccion")
        buf = io.BytesIO(img_bytes)
        buf.seek(0)
        pdf.image(buf, x=10, w=190)
        pdf.ln(4)
    except Exception as e:
        logger.error("Error generando grafica plan en PDF: %s", e, exc_info=True)

    # Gráfica 2: Utilización de insumos
    if stocks_usados:
        try:
            img_rec = _img_recursos(stocks_usados)
            if img_rec:
                pdf.add_page()
                pdf.section_title("Grafica - Utilizacion de Insumos")
                buf_rec = io.BytesIO(img_rec)
                buf_rec.seek(0)
                pdf.image(buf_rec, x=10, w=190)
                pdf.ln(4)
        except Exception as e:
            logger.error("Error generando grafica recursos en PDF: %s", e, exc_info=True)

    # Gráfica 3: Región factible (método gráfico PL)
    try:
        img_region = _img_region_factible_from_html(grafica_region_html)
        if img_region:
            pdf.add_page()
            pdf.section_title("Grafica - Region Factible (Metodo Grafico PL)")
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(107, 114, 128)
            pdf.multi_cell(
                0, 5,
                "Proyeccion bidimensional (x1=Pantalon Diario, x2=Camisa Diaria) "
                "con Sueter Diario, Pantalon EF y Sueter EF fijados en sus valores optimos. "
                "Area azul = region factible. Punto rojo = solucion optima.",
            )
            pdf.set_text_color(17, 24, 39)
            pdf.ln(3)
            buf_reg = io.BytesIO(img_region)
            buf_reg.seek(0)
            pdf.image(buf_reg, x=10, w=190)
        else:
            logger.warning("grafica_region_html vacio o sin datos base64 - omitiendo del PDF")
    except Exception as e:
        logger.error("Error incluyendo region factible en PDF: %s", e, exc_info=True)

    return bytes(pdf.output())
