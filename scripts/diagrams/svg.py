"""A small SVG builder, so every diagram in docs/img speaks one visual language.

Hand-written SVG gives exact control, which auto-layout tools do not: a
microarchitecture drawing is mostly about *where* things sit relative to each
other, and a force-directed layout will happily put the MSHR file on the wrong
side of the pipeline. What auto-layout is good at -- state machines, dependency
graphs -- is done in Mermaid instead, inline in the markdown, so GitHub renders
it without an image file.

Conventions this file exists to enforce:

  * one palette, by ROLE: storage, control logic, datapath, interface, external.
  * virtual networks are always the same three colours, everywhere: VN0
    request, VN1 forward, VN2 response. A reader who learns them once on the
    tile diagram reads the router diagram for free.
  * an explicit white background, so the files render identically on GitHub's
    light and dark themes rather than becoming dark-on-dark.
  * text is `font-family: ui-monospace, monospace` for anything that names a
    signal or a module, and sans for prose labels -- the same split the code
    and the docs use.
"""

from xml.sax.saxutils import escape

# -- palette ----------------------------------------------------------------
INK      = "#1b2733"
MUTED    = "#5b6b7c"
RULE     = "#b9c6d3"
BG       = "#ffffff"

ROLE = {
    "storage":  ("#fdf2d8", "#d9a441"),   # arrays, buffers, queues
    "control":  ("#e4ecf7", "#5b86c4"),   # FSMs, tables, allocators
    "datapath": ("#e8f3ea", "#5a9e68"),   # muxes, crossbars, merge
    "iface":    ("#f0e9f6", "#8d6cae"),   # NIC, ports, boundary
    "external": ("#eef1f4", "#8fa0b0"),   # memory, core, off-block
    "plain":    ("#ffffff", "#8fa0b0"),
    "accent":   ("#fbe6e6", "#c2565c"),   # the thing the diagram is about
}

VNET = {
    0: ("#c2565c", "VN0 request"),
    1: ("#d9a441", "VN1 forward"),
    2: ("#3f7fb5", "VN2 response"),
}


