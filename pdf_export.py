"""
Genera un PDF profesional del resultado de optimización.
Stack: fpdf2 (layout) + matplotlib (gráficas) + PNG base64 desde BD.

Layout:
  Pág 1 — Título / KPIs / Tabla plan / Gráfica de barras plan
  Pág 2 — Tabla utilización de insumos
  Pág N — Región factible a página completa (PNG desde BD, sin regenerar)

Estrategia de memoria (Render free tier 512 MB):
- Con stocks realistas el RSS post-optimización es ~100-150 MB.
- Gráfica de barras del plan: matplotlib simple, ~20 MB extra → seguro.
- Región factible: extraída del base64 guardado en BD → ~5 MB Pillow.
- Chequeo de RSS antes de cada imagen: si > 420 MB se omite con nota.
"""

import base64
import io
import json
import logging
import os
import re
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rss_mb() -> float:
    """
    Retorna el RSS actual del proceso en MB (Linux/Render).
    Lee /proc/self/status; devuelve 0.0 si no está disponible (Windows/macOS dev).
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # kB → MB
    except Exception:
        pass
    return 0.0


def _png_from_region_html(grafica_region_html: str) -> bytes | None:
    """
    Extrae el PNG base64 del HTML almacenado en BD (grafica_region_html).
    El HTML tiene la forma: <img src="data:image/png;base64,XXXX" ...>
    No regenera ninguna figura — solo decodifica el string ya existente.
    """
    if not grafica_region_html:
        return None
    m = re.search(r'src="data:image/png;base64,([^"]+)"', grafica_region_html)
    if not m:
        return None
    try:
        return base64.b64decode(m.group(1))
    except Exception:
        return None


def _nota_grafica_no_disponible(pdf: "FPDF", rss: float, motivo: str):
    """Muestra un recuadro azul informativo cuando la imagen no se puede incrustar."""
    pdf.set_fill_color(239, 246, 255)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(37, 99, 235)
    if motivo == "memoria":
        msg = (f"Grafica omitida: memoria del servidor alta ({rss:.0f} MB / 512 MB). "
               "Descargue el PDF unos minutos despues de la optimizacion.")
    elif motivo == "sin_datos":
        msg = ("Grafica no disponible para esta ejecucion. "
               "Ejecute una nueva optimizacion para generarla.")
    else:
        msg = "No se pudo incrustar la grafica. Disponible en el Dashboard > Optimizacion."
    pdf.multi_cell(0, 6, msg, fill=True, align="C")
    pdf.set_text_color(17, 24, 39)


def _generar_grafica_plan_png(plan: dict) -> bytes | None:
    """
    Gráfica de barras del plan de producción: 5 prendas × unidades.
    Generada en el endpoint PDF (RSS bajo con stocks realistas).
    """
    try:
        labels = [NOMBRES_PRENDAS[k] for k in PLAN_KEYS]
        values = [plan.get(k, 0) for k in PLAN_KEYS]
        max_v  = max(values) if any(values) else 1

        fig, ax = plt.subplots(figsize=(10, 4.5))
        fig.patch.set_facecolor("white")
        ax.set_facecolor("#F8FAFC")

        bars = ax.bar(labels, values, color=COLORES_PLAN, edgecolor="white",
                      linewidth=0.8, zorder=3)

        # Etiqueta encima de cada barra
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_v * 0.025,
                f"{val:,}",
                ha="center", va="bottom",
                fontsize=10, fontweight="bold", color="#111827",
            )

        ax.set_ylabel("Unidades a producir", fontsize=10, color="#374151")
        ax.set_title("Plan de Produccion Optimo", fontsize=13,
                     fontweight="bold", color="#111827", pad=14)
        ax.set_ylim(0, max_v * 1.20)
        ax.tick_params(axis="x", labelsize=9, colors="#374151")
        ax.tick_params(axis="y", labelsize=8, colors="#9CA3AF")
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, _: f"{int(x):,}")
        )
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_color("#E5E7EB")
        ax.spines["bottom"].set_color("#E5E7EB")
        ax.grid(True, axis="y", alpha=0.30, linestyle="--", color="#9CA3AF", zorder=0)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, bbox_inches="tight")
        plt.close(fig)
        plt.close("all")
        buf.seek(0)
        data = buf.read()
        buf.close()
        return data
    except Exception as exc:
        logger.warning("No se pudo generar grafica de plan: %s", exc)
        plt.close("all")
        return None


# ── Main function ─────────────────────────────────────────────────────────────

def generar_pdf_optimizacion(item: dict) -> bytes:
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

    # ── Plan de producción — tabla ────────────────────────────────────
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
    pdf.ln(8)

    # ── Plan de producción — gráfica de barras ────────────────────────
    rss = _rss_mb()
    logger.info("RSS antes de grafica de barras: %.0f MB", rss)
    if rss == 0.0 or rss < 420:
        plan_png = _generar_grafica_plan_png(plan)
        if plan_png:
            try:
                buf = io.BytesIO(plan_png)
                pdf.image(buf, x=10, w=190)
                buf.close()
                del buf, plan_png
            except Exception as exc:
                logger.warning("No se pudo incrustar grafica de plan: %s", exc)
    pdf.ln(6)

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

    # ── Región Factible — página completa ────────────────────────────
    grafica_region_html = item.get("grafica_region_html") or ""
    png_bytes = _png_from_region_html(grafica_region_html)

    rss2 = _rss_mb()
    imagen_viable = png_bytes is not None and (rss2 == 0.0 or rss2 < 420)
    logger.info("RSS antes de region factible: %.0f MB | viable=%s", rss2, imagen_viable)

    pdf.add_page()

    pdf.section_title("Region Factible - Metodo Grafico PL")
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(107, 114, 128)
    pdf.cell(
        0, 5,
        "Proyeccion en (x1=Pant. Diario, x2=Camisa Diaria)  |  x3, x4, x5 fijos en el optimo",
        ln=True,
    )
    pdf.set_text_color(17, 24, 39)
    pdf.ln(3)

    if imagen_viable:
        try:
            buf = io.BytesIO(png_bytes)
            # Imagen centrada a ancho completo; alto proporcional (~143 mm con aspect 10:7.5)
            pdf.image(buf, x=10, w=190)
            buf.close()
            del buf, png_bytes
        except Exception as exc:
            logger.warning("No se pudo incrustar region factible: %s", exc)
            _nota_grafica_no_disponible(pdf, rss2, motivo="error")
    else:
        _nota_grafica_no_disponible(pdf, rss2, motivo="memoria" if png_bytes else "sin_datos")

    # ── Nota pie de página ────────────────────────────────────────────
    pdf.ln(6)
    pdf.set_fill_color(239, 246, 255)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(37, 99, 235)
    pdf.multi_cell(
        0, 6,
        "Grafica interactiva de Plan de Produccion disponible en el Dashboard > Optimizacion.",
        fill=True, align="C",
    )
    pdf.set_text_color(17, 24, 39)

    return bytes(pdf.output())
