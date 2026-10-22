# Copyright (c) 2026 Viewstreet Technologies
# SPDX-License-Identifier: MIT

"""Export the plotted canvas as a standalone SVG without screenshot dependencies."""
import xml.etree.ElementTree as ET
from tkinter import font as tkfont


def save_svg(canvas, path, title):
    width, height = canvas.winfo_width(), canvas.winfo_height()
    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg",
                     width=str(width), height=str(height), viewBox=f"0 0 {width} {height}")
    ET.SubElement(svg, "title").text = title
    ET.SubElement(svg, "rect", width="100%", height="100%", fill=canvas.cget("background"))
    for item in canvas.find_all():
        if "tooltip" in canvas.gettags(item) or canvas.itemcget(item, "state") == "hidden":
            continue
        kind, coords = canvas.type(item), canvas.coords(item)
        if kind in ("line", "oval", "rectangle"):
            fill = canvas.itemcget(item, "fill") or "none"
            stroke = fill if kind == "line" else canvas.itemcget(item, "outline") or "none"
            attrs = dict(fill="none" if kind == "line" else fill, stroke=stroke,
                         **{"stroke-width": canvas.itemcget(item, "width")})
            if kind == "line":
                dash = canvas.itemcget(item, "dash")
                if dash:
                    attrs["stroke-dasharray"] = dash.replace(" ", ",")
                ET.SubElement(svg, "polyline", points=" ".join(
                    f"{coords[i]},{coords[i+1]}" for i in range(0, len(coords), 2)), **attrs)
            else:
                x1, y1, x2, y2 = coords
                if kind == "oval":
                    ET.SubElement(svg, "ellipse", cx=str((x1+x2)/2), cy=str((y1+y2)/2),
                                  rx=str((x2-x1)/2), ry=str((y2-y1)/2), **attrs)
                else:
                    ET.SubElement(svg, "rect", x=str(x1), y=str(y1),
                                  width=str(x2-x1), height=str(y2-y1), **attrs)
        elif kind == "text":
            font = tkfont.Font(root=canvas, font=canvas.itemcget(item, "font"))
            actual = font.actual()
            size = actual["size"]
            pixels = abs(size) if size < 0 else canvas.winfo_fpixels(f"{size}p")
            attrs = {"font-family": actual["family"], "font-size": str(pixels),
                     "font-weight": actual["weight"], "fill": canvas.itemcget(item, "fill")}
            angle = float(canvas.itemcget(item, "angle"))
            x, y = coords
            lines = canvas.itemcget(item, "text").splitlines() or [""]
            if angle:
                attrs.update({"text-anchor": "middle", "dominant-baseline": "central",
                              "transform": f"rotate({-angle} {x} {y})"})
            else:
                # Tk's actual pixel bounds preserve placement and DPI scaling.
                x1, y1, x2, _ = canvas.bbox(item)
                anchor = canvas.itemcget(item, "anchor")
                horizontal = "end" if anchor in ("e", "ne", "se") else "start" if anchor in ("w", "nw", "sw") else "middle"
                x = x2-2 if horizontal == "end" else x1+2 if horizontal == "start" else (x1+x2)/2
                y = y1+font.metrics("ascent")
                attrs["text-anchor"] = horizontal
            text = ET.SubElement(svg, "text", x=str(x), y=str(y), **attrs)
            for index, line in enumerate(lines):
                ET.SubElement(text, "tspan", x=str(x), dy="0" if index == 0 else str(font.metrics("linespace"))).text = line
    ET.ElementTree(svg).write(path, encoding="utf-8", xml_declaration=True)
