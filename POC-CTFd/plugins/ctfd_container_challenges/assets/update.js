CTFd.plugin.run((CTFd) => {
  const uploadEndpoint = "/plugins/ctfd_container_challenges/api/admin/images/upload";

  function bindArchiveUpload(formSelector) {
    const form = document.querySelector(formSelector);
    if (!form || form.dataset.archiveUploadBound === "true") {
      return;
    }
    form.dataset.archiveUploadBound = "true";

    const fileInput = form.querySelector('input[name="image_archive"]');
    const tokenInput = form.querySelector('input[name="uploaded_image_token"]');
    const imageInput = form.querySelector('input[name="image"]');
    const statusNode = form.querySelector("[data-upload-status]");

    if (!fileInput || !tokenInput || !imageInput || !statusNode) {
      return;
    }

    imageInput.addEventListener("input", () => {
      if (
        tokenInput.value &&
        imageInput.dataset.uploadedImageTag &&
        imageInput.value.trim() !== imageInput.dataset.uploadedImageTag
      ) {
        tokenInput.value = "";
        statusNode.textContent = "Using manually specified image. Uploaded archive token cleared for this update.";
      }
    });

    form.addEventListener(
      "submit",
      async (event) => {
        if (form.dataset.archiveUploadReady === "true") {
          form.dataset.archiveUploadReady = "false";
          return;
        }

        if (!fileInput.files || fileInput.files.length === 0) {
          return;
        }

        event.preventDefault();
        event.stopImmediatePropagation();

        if (form.dataset.archiveUploadBusy === "true") {
          return;
        }

        form.dataset.archiveUploadBusy = "true";
        statusNode.textContent = "Uploading Docker archive...";

        try {
          const data = new FormData();
          data.append("image_archive", fileInput.files[0]);
          data.append("nonce", CTFd.config.csrfNonce);

          const response = await CTFd.fetch(uploadEndpoint, {
            method: "POST",
            credentials: "same-origin",
            body: data,
          });
          const body = await response.json();
          if (!response.ok || !body.success) {
            throw new Error(body.error || "Failed to upload Docker archive");
          }

          tokenInput.value = body.asset.asset_token;
          imageInput.value = body.asset.image_tag;
          imageInput.dataset.uploadedImageTag = body.asset.image_tag;
          statusNode.textContent = `Uploaded ${body.asset.original_filename} as ${body.asset.image_tag}.`;
          fileInput.value = "";

          form.dataset.archiveUploadReady = "true";
          form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
        } catch (error) {
          statusNode.textContent = error.message || "Failed to upload Docker archive.";
          window.alert(statusNode.textContent);
        } finally {
          form.dataset.archiveUploadBusy = "false";
        }
      },
      true,
    );
  }

  window.setTimeout(() => {
    bindArchiveUpload("#challenge-update-container > form");
  }, 0);

  return CTFd;
});
