CTFd._internal.challenge.data = undefined;
CTFd._internal.challenge.preRender = function() {};

function initializeRuntimePanel() {
  const challenge = CTFd._internal.challenge.data;
  if (!challenge) {
    return;
  }

  const root = document.getElementById("container-runtime-panel");
  if (!root || root.dataset.runtimeInitialized === "true") {
    return;
  }
  root.dataset.runtimeInitialized = "true";

  const statusNode = document.getElementById("container-runtime-status");
  const accessNode = document.getElementById("container-runtime-access");
  const expiryNode = document.getElementById("container-runtime-expiry");
  const startButton = document.getElementById("container-runtime-start");
  const stopButton = document.getElementById("container-runtime-stop");
  const challengeSubmitButton = document.getElementById("challenge-submit");
  const endpoint = `/plugins/ctfd_container_challenges/api/challenges/${challenge.id}/instance`;
  const stopReasonStorageKey = `ctfd-container-last-stop-${challenge.id}`;
  const statusClasses = [
    "ctfd-container-panel__status--muted",
    "ctfd-container-panel__status--success",
    "ctfd-container-panel__status--warning",
    "ctfd-container-panel__status--error",
  ];

  const setBusy = (busy) => {
    startButton.disabled = busy;
    stopButton.disabled = busy || stopButton.dataset.running !== "true";
  };

  const setLastStopReason = (reason) => {
    if (reason) {
      root.dataset.lastStopReason = reason;
      window.sessionStorage.setItem(stopReasonStorageKey, reason);
      return;
    }

    delete root.dataset.lastStopReason;
    window.sessionStorage.removeItem(stopReasonStorageKey);
  };

  const setStatus = (message, tone = "muted") => {
    statusNode.textContent = message;
    statusNode.classList.remove(...statusClasses);
    statusNode.classList.add(`ctfd-container-panel__status--${tone}`);
  };

  const renderInstance = (instance) => {
    if (!instance) {
      if (root.dataset.lastStopReason === "challenge-solved") {
        setStatus("Instance stopped automatically because you solved the challenge.", "success");
      } else {
        setStatus("No active instance.");
      }
      accessNode.innerHTML = "";
      expiryNode.textContent = "";
      stopButton.dataset.running = "false";
      stopButton.disabled = true;
      startButton.disabled = false;
      return;
    }

    setStatus("Instance running.", "success");
    if (Array.isArray(instance.port_bindings) && instance.port_bindings.length > 0) {
      accessNode.innerHTML = instance.port_bindings
        .map((binding) => {
          const label = binding.container_port
            ? `Container ${binding.container_port}`
            : "Access";
          return `<div><small class="text-muted">${label}</small><br><a href="${binding.access_url}" target="_blank" rel="noopener noreferrer">${binding.access_url}</a></div>`;
        })
        .join("");
    } else if (instance.access_url) {
      accessNode.innerHTML = `<a href="${instance.access_url}" target="_blank" rel="noopener noreferrer">${instance.access_url}</a>`;
    } else {
      accessNode.innerHTML = "";
    }
    expiryNode.textContent = `Expires at ${instance.expires_at}`;
    stopButton.dataset.running = "true";
    stopButton.disabled = false;
    startButton.disabled = false;
    setLastStopReason(null);
  };

  const renderStopped = (reason) => {
    setLastStopReason(reason);
    renderInstance(null);
  };

  const handleSolvedSubmission = () => {
    renderStopped("challenge-solved");
    window.setTimeout(() => {
      refresh();
    }, 0);
  };

  const monitorSubmissionResult = () => {
    let attempts = 0;
    const maxAttempts = 12;

    const poll = () => {
      const alertNode = document.querySelector(".notification-row [role='alert'] strong");
      const message = alertNode ? alertNode.textContent.trim().toLowerCase() : "";
      if (message.includes("correct")) {
        handleSolvedSubmission();
        return;
      }

      attempts += 1;
      if (attempts < maxAttempts) {
        window.setTimeout(poll, 250);
      }
    };

    window.setTimeout(poll, 150);
  };

  const handleError = async (response) => {
    let message = "Unexpected plugin error";
    try {
      const body = await response.json();
      if (body && body.error) {
        message = body.error;
      }
    } catch (_error) {
      message = `${message} (${response.status})`;
    }
    setStatus(message, "error");
  };

  const refresh = async () => {
    setBusy(true);
    try {
      const response = await CTFd.fetch(endpoint, {
        method: "GET",
        credentials: "same-origin",
      });
      if (!response.ok) {
        await handleError(response);
        return;
      }
      const body = await response.json();
      renderInstance(body.instance);
    } catch (_error) {
      setStatus("Unable to reach the runtime API.", "error");
    } finally {
      setBusy(false);
    }
  };
  root.refreshRuntimePanel = refresh;
  const persistedStopReason = window.sessionStorage.getItem(stopReasonStorageKey);
  if (persistedStopReason) {
    root.dataset.lastStopReason = persistedStopReason;
  }
  if (challengeSubmitButton && challengeSubmitButton.dataset.runtimeMonitorBound !== "true") {
    challengeSubmitButton.dataset.runtimeMonitorBound = "true";
    challengeSubmitButton.addEventListener("click", monitorSubmissionResult);
  }

  startButton.addEventListener("click", async () => {
    setBusy(true);
    setLastStopReason(null);
    setStatus("Starting instance...", "warning");
    try {
      const response = await CTFd.fetch(endpoint, {
        method: "POST",
        credentials: "same-origin",
        body: JSON.stringify({ challenge_id: challenge.id }),
      });
      if (!response.ok) {
        await handleError(response);
        return;
      }
      const body = await response.json();
      renderInstance(body.instance);
    } catch (_error) {
      setStatus("Unable to start the instance.", "error");
    } finally {
      setBusy(false);
    }
  });

  stopButton.addEventListener("click", async () => {
    setBusy(true);
    setLastStopReason("manual");
    setStatus("Stopping instance...", "warning");
    try {
      const response = await CTFd.fetch(endpoint, {
        method: "DELETE",
        credentials: "same-origin",
      });
      if (!response.ok) {
        await handleError(response);
        return;
      }
      const body = await response.json();
      renderInstance(body.instance);
    } catch (_error) {
      setStatus("Unable to stop the instance.", "error");
    } finally {
      setBusy(false);
    }
  });

  refresh();
}

CTFd._internal.challenge.postRender = function() {
  window.requestAnimationFrame(() => {
    window.setTimeout(initializeRuntimePanel, 0);
  });
};

CTFd._internal.challenge.submit = function(preview) {
  const challenge_id = parseInt(CTFd.lib.$("#challenge-id").val(), 10);
  const submission = CTFd.lib.$("#challenge-input").val();
  const body = {
    challenge_id: challenge_id,
    submission: submission
  };
  const params = {};
  if (preview) {
    params.preview = true;
  }
  return CTFd.api.post_challenge_attempt(params, body).then(function(response) {
    const status = response && response.data ? response.data.status : null;
    if (status === "correct" || status === "already_solved") {
      handleSolvedSubmission();
    }
    return response;
  });
};
