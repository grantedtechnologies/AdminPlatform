const Granted = (() => {
  const charts = {};
  const colors = {
    green: "#2d8a29",
    blue: "#2563eb",
    amber: "#d97706",
    purple: "#7c3aed",
    red: "#dc2626",
    gray: "#94a3b8",
    ink: "#1a1a1a",
  };
  const statusColor = { upcoming: "#2563eb", active: "#2d8a29", ended: "#94a3b8" };

  function isoDay(value) {
    return String(value || "").slice(0, 10);
  }

  function filterDays(rows, start, end) {
    return (rows || []).filter((row) => {
      const day = isoDay(row.day);
      if (start && day < start) return false;
      if (end && day > end) return false;
      return true;
    });
  }

  function num(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function dates() {
    const start = document.getElementById("start-date");
    const end = document.getElementById("end-date");
    return {
      start: start ? start.value : "",
      end: end ? end.value : "",
    };
  }

  function onFilter(render) {
    ["start-date", "end-date", "pct-toggle"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("change", render);
    });
  }

  function setStatus(text, isError) {
    const el = document.getElementById("status");
    if (!el) return;
    if (!text) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.textContent = text;
    el.classList.toggle("error", Boolean(isError));
  }

  function setChart(id, config) {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    const empty = canvas.parentElement.querySelector(".empty");
    const labels = config.data.labels || [];
    const hasData = labels.length > 0;
    if (!hasData) {
      if (charts[id]) {
        charts[id].destroy();
        delete charts[id];
      }
      canvas.hidden = true;
      if (empty) empty.hidden = false;
      return;
    }
    canvas.hidden = false;
    if (empty) empty.hidden = true;
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(canvas, config);
  }

  function baseOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: "bottom" } },
    };
  }

  function lineChart(id, labels, datasets) {
    setChart(id, {
      type: "line",
      data: { labels, datasets },
      options: baseOptions(),
    });
  }

  function barChart(id, labels, dataset, horizontal) {
    setChart(id, {
      type: "bar",
      data: { labels, datasets: [dataset] },
      options: {
        ...baseOptions(),
        indexAxis: horizontal ? "y" : "x",
      },
    });
  }

  function pieChart(id, labels, data, backgroundColor) {
    setChart(id, {
      type: "pie",
      data: { labels, datasets: [{ data, backgroundColor }] },
      options: baseOptions(),
    });
  }

  function cards(targetId, items) {
    const el = document.getElementById(targetId);
    if (!el) return;
    el.innerHTML = items.map((item) => (
      `<article class="card"><div class="label">${item.label}</div>`
      + `<div class="value">${item.value}</div>`
      + `<div class="hint">${item.hint || ""}</div></article>`
    )).join("");
  }

  function mergeDays(series) {
    const days = new Set();
    series.forEach((rows) => rows.forEach((row) => days.add(isoDay(row.day))));
    return [...days].sort();
  }

  function mapByDay(rows, field) {
    const out = {};
    (rows || []).forEach((row) => { out[isoDay(row.day)] = num(row[field]); });
    return out;
  }

  async function load(url, build) {
    setStatus("Loading…");
    try {
      const response = await fetch(url);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Could not load this page.");
      setStatus("");
      build(data);
    } catch (err) {
      setStatus(err.message || "Could not load this page.", true);
    }
  }

  function featuresPage(url) {
    let payload = null;
    function render() {
      if (!payload) return;
      const { start, end } = dates();
      const pct = Boolean(document.getElementById("pct-toggle") && document.getElementById("pct-toggle").checked);
      const usage = filterDays(payload.usage_daily, start, end);
      const vocab = filterDays(payload.vocab_daily, start, end);
      const adoption = payload.adoption || {};
      const totalTeachers = num(adoption.total_teachers);
      cards("adoption-cards", [
        { label: "Used phonics", value: `${num(adoption.used_phonics)} / ${totalTeachers}`, hint: "Teachers with at least one phonics worksheet" },
        { label: "Used vocab", value: `${num(adoption.used_vocab)} / ${totalTeachers}`, hint: "Teachers who attached a vocab list" },
        { label: "Used custom DOK", value: `${num(adoption.used_custom_dok)} / ${totalTeachers}`, hint: "Teachers who set a custom DOK" },
      ]);
      const labels = usage.map((row) => isoDay(row.day));
      const seriesFor = (field) => usage.map((row) => {
        if (!pct) return num(row[field]);
        const total = num(row.total);
        return total ? Math.round((num(row[field]) / total) * 1000) / 10 : 0;
      });
      lineChart("usage-chart", labels, [
        { label: pct ? "Phonics %" : "Phonics", data: seriesFor("phonics"), borderColor: colors.green, backgroundColor: colors.green, tension: 0.2 },
        { label: pct ? "Vocab %" : "Vocab", data: seriesFor("vocab"), borderColor: colors.blue, backgroundColor: colors.blue, tension: 0.2 },
        { label: pct ? "Custom DOK %" : "Custom DOK", data: seriesFor("custom_dok"), borderColor: colors.amber, backgroundColor: colors.amber, tension: 0.2 },
      ]);
      barChart("dok-chart", (payload.dok || []).map((row) => row.DOKLevel), {
        label: "Worksheets",
        data: (payload.dok || []).map((row) => num(row.worksheets)),
        backgroundColor: colors.green,
      });
      lineChart("vocab-chart", vocab.map((row) => isoDay(row.day)), [{
        label: "Lists created",
        data: vocab.map((row) => num(row.lists_created)),
        borderColor: colors.blue,
        backgroundColor: colors.blue,
        tension: 0.2,
      }]);
    }
    onFilter(render);
    load(url, (data) => { payload = data; render(); });
  }

  function usagePage(url, options) {
    const teacherTypes = options && options.teacherTypes;
    let payload = null;
    function render() {
      if (!payload) return;
      const { start, end } = dates();
      const worksheets = filterDays(payload.usage_daily, start, end);
      const signups = filterDays(payload.signups, start, end);
      const active = filterDays(payload.active_teachers, start, end);
      const hours = filterDays(payload.active_hours, start, end);
      const returns = payload.return_days || [];
      const returned = returns.filter((row) => num(row.days_active) >= 2).length;
      const pct = returns.length ? Math.round((returned / returns.length) * 1000) / 10 : 0;
      cards("retention-cards", [{
        label: "Returned",
        value: `${pct}%`,
        hint: `${returned} of ${returns.length} teachers active on 2+ days`,
      }]);
      lineChart("worksheets-chart", worksheets.map((row) => isoDay(row.day)), [{
        label: "Worksheets",
        data: worksheets.map((row) => num(row.total)),
        borderColor: colors.green,
        backgroundColor: colors.green,
        tension: 0.2,
      }]);
      const activityDays = mergeDays([signups, active]);
      const signupMap = mapByDay(signups, "signups");
      const activeMap = mapByDay(active, "teachers");
      lineChart("activity-chart", activityDays, [
        { label: "Signups", data: activityDays.map((day) => signupMap[day] || 0), borderColor: colors.blue, backgroundColor: colors.blue, tension: 0.2 },
        { label: "Active teachers", data: activityDays.map((day) => activeMap[day] || 0), borderColor: colors.green, backgroundColor: colors.green, tension: 0.2 },
      ]);
      barChart("hours-chart", hours.map((row) => isoDay(row.day)), {
        label: "Approximate hours",
        data: hours.map((row) => num(row.hours)),
        backgroundColor: colors.purple,
      });
      const edited = num(payload.edit_rate && payload.edit_rate.edited);
      const notEdited = num(payload.edit_rate && payload.edit_rate.not_edited);
      if (edited + notEdited === 0) {
        pieChart("edit-chart", [], [], []);
      } else {
        pieChart("edit-chart", ["Edited", "Not edited"], [edited, notEdited], [colors.green, colors.gray]);
      }
      const funnel = payload.funnel || [];
      const funnelLabels = funnel.map((row, i) => {
        const prev = i === 0 ? num(row.teachers) : num(funnel[i - 1].teachers);
        const kept = prev ? Math.round((num(row.teachers) / prev) * 100) : 0;
        return `${row.eventType} (${kept}% kept)`;
      });
      barChart("funnel-chart", funnelLabels, {
        label: "Teachers",
        data: funnel.map((row) => num(row.teachers)),
        backgroundColor: colors.blue,
      }, true);
      const counts = {};
      returns.forEach((row) => {
        const key = String(num(row.days_active));
        counts[key] = (counts[key] || 0) + 1;
      });
      const dayLabels = Object.keys(counts).sort((a, b) => Number(a) - Number(b));
      barChart("retention-chart", dayLabels.map((d) => `${d} day${d === "1" ? "" : "s"}`), {
        label: "Teachers",
        data: dayLabels.map((d) => counts[d]),
        backgroundColor: colors.amber,
      });
      if (teacherTypes) {
        const types = payload.teacher_types || [];
        barChart("types-chart", types.map((row) => `G${row.gradeLevel} ${row.subject}`), {
          label: "Teachers",
          data: types.map((row) => num(row.teachers)),
          backgroundColor: colors.green,
        });
      }
    }
    onFilter(render);
    load(url, (data) => { payload = data; render(); });
  }

  function addDay(iso, days) {
    const parts = iso.split("-").map(Number);
    const dt = new Date(parts[0], parts[1] - 1, parts[2] + days);
    const y = dt.getFullYear();
    const m = String(dt.getMonth() + 1).padStart(2, "0");
    const d = String(dt.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }

  function calendarPage(url) {
    load(url, (data) => {
      const el = document.getElementById("calendar");
      const pilots = data.pilots || [];
      if (!el) return;
      if (!pilots.length) {
        el.innerHTML = '<p class="empty">No data yet</p>';
        return;
      }
      const calendar = new FullCalendar.Calendar(el, {
        initialView: "dayGridMonth",
        height: "auto",
        events: pilots.map((pilot) => ({
          title: pilot.name,
          start: isoDay(pilot.startTime),
          end: addDay(isoDay(pilot.endTime), 1),
          allDay: true,
          color: statusColor[pilot.status] || colors.gray,
        })),
      });
      calendar.render();
    });
  }

  function feedbackPage(url) {
    load(url, (data) => {
      const sources = data.rating_by_source || [];
      barChart("source-chart", sources.map((row) => row.source), {
        label: "Average rating",
        data: sources.map((row) => num(row.avg_rating)),
        backgroundColor: colors.green,
      });
      const dist = data.rating_distribution || [];
      barChart("rating-chart", dist.map((row) => String(row.rating)), {
        label: "Ratings",
        data: dist.map((row) => num(row.ratings)),
        backgroundColor: colors.amber,
      });
      const comments = data.recent_feedback || [];
      const box = document.getElementById("feedback-table");
      if (!box) return;
      if (!comments.length) {
        box.innerHTML = '<p class="empty">No data yet</p>';
        return;
      }
      box.innerHTML = '<div class="table-wrap"><table><thead><tr><th>When</th><th>Rating</th><th>Source</th><th>Comment</th></tr></thead><tbody>'
        + comments.map((row) => (
          `<tr><td>${row.createdAt || "—"}</td><td>${row.rating ?? "—"}</td><td>${row.source || "—"}</td><td>${escapeHtml(row.feedback || "")}</td></tr>`
        )).join("")
        + "</tbody></table></div>";
    });
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function surveyDetailPage(url) {
    load(url, (data) => {
      const wrap = document.getElementById("result-charts");
      const grouped = {};
      (data.results || []).forEach((row) => {
        if (!grouped[row.questionid]) {
          grouped[row.questionid] = { question: row.question, type: row.questionType, labels: [], values: [] };
        }
        grouped[row.questionid].labels.push(String(row.answer));
        grouped[row.questionid].values.push(num(row.responses));
      });
      const ids = Object.keys(grouped);
      if (!ids.length) {
        wrap.innerHTML = '<section class="panel"><p class="empty">No data yet</p></section>';
      } else {
        wrap.innerHTML = ids.map((id) => (
          `<section class="panel"><h2>${escapeHtml(grouped[id].question)}</h2>`
          + `<div class="chart-wrap"><canvas id="q-${id}"></canvas><p class="empty" hidden>No data yet</p></div></section>`
        )).join("");
        ids.forEach((id) => {
          barChart(`q-${id}`, grouped[id].labels, {
            label: "Responses",
            data: grouped[id].values,
            backgroundColor: colors.blue,
          });
        });
      }
      const texts = data.text_answers || [];
      const box = document.getElementById("text-answers");
      if (!texts.length) {
        box.innerHTML = '<p class="empty">No data yet</p>';
        return;
      }
      const byQuestion = {};
      texts.forEach((row) => {
        if (!byQuestion[row.question]) byQuestion[row.question] = [];
        byQuestion[row.question].push(row);
      });
      box.innerHTML = Object.keys(byQuestion).map((question) => (
        `<h3>${escapeHtml(question)}</h3><div class="table-wrap"><table><thead><tr><th>When</th><th>Answer</th></tr></thead><tbody>`
        + byQuestion[question].map((row) => `<tr><td>${row.answertime || "—"}</td><td>${escapeHtml(row.answer || "")}</td></tr>`).join("")
        + "</tbody></table></div>"
      )).join("");
    });
  }

  function auditPage(url) {
    load(url, (data) => {
      const rows = data.failures || [];
      barChart("failure-chart", rows.map((row) => row.eventType), {
        label: "Failures",
        data: rows.map((row) => num(row.failures)),
        backgroundColor: colors.red,
      });
    });
  }

  return { featuresPage, usagePage, calendarPage, feedbackPage, surveyDetailPage, auditPage };
})();
