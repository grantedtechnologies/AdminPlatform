const statusLine = document.getElementById("status");
const statsNote = document.getElementById("stats-note");
const statsBody = document.getElementById("stats-body");
let countdown = null;
let busy = false;

function formatMs(value) {
  if (value == null) return "—";
  if (value >= 1000) return (value / 1000).toFixed(2) + " s";
  return (value < 10 ? value.toFixed(1) : Math.round(value)) + " ms";
}

function formatCount(value) {
  return Number(value || 0).toLocaleString();
}

function statBlock(stats) {
  const checks = stats.checks_total
    ? Math.round(stats.checks_passed / stats.checks_total * 1000) / 10 + "%"
    : "—";
  const rows = [
    ["Checks", checks],
    ["Passed", formatCount(stats.checks_passed) + " / " + formatCount(stats.checks_total)],
    ["Iterations", formatCount(stats.iterations)],
    ["Requests", formatCount(stats.requests)],
    ["Failed requests", formatCount(stats.failed_requests)],
    ["Average", formatMs(stats.avg_ms)],
    ["Median", formatMs(stats.median_ms)],
    ["p95", formatMs(stats.p95_ms)],
    ["Min", formatMs(stats.min_ms)],
    ["Max", formatMs(stats.max_ms)],
  ];
  return rows.map(function (row) {
    return "<div class=\"stat\"><span>" + row[0] + "</span><strong>" + row[1] + "</strong></div>";
  }).join("");
}

function showRuns(runs, note) {
  document.querySelector(".stats h2").textContent = "Results";
  statsNote.textContent = note || "";
  statsNote.hidden = !note;
  if (!runs.length) {
    statsBody.hidden = true;
    statsBody.innerHTML = "";
    return;
  }
  statsBody.hidden = false;
  statsBody.innerHTML = runs.map(function (run) {
    return "<section class=\"run-card\"><h3>" + run.title + "</h3>"
      + "<p class=\"detail\">" + (run.detail || "") + "</p>"
      + (run.stats ? statBlock(run.stats) : "")
      + "</section>";
  }).join("");
}

function stopCountdown() {
  if (countdown) clearInterval(countdown);
  countdown = null;
}

function clock(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const ss = String(secs).padStart(2, "0");
  if (hours) return hours + ":" + String(minutes).padStart(2, "0") + ":" + ss;
  return minutes + ":" + ss;
}

function startCountdown(cell) {
  stopCountdown();
  const ends = Date.now() + Number(cell.dataset.seconds) * 1000;
  const result = cell.querySelector(".result");
  const endAt = new Date(ends).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  function tick() {
    const left = ends - Date.now();
    if (left <= 0) {
      result.textContent = "Finishing up";
      statusLine.textContent = cell.dataset.label + " · finishing up.";
      return;
    }
    const label = clock(left) + " left · ends " + endAt;
    result.textContent = label;
    statusLine.textContent = cell.dataset.label + " · " + label;
  }
  tick();
  countdown = setInterval(tick, 1000);
}

function runCell(cell) {
  const result = cell.querySelector(".result");
  cell.classList.remove("passed", "failed");
  cell.classList.add("running");
  result.hidden = false;
  startCountdown(cell);
  return fetch("/api/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: cell.dataset.id }),
  }).then(function (response) {
    return response.json().then(function (data) {
      stopCountdown();
      cell.classList.remove("running");
      if (!response.ok) {
        result.hidden = true;
        statusLine.textContent = data.message || "That test did not start.";
        return null;
      }
      cell.classList.add(data.ok ? "passed" : "failed");
      result.textContent = data.detail;
      statusLine.textContent = cell.dataset.label + " · " + data.detail;
      return data;
    });
  }).catch(function () {
    stopCountdown();
    cell.classList.remove("running");
    result.hidden = true;
    statusLine.textContent = "Could not reach the test app.";
    return null;
  });
}

document.querySelectorAll(".cell").forEach(function (cell) {
  cell.addEventListener("click", function () {
    if (busy || cell.classList.contains("running")) {
      statusLine.textContent = "A test is already running.";
      return;
    }
    busy = true;
    showRuns([], "Collecting timings…");
    runCell(cell).then(function (data) {
      if (!data) {
        showRuns([], statusLine.textContent);
        return;
      }
      showRuns([{
        title: cell.dataset.label,
        detail: data.detail,
        stats: data.stats,
      }]);
    }).finally(function () {
      busy = false;
    });
  });
});

