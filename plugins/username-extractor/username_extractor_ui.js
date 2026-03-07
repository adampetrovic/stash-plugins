(function () {
  "use strict";

  const PLUGIN_ID = "username-extractor";
  const BUTTON_ID = "username-extractor-btn";

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
      `mutation RunPluginTask($plugin_id: String!, $task_name: String!, $args_map: Map) {
        runPluginTask(plugin_id: $plugin_id, task_name: $task_name, args_map: $args_map)
      }`,
      { plugin_id: PLUGIN_ID, task_name: taskName, args_map: argsMap }
    );
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
  // Button creation
  // ---------------------------------------------------------------------------
  function createButton(type, id) {
    const btn = document.createElement("button");
    btn.id = BUTTON_ID;
    btn.type = "button";
    btn.className = "btn btn-secondary ml-2";
    btn.title = "Extract username via OCR";
    btn.innerHTML = '<span class="mr-1">🔍</span>Extract Username';

    btn.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      btn.disabled = true;
      btn.innerHTML = '<span class="mr-1">⏳</span>Extracting…';

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
          btn.innerHTML = '<span class="mr-1">❌</span>Error';
          console.error("[username-extractor]", result.errors);
          setTimeout(() => resetButton(btn), 4000);
        } else {
          btn.innerHTML = '<span class="mr-1">✅</span>Done — reloading…';
          setTimeout(() => location.reload(), 1500);
        }
      } catch (err) {
        btn.innerHTML = '<span class="mr-1">❌</span>Error';
        console.error("[username-extractor]", err);
        setTimeout(() => resetButton(btn), 4000);
      }
    });

    return btn;
  }

  function resetButton(btn) {
    btn.disabled = false;
    btn.innerHTML = '<span class="mr-1">🔍</span>Extract Username';
  }

  // ---------------------------------------------------------------------------
  // Injection
  // ---------------------------------------------------------------------------
  function tryInject() {
    // Don't duplicate
    if (document.getElementById(BUTTON_ID)) return;

    const sceneId = getSceneId();
    const imageId = getImageId();
    if (!sceneId && !imageId) return;

    const type = sceneId ? "scene" : "image";
    const id = sceneId || imageId;

    // Try various selectors for the scene/image detail page action areas.
    // Stash UI evolves between versions so we try several.
    const selectors = [
      // v0.26+ scene/image detail toolbar
      ".detail-container .detail-header .btn-group",
      ".detail-container .detail-header-group .btn-group",
      // Scene operations bar
      ".scene-toolbar .btn-group",
      // Generic detail header buttons
      ".detail-header > .btn-group",
      // Fallback: any button group inside the detail container
      ".detail-container .btn-group",
    ];

    for (const sel of selectors) {
      const container = document.querySelector(sel);
      if (container) {
        container.appendChild(createButton(type, id));
        return;
      }
    }

    // Last resort: append after the detail header
    const header = document.querySelector(
      ".detail-container .detail-header"
    );
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
    // Remove stale button from previous page
    const old = document.getElementById(BUTTON_ID);
    if (old) old.remove();
    // Wait for React to render the new page
    setTimeout(tryInject, 600);
    setTimeout(tryInject, 1500); // retry in case of slow render
  }

  const observer = new MutationObserver(onNavigate);
  observer.observe(document.body, { childList: true, subtree: true });

  // Initial load
  setTimeout(tryInject, 1000);
})();
