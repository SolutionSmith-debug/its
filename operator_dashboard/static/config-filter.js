/* Config-editor live filter. Dependency-free progressive enhancement: without
 * it the full page renders as always — this only hides non-matching rows.
 *
 * Filters every .config-table row (and whole sections, including the
 * form-only ones like breaker/restart) by their visible text. Prefills from
 * ?f=<text> so other pages (the system map's "edit gate" links) can deep-link
 * straight to one setting.
 */
(function () {
  "use strict";

  var input = document.getElementById("config-filter-input");
  var count = document.getElementById("config-filter-count");
  if (!input) return;

  var groups = Array.prototype.slice.call(document.querySelectorAll(".config-group"));
  var railLinks = Array.prototype.slice.call(document.querySelectorAll(".config-rail a"));

  function apply(q) {
    q = q.trim().toLowerCase();
    var shownRows = 0;
    groups.forEach(function (group) {
      var rows = Array.prototype.slice.call(group.querySelectorAll("tbody tr"));
      var groupShown = false;
      if (!q) {
        rows.forEach(function (r) {
          r.classList.remove("cfg-row-hidden");
        });
        group.classList.remove("cfg-group-hidden");
        shownRows += rows.length;
        return;
      }
      if (rows.length) {
        rows.forEach(function (r) {
          var hit = r.textContent.toLowerCase().indexOf(q) !== -1;
          r.classList.toggle("cfg-row-hidden", !hit);
          if (hit) {
            groupShown = true;
            shownRows += 1;
          }
        });
      } else {
        // Form-only sections (breaker, error log, restart…) match on their text.
        groupShown = group.textContent.toLowerCase().indexOf(q) !== -1;
        if (groupShown) shownRows += 1;
      }
      group.classList.toggle("cfg-group-hidden", !groupShown);
    });
    if (count) count.textContent = q ? shownRows + " match" + (shownRows === 1 ? "" : "es") : "";
    // Rail entries for hidden sections dim out.
    railLinks.forEach(function (a) {
      var target = document.querySelector(a.getAttribute("href"));
      a.style.opacity = target && target.classList.contains("cfg-group-hidden") ? "0.35" : "";
    });
  }

  var pending = null;
  input.addEventListener("input", function () {
    if (pending) clearTimeout(pending);
    pending = setTimeout(function () {
      apply(input.value);
    }, 120);
  });

  // Deep-link prefill: /config?f=<setting-key>.
  try {
    var preset = new URL(window.location).searchParams.get("f");
    if (preset) {
      input.value = preset;
      apply(preset);
      input.scrollIntoView({ block: "start" });
    }
  } catch (err) {
    /* prefill is a nicety, never a failure */
  }
})();
