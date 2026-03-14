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
  const endpoint = `/plugins/ctfd_container_challenges/api/challenges/${challenge.id}/instance`;

  const setBusy = (busy) => {
    startButton.disabled = busy;
    stopButton.disabled = busy || stopButton.dataset.running !== "true";
  };

  const renderInstance = (instance) => {
    if (!instance) {
      statusNode.textContent = "No active instance.";
      accessNode.innerHTML = "";
      expiryNode.textContent = "";
      stopButton.dataset.running = "false";
      stopButton.disabled = true;
      startButton.disabled = false;
      return;
    }

    statusNode.textContent = "Instance running.";
    accessNode.innerHTML = `<a href="${instance.access_url}" target="_blank" rel="noopener noreferrer">${instance.access_url}</a>`;
    expiryNode.textContent = `Expires at ${instance.expires_at}`;
    stopButton.dataset.running = "true";
    stopButton.disabled = false;
    startButton.disabled = false;
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
    statusNode.textContent = message;
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
      statusNode.textContent = "Unable to reach the runtime API.";
    } finally {
      setBusy(false);
    }
  };

  startButton.addEventListener("click", async () => {
    setBusy(true);
    statusNode.textContent = "Starting instance...";
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
      statusNode.textContent = "Unable to start the instance.";
    } finally {
      setBusy(false);
    }
  });

  stopButton.addEventListener("click", async () => {
    setBusy(true);
    statusNode.textContent = "Stopping instance...";
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
      statusNode.textContent = "Unable to stop the instance.";
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
    return response;
  });
};
