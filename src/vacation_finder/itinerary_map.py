"""The itinerary map on each trip page.

Pins come from ``plans/maps/<plan>.yaml``, one per place in the day-by-day
plan, each with its days, coordinates and the base it belongs to (see
``plans/maps/README.md``). The map is Leaflet over OpenStreetMap tiles,
loaded from a CDN, so GitHub Pages needs no build step. Below it, the same
places are listed by day as plain buttons, so the page still says where
everything is if the map can't load.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

KINDS = {"sight", "stay", "airport", "option"}
FIELDS = {"name", "days", "at", "area", "kind"}

LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist"
HEAD = (
    f'<link rel="stylesheet" href="{LEAFLET}/leaflet.css" crossorigin="">'
    f'<script src="{LEAFLET}/leaflet.js" crossorigin="" defer></script>'
)

CSS = """
.tripmap{margin:30px 0 8px}
.tripmap .map{height:400px;border:1px solid var(--line);border-radius:10px;background:var(--accent-soft)}
@media (max-width:600px){.tripmap .map{height:320px}}
.map-areas{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 4px}
.map-areas button,.map-days button{font:inherit;cursor:pointer;color:var(--accent);background:none;border:0;padding:0}
.map-areas button{border:1px solid var(--line);border-radius:99px;padding:4px 12px;font-size:13px;background:var(--card)}
.map-areas button:hover,.map-areas button[aria-pressed=true]{border-color:var(--accent)}
.map-areas button:focus-visible,.map-days button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.map-days{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin-top:12px;font-size:14px}
.map-days dt{font-weight:600;color:var(--muted);white-space:nowrap}
.map-days dd{margin:0}
.map-days button{text-decoration:underline;text-underline-offset:2px;text-align:left}
.pin-wrap{background:none;border:0}
.pin{position:absolute;transform:translate(-50%,-50%);display:flex;align-items:center;justify-content:center;
min-width:24px;height:24px;padding:0 6px;border-radius:12px;font:700 12px/1 system-ui,sans-serif;
white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,.35);border:2px solid #fff;color:#fff;background:#2b4c9b}
.pin-stay{background:#b45309;border-radius:5px;font-size:14px}
.pin-airport{background:#1b2233;font-size:13px}
.pin-option{background:#fff;color:#2b4c9b;border:2px dashed #2b4c9b}
"""

SCRIPT = """
<script>
window.addEventListener("DOMContentLoaded", function () {
  var el = document.getElementById("trip-map");
  var raw = document.getElementById("trip-map-data");
  if (!el || !raw) return;
  if (!window.L) { el.hidden = true; return; }
  var places = JSON.parse(raw.textContent);
  var map = L.map(el, { scrollWheelZoom: false });
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
  }).addTo(map);
  var markers = places.map(function (p) {
    var icon = L.divIcon({
      className: "pin-wrap", iconSize: [0, 0],
      html: '<span class="pin pin-' + p.kind + '">' + p.label + "</span>"
    });
    var popup = document.createElement("div");
    var b = document.createElement("b"); b.textContent = p.name; popup.appendChild(b);
    if (p.when) { popup.appendChild(document.createElement("br")); popup.appendChild(document.createTextNode(p.when)); }
    return L.marker(p.at, { icon: icon, title: p.name }).addTo(map).bindPopup(popup);
  });
  function fit(area) {
    var pts = places.filter(function (p) { return !area || p.area === area; })
      .map(function (p) { return p.at; });
    if (pts.length === 1) map.setView(pts[0], 13);
    else if (pts.length) map.fitBounds(pts, { padding: [28, 28], maxZoom: 14 });
    document.querySelectorAll(".map-areas button").forEach(function (btn) {
      btn.setAttribute("aria-pressed", String((btn.dataset.area || "") === (area || "")));
    });
  }
  document.querySelectorAll(".map-areas button").forEach(function (btn) {
    btn.addEventListener("click", function () { fit(btn.dataset.area || ""); });
  });
  document.querySelectorAll(".map-days button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var m = markers[+btn.dataset.i];
      map.setView(m.getLatLng(), Math.max(map.getZoom(), 14));
      m.openPopup();
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  });
  fit("");
});
</script>
"""


class MapError(ValueError):
    """A map file that can't be drawn as written."""


@dataclass(frozen=True)
class Place:
    name: str
    at: tuple[float, float]
    days: tuple[int, ...] = ()
    area: str = ""
    kind: str = "sight"

    @property
    def label(self) -> str:
        """What the pin shows: the day number(s), or a symbol."""
        if self.kind == "stay":
            return "⌂"
        if self.kind == "airport":
            return "✈"
        if self.kind == "option" and not self.days:
            return "+"
        return _span(self.days)

    @property
    def when(self) -> str:
        if not self.days:
            return "Option" if self.kind == "option" else ""
        word = "Day" if len(self.days) == 1 else "Days"
        return f"{word} {_span(self.days, sep=', ')}" + (" (option)" if self.kind == "option" else "")


