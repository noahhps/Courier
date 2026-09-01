import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useSkills } from "../hooks/useSkills";
import { iconForServer } from "../lib/mcpIcons";
import { useDialog } from "./Dialog";
import { Icon } from "./Icon";
import { ServiceIcon } from "./ServiceIcon";

function SkillRow({ skill, busy, onToggle, onKey }) {
  const [key, setKey] = useState("");
  const [open, setOpen] = useState(false);
  const blocked = skill.requires && !skill.available;

  const save = async (event) => {
    event.preventDefault();
    if (busy) return;
    if (await onKey(skill.name, key.trim())) {
      setKey("");
      setOpen(false);
    }
  };

  return (
    <div className="skill-item" data-off={skill.enabled ? undefined : ""}>
      <div className="skill-item-text">
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span className="h">{skill.name}</span>
          {/* Which server, not merely that there is one. Every MCP tool used
              to wear the same "MCP" badge, so a list of thirty of them said
              thirty times over the one thing they had in common. */}
          {skill.server ? (
            <span className="mcp-tag" title={`From the ${skill.server} MCP server`}>
              <Icon name={iconForServer(skill.server)} />
              {skill.server}
            </span>
          ) : skill.requires?.includes("MCP") ? (
            <span className="mcp-tag">
              <Icon name="device_hub" />
              MCP
            </span>
          ) : null}
        </div>
        <p className="skill-body">{skill.description}</p>

        {blocked ? (
          <div className="skill-needs">
            <span className="mi">Needs {skill.requires}</span>
            {skill.configurable ? (
              <button type="button" className="mi" onClick={() => setOpen((was) => !was)}>
                {open ? "cancel" : "add key"}
              </button>
            ) : null}
          </div>
        ) : null}

        {skill.configurable && skill.available ? (
          <div className="skill-needs">
            <span className="mi">Key saved</span>
            <button
              type="button"
              className="mi"
              disabled={busy}
              onClick={() => onKey(skill.name, "")}
            >
              remove
            </button>
          </div>
        ) : null}

        {open && blocked && skill.configurable ? (
          <form className="skill-key" onSubmit={save}>
            <input
              type="password"
              value={key}
              autoFocus
              autoComplete="off"
              spellCheck="false"
              placeholder="Paste the key"
              aria-label={`${skill.requires} for ${skill.name}`}
              onChange={(event) => setKey(event.target.value)}
            />
            <button type="submit" className="btnp" disabled={!key.trim() || busy}>
              {busy ? "Checking…" : "Save"}
            </button>
          </form>
        ) : null}
      </div>
      <button
        type="button"
        className="switch"
        role="switch"
        aria-checked={skill.enabled}
        aria-label={`${skill.enabled ? "Disable" : "Enable"} ${skill.name}`}
        aria-pressed={skill.enabled}
        disabled={busy || blocked}
        title={blocked ? `Needs ${skill.requires} first` : undefined}
        onClick={() => onToggle(skill.name, !skill.enabled)}
      >
        <i />
      </button>
    </div>
  );
}

