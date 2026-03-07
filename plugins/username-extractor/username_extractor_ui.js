(function () {
  "use strict";

  const PLUGIN_ID = "username-extractor";
  const BUTTON_ID = "username-extractor-btn";
  const PROCESSED_TAG = "auto:ocr";
  const POLL_INTERVAL_MS = 2000;
  const POLL_TIMEOUT_MS = 120000;

  // ---------------------------------------------------------------------------
  // GraphQL helper
  // ---------------------------------------------------------------------------
  async function gql(query, variables) {
    const res = await fetch("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, variables }),
    });
    return res.json();
  }

  async function runTask(taskName, argsMap) {
    return gql(
      `mutation RunPluginTask($plugin_id: ID!, $task_name: String, $args_map: Map) {
        runPluginTask(plugin_id: $plugin_id, task_name: $task_name, args_map: $args_map)
      }`,
      { plugin_id: PLUGIN_ID, task_name: taskName, args_map: argsMap }
    );
  }

  // ---------------------------------------------------------------------------
  // Polling — wait for the auto:ocr tag to appear on the scene/image
  // ---------------------------------------------------------------------------
  async function hasProcessedTag(type, id) {
    const query =
      type === "scene"
        ? `query($id: ID!) { findScene(id: $id) { tags { name } } }`
        : `query($id: ID!) { findImage(id: $id) { tags { name } } }`;
    const res = await gql(query, { id: id });
    const item =
      type === "scene" ? res?.data?.findScene : res?.data?.findImage;
    if (!item) return false;
    return (item.tags || []).some(
      (t) => t.name.toLowerCase() === PROCESSED_TAG
    );
  }

  async function pollUntilProcessed(type, id) {
    const start = Date.now();
    while (Date.now() - start < POLL_TIMEOUT_MS) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      if (await hasProcessedTag(type, id)) return true;
    }
    return false;
  }

  // ---------------------------------------------------------------------------
  // Page detection
  // ---------------------------------------------------------------------------
  function getSceneId() {
    const m = window.location.pathname.match(/\/scenes\/(\d+)/);
    return m ? m[1] : null;
  }

  function getImageId() {
    const m = window.location.pathname.match(/\/images\/(\d+)/);
    return m ? m[1] : null;
  }

  // ---------------------------------------------------------------------------
  // Button
  // ---------------------------------------------------------------------------
  function createButton(type, id) {
    const btn = document.createElement("button");
    btn.id = BUTTON_ID;
    btn.type = "button";
    btn.className = "btn btn-secondary minimal";
    btn.title = "Extract username via OCR";

    const ICON_DEFAULT =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:1em;height:1em"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/><line x1="11" y1="8" x2="11" y2="14"/></svg>';
    const ICON_SPIN =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:1em;height:1em;animation:spin 1s linear infinite"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>';
    const ICON_OK =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:1em;height:1em"><polyline points="20 6 9 17 4 12"/></svg>';
    const ICON_ERR =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width:1em;height:1em"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

    btn.innerHTML = ICON_DEFAULT;

    // Inject keyframe once
    if (!document.getElementById("username-extractor-style")) {
      const style = document.createElement("style");
      style.id = "username-extractor-style";
      style.textContent =
        "@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}";
      document.head.appendChild(style);
    }

    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      btn.disabled = true;
      btn.innerHTML = ICON_SPIN;
      btn.title = "Extracting username…";

      try {
        const taskName =
          type === "scene" ? "Process Single Scene" : "Process Single Image";
        const argsKey = type === "scene" ? "scene_id" : "image_id";
        const mode = type === "scene" ? "single_scene" : "single_image";

        const result = await runTask(taskName, {
          mode: mode,
          [argsKey]: id,
        });

        if (result.errors) {
          btn.innerHTML = ICON_ERR;
          btn.title = result.errors[0]?.message || "Error";
          console.error("[username-extractor]", result.errors);
          setTimeout(() => reset(), 5000);
          return;
        }

        // Task was queued — poll until the auto:ocr tag appears
        const done = await pollUntilProcessed(type, id);

        if (done) {
          btn.innerHTML = ICON_OK;
          btn.title = "Done — reloading…";
          setTimeout(() => location.reload(), 1000);
        } else {
          btn.innerHTML = ICON_ERR;
          btn.title = "Timed out waiting for results";
          setTimeout(() => reset(), 5000);
        }
      } catch (err) {
        btn.innerHTML = ICON_ERR;
        btn.title = String(err);
        console.error("[username-extractor]", err);
        setTimeout(() => reset(), 5000);
      }

      function reset() {
        btn.disabled = false;
        btn.innerHTML = ICON_DEFAULT;
        btn.title = "Extract username via OCR";
      }
    });

    return btn;
  }

  // ---------------------------------------------------------------------------
  // Injection
  // ---------------------------------------------------------------------------
  function tryInject() {
    if (document.getElementById(BUTTON_ID)) return;

    const sceneId = getSceneId();
    const imageId = getImageId();
    if (!sceneId && !imageId) return;

    const type = sceneId ? "scene" : "image";
    const id = sceneId || imageId;

    const selectors = [
      ".detail-container .detail-header .btn-group",
      ".detail-container .detail-header-group .btn-group",
      ".scene-toolbar .btn-group",
      ".detail-header > .btn-group",
      ".detail-container .btn-group",
    ];

    for (const sel of selectors) {
      const container = document.querySelector(sel);
      if (container) {
        container.appendChild(createButton(type, id));
        return;
      }
    }

    const header = document.querySelector(".detail-container .detail-header");
    if (header) {
      header.appendChild(createButton(type, id));
    }
  }

  // ---------------------------------------------------------------------------
  // SPA navigation watcher
  // ---------------------------------------------------------------------------
  let lastHref = "";
  function onNavigate() {
    const href = window.location.href;
    if (href === lastHref) return;
    lastHref = href;
    const old = document.getElementById(BUTTON_ID);
    if (old) old.remove();
    setTimeout(tryInject, 600);
    setTimeout(tryInject, 1500);
  }

  const observer = new MutationObserver(onNavigate);
  observer.observe(document.body, { childList: true, subtree: true });
  setTimeout(tryInject, 1000);
})();
