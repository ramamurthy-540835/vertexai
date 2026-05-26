  /**
   * Drive → GitHub Actions trigger
   *
   * This Apps Script watches a specific Drive folder for a trigger file
   * ("process_pos_data.txt"). When found, it dispatches a GitHub Actions
   * workflow_dispatch event, which then triggers the Cloud Run Job.
   *
   * Setup steps (one-time, manual):
   *   1. Set all config values in the CONFIG block below OR store them
   *      in Script Properties (recommended for secrets like GH_TOKEN):
   *        Apps Script → Project Settings → Script Properties
   *        Keys: GH_TOKEN, DRIVE_FOLDER_ID, GH_OWNER, GH_REPO, GH_WORKFLOW_ID
   *   2. Run installTrigger() once to register the time-based polling trigger.
   *   3. Deploy the script (via clasp or manually).
   */

  // ── CONFIG ────────────────────────────────────────────────────────────────────
  // Values are read from Script Properties first, falling back to these defaults.
  // Store secrets (especially GH_TOKEN) in Script Properties — never hardcode them.
  const CONFIG = {
    TRIGGER_FILENAME : "process_pos_data.txt",
    DRIVE_FOLDER_ID  : getprop("DRIVE_FOLDER_ID",  "YOUR_DRIVE_FOLDER_ID"),
    GH_OWNER         : getprop("GH_OWNER",          "your-github-org-or-username"),
    GH_REPO          : getprop("GH_REPO",            "your-repo-name"),
    GH_WORKFLOW_ID   : getprop("GH_WORKFLOW_ID",     "trigger-cloud-run.yml"),  // filename or numeric ID
    GH_REF           : getprop("GH_REF",             "main"),                   // branch to run on
    GH_TOKEN         : getprop("GH_TOKEN",           ""),                       // PAT with actions:write
    POLL_INTERVAL_MIN: 5,   // how often the time-based trigger fires (minutes)
  };

  // ── HELPERS ───────────────────────────────────────────────────────────────────

  /** Read a Script Property, falling back to a default. */
  function getprop(key, fallback) {
    const val = PropertiesService.getScriptProperties().getProperty(key);
    return val !== null ? val : fallback;
  }

  /** Alias with matching case used inside CONFIG literal above. */
  function getprop(key, fallback) {
    const val = PropertiesService.getScriptProperties().getProperty(key);
    return val !== null ? val : fallback;
  }

  // Apps Script doesn't hoist properly in some contexts — define before CONFIG.
  // Redefine cleanly here for use everywhere else in the script.
  function getprop(key, fallback) {
    const props = PropertiesService.getScriptProperties();
    const val   = props.getProperty(key);
    return (val !== null && val !== "") ? val : fallback;
  }

  // ── CORE LOGIC ────────────────────────────────────────────────────────────────

  /**
   * Checks whether the trigger file exists in the configured Drive folder.
   * Returns the File object if found, null otherwise.
   */
  function findTriggerFile() {
    const folder = DriveApp.getFolderById(CONFIG.DRIVE_FOLDER_ID);
    const files   = folder.getFilesByName(CONFIG.TRIGGER_FILENAME);
    return files.hasNext() ? files.next() : null;
  }


  /**
   * Dispatches a workflow_dispatch event to GitHub Actions.
   * Passes along contextual inputs so the workflow knows what triggered it.
   */
  function dispatchGitHubWorkflow() {
    const url = `https://api.github.com/repos/${CONFIG.GH_OWNER}/${CONFIG.GH_REPO}`
              + `/actions/workflows/${CONFIG.GH_WORKFLOW_ID}/dispatches`;

    Logger.log("🔗 URL: %s", url);
    Logger.log("📋 Owner: %s", CONFIG.GH_OWNER);
    Logger.log("📋 Repo: %s", CONFIG.GH_REPO);
    Logger.log("📋 Workflow: %s", CONFIG.GH_WORKFLOW_ID);
    Logger.log("📋 Ref: %s", CONFIG.GH_REF);

    const payload = {
      ref   : CONFIG.GH_REF,
      inputs: {
        environment:   CONFIG.ENVIRONMENT, 
        drive_folder_id    : CONFIG.DRIVE_FOLDER_ID,
        create_manifest_after:   "true",
      },
    };

    const options = {
      method      : "post",
      contentType : "application/json",
      headers     : {
        Authorization        : `Bearer ${CONFIG.GH_TOKEN}`,
        Accept               : "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      payload     : JSON.stringify(payload),
      muteHttpExceptions: true,
    };

    const response = UrlFetchApp.fetch(url, options);
    const status   = response.getResponseCode();

    if (status === 204) {
      Logger.log("✅ GitHub Actions workflow dispatched successfully.");
    } else {
      const body = response.getContentText();
      Logger.log(`❌ GitHub dispatch failed. Status: ${status}, Body: ${body}`);
      throw new Error(`GitHub dispatch failed with status ${status}: ${body}`);
    }
  }


  /**
   * Main entry point — called by the time-based trigger every N minutes.
   * 1. Look for the trigger file.
   * 2. If found, dispatch the GitHub Actions workflow.
   * 3. Log result; errors are caught so the trigger keeps running.
   */
  function checkAndTrigger() {
  Logger.log("🔍 Checking for trigger file: %s", CONFIG.TRIGGER_FILENAME);

  try {
    const triggerFile = findTriggerFile();

    if (!triggerFile) {
      Logger.log("⏭️  Trigger file not found. Nothing to do.");
      return;
    }

    Logger.log("🚀 Trigger file detected! Dispatching GitHub Actions workflow...");
    dispatchGitHubWorkflow();
    
    // Delete file so it doesn't trigger again
    triggerFile.setTrashed(true);
    Logger.log("🗑️ Trigger file moved to trash.");
    Logger.log("✅ Done.");

  } catch (err) {
    Logger.log("❌ Error in checkAndTrigger: %s", err.message);
    throw err;
  }
}

  // ── TRIGGER MANAGEMENT ────────────────────────────────────────────────────────

  /**
   * Run this ONCE to install the polling trigger.
   * After running, the trigger appears under:
   *   Apps Script → Triggers (clock icon on the left sidebar)
   */
  function installTrigger() {
    // Remove any existing triggers for this function to avoid duplicates
    removeTrigger();

    ScriptApp.newTrigger("checkAndTrigger")
      .timeBased()
      .everyMinutes(CONFIG.POLL_INTERVAL_MIN)
      .create();

    Logger.log(
      "⏰ Trigger installed: checkAndTrigger fires every %d minute(s).",
      CONFIG.POLL_INTERVAL_MIN
    );
  }


  /**
   * Removes all existing checkAndTrigger time-based triggers.
   * Safe to run multiple times.
   */
  function removeTrigger() {
    ScriptApp.getProjectTriggers()
      .filter(t => t.getHandlerFunction() === "checkAndTrigger")
      .forEach(t => {
        ScriptApp.deleteTrigger(t);
        Logger.log("🗑️  Removed existing trigger: %s", t.getUniqueId());
      });
  }


  /**
   * Utility: manually fire the check right now (useful for testing).
   * Run this from the Apps Script editor to simulate a trigger fire.
   */
  function runManually() {
    Logger.log("🛠️  Manual run initiated.");
    checkAndTrigger();
  }