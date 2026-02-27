const eventLog = document.getElementById("event-log");
const challengeTable = document.getElementById("challenge-table");
const instanceTable = document.getElementById("instance-table");
const challengeCount = document.getElementById("challenge-count");
const instanceCount = document.getElementById("instance-count");
const healthBox = document.getElementById("health-box");
const metricChallenges = document.getElementById("metric-challenges");
const metricRunning = document.getElementById("metric-running");
const metricInactive = document.getElementById("metric-inactive");
const metricBackend = document.getElementById("metric-backend");
const startInstanceBtn = document.getElementById("start-instance-btn");
const challengeSelect = document.getElementById("challenge-select");
const teamIdInput = document.getElementById("team-id-input");

let cachedChallenges = [];

function formatLogData(data) {
  if (data === undefined || data === null) {
    return "";
  }
  try {
    return typeof data === "string" ? data : JSON.stringify(data);
  } catch {
    return String(data);
  }
}

function formatLogLine(item) {
  const timestamp = item.created_at || new Date().toISOString();
  const level = item.level ? `[${String(item.level).toUpperCase()}] ` : "";
  const metadataText = formatLogData(item.metadata);
  return metadataText
    ? `${timestamp} ${level}${item.message} ${metadataText}`
    : `${timestamp} ${level}${item.message}`;
}

async function loadLogs() {
  const payload = await api("/api/logs?limit=200");
  const lines = payload.items.map((item) => formatLogLine(item));
  eventLog.textContent = lines.join("\n");
}

async function logEvent(message, data, level = "info") {
  try {
    await api("/api/logs", {
      method: "POST",
      body: JSON.stringify({
        message,
        metadata: data ?? null,
        level,
      }),
    });
    await loadLogs();
  } catch {
    // Fallback only if DB/API logging fails.
    const timestamp = new Date().toISOString();
    const dataText = formatLogData(data);
    const line = dataText ? `${timestamp} ${message} ${dataText}` : `${timestamp} ${message}`;
    eventLog.textContent = `${line}\n${eventLog.textContent}`.slice(0, 12000);
  }
}