def _span(days: tuple[int, ...], sep: str = ",") -> str:
    """'3', '1–5' for a run of days, '1,4' otherwise."""
    if len(days) > 2 and list(days) == list(range(days[0], days[-1] + 1)):
        return f"{days[0]}–{days[-1]}"
    return sep.join(str(d) for d in days)


def map_path(plan: Path) -> Path:
    return plan.parent / "maps" / f"{plan.stem}.yaml"


def load_places(path: Path, strict: bool = False) -> list[Place]:
    """The places in a map file. A bad entry is skipped with a warning (the
    dashboard must still build); with `strict`, it raises MapError instead."""
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text()) or {}
    places = []
    for i, item in enumerate(raw.get("places") or []):
        try:
            places.append(_place(item))
        except MapError as exc:
            if strict:
                raise MapError(f"{path.name} place {i + 1}: {exc}") from exc
            log.warning("%s place %d skipped: %s", path.name, i + 1, exc)
    return places


def _place(item: object) -> Place:
    if not isinstance(item, dict):
        raise MapError("each place must be a mapping")
    # An unquoted comma inside {...} silently splits a name into two keys;
    # catch that rather than drawing a pin called half its name.
    extra = set(item) - FIELDS
    if extra:
        raise MapError(f"unknown key(s) {sorted(map(str, extra))} — quote names that contain commas")
    name = item.get("name")
    at = item.get("at")
    if not isinstance(name, str) or not name.strip():
        raise MapError("'name' is required")
    if not (isinstance(at, list) and len(at) == 2 and all(isinstance(v, (int, float)) for v in at)):
        raise MapError(f"{name}: 'at' must be [latitude, longitude]")
    lat, lon = float(at[0]), float(at[1])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise MapError(f"{name}: coordinates out of range")
    kind = str(item.get("kind", "sight"))
    if kind not in KINDS:
        raise MapError(f"{name}: kind must be one of {sorted(KINDS)}")
    days = item.get("days") or []
    if not all(isinstance(d, int) and d > 0 for d in days):
        raise MapError(f"{name}: 'days' must be day numbers")
    return Place(name.strip(), (lat, lon), tuple(sorted(days)), str(item.get("area", "")), kind)


def _esc(text: object) -> str:
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def map_section(places: list[Place]) -> str:
    """The map, its zoom-to buttons, and the places listed by day."""
    if not places:
        return ""
    data = json.dumps(
        [
            {"name": p.name, "at": list(p.at), "kind": p.kind, "area": p.area,
             "label": p.label, "when": p.when}
            for p in places
        ],
        ensure_ascii=False,
    ).replace("</", "<\\/")

    areas = list(dict.fromkeys(p.area for p in places if p.area))
    buttons = "".join(
        [f'<button type="button" data-area="" aria-pressed="true">Whole trip</button>']
        + [f'<button type="button" data-area="{_esc(a)}">{_esc(a)}</button>' for a in areas]
    ) if len(areas) > 1 else ""

    def link(i: int, place: Place) -> str:
        return f'<button type="button" data-i="{i}">{_esc(place.name)}</button>'

    indexed = list(enumerate(places))
    rows = []
    stays = [link(i, p) for i, p in indexed if p.kind == "stay"]
    if stays:
        rows.append(("Staying", stays))
    for day in sorted({d for p in places if p.kind not in {"stay", "airport"} for d in p.days}):
        rows.append((f"Day {day}", [
            link(i, p) for i, p in indexed
            if day in p.days and p.kind not in {"stay", "airport"}
        ]))
    options = [link(i, p) for i, p in indexed if p.kind == "option" and not p.days]
    if options:
        rows.append(("Options", options))
    listing = "".join(f"<dt>{_esc(label)}</dt><dd>{' · '.join(items)}</dd>" for label, items in rows)

    return (
        '<section class="tripmap"><h2>Map</h2>'
        '<div class="map" id="trip-map" role="region" aria-label="Map of the itinerary"></div>'
        + (f'<div class="map-areas">{buttons}</div>' if buttons else "")
        + '<p class="note">Numbered pins are the itinerary days; ⌂ is where you stay, ✈ an '
        "airport, + an option. Pins are placed by name and approximate.</p>"
        + f'<dl class="map-days">{listing}</dl>'
        + f'<script type="application/json" id="trip-map-data">{data}</script>'
        + "</section>"
    )


def with_map(plan_html: str, places: list[Place]) -> str:
    """The plan with its map placed just above 'Day by day' (or above the
    first section, for a plan without one)."""
    section = map_section(places)
    if not section:
        return plan_html
    for marker in ("<h2>Day by day</h2>", "<h2>"):
        at = plan_html.find(marker)
        if at >= 0:
            return plan_html[:at] + section + plan_html[at:]
    return plan_html + section