function PresetCard({ api, preset, isInstalled, onInstall, busy }) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState({});

  // A preset declares what it needs in `inputs`; the older shape derived the
  // fields from `env`, which meant a preset whose secret belongs in a header
  // -- or in an argv flag -- had nowhere to put one.
  const inputs = preset.inputs || Object.keys(preset.env || {}).map((key) => ({ key }));
  const unfilled = inputs.filter((i) => i.required && !values[i.key]);

  const handleInstall = async (e) => {
    e.preventDefault();
    if (unfilled.length) return;
    await onInstall(preset.id, values);
    setOpen(false);
  };

  return (
    <div
      style={{
        padding: "16px",
        background: "var(--surface)",
        border: "1px solid var(--line-soft)",
        borderRadius: "var(--r-surface)",
        display: "flex",
        flexDirection: "column",
        gap: "10px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
        <ServiceIcon api={api} name={preset.name} presetId={preset.id} hasIcon={preset.has_icon} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <span className="h" style={{ fontSize: "var(--t-md)" }}>
            {preset.name}
          </span>
          <p style={{ margin: "4px 0 0", color: "var(--text-dim)", fontSize: "var(--t-sm)" }}>
            {preset.description}
          </p>
        </div>
        {isInstalled ? (
          <span
            style={{
              fontSize: "var(--t-xs)",
              padding: "3px 8px",
              background: "var(--green-field)",
              color: "var(--green)",
              borderRadius: "var(--r-pill)",
              fontWeight: 600,
            }}
          >
            Active
          </span>
        ) : (
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() => setOpen((was) => !was)}
          >
            {open ? "Cancel" : "Add Server"}
          </button>
        )}
      </div>

      {open && !isInstalled ? (
        <form
          onSubmit={handleInstall}
          style={{
            marginTop: "8px",
            padding: "12px",
            background: "var(--rail)",
            borderRadius: "var(--r-surface)",
            display: "flex",
            flexDirection: "column",
            gap: "10px",
          }}
        >
          {preset.notes ? (
            <p
              style={{
                margin: 0,
                fontSize: "var(--t-xs)",
                color: "var(--text-dim)",
                lineHeight: 1.5,
              }}
            >
              {preset.notes}
            </p>
          ) : null}
          {inputs.length === 0 ? (
            <p style={{ margin: 0, fontSize: "var(--t-xs)", color: "var(--text-faint)" }}>
              Nothing to configure — this server authenticates on its own.
            </p>
          ) : null}
          {inputs.map((input) => (
            <div key={input.key}>
              <label
                style={{
                  display: "block",
                  fontSize: "var(--t-xs)",
                  color: "var(--text-faint)",
                  marginBottom: "4px",
                  fontFamily: "var(--font-mono)",
                }}
              >
                {input.label || input.key}
                {input.required ? " *" : ""}
              </label>
              <input
                type={input.secret ? "password" : "text"}
                value={values[input.key] || ""}
                autoComplete="off"
                placeholder={input.help || input.key}
                style={{
                  width: "100%",
                  padding: "6px 10px",
                  fontSize: "var(--t-sm)",
                  borderRadius: "var(--r-surface)",
                  border: "1px solid var(--line)",
                  background: "var(--surface)",
                }}
                onChange={(e) => setValues({ ...values, [input.key]: e.target.value })}
              />
            </div>
          ))}
          <button
            type="submit"
            className="btnp"
            style={{ alignSelf: "flex-start" }}
            disabled={busy || unfilled.length > 0}
          >
            {busy ? "Connecting…" : "Activate Preset"}
          </button>
        </form>
      ) : null}
    </div>
  );
}

