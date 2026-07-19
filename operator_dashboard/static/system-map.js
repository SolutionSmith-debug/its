/* System-map edge underlay. Dependency-free progressive enhancement: without
 * this script the lanes, nodes, walls, and rail links all still work — only
 * the drawn connections are missing.
 *
 * Reads the edge list from #sm-edge-data, measures node chip positions inside
 * #sysmap, and draws smooth cubic paths into the absolutely-positioned
 * #sm-svg underlay. Send-gate crossings get a gold "port" glyph where the
 * path meets the wall. Redraws on resize and after htmx swaps (rail loading
 * can reflow the grid).
 */
(function () {
  "use strict";

  var map = document.getElementById("sysmap");
  var svg = document.getElementById("sm-svg");
  var dataEl = document.getElementById("sm-edge-data");
  if (!map || !svg || !dataEl) return;

  var edges;
  try {
    edges = JSON.parse(dataEl.textContent || "[]");
  } catch (e) {
    return;
  }

  var NS = "http://www.w3.org/2000/svg";

  function anchor(el, side) {
    // Position of an element edge-midpoint relative to the map container.
    var r = el.getBoundingClientRect();
    var m = map.getBoundingClientRect();
    var x = side === "left" ? r.left - m.left : side === "right" ? r.right - m.left : r.left - m.left + r.width / 2;
    var y = side === "top" ? r.top - m.top : side === "bottom" ? r.bottom - m.top : r.top - m.top + r.height / 2;
    return { x: x, y: y };
  }

  function wallX(id) {
    var w = document.getElementById(id);
    if (!w) return null;
    var r = w.getBoundingClientRect();
    var m = map.getBoundingClientRect();
    return r.left - m.left + r.width / 2;
  }

  function focusId() {
    return map.getAttribute("data-focus") || "";
  }

  function draw() {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.setAttribute("width", map.scrollWidth);
    svg.setAttribute("height", map.scrollHeight);
    svg.setAttribute("viewBox", "0 0 " + map.scrollWidth + " " + map.scrollHeight);

    var gateX = wallX("wall-send");
    var ingressX = wallX("wall-ingress");
    var hot = focusId();

    edges.forEach(function (e) {
      var s = document.getElementById("node-" + e.src);
      var d = document.getElementById("node-" + e.dst);
      if (!s || !d) return;

      var sr = s.getBoundingClientRect();
      var dr = d.getBoundingClientRect();
      var a, b, path;
      var horizontal = Math.abs(dr.left - sr.left) >= Math.abs(dr.top - sr.top);

      if (horizontal && dr.left >= sr.right) {
        a = anchor(s, "right");
        b = anchor(d, "left");
      } else if (horizontal && sr.left >= dr.right) {
        a = anchor(s, "left");
        b = anchor(d, "right");
      } else if (dr.top >= sr.bottom) {
        a = anchor(s, "bottom");
        b = anchor(d, "top");
      } else {
        a = anchor(s, "top");
        b = anchor(d, "bottom");
      }

      var el = document.createElementNS(NS, "path");
      if (horizontal && Math.abs(b.x - a.x) > 1) {
        var mx = (a.x + b.x) / 2;
        path = "M" + a.x + "," + a.y + " C" + mx + "," + a.y + " " + mx + "," + b.y + " " + b.x + "," + b.y;
      } else {
        var my = (a.y + b.y) / 2;
        path = "M" + a.x + "," + a.y + " C" + a.x + "," + my + " " + b.x + "," + my + " " + b.x + "," + b.y;
      }
      el.setAttribute("d", path);
      el.setAttribute("class", "sm-edge e-" + e.kind + (hot && (e.src === hot || e.dst === hot) ? " edge-hot" : ""));
      var title = document.createElementNS(NS, "title");
      title.textContent = e.src + " → " + e.dst + ": " + e.label;
      el.appendChild(title);
      svg.appendChild(el);

      // Port glyph where a labeled edge crosses a wall (linear y-interp is
      // close enough on these gentle curves).
      var walls = [
        { x: gateX, cls: "sm-port-gate" },
        { x: ingressX, cls: "sm-port-ingress" },
      ];
      walls.forEach(function (w) {
        if (w.x === null || !e.port) return;
        var lo = Math.min(a.x, b.x);
        var hi = Math.max(a.x, b.x);
        if (w.x <= lo || w.x >= hi) return;
        var t = (w.x - a.x) / (b.x - a.x);
        var y = a.y + (b.y - a.y) * t;
        var c = document.createElementNS(NS, "circle");
        c.setAttribute("cx", w.x);
        c.setAttribute("cy", y);
        c.setAttribute("r", 5);
        c.setAttribute("class", "sm-port " + w.cls);
        var pt = document.createElementNS(NS, "title");
        pt.textContent = e.port;
        c.appendChild(pt);
        svg.appendChild(c);
      });
    });
  }

  function setFocus(id, scroll) {
    map.setAttribute("data-focus", id);
    map.querySelectorAll(".sm-node.is-focus").forEach(function (n) {
      n.classList.remove("is-focus");
    });
    var el = document.getElementById("node-" + id);
    if (el) {
      el.classList.add("is-focus");
      if (scroll) el.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
    }
    try {
      var url = new URL(window.location);
      url.searchParams.set("focus", id);
      history.replaceState(null, "", url);
    } catch (err) {
      /* history is a nicety, never a failure */
    }
    draw();
  }

  map.addEventListener("click", function (ev) {
    var chip = ev.target.closest(".sm-node");
    if (chip && chip.dataset.node) setFocus(chip.dataset.node, false);
  });

  var pending = null;
  function scheduleDraw() {
    if (pending) return;
    pending = requestAnimationFrame(function () {
      pending = null;
      draw();
    });
  }

  window.addEventListener("resize", scheduleDraw);
  document.body.addEventListener("htmx:afterSwap", scheduleDraw);

  // Fonts loading can shift chip widths after first paint.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(scheduleDraw);

  draw();
  var initial = focusId();
  if (initial) setFocus(initial, true);
})();