function appendLocalLog(message, data) {
  const timestamp = new Date().toISOString();
  const dataText = formatLogData(data);
  const line = dataText ? `${timestamp} ${message} ${dataText}` : `${timestamp} ${message}`;
  eventLog.textContent = `${line}\n${eventLog.textContent}`.slice(0, 12000);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function formToJson(form) {
  const data = new FormData(form);
  return Object.fromEntries(data.entries());
}

function numeric(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function renderChallenges(items) {
  cachedChallenges = items;
  challengeCount.textContent = `${items.length} items`;
  challengeTable.innerHTML = items
    .map(
      (item) => `
      <tr>
        <td>${item.challenge_id}</td>
        <td>${item.image}</td>
        <td>${item.container_port}</td>
        <td>${item.cpu_limit}</td>
        <td>${item.memory_limit_mb} MB</td>
        <td>${item.timeout_seconds}s</td>
        <td>${item.max_instances}</td>
        <td><button class="btn btn-ghost" data-use-challenge="${item.challenge_id}" type="button">Use</button></td>
      </tr>
    `,
    )
    .join("");

  refreshChallengeSelect(items);
  challengeTable.querySelectorAll("button[data-use-challenge]").forEach((button) => {
    button.addEventListener("click", () => {
      const challengeId = button.dataset.useChallenge;
      if (!challengeId) {
        return;
      }
      challengeSelect.value = challengeId;
      teamIdInput.focus();
      void logEvent("challenge selected from registry", { challenge_id: challengeId });
    });
  });
}

function refreshChallengeSelect(items) {
  const previous = challengeSelect.value;
  const options = [
    '<option value="" disabled>Select a registered container...</option>',
    ...items.map(
      (item) =>
        `<option value="${item.challenge_id}">${item.challenge_id} - ${item.name} (${item.image})</option>`,
    ),
  ];
  challengeSelect.innerHTML = options.join("");
  if (previous && items.some((item) => item.challenge_id === previous)) {
    challengeSelect.value = previous;
  } else {
    challengeSelect.selectedIndex = 0;
  }
  startInstanceBtn.disabled = items.length === 0;
}

function statusTag(status) {
  return `<span class="tag tag-${status}">${status}</span>`;
}

function renderInstances(items) {
  instanceCount.textContent = `${items.length} items`;
  instanceTable.innerHTML = items
    .map((item) => {
      const access = item.access_url
        ? `<a href="${item.access_url}" target="_blank" rel="noopener noreferrer">open</a>`
        : "-";
      const stopBtn =
        item.status === "running"
          ? `<button class="btn btn-ghost" data-stop="${item.instance_id}">Stop</button>`
          : "-";
      return `
      <tr>
        <td>${item.instance_id.slice(0, 12)}</td>
        <td>${item.challenge_id}</td>
        <td>${item.user_id}</td>
        <td>${statusTag(item.status)}</td>
        <td>${item.host_port ?? "-"}</td>
        <td>${access}</td>
        <td>${item.expires_at}</td>
        <td>${stopBtn}</td>
      </tr>
      `;
    })
    .join("");

  instanceTable.querySelectorAll("button[data-stop]").forEach((button) => {
    button.addEventListener("click", async () => {
      const instanceId = button.dataset.stop;
      try {
        const stopped = await api(`/api/instances/${instanceId}/stop`, {
          method: "POST",
          body: JSON.stringify({ reason: "manual" }),
        });
        await refreshData();
      } catch (error) {
        await logEvent("instance stop failed", { instanceId, error: error.message }, "warn");
      }
    });
  });
}

async function refreshHealth() {
  try {
    const health = await api("/healthz");
    const backendStatus = health.backend?.status || "unknown";
    healthBox.textContent = `Storage: ${health.storage.challenges} challenges, ${health.storage.instances} instances. Backend: ${backendStatus}`;
    metricBackend.textContent = backendStatus;
  } catch (error) {
    healthBox.textContent = `Health check failed: ${error.message}`;
    metricBackend.textContent = "error";
  }
}

async function refreshData() {
  const [challenges, instances] = await Promise.all([
    api("/api/challenges"),
    api("/api/instances"),
  ]);
  renderChallenges(challenges.items);
  renderInstances(instances.items);
  metricChallenges.textContent = String(challenges.items.length);
  const running = instances.items.filter((item) => item.status === "running").length;
  metricRunning.textContent = String(running);
  metricInactive.textContent = String(Math.max(instances.items.length - running, 0));
  await refreshHealth();
  await loadLogs();
}

document.getElementById("challenge-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = formToJson(event.target);
  payload.container_port = numeric(payload.container_port, 8080);
  payload.cpu_limit = numeric(payload.cpu_limit, 0.5);
  payload.memory_limit_mb = numeric(payload.memory_limit_mb, 256);
  payload.timeout_seconds = numeric(payload.timeout_seconds, 900);
  payload.max_instances = numeric(payload.max_instances, 30);
  try {
    const challenge = await api("/api/challenges", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    challengeSelect.value = challenge.challenge_id;
    await refreshData();
  } catch (error) {
    await logEvent("registry save failed", { error: error.message }, "warn");
  }
});

document.getElementById("start-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!challengeSelect.value) {
    await logEvent("start blocked", { error: "Select a registered container first" }, "warn");
    return;
  }
  const payload = formToJson(event.target);
  startInstanceBtn.disabled = true;
  try {
    const result = await api("/api/instances/start", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await refreshData();
  } catch (error) {
    await logEvent("start failed", { error: error.message }, "warn");
  } finally {
    startInstanceBtn.disabled = cachedChallenges.length === 0;
  }
});

document.getElementById("reap-now").addEventListener("click", async () => {
  try {
    await api("/api/reaper/run", { method: "POST", body: JSON.stringify({}) });
    await refreshData();
  } catch (error) {
    await logEvent("reaper failed", { error: error.message }, "warn");
  }
});

document.getElementById("stop-all").addEventListener("click", async () => {
  try {
    await api("/api/instances/stop-all", {
      method: "POST",
      body: JSON.stringify({ reason: "bulk-stop" }),
    });
    await refreshData();
  } catch (error) {
    await logEvent("bulk stop failed", { error: error.message }, "warn");
  }
});

document.getElementById("clear-log").addEventListener("click", async () => {
  try {
    await api("/api/logs", { method: "DELETE" });
    eventLog.textContent = "";
  } catch (error) {
    appendLocalLog("log clear failed", { error: error.message });
  }
});

document.getElementById("refresh-all").addEventListener("click", async () => {
  try {
    await refreshData();
    await logEvent("manual refresh complete", null);
  } catch (error) {
    await logEvent("refresh failed", { error: error.message }, "warn");
  }
});

setInterval(() => {
  refreshData().catch((error) =>
    void logEvent("background refresh failed", { error: error.message }, "warn"),
  );
}, 10000);

refreshData()
  .then(() => logEvent("dashboard loaded", null))
  .catch((error) => appendLocalLog("initial load failed", { error: error.message }));