document.querySelectorAll(".full-test").forEach(function (button) {
  button.addEventListener("click", function () {
    if (busy) {
      statusLine.textContent = "A test is already running.";
      return;
    }
    const kind = button.dataset.kind;
    const cells = Array.from(document.querySelectorAll(".cell")).filter(function (cell) {
      return cell.dataset.id.endsWith("-" + kind);
    });
    cells.forEach(function (cell) {
      cell.classList.remove("passed", "failed", "running");
      const result = cell.querySelector(".result");
      result.hidden = true;
      result.textContent = "";
    });
    busy = true;
    const done = [];
    let index = 0;
    function next() {
      if (index >= cells.length) {
        statusLine.textContent = button.parentElement.querySelector("span").textContent + " full test finished.";
        busy = false;
        return;
      }
      const cell = cells[index];
      index += 1;
      showRuns(done, "Running " + cell.dataset.label + "…");
      runCell(cell).then(function (data) {
        if (!data) {
          showRuns(done, statusLine.textContent);
          busy = false;
          return;
        }
        done.push({
          title: cell.dataset.label,
          detail: data.detail,
          stats: data.stats,
        });
        showRuns(done, data.ok ? "" : "Stopped. " + cell.dataset.label + " failed.");
        if (!data.ok) {
          statusLine.textContent = cell.dataset.label + " failed. Stopped before the next level.";
          busy = false;
          return;
        }
        next();
      });
    }
    next();
  });
});

const customCard = document.getElementById("custom-card");
if (customCard) {
  const kinds = window.TEST_KINDS || [];
  const kindSelect = document.getElementById("custom-kind");
  const about = document.getElementById("custom-about");
  function paintAbout() {
    const match = kinds.find(function (kind) { return kind.id === kindSelect.value; });
    about.textContent = match ? match.about : "";
  }
  kindSelect.addEventListener("change", paintAbout);
  paintAbout();
  customCard.addEventListener("submit", function (event) {
    event.preventDefault();
    if (busy) {
      statusLine.textContent = "A test is already running.";
      return;
    }
    const minutes = Number(document.getElementById("custom-minutes").value);
    const users = Number(document.getElementById("custom-users").value);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 120) {
      statusLine.textContent = "Duration must be 1 to 120 minutes.";
      return;
    }
    if (!Number.isInteger(users) || users < 1 || users > 500) {
      statusLine.textContent = "Users must be 1 to 500.";
      return;
    }
    const name = kindSelect.options[kindSelect.selectedIndex].text;
    customCard.dataset.seconds = String(minutes * 60 + 15);
    customCard.dataset.label = name + ", " + users + " users, " + minutes + " min";
    customCard.classList.remove("passed", "failed");
    busy = true;
    showRuns([], "Collecting timings…");
    const result = customCard.querySelector(".result");
    customCard.classList.add("running");
    result.hidden = false;
    startCountdown(customCard);
    fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind: kindSelect.value,
        minutes: minutes,
        users: users,
      }),
    }).then(function (response) {
      return response.json().then(function (data) {
        stopCountdown();
        customCard.classList.remove("running");
        if (!response.ok) {
          result.hidden = true;
          statusLine.textContent = data.message || "That test did not start.";
          showRuns([], statusLine.textContent);
          return;
        }
        customCard.classList.add(data.ok ? "passed" : "failed");
        result.textContent = data.detail;
        statusLine.textContent = customCard.dataset.label + " · " + data.detail;
        showRuns([{
          title: customCard.dataset.label,
          detail: data.detail,
          stats: data.stats,
        }]);
      });
    }).catch(function () {
      stopCountdown();
      customCard.classList.remove("running");
      result.hidden = true;
      statusLine.textContent = "Could not reach the test app.";
      showRuns([], statusLine.textContent);
    }).finally(function () {
      busy = false;
    });
  });
}

document.getElementById("reset").addEventListener("click", function () {
  document.querySelectorAll(".cell").forEach(function (cell) {
    if (cell.classList.contains("running")) return;
    cell.classList.remove("passed", "failed");
    const result = cell.querySelector(".result");
    result.hidden = true;
    result.textContent = "";
  });
  if (customCard) {
    customCard.classList.remove("running", "passed", "failed");
    const customResult = customCard.querySelector(".result");
    customResult.hidden = true;
    customResult.textContent = "";
    statusLine.textContent = "Pick a test, then choose how long it runs and how many users.";
    showRuns([], "Pick a test, then choose how long it runs and how many users.");
    return;
  }
  statusLine.textContent = "Click a square to start that test.";
  showRuns([], "Click a square. Timing shows up here when it finishes.");
});
