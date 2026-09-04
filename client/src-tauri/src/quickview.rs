//! The hotkey panel.
//!
//! A temporary conversation summoned over whatever the reader is already doing,
//! and dismissed without ceremony. Everything it does once it is open belongs to
//! the React app and the server; what lives here is the part a webview cannot do
//! for itself -- a shortcut that fires while another application is focused, a
//! window with no chrome that floats above everything, and the frosted material
//! behind it.
//!
//! The shortcut is configurable and defaults to Ctrl+Space, which on macOS is
//! *already taken*: it is the system default for "Select the previous input
//! source". Registration then fails, and this says so out loud rather than
//! leaving a hotkey that silently does nothing -- see `register`.

use tauri::{AppHandle, Emitter, Manager, WebviewWindow};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

/// What the panel opens on when nothing has been configured.
pub const DEFAULT_SHORTCUT: &str = "ctrl+space";

/// The window label, matching `tauri.conf.json`.
const LABEL: &str = "quickview";

/// Read the shortcut from the environment, falling back to the default.
///
/// An environment variable rather than a settings file *for now*: the panel has
/// to exist before there is a screen to configure it from, and this keeps the
/// choice in one place until that screen lands.
pub fn shortcut_spec() -> String {
    std::env::var("COURIER_QUICKVIEW_SHORTCUT").unwrap_or_else(|_| DEFAULT_SHORTCUT.into())
}

/// Frost the window behind the page.
///
/// `backdrop-filter` in CSS blurs what is behind an element *within the
/// document*. Blurring the desktop behind the window is a native material, so
/// it is applied to the window and the page is left transparent on top of it.
fn frost(window: &WebviewWindow) {
    #[cfg(target_os = "macos")]
    {
        use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial, NSVisualEffectState};
        // HudWindow is the material the system's own floating panels use, and
        // `Active` keeps it frosted even when the panel does not hold focus --
        // which is the normal case here, since it opens over another app.
        let _ = apply_vibrancy(
            window,
            NSVisualEffectMaterial::HudWindow,
            Some(NSVisualEffectState::Active),
            Some(12.0),
        );
    }
    #[cfg(target_os = "windows")]
    {
        let _ = window_vibrancy::apply_acrylic(window, Some((18, 18, 22, 125)));
    }
}

/// Show the panel, or put it away if it is already up.
///
/// Toggling rather than only showing is what makes the shortcut a switch: the
/// same key that summoned it dismisses it, so the panel never has to be aimed
/// at to be closed.
pub fn toggle(app: &AppHandle) {
    let Some(window) = app.get_webview_window(LABEL) else {
        eprintln!("[quickview] no window labelled {LABEL}");
        return;
    };

    if window.is_visible().unwrap_or(false) {
        let _ = window.hide();
        return;
    }

    // Centred on show rather than once at startup: the panel should arrive on
    // the display the reader is looking at, and that is not necessarily the one
    // it was last on.
    let _ = window.center();
    let _ = window.show();
    let _ = window.set_focus();
    // The page decides what "opened" means -- a fresh conversation, the input
    // focused -- because that is a product question, not a windowing one.
    let _ = window.emit_to(LABEL, "quickview://opened", ());
}

/// Register the global shortcut. Returns the spec on success.
///
/// A failure here is reported and swallowed. The rest of the app works without
/// the panel, and taking the whole thing down because a key combination was
/// already spoken for would be a poor trade -- but it is printed, because a
/// hotkey that was never registered and a hotkey that does nothing look
/// identical from the keyboard.
pub fn register(app: &AppHandle) -> Option<String> {
    let spec = shortcut_spec();
    let shortcut: Shortcut = match spec.parse() {
        Ok(parsed) => parsed,
        Err(err) => {
            eprintln!("[quickview] {spec:?} is not a shortcut I can parse: {err}");
            return None;
        }
    };

    let handle = app.clone();
    let outcome = app.global_shortcut().on_shortcut(shortcut, move |_, _, event| {
        // Press only. Without this the panel toggles twice per keypress -- once
        // down, once up -- and appears to ignore the shortcut entirely.
        if event.state() == ShortcutState::Pressed {
            toggle(&handle);
        }
    });

    match outcome {
        Ok(()) => Some(spec),
        Err(err) => {
            eprintln!(
                "[quickview] could not register {spec:?}: {err}\n\
                 [quickview] on macOS Ctrl+Space is the system shortcut for \
                 'Select the previous input source'. Either turn that off in \
                 System Settings > Keyboard > Keyboard Shortcuts > Input Sources, \
                 or set COURIER_QUICKVIEW_SHORTCUT to something else."
            );
            None
        }
    }
}

/// Prepare the window at startup: frost it, and teach it to put itself away.
pub fn setup(app: &AppHandle) {
    let Some(window) = app.get_webview_window(LABEL) else {
        return;
    };
    frost(&window);

    // Dismiss on losing focus, the way every other summoned panel behaves:
    // clicking back into the thing you were doing should not leave this
    // floating over it. Esc is handled in the page, where the keystroke lands.
    let hidden = window.clone();
    window.on_window_event(move |event| {
        // Handle window events for focus changes and closing
        match event {
            tauri::WindowEvent::Focused(false) => {
                // Hide when losing focus (as before)
                let _ = hidden.hide();
            }
            _ => {}
        }
    });
}
