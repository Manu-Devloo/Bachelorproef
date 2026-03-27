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

    async function uploadArchive({ submitAfterUpload = false } = {}) {
      if (!fileInput.files || fileInput.files.length === 0) {
        return false;
      }

      if (form.dataset.archiveUploadBusy === "true") {
        if (submitAfterUpload) {
          form.dataset.archiveUploadPendingSubmit = "true";
        }
        return false;
      }

      form.dataset.archiveUploadBusy = "true";
      if (submitAfterUpload) {
        form.dataset.archiveUploadPendingSubmit = "true";
      }
      statusNode.textContent = "Uploading Docker archive...";

      try {
        const data = new FormData();
        data.append("image_archive", fileInput.files[0]);
        data.append("nonce", CTFd.config.csrfNonce);

        const response = await window.fetch(uploadEndpoint, {
          method: "POST",
          credentials: "same-origin",
          headers: {
            Accept: "application/json",
          },
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

        if (form.dataset.archiveUploadPendingSubmit === "true") {
          form.dataset.archiveUploadPendingSubmit = "false";
          form.dataset.archiveUploadReady = "true";
          if (typeof form.requestSubmit === "function") {
            form.requestSubmit();
          } else {
            form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
          }
        }
        return true;
      } catch (error) {
        form.dataset.archiveUploadPendingSubmit = "false";
        statusNode.textContent = error.message || "Failed to upload Docker archive.";
        window.alert(statusNode.textContent);
        return false;
      } finally {
        form.dataset.archiveUploadBusy = "false";
      }
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

    fileInput.addEventListener("change", () => {
      if (!fileInput.files || fileInput.files.length === 0) {
        return;
      }
      tokenInput.value = "";
      uploadArchive();
    });

    form.addEventListener(
      "submit",
      async (event) => {
        if (form.dataset.archiveUploadReady === "true") {
          form.dataset.archiveUploadReady = "false";
          return;
        }

        if (form.dataset.archiveUploadBusy === "true") {
          event.preventDefault();
          form.dataset.archiveUploadPendingSubmit = "true";
          return;
        }

        if (!fileInput.files || fileInput.files.length === 0) {
          return;
        }

        event.preventDefault();
        event.stopImmediatePropagation();
        await uploadArchive({ submitAfterUpload: true });
      },
      true,
    );
  }

  window.setTimeout(() => {
    bindArchiveUpload("#challenge-update-container > form");
  }, 0);

  return CTFd;
});
