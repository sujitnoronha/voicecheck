/**
 * VoiceCheck Dashboard — shared JS utilities.
 */
const VC = {
  /** Fetch JSON from the API with error handling. */
  async fetchAPI(path) {
    const resp = await fetch(path);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`API ${resp.status}: ${text}`);
    }
    return resp.json();
  },

  /** Format ISO date to readable local string. */
  formatDate(iso) {
    if (!iso) return "\u2014";
    return iso.slice(0, 19).replace("T", " ");
  },

  /** Format milliseconds with unit. */
  formatMs(ms) {
    if (!ms || ms <= 0) return "\u2014";
    return `${Math.round(ms)}ms`;
  },

  /** CSS class for latency value. */
  latencyClass(ms) {
    if (!ms || ms <= 0) return "";
    if (ms < 2000) return "pass";
    if (ms < 4000) return "warn";
    return "fail";
  },

  /** CSS class for pass rate percentage. */
  rateClass(rate) {
    if (rate >= 80) return "pass";
    if (rate >= 50) return "warn";
    return "fail";
  },

  /** Create pass/fail badge HTML. */
  badge(passed) {
    return passed
      ? '<span class="badge badge-pass">PASS</span>'
      : '<span class="badge badge-fail">FAIL</span>';
  },

  /** Escape HTML special characters. */
  esc(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
  },

  /** Navigate to a URL. */
  go(url) {
    window.location.href = url;
  },

  /**
   * Create a Chart.js latency + pass rate dual-axis line chart.
   * @param {string} canvasId  - Canvas element ID.
   * @param {Array}  history   - Array of run objects (most recent first from API).
   */
  createTrendChart(canvasId, history) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    // Reverse to chronological (oldest first), limit to 50
    const data = [...history].reverse().slice(-50);
    const labels = data.map((h) => h.created_at.slice(0, 16).replace("T", " "));
    const latencies = data.map((h) => h.avg_first_byte_ms || 0);
    const passRates = data.map((h) =>
      h.total_turns ? (h.passed_turns / h.total_turns) * 100 : 0
    );

    return new Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "First Byte (ms)",
            data: latencies,
            borderColor: "#6366f1",
            backgroundColor: "rgba(99,102,241,0.1)",
            fill: true,
            tension: 0.3,
            yAxisID: "y",
          },
          {
            label: "Pass Rate (%)",
            data: passRates,
            borderColor: "#22c55e",
            borderDash: [5, 5],
            tension: 0.3,
            yAxisID: "y1",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { color: "#8b8d98", font: { size: 11 } } },
        },
        scales: {
          x: { display: false },
          y: {
            position: "left",
            title: { display: true, text: "Latency (ms)", color: "#8b8d98" },
            grid: { color: "rgba(255,255,255,0.05)" },
            ticks: { color: "#8b8d98" },
          },
          y1: {
            position: "right",
            min: 0,
            max: 100,
            title: { display: true, text: "Pass %", color: "#8b8d98" },
            grid: { drawOnChartArea: false },
            ticks: { color: "#8b8d98" },
          },
        },
      },
    });
  },

  /**
   * Create a horizontal bar chart comparing a metric across scenarios.
   * @param {string} canvasId
   * @param {Array}  scenarios - Array of {name, value}
   * @param {string} label
   * @param {string} color
   */
  createBarChart(canvasId, scenarios, label, color) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return null;

    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: scenarios.map((s) => s.name),
        datasets: [
          {
            label,
            data: scenarios.map((s) => s.value),
            backgroundColor: color || "rgba(99,102,241,0.6)",
            borderColor: color || "#6366f1",
            borderWidth: 1,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
        },
        scales: {
          x: {
            title: { display: true, text: label, color: "#8b8d98" },
            grid: { color: "rgba(255,255,255,0.05)" },
            ticks: { color: "#8b8d98" },
          },
          y: {
            grid: { display: false },
            ticks: { color: "#e4e4e7" },
          },
        },
      },
    });
  },

  /**
   * Render timeline dots HTML for a run history array.
   * @param {Array} history - Most recent first.
   * @returns {string} HTML string.
   */
  renderTimeline(history) {
    // Show up to 50, oldest to newest (left to right)
    const runs = [...history].reverse().slice(-50);
    return runs
      .map((r) => {
        const cls = r.passed ? "dot-pass" : "dot-fail";
        const date = (r.created_at || "").slice(0, 10);
        return `<div class="timeline-dot ${cls}" title="${date}"></div>`;
      })
      .join("");
  },
};