function McpServerRow({ api, server, onToggle, onDelete, onSync, onLogo, onResetLogo, busy }) {
  const fileInput = useRef(null);

  const pickLogo = (event) => {
    const file = event.target.files?.[0];
    // Cleared immediately so choosing the same file twice still fires a change.
    event.target.value = "";
    if (file) onLogo(server.id, file);
  };

  return (
    <div
      style={{
        padding: "14px 16px",
        background: "var(--surface)",
        border: "1px solid var(--line-soft)",
        borderRadius: "var(--r-surface)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "12px",
      }}
    >
      <ServiceIcon
        api={api}
        name={server.name}
        serverId={server.id}
        hasIcon={server.has_icon}
        off={!server.connected}
      />

      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <span
            style={{
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: server.connected ? "var(--green)" : "var(--text-faint)",
              display: "inline-block",
            }}
            title={server.connected ? "Connected" : "Disconnected"}
          />
          <span className="h" style={{ fontSize: "var(--t-md)" }}>
            {server.name}
          </span>
          <span
            style={{
              fontSize: "var(--t-micro)",
              fontFamily: "var(--font-mono)",
              color: "var(--text-faint)",
              background: "var(--rail)",
              padding: "2px 6px",
              borderRadius: "var(--r-pill)",
            }}
          >
            {server.transport}
          </span>
        </div>
        <p style={{ margin: "4px 0 0", color: "var(--text-dim)", fontSize: "var(--t-sm)" }}>
          {server.description ||
            (server.transport === "stdio" ? server.command : server.url)}
        </p>
        <div style={{ marginTop: "6px", display: "flex", gap: "8px", alignItems: "center" }}>
          <span style={{ fontSize: "var(--t-xs)", color: "var(--text-faint)" }}>
            {server.tools?.length || 0} tool(s) registered
          </span>
          {server.error ? (
            <span style={{ fontSize: "var(--t-xs)", color: "var(--ochre)" }}>
              {server.error}
            </span>
          ) : null}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml,.ico"
          style={{ display: "none" }}
          onChange={pickLogo}
        />
        <button
          type="button"
          className="btn"
          disabled={busy}
          title="Use your own logo for this server"
          onClick={() => fileInput.current?.click()}
        >
          Logo
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy}
          title="Drop any custom logo and look the service's own up again"
          onClick={() => onResetLogo(server.id)}
        >
          ↻
        </button>
        <button
          type="button"
          className="btn"
          disabled={busy}
          title="Force tool re-discovery"
          onClick={() => onSync(server.id)}
        >
          Sync
        </button>
        <button
          type="button"
          className="switch"
          role="switch"
          aria-checked={server.enabled}
          aria-label={`${server.enabled ? "Disable" : "Enable"} ${server.name}`}
          aria-pressed={server.enabled}
          disabled={busy}
          onClick={() => onToggle(server.id, !server.enabled)}
        >
          <i />
        </button>
        <button
          type="button"
          style={{
            background: "none",
            border: "none",
            color: "var(--text-faint)",
            cursor: "pointer",
            fontSize: "var(--t-lg)",
            padding: "4px 8px",
          }}
          title="Remove server"
          disabled={busy}
          onClick={() => onDelete(server.id, server.name)}
        >
          ×
        </button>
      </div>
    </div>
  );
}