class Svg:
    def __init__(self, w, h, title, subtitle=""):
        self.w, self.h = w, h
        self.title, self.subtitle = title, subtitle
        self.body = []
        self.defs = []
        self._markers = set()

    # -- primitives ---------------------------------------------------------
    def raw(self, s):
        self.body.append(s)

    def rect(self, x, y, w, h, fill=BG, stroke=RULE, rx=6, sw=1.4, dash=None,
             opacity=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{opacity}"' if opacity is not None else ""
        self.raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}{o}/>')

    def text(self, x, y, s, size=13, anchor="middle", fill=INK, mono=True,
             weight="normal", italic=False):
        fam = ("ui-monospace, SFMono-Regular, Menlo, monospace" if mono
               else "system-ui, -apple-system, Segoe UI, sans-serif")
        st = ' font-style="italic"' if italic else ""
        self.raw(f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" '
                 f'font-weight="{weight}" text-anchor="{anchor}" fill="{fill}"'
                 f'{st}>{escape(s)}</text>')

    def lines(self, x, y, rows, size=11, anchor="middle", fill=MUTED, gap=None,
              mono=True):
        gap = gap or size + 3
        for i, r in enumerate(rows):
            self.text(x, y + i * gap, r, size=size, anchor=anchor, fill=fill,
                      mono=mono)

    # -- composites ---------------------------------------------------------
    def block(self, x, y, w, h, label, sub=(), role="plain", size=13, rx=6,
              dash=None, label_dy=None):
        fill, stroke = ROLE[role]
        self.rect(x, y, w, h, fill=fill, stroke=stroke, rx=rx, dash=dash)
        sub = list(sub)
        if label_dy is None:
            label_dy = h / 2 - (len(sub) * 6) + 5 if sub else h / 2 + 5
        self.text(x + w / 2, y + label_dy, label, size=size, weight="600")
        if sub:
            self.lines(x + w / 2, y + label_dy + 15, sub, size=10.5)

    def group_box(self, x, y, w, h, label, role="plain", dash="5 4"):
        _, stroke = ROLE[role]
        self.rect(x, y, w, h, fill="none", stroke=stroke, rx=10, sw=1.2,
                  dash=dash)
        self.text(x + 12, y + 17, label, size=11.5, anchor="start",
                  fill=stroke, weight="600")

    def marker(self, color):
        key = color.replace("#", "")
        if key not in self._markers:
            self._markers.add(key)
            self.defs.append(
                f'<marker id="a{key}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M0,1 L9,5 L0,9 z" fill="{color}"/></marker>')
        return f"a{key}"

    def arrow(self, pts, color=MUTED, label=None, label_at=0.5, sw=1.8,
              dash=None, label_side=-1, label_size=10.5, both=False,
              label_mono=True):
        mid = self.marker(color)
        d = "M " + " L ".join(f"{x},{y}" for x, y in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        start = f' marker-start="url(#{mid})"' if both else ""
        self.raw(f'<path d="{d}" fill="none" stroke="{color}" '
                 f'stroke-width="{sw}"{da} marker-end="url(#{mid})"{start}/>')
        if label:
            i = max(0, min(len(pts) - 2, int((len(pts) - 1) * label_at)))
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
            if abs(x2 - x1) >= abs(y2 - y1):
                my += label_side * 8
            else:
                mx += label_side * 8
            anchor = "middle" if abs(x2 - x1) >= abs(y2 - y1) else (
                "end" if label_side < 0 else "start")
            self.text(mx, my, label, size=label_size, anchor=anchor,
                      fill=color, mono=label_mono)

    def legend(self, x, y, items, title=None, cols=1, dy=17, mono=False):
        if title:
            self.text(x, y, title, size=11, anchor="start", fill=MUTED,
                      weight="600", mono=False)
            y += 16
        per = (len(items) + cols - 1) // cols
        for i, (color, label) in enumerate(items):
            cx = x + (i // per) * 210
            cy = y + (i % per) * dy
            self.raw(f'<rect x="{cx}" y="{cy - 8}" width="20" height="3.5" '
                     f'rx="1.5" fill="{color}"/>')
            self.text(cx + 27, cy - 1, label, size=10.5, anchor="start",
                      fill=INK, mono=mono)

    def note(self, x, y, rows, w=None, anchor="start", size=10.5):
        for i, r in enumerate(rows):
            self.text(x, y + i * 14, r, size=size, anchor=anchor, fill=MUTED,
                      mono=False, italic=(i == 0 and r.startswith("*")))

    def bitfield(self, x0, y, w, h, fields, role_of=None, min_w=34,
                 show_bits=True):
        """A bit-field row whose widths sum to exactly `w`.

        Every field gets `min_w` first, and only the remainder is shared out in
        proportion to bit count -- otherwise a 1-bit flag beside a 128-bit
        payload is a sliver with no room for its name. Narrow fields get their
        label rotated rather than clipped.
        """
        total = sum(b for _, b in fields)
        spare = max(0.0, w - min_w * len(fields))
        geom = []
        x = x0
        for name, bits in fields:
            fw = min_w + spare * bits / total
            geom.append((name, bits, x, fw))
            x += fw
        for name, bits, fx, fw in geom:
            role = (role_of or (lambda n: "plain"))(name)
            fill, stroke = ROLE[role]
            self.rect(fx, y, fw, h, fill=fill, stroke=stroke, rx=4)
            cx, cy = fx + fw / 2, y + h / 2
            if fw >= 58:
                self.text(cx, cy + 1, name, size=11.5, weight="600")
                if show_bits:
                    self.text(cx, cy + 16, str(bits), size=9.5, fill=MUTED)
            else:
                # No room for a horizontal name, so rotate it -- and fold the
                # bit count into the same string, because a separate number
                # underneath would sit on top of the rotated text.
                lab = f"{name} {bits}" if show_bits else name
                # A rotated label is bounded by the box HEIGHT, not its width,
                # so shrink it to fit rather than letting it spill out the top.
                size = max(7.0, min(9.5, (h - 8) / (0.60 * max(1, len(lab)))))
                self.raw(f'<g transform="translate({cx},{cy}) rotate(-90)">')
                self.text(0, 4, lab, size=size, weight="600")
                self.raw("</g>")
        return geom

    # -- output -------------------------------------------------------------
    def render(self) -> str:
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" '
                f'height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
                f'role="img" aria-label="{escape(self.title)}">')
        defs = "<defs>" + "".join(self.defs) + "</defs>" if self.defs else ""
        bg = (f'<rect width="{self.w}" height="{self.h}" fill="{BG}"/>')
        t = []
        if self.title:
            t.append(f'<text x="24" y="30" font-family="system-ui, sans-serif" '
                     f'font-size="16" font-weight="650" fill="{INK}">'
                     f'{escape(self.title)}</text>')
        if self.subtitle:
            t.append(f'<text x="24" y="50" font-family="system-ui, sans-serif" '
                     f'font-size="11.5" fill="{MUTED}">'
                     f'{escape(self.subtitle)}</text>')
        return (head + defs + bg + "".join(t) + "".join(self.body) + "</svg>\n")

    def write(self, path):
        with open(path, "w") as f:
            f.write(self.render())
        return path