export function Skills({ api }) {
  const { confirm, notify } = useDialog();
  const { skills, loading, error, refresh, setEnabled, setKey, pending } =
    useSkills(api);
  const [tab, setTab] = useState("skills"); // "skills" | "mcp"
  const [query, setQuery] = useState("");

  // MCP State
  const [mcpServers, setMcpServers] = useState([]);
  const [mcpPresets, setMcpPresets] = useState([]);
  const [mcpBusy, setMcpBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState("");
  const [customOpen, setCustomOpen] = useState(false);
  const [customServer, setCustomServer] = useState({
    name: "",
    transport: "stdio",
    command: "",
    args: "",
    url: "",
    description: "",
    homepage: "",
  });

  const loadMcpData = useCallback(async () => {
    try {
      const [serversRes, presetsRes] = await Promise.all([
        api.listMcpServers(),
        api.listMcpPresets(),
      ]);
      setMcpServers(serversRes.servers || []);
      setMcpPresets(presetsRes.presets || []);
    } catch (err) {
      console.error("Failed loading MCP servers/presets", err);
    }
  }, [api]);

  useEffect(() => {
    loadMcpData();
  }, [loadMcpData]);

  /* A file, as the base64 the icon endpoint takes. readAsDataURL rather than
   * readAsArrayBuffer: the prefix is trivial to strip and the alternative is
   * hand-rolling base64 over a byte array. */
  const readAsBase64 = (file) =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("could not read that file"));
      reader.onload = () => resolve(String(reader.result).split(",")[1] || "");
      reader.readAsDataURL(file);
    });

  const handleUploadLogo = async (serverId, file) => {
    setMcpBusy(true);
    try {
      await api.uploadMcpIcon(serverId, await readAsBase64(file), file.type || null);
      await loadMcpData();
    } catch (err) {
      await notify(`Could not use that image: ${err.message || err}`);
    } finally {
      setMcpBusy(false);
    }
  };

  const handleResetLogo = async (serverId) => {
    setMcpBusy(true);
    try {
      // Clear any upload first, then go back to the service's own site. Both
      // are quiet about finding nothing -- plenty of sites offer no icon.
      await api.clearMcpIcon(serverId);
      await api.refreshMcpIcon(serverId).catch(() => {});
      await loadMcpData();
    } catch (err) {
      await notify(`Could not reset that logo: ${err.message || err}`);
    } finally {
      setMcpBusy(false);
    }
  };

  const handleImportConfig = async (event) => {
    event.preventDefault();
    let parsed;
    try {
      parsed = JSON.parse(importText);
    } catch (err) {
      await notify(`That is not valid JSON: ${err.message}`);
      return;
    }
    setMcpBusy(true);
    try {
      const result = await api.importMcpServers(parsed, true);
      await loadMcpData();
      setImportText("");
      setImportOpen(false);
      const added = result.added.map((s) => s.name);
      const skipped = result.skipped.map((s) => `${s.name} (${s.reason})`);
      await notify(
        [
          added.length ? `Added: ${added.join(", ")}` : "Nothing added.",
          skipped.length ? `Skipped: ${skipped.join(", ")}` : "",
        ]
          .filter(Boolean)
          .join("\n"),
      );
    } catch (err) {
      await notify(`Import failed: ${err.message || err}`);
    } finally {
      setMcpBusy(false);
    }
  };

  const handleInstallPreset = async (presetId, values) => {
    setMcpBusy(true);
    try {
      await api.instantiateMcpPreset({ preset: presetId, values });
      await loadMcpData();
      await refresh();
    } catch (err) {
      await notify(`Failed activating preset: ${err.message || err}`);
    } finally {
      setMcpBusy(false);
    }
  };

  const handleToggleMcpServer = async (serverId, enabled) => {
    setMcpBusy(true);
    try {
      await api.updateMcpServer(serverId, { enabled });
      await loadMcpData();
      await refresh();
    } catch (err) {
      await notify(`Failed toggling server: ${err.message || err}`);
    } finally {
      setMcpBusy(false);
    }
  };

  const handleDeleteMcpServer = async (serverId, serverName) => {
    const yes = await confirm(`Delete MCP server "${serverName}"?`, {
      title: "Delete server",
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!yes) return;
    setMcpBusy(true);
    try {
      await api.deleteMcpServer(serverId);
      await loadMcpData();
      await refresh();
    } catch (err) {
      await notify(`Failed deleting server: ${err.message || err}`);
    } finally {
      setMcpBusy(false);
    }
  };

  const handleSyncMcpServer = async (serverId) => {
    setMcpBusy(true);
    try {
      await api.syncMcpServer(serverId);
      await loadMcpData();
      await refresh();
    } catch (err) {
      await notify(`Sync error: ${err.message || err}`);
    } finally {
      setMcpBusy(false);
    }
  };

  const handleAddCustomServer = async (e) => {
    e.preventDefault();
    if (!customServer.name.trim()) return;
    setMcpBusy(true);
    try {
      const argsList = customServer.args
        .split(" ")
        .map((a) => a.trim())
        .filter(Boolean);
      await api.createMcpServer({
        name: customServer.name.trim(),
        transport: customServer.transport,
        command: customServer.command.trim() || undefined,
        args: argsList,
        url: customServer.url.trim() || undefined,
        description: customServer.description.trim() || undefined,
        // Only needed for stdio: an HTTP server's logo is looked up from its
        // own endpoint host, so leaving this blank still finds one.
        homepage: customServer.homepage.trim() || undefined,
        enabled: true,
      });
      setCustomOpen(false);
      setCustomServer({
        name: "",
        transport: "stdio",
        command: "",
        args: "",
        url: "",
        description: "",
        homepage: "",
      });
      await loadMcpData();
      await refresh();
    } catch (err) {
      await notify(`Failed adding server: ${err.message || err}`);
    } finally {
      setMcpBusy(false);
    }
  };

  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return skills;
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        (s.description || "").toLowerCase().includes(needle),
    );
  }, [skills, query]);

  const on = skills.filter((s) => s.enabled).length;
  const installedServerNames = new Set(mcpServers.map((s) => s.name));

  return (
    <div className="page">
      <div className="page-head" data-tint="green">
        <div
          className="sw"
          style={{
            left: "-90px",
            top: "-110px",
            width: "280px",
            height: "280px",
            background: "#d5e8db",
          }}
        />
        <div
          className="sw"
          style={{
            left: "120px",
            top: "-70px",
            width: "130px",
            height: "130px",
            background: "#f6efd4",
          }}
        />
        <div className="inner">
          <div>
            <h1 className="h">Skills & MCP</h1>
            <p>
              Tools and Model Context Protocol servers I can use. Switch one off
              and I stop being offered it.
            </p>
          </div>
          <div className="actions">
            <button
              type="button"
              className="btn"
              onClick={() => {
                refresh();
                loadMcpData();
              }}
              disabled={loading || mcpBusy}
            >
              {loading ? "Checking…" : "Check again"}
            </button>
          </div>
        </div>
      </div>

      <div className="page-body" style={{ flexDirection: "column" }}>
        <div className="page-col" style={{ alignSelf: "stretch" }}>
          {/* Navigation Tabs */}
          <div style={{ display: "flex", gap: "8px", borderBottom: "1px solid var(--line-soft)", paddingBottom: "12px", marginBottom: "16px" }}>
            <button
              type="button"
              className={tab === "skills" ? "btnp" : "btn"}
              onClick={() => setTab("skills")}
            >
              Active Skills ({skills.length})
            </button>
            <button
              type="button"
              className={tab === "mcp" ? "btnp" : "btn"}
              onClick={() => setTab("mcp")}
            >
              MCP Servers ({mcpServers.length}) & Presets
            </button>
          </div>

          {tab === "skills" ? (
            <>
              <div className="lane">
                <span className="mi" data-strong>
                  {loading ? "Checking" : `${on} on · ${skills.length} registered`}
                </span>
                <i />
              </div>

              {skills.length > 3 ? (
                <div className="search" style={{ marginBottom: "16px" }}>
                  <input
                    type="search"
                    value={query}
                    placeholder="Filter skills by name or keyword"
                    aria-label="Filter skills"
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </div>
              ) : null}

              {error ? (
                <div className="callout" data-tint="ochre">
                  <div style={{ flex: 1 }}>
                    <span className="h" style={{ fontSize: "var(--t-lg)" }}>
                      {error.answered ? "That did not work" : "Could not reach the server"}
                    </span>
                    <p style={{ margin: "7px 0 0", color: "var(--text-dim)", fontSize: "var(--t-sm)", lineHeight: 1.7 }}>
                      {error.message}
                    </p>
                  </div>
                  <button type="button" className="btn" onClick={refresh}>
                    Try again
                  </button>
                </div>
              ) : null}

              {skills.length === 0 && !loading ? (
                <p className="p" style={{ color: "var(--text-dim)" }}>
                  No skills registered. Go to the <strong>MCP Servers & Presets</strong> tab to activate GitHub, Figma, Google Workspace, or custom MCP servers.
                </p>
              ) : (
                <div className="skill-list">
                  {shown.map((skill) => (
                    <SkillRow
                      key={skill.name}
                      skill={skill}
                      busy={pending.includes(skill.name)}
                      onToggle={setEnabled}
                      onKey={setKey}
                    />
                  ))}
                </div>
              )}
            </>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
              {/* Presets */}
              <div>
                <div className="lane" style={{ marginBottom: "12px" }}>
                  <span className="mi" data-strong>
                    Pre-configured G Suite & Figma Integrations
                  </span>
                  <i />
                </div>
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
                    gap: "12px",
                  }}
                >
                  {mcpPresets.map((preset) => (
                    <PresetCard
                      key={preset.id}
                      api={api}
                      preset={preset}
                      isInstalled={installedServerNames.has(preset.name)}
                      onInstall={handleInstallPreset}
                      busy={mcpBusy}
                    />
                  ))}
                </div>
              </div>

              {/* Installed MCP Servers */}
              <div>
                <div
                  className="lane"
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: "12px",
                  }}
                >
                  <span className="mi" data-strong>
                    Installed MCP Servers ({mcpServers.length})
                  </span>
                  <div style={{ display: "flex", gap: "8px" }}>
                    <button
                      type="button"
                      className="btn"
                      onClick={() => {
                        setImportOpen(false);
                        setCustomOpen((was) => !was);
                      }}
                    >
                      {customOpen ? "Close Form" : "+ Add Custom MCP Server"}
                    </button>
                    <button
                      type="button"
                      className="btn"
                      title="Paste an mcpServers config from anywhere"
                      onClick={() => {
                        setCustomOpen(false);
                        setImportOpen((was) => !was);
                      }}
                    >
                      {importOpen ? "Close Import" : "Import Config"}
                    </button>
                  </div>
                </div>

                {importOpen ? (
                  <form
                    onSubmit={handleImportConfig}
                    style={{
                      marginBottom: "16px",
                      padding: "16px",
                      background: "var(--rail)",
                      borderRadius: "var(--r-surface)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "12px",
                    }}
                  >
                    <span className="h" style={{ fontSize: "var(--t-md)" }}>
                      Import MCP Servers
                    </span>
                    <p
                      style={{
                        margin: 0,
                        fontSize: "var(--t-sm)",
                        color: "var(--text-dim)",
                        lineHeight: 1.5,
                      }}
                    >
                      Paste the <code>mcpServers</code> block from any MCP install page.
                      Both the whole config file and the bare mapping work. Servers
                      whose names are already taken are reported and skipped, not
                      overwritten.
                    </p>
                    <textarea
                      value={importText}
                      required
                      rows={9}
                      spellCheck={false}
                      placeholder={'{\n  "mcpServers": {\n    "sqlite": {\n      "command": "uvx",\n      "args": ["mcp-server-sqlite", "--db-path", "/data/app.db"]\n    }\n  }\n}'}
                      style={{
                        padding: "10px",
                        borderRadius: "var(--r-surface)",
                        border: "1px solid var(--line)",
                        background: "var(--surface)",
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--t-sm)",
                        resize: "vertical",
                      }}
                      onChange={(e) => setImportText(e.target.value)}
                    />
                    <button
                      type="submit"
                      className="btnp"
                      style={{ alignSelf: "flex-start" }}
                      disabled={mcpBusy}
                    >
                      {mcpBusy ? "Importing…" : "Import & Connect"}
                    </button>
                  </form>
                ) : null}

                {customOpen ? (
                  <form
                    onSubmit={handleAddCustomServer}
                    style={{
                      padding: "16px",
                      background: "var(--surface)",
                      border: "1px solid var(--line-soft)",
                      borderRadius: "var(--r-surface)",
                      marginBottom: "16px",
                      display: "flex",
                      flexDirection: "column",
                      gap: "12px",
                    }}
                  >
                    <span className="h" style={{ fontSize: "var(--t-md)" }}>
                      Add Custom MCP Server
                    </span>
                    <div style={{ display: "flex", gap: "12px" }}>
                      <input
                        type="text"
                        placeholder="Server Name (e.g. postgres, filesystem)"
                        value={customServer.name}
                        required
                        style={{
                          flex: 1,
                          padding: "6px 10px",
                          borderRadius: "var(--r-surface)",
                          border: "1px solid var(--line)",
                        }}
                        onChange={(e) => setCustomServer({ ...customServer, name: e.target.value })}
                      />
                      <select
                        value={customServer.transport}
                        style={{
                          padding: "6px 10px",
                          borderRadius: "var(--r-surface)",
                          border: "1px solid var(--line)",
                        }}
                        onChange={(e) => setCustomServer({ ...customServer, transport: e.target.value })}
                      >
                        <option value="stdio">stdio (subprocess)</option>
                        <option value="sse">sse (HTTP/SSE)</option>
                      </select>
                    </div>

                    {customServer.transport === "stdio" ? (
                      <>
                        <input
                          type="text"
                          placeholder="Command (e.g. npx, uvx, python)"
                          value={customServer.command}
                          required
                          style={{
                            padding: "6px 10px",
                            borderRadius: "var(--r-surface)",
                            border: "1px solid var(--line)",
                          }}
                          onChange={(e) => setCustomServer({ ...customServer, command: e.target.value })}
                        />
                        <input
                          type="text"
                          placeholder="Arguments (space-separated, e.g. -y @mcp/server-sqlite /data/db.sqlite)"
                          value={customServer.args}
                          style={{
                            padding: "6px 10px",
                            borderRadius: "var(--r-surface)",
                            border: "1px solid var(--line)",
                          }}
                          onChange={(e) => setCustomServer({ ...customServer, args: e.target.value })}
                        />
                      </>
                    ) : (
                      <input
                        type="url"
                        placeholder="Endpoint URL (e.g. https://api.example.com/sse)"
                        value={customServer.url}
                        required
                        style={{
                          padding: "6px 10px",
                          borderRadius: "var(--r-surface)",
                          border: "1px solid var(--line)",
                        }}
                        onChange={(e) => setCustomServer({ ...customServer, url: e.target.value })}
                      />
                    )}

                    <input
                      type="text"
                      placeholder="Description (optional)"
                      value={customServer.description}
                      style={{
                        padding: "6px 10px",
                        borderRadius: "var(--r-surface)",
                        border: "1px solid var(--line)",
                      }}
                      onChange={(e) => setCustomServer({ ...customServer, description: e.target.value })}
                    />

                    <input
                      type="text"
                      placeholder={
                        customServer.transport === "stdio"
                          ? "Website for the logo (optional, e.g. sentry.io)"
                          : "Website for the logo (optional — taken from the URL if blank)"
                      }
                      value={customServer.homepage}
                      style={{
                        padding: "6px 10px",
                        borderRadius: "var(--r-surface)",
                        border: "1px solid var(--line)",
                      }}
                      onChange={(e) => setCustomServer({ ...customServer, homepage: e.target.value })}
                    />

                    <button type="submit" className="btnp" style={{ alignSelf: "flex-start" }} disabled={mcpBusy}>
                      {mcpBusy ? "Connecting…" : "Save & Connect Server"}
                    </button>
                  </form>
                ) : null}

                {mcpServers.length === 0 ? (
                  <p style={{ color: "var(--text-dim)", fontSize: "var(--t-sm)" }}>
                    No MCP servers configured yet. Pick an integration above — GitHub, Figma, Exa, Context7 and more — or add your own with "+ Add Custom MCP Server" or "Import Config".
                  </p>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {mcpServers.map((server) => (
                      <McpServerRow
                        key={server.id}
                        api={api}
                        server={server}
                        onToggle={handleToggleMcpServer}
                        onDelete={handleDeleteMcpServer}
                        onSync={handleSyncMcpServer}
                        onLogo={handleUploadLogo}
                        onResetLogo={handleResetLogo}
                        busy={mcpBusy}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
